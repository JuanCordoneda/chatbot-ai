from flask import Flask, request, jsonify, Response, stream_with_context
import os
import json
import time
import uuid
import threading
import anthropic
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from google.oauth2 import service_account
from googleapiclient.discovery import build
import sys
sys.path.insert(0, os.path.dirname(__file__))
from modules.engagement_flow import es_link_instagram, extraer_link, procesar_post

app = Flask(__name__)
claude = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

CALENDAR_ID = os.environ.get("CALENDAR_ID", "primary")
CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "admin-key.json")
TZ = ZoneInfo("America/Argentina/Buenos_Aires")
SESSION_TTL_HOURS = 6
MAX_SESSION_MESSAGES = 20

_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()
_calendar_service = None
_calendar_lock = threading.Lock()

def _clasificar_error_ia(err: str) -> dict:
    """Traduce el error crudo de la API de Claude a algo que el vendedor entienda,
    y dice si conviene reintentar. Devuelve {mensaje, motivo, reintentable}."""
    e = (err or "").lower()
    if "overloaded" in e or "529" in e:
        return {"motivo": "saturada", "reintentable": True,
                "mensaje": "La IA está saturada en este momento. "
                           "Esperá unos segundos y reintentá."}
    if "rate_limit" in e or "429" in e:
        return {"motivo": "rate_limit", "reintentable": True,
                "mensaje": "Demasiadas generaciones seguidas. "
                           "Esperá un momento y reintentá."}
    if "credit balance" in e or "billing" in e or "insufficient" in e:
        return {"motivo": "sin_credito", "reintentable": False,
                "mensaje": "El servicio de IA se quedó sin crédito. "
                           "Avisá al administrador para recargarlo."}
    if "timeout" in e or "timed out" in e:
        return {"motivo": "timeout", "reintentable": True,
                "mensaje": "La IA tardó demasiado en responder. Reintentá."}
    return {"motivo": "desconocido", "reintentable": True,
            "mensaje": "No se pudieron generar los comentarios (error de la IA). "
                       "Reintentá en un momento."}


_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
# Cuánto vive un job en memoria después de creado (el front ya terminó de leerlo
# mucho antes; esto es solo para no acumular basura).
_JOB_TTL = int(os.environ.get("JOB_TTL", "1800"))    # 30 min


def _datos_cliente(ig_username: str, es_cliente: bool):
    """(ranges, gender) del cliente del post. Si el post NO es de un cliente
    cargado, salen del cliente GENÉRICO global (lo configura el admin, es el
    mismo para todos los vendedores). Antes quedaban vacíos y el modal de
    órdenes no ofrecía la cantidad random."""
    try:
        from common import repository as _repo
        row = _repo.get_client_by_ig_username(ig_username) if (ig_username and es_cliente) else None
        if row is None:
            row = _repo.get_generic_client()
        return (row or {}).get("ranges") or {}, (row or {}).get("gender")
    except Exception as e:
        print(f"[cliente] datos no disponibles ({e})", flush=True)
        return {}, None


# ── Session management ────────────────────────────────────────────────────────

def _get_session(phone: str) -> tuple[list, bool]:
    now = datetime.now(tz=timezone.utc)
    with _sessions_lock:
        session = _sessions.get(phone)
        if session is None or (now - session["last_activity"]).total_seconds() > SESSION_TTL_HOURS * 3600:
            _sessions[phone] = {"messages": [], "last_activity": now}
            return [], True
        session["last_activity"] = now
        return session["messages"], False


def _save_session(phone: str, messages: list):
    with _sessions_lock:
        if phone in _sessions:
            _sessions[phone]["messages"] = messages[-MAX_SESSION_MESSAGES:]
            _sessions[phone]["last_activity"] = datetime.now(tz=timezone.utc)


# ── Calendar ──────────────────────────────────────────────────────────────────

def _get_calendar_service():
    global _calendar_service
    if _calendar_service is None:
        with _calendar_lock:
            if _calendar_service is None:
                creds = service_account.Credentials.from_service_account_file(
                    CREDENTIALS_PATH,
                    scopes=["https://www.googleapis.com/auth/calendar"],
                )
                _calendar_service = build("calendar", "v3", credentials=creds)
    return _calendar_service


def check_availability(date_str: str, time_str: str, duration_hours: int = 2) -> dict:
    try:
        dt_start = datetime.fromisoformat(f"{date_str}T{time_str}:00").replace(tzinfo=TZ)
        dt_end = dt_start + timedelta(hours=duration_hours)
        service = _get_calendar_service()
        result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=dt_start.isoformat(),
            timeMax=dt_end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        events = result.get("items", [])
        if events:
            return {
                "available": False,
                "message": f"El horario {time_str} del {date_str} ya está reservado.",
                "conflicts": [e.get("summary", "Reserva") for e in events],
            }
        return {"available": True, "message": f"El horario {time_str} del {date_str} está disponible."}
    except Exception as e:
        return {"available": False, "message": f"Error consultando disponibilidad: {e}"}


def create_reservation(date_str: str, time_str: str, client_name: str, client_email: str, duration_hours: int = 2) -> dict:
    try:
        dt_start = datetime.fromisoformat(f"{date_str}T{time_str}:00").replace(tzinfo=TZ)
        dt_end = dt_start + timedelta(hours=duration_hours)
        service = _get_calendar_service()
        event = {
            "summary": f"Reserva - {client_name}",
            "description": f"Cliente: {client_name}\nEmail: {client_email}",
            "start": {"dateTime": dt_start.isoformat(), "timeZone": "America/Argentina/Buenos_Aires"},
            "end": {"dateTime": dt_end.isoformat(), "timeZone": "America/Argentina/Buenos_Aires"},
        }
        created = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        return {
            "success": True,
            "message": f"Reserva confirmada para {client_name} el {date_str} a las {time_str}.",
            "event_id": created.get("id"),
        }
    except Exception as e:
        return {"success": False, "message": f"Error creando la reserva: {e}"}


# ── Tools ─────────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "check_availability",
        "description": "Consulta si hay disponibilidad en el calendario para una fecha y hora. Llamar SIEMPRE antes de confirmar cualquier reserva.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_str": {"type": "string", "description": "Fecha en formato YYYY-MM-DD, ej: 2026-07-15"},
                "time_str": {"type": "string", "description": "Hora en formato HH:MM, ej: 20:00"},
                "duration_hours": {"type": "integer", "description": "Duración en horas (por defecto 2)", "default": 2},
            },
            "required": ["date_str", "time_str"],
        },
    },
    {
        "name": "create_reservation",
        "description": "Crea una reserva en el calendario. Llamar solo después de confirmar disponibilidad y obtener nombre y email del cliente.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_str": {"type": "string", "description": "Fecha en formato YYYY-MM-DD"},
                "time_str": {"type": "string", "description": "Hora en formato HH:MM"},
                "client_name": {"type": "string", "description": "Nombre completo del cliente"},
                "client_email": {"type": "string", "description": "Email del cliente"},
                "duration_hours": {"type": "integer", "description": "Duración en horas (por defecto 2)", "default": 2},
            },
            "required": ["date_str", "time_str", "client_name", "client_email"],
        },
    },
]

TOOL_HANDLERS = {
    "check_availability": lambda i: check_availability(i["date_str"], i["time_str"], i.get("duration_hours", 2)),
    "create_reservation": lambda i: create_reservation(i["date_str"], i["time_str"], i["client_name"], i["client_email"], i.get("duration_hours", 2)),
}


def run_tool(name: str, input: dict) -> str:
    handler = TOOL_HANDLERS.get(name)
    result = handler(input) if handler else {"error": f"Herramienta desconocida: {name}"}
    return json.dumps(result, ensure_ascii=False)


# ── System prompt ─────────────────────────────────────────────────────────────

def build_system_prompt(is_first_message: bool = False) -> str:
    today = datetime.now(tz=TZ).strftime("%d/%m/%Y")
    greeting = (
        "\nPRIMER MENSAJE: Saludá de forma corta y natural, presentá el bar en una línea y mencioná "
        "que pueden reservar o preguntar lo que quieran. Máximo 3 frases.\n"
        if is_first_message else ""
    )
    return f"""Hoy es {today}.
{greeting}
Sos quien atiende el WhatsApp del bar. No sos un bot, sos una persona real que trabaja ahí. Nunca lo menciones.

ESTILO: Corto y directo, como un mensaje de WhatsApp. Una o dos frases máximo. Sin listas, asteriscos, títulos ni formato. Sin emojis salvo que el cliente los use primero. Respondés en el idioma del cliente. Si cambia de idioma, vos también.

No uses frases de bot: "claro que sí", "por supuesto", "encantado de ayudarte". Hablá como habla la gente.

EL BAR:
Horarios: lunes a viernes 18-02, sábados y domingos 16-03.
Carta: cerveza artesanal $1200, copa de vino $1500, cóctel de la casa $2000, gaseosa $800.
Ante otras dudas del menú o del lugar, decile que llame al local.

RESERVAS: Tenés herramientas reales para el calendario. Úsalas siempre.
Flujo: pedís fecha/hora → check_availability → si hay lugar pedís nombre y mail → create_reservation → confirmás en una frase.
Si está ocupado, lo decís natural y ofrecés el horario más cercano.

Recordá el nombre del cliente cuando te lo diga y usalo con naturalidad."""


# ── Main route ────────────────────────────────────────────────────────────────

@app.route("/getresponsegpt", methods=["GET"])
def get_response_gpt():
    user_prompt = request.args.get("user_prompt", "").strip()
    phone = request.args.get("phone_number", "default")

    if not user_prompt:
        return "", 200

    if es_link_instagram(user_prompt):
        return procesar_post(extraer_link(user_prompt))

    messages, is_first = _get_session(phone)
    messages.append({"role": "user", "content": user_prompt})

    while True:
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=build_system_prompt(is_first),
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            text = next((b.text for b in response.content if b.type == "text"), "")
            messages.append({"role": "assistant", "content": text})
            _save_session(phone, messages)
            return text

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = [
                {"type": "tool_result", "tool_use_id": b.id, "content": run_tool(b.name, b.input)}
                for b in response.content if b.type == "tool_use"
            ]
            messages.append({"role": "user", "content": tool_results})
        else:
            break

    _save_session(phone, messages)
    return "Hubo un problema, intentá de nuevo."


@app.route("/health/instagram", methods=["GET"])
def health_instagram():
    from modules.post_processor import _fetch_instagram_api
    test_shortcode = request.args.get("shortcode", "")
    if not test_shortcode:
        return jsonify({"error": "Pasá ?shortcode=<un_shortcode_publico_valido> para probar"}), 400
    try:
        result = _fetch_instagram_api(test_shortcode)
        if not result.get("owner_username"):
            return jsonify({"ok": False, "error": "sin sesión o post no accesible"}), 503
        return jsonify({"ok": True, "owner_username": result.get("owner_username", "")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503


@app.route("/clear_session", methods=["GET"])
def clear_session():
    phone = request.args.get("phone_number", "default")
    with _sessions_lock:
        _sessions.pop(phone, None)
    return "ok"


# ── Web app endpoints ─────────────────────────────────────────────────────────

@app.route("/procesar_post", methods=["POST"])
def procesar_post_web():
    data = request.get_json(silent=True) or {}
    post_url = data.get("url", "").strip()

    if not post_url:
        return jsonify({"error": "Falta el campo 'url'"}), 400
    if not es_link_instagram(post_url):
        return jsonify({"error": "El link no parece ser de Instagram"}), 400

    # "Cargar más" manda los comentarios ya generados para que la nueva tanda no
    # los repita ni parafrasee.
    evitar = data.get("evitar", []) or []

    job_id = str(uuid.uuid4())
    with _jobs_lock:
        # Purga de jobs viejos: el dict crecía para siempre (cada job se queda con
        # sus ~70 comentarios en memoria). Con muchas generaciones al día eso solo
        # sube, y el proceso termina sin RAM — otra vía de "se satura el sistema".
        limite = time.time() - _JOB_TTL
        viejos = [k for k, v in _jobs.items() if v.get("_creado", 0) < limite]
        for k in viejos:
            _jobs.pop(k, None)
        if viejos:
            print(f"[jobs] purgados {len(viejos)} jobs viejos (quedan {len(_jobs)})", flush=True)
        _jobs[job_id] = {
            "_creado": time.time(),
            "comentarios": [],
            "progreso": [],
            "meta": {},
            "scrape_ready": False,
            "transcription_ready": False,
            "current_chunk": "",
            "step": "",
            "done": False,
            "error": None,
            "resets": 0,
            # Se prende cuando el vendedor ya eligió sus comentarios y apretó
            # "Publicar seleccionados": no tiene sentido seguir quemando tokens
            # generando comentarios que nadie va a mirar.
            "cancelado": False,
        }

    def run():
        from modules.post_processor import scrape_post
        from modules.ai_generator import generar_comentarios_stream, GENERIC_CLIENT_ID
        from modules.engagement_flow import detectar_cliente, alias_owner

        job = _jobs[job_id]
        try:
            t0 = time.time()
            job["progreso"].append("Accediendo al post de Instagram...")

            # Fast fetch para preview inmediato en UI
            from modules.post_processor import _fetch_fast, extract_shortcode, is_video_url
            shortcode = extract_shortcode(post_url)
            if shortcode:
                try:
                    fast_preview = _fetch_fast(shortcode)
                    if fast_preview.get("caption") or fast_preview.get("owner_username"):
                        url_is_video = is_video_url(post_url)
                        # Mismo alias de collab que abajo: el preview también
                        # alimenta el _clientIg del front.
                        preview_owner = alias_owner(fast_preview.get("owner_username", "")) or ""
                        preview_client_id = detectar_cliente(preview_owner) if preview_owner else None
                        preview_ranges, preview_gender = _datos_cliente(
                            preview_owner, bool(preview_client_id))
                        preview_meta = {
                            "caption": fast_preview.get("caption", ""),
                            "owner_username": preview_owner,
                            "client_id": preview_client_id or preview_owner,
                            # Sin cliente detrás del post, el front pide elegir
                            # a mano de qué campaña sale la plata.
                            "cliente_asignado": bool(preview_client_id),
                            "photo_description": "",
                            "transcription": "",
                            "is_video": url_is_video or fast_preview.get("is_video", False),
                            # Rangos ya desde el preview: el modal de órdenes puede
                            # ofrecer la cantidad random sin esperar el scrape completo.
                            "ranges": preview_ranges,
                            "gender": preview_gender,
                        }
                        job["meta"] = preview_meta
                        job["scrape_ready"] = True
                        if preview_meta["is_video"]:
                            job["step"] = "transcription"
                            job["progreso"].append("Video detectado. Generando transcripción (Menos de 60 segundos)...")
                        else:
                            job["step"] = "transcription"
                            job["progreso"].append("Generando transcripción (Menos de 60 segundos)...")

                except Exception:
                    pass

            # Garantizar que el step esté seteado antes del scrape lento
            # (puede ser video aunque el fast preview no lo haya detectado)
            if job["step"] != "transcription":
                job["step"] = "transcription"

            post_data = scrape_post(post_url)
            t_scrape = time.time() - t0
            print(f"[TIMING] scrape: {t_scrape:.2f}s", flush=True)

            job["step"] = ""
            if post_data.transcription and not post_data.transcription.startswith("("):
                job["progreso"].append("Transcripción lista.")
            elif post_data.transcription.startswith("("):
                # Falló: mostramos el motivo concreto, no un silencio.
                job["progreso"].append("⚠️ " + post_data.transcription.strip("()"))
            elif post_data.photo_description:
                job["progreso"].append("Imagen analizada.")

            # Collabs: un post publicado desde la cuenta partner es del cliente
            # principal. Resolvemos el alias UNA vez, acá, y de ahí en adelante
            # todo (prompt, rangos, género y la CAMPAÑA de la que sale la plata)
            # usa el @usuario del cliente real. Antes el alias solo lo aplicaba
            # detectar_cliente por dentro: el resto del flujo veía la cuenta de
            # la collab, no encontraba campaña y el tráfico se le descontaba a
            # la venta por defecto del .env.
            owner_ig = alias_owner(post_data.owner_username) if post_data.owner_username else None
            client_id = detectar_cliente(owner_ig) if owner_ig else None
            print(f"[client] owner_username={post_data.owner_username!r} → owner_ig={owner_ig!r} "
                  f"→ client_id={client_id!r}", flush=True)
            if client_id:
                job["progreso"].append(f"Cliente detectado: {client_id}")

            # Prompt: si hay cliente mapeado usamos su propio prompt (owner_ig);
            # si no, el prompt GENÉRICO. Antes caía en el de Peter Fournier, que
            # mete sus personajes (Holly, Natalie...), sus inside jokes y sus
            # @menciones en posts de cuentas que no tienen nada que ver.
            prompt_client_id = owner_ig.lower() if client_id else GENERIC_CLIENT_ID

            # Rangos de cantidades del cliente (TAREA 6): el front los usa para
            # autocompletar likes/views/shares con un valor random dentro del rango.
            # ranges: el front autocompleta likes/views/shares con un valor random
            # dentro del rango. gender: male/female -> UNA sección, None -> mixto.
            # Sin cliente, ambos salen del genérico (ver _datos_cliente).
            ranges, client_gender = _datos_cliente(owner_ig, bool(client_id))

            job["meta"] = {
                "client_id": client_id or post_data.owner_full_name or post_data.owner_username,
                "cliente_asignado": bool(client_id),
                # Ya con el alias resuelto: es el @usuario con el que el front
                # pide la campaña al enviar tráfico.
                "owner_username": owner_ig or post_data.owner_username,
                "transcription": post_data.transcription,
                "photo_description": post_data.photo_description,
                "caption": post_data.caption,
                "is_video": post_data.is_video,
                "ranges": ranges,
                # male/female -> el front muestra UNA sola sección; None -> mixto (2 columnas)
                "gender": client_gender,
            }
            job["scrape_ready"] = True
            job["transcription_ready"] = True

            # Cancelaron durante el scrape: ni arrancamos la generación.
            if job["cancelado"]:
                print(f"[jobs] {job_id} cancelado antes de generar", flush=True)
                job["done"] = True
                return

            t1 = time.time()
            job["progreso"].append("Generando comentarios con IA...")
            for tipo, data in generar_comentarios_stream(
                post_data.caption, post_data.comments, prompt_client_id,
                post_data.transcription, post_data.photo_description,
                post_data.is_video, evitar,
                image_b64=post_data.image_b64,
                image_media_type=post_data.image_media_type,
                client_gender=client_gender,
            ):
                # Cortar acá deja de consumir el stream de la API: la conexión
                # se cierra al salir del for y no se generan más comentarios.
                if job["cancelado"]:
                    print(f"[jobs] {job_id} cancelado por el usuario: corto la generación", flush=True)
                    job["progreso"].append("Generación cortada: ya publicaste los seleccionados.")
                    break
                if tipo == "chunk":
                    job["current_chunk"] += data
                elif tipo == "comentario":
                    job["comentarios"].append(data)
                    job["current_chunk"] = ""
                elif tipo == "reset":
                    # la generación salió cortada y se reintenta: descartamos lo emitido
                    job["comentarios"] = []
                    job["current_chunk"] = ""
                    job["resets"] += 1
            t_ai = time.time() - t1
            print(f"[TIMING] ai generation: {t_ai:.2f}s | total: {time.time()-t0:.2f}s", flush=True)
            job["progreso"].append(f"[debug] IA: {t_ai:.2f}s | total: {time.time()-t0:.2f}s")

            job["done"] = True
        except Exception as e:
            print(f"[ERROR] job failed: {e}", flush=True)
            job["error"] = str(e)
            job["error_info"] = _clasificar_error_ia(str(e))
            job["done"] = True

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/procesar_post/cancelar/<job_id>", methods=["POST"])
def procesar_post_cancelar(job_id):
    """Corta la generación en curso. La llama el front cuando el vendedor aprieta
    "Publicar seleccionados": ya eligió, generar más es gastar tokens al pedo."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return jsonify({"cancelado": False, "motivo": "job no encontrado"}), 404
        ya_estaba = job["done"]
        job["cancelado"] = True
    return jsonify({"cancelado": True, "ya_terminado": ya_estaba})


@app.route("/procesar_post/stream/<job_id>", methods=["GET"])
def procesar_post_stream(job_id):
    with _jobs_lock:
        if job_id not in _jobs:
            return jsonify({"error": "Job no encontrado"}), 404

    offset_c = int(request.args.get("offset", 0))
    offset_p = int(request.args.get("progreso_offset", 0))
    # Cuántos "reset" ya aplicó el cliente. Al reconectar, así NO le re-emitimos
    # resets viejos (que le borrarían los comentarios ya mostrados).
    resets_seen = int(request.args.get("resets", 0))

    def generate():
        def evento(tipo, **kwargs):
            return f"data: {json.dumps({'tipo': tipo, **kwargs}, ensure_ascii=False)}\n\n"

        job = _jobs[job_id]
        oc = offset_c
        op = offset_p
        scrape_sent = offset_c > 0
        transcription_sent = offset_c > 0
        last_chunk = ""
        last_step = ""
        seen_resets = resets_seen

        while True:
            # Cancelado: se cierra el stream sin más eventos (el front ya está
            # en la pantalla de órdenes y no quiere ver comentarios nuevos).
            if job.get("cancelado"):
                yield evento("cancelado")
                break

            while op < len(job["progreso"]):
                yield evento("progreso", mensaje=job["progreso"][op])
                op += 1

            # Si la generación se reinició (salió cortada), avisamos al front para
            # que descarte los comentarios ya mostrados y arrancamos el offset de cero.
            if job.get("resets", 0) > seen_resets:
                yield evento("reset")
                oc = 0
                last_chunk = ""
                seen_resets = job["resets"]

            if not scrape_sent and job["scrape_ready"]:
                yield evento("scrape", **job["meta"])
                scrape_sent = True

            current_step = job["step"]
            if current_step != last_step:
                yield evento("step", nombre=current_step)
                last_step = current_step

            if not transcription_sent and job.get("transcription_ready"):
                yield evento("scrape", **job["meta"])
                yield evento("transcripcion", texto=job["meta"].get("transcription", ""))
                transcription_sent = True

            while oc < len(job["comentarios"]):
                yield evento("comentario", texto=job["comentarios"][oc], index=oc)
                oc += 1
                last_chunk = ""

            # Emitir chunk en curso si cambió
            current = job["current_chunk"]
            if current and current != last_chunk:
                yield evento("chunk", texto=current)
                last_chunk = current

            if job["done"]:
                if job["error"]:
                    info = job.get("error_info") or _clasificar_error_ia(job["error"])
                    # parcial = ya scrapeamos el post (hay transcripción/descripción
                    # que mostrar). El front, en ese caso, no borra la pantalla:
                    # deja lo que salió y avisa que la IA falló, con opción a reintentar.
                    yield evento("error", mensaje=info["mensaje"],
                                 motivo=info["motivo"], reintentable=info["reintentable"],
                                 parcial=bool(job.get("scrape_ready")))
                else:
                    yield evento("listo", **job["meta"], total=len(job["comentarios"]))
                break

            time.sleep(0.05)

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/publicar", methods=["POST"])
def publicar_web():
    data = request.get_json(silent=True) or {}
    post_url    = data.get("url", "").strip()
    comentarios = data.get("comentarios", [])
    ordenes     = data.get("ordenes", [])
    disponible  = float(data.get("disponible", 150))

    if not post_url or not comentarios:
        return jsonify({"error": "Faltan datos (url o comentarios)"}), 400

    try:
        from modules.reporter import generar_informe
        resultado = None
        error = None
        try:
            from modules.growi_client import ejecutar_campana
            resultado = ejecutar_campana(post_url, comentarios, ordenes, disponible)
            informe = generar_informe(post_url, comentarios, resultado)
        except NotImplementedError as e:
            error = str(e)
            informe = generar_informe(post_url, comentarios, None, error=error)
        except Exception as e:
            error = f"Error en Growi: {e}"
            informe = generar_informe(post_url, comentarios, None, error=error)

        # `informe` es el texto plano de siempre (queda como detalle técnico);
        # `resultado` es lo mismo pero en campos, para que el front arme la
        # pantalla de resultado en vez de imprimir un bloque de texto.
        return jsonify({"informe": informe, "resultado": {
            "ok": bool(resultado and resultado.success) and not error,
            "insertadas": resultado.insertadas if resultado else 0,
            "messages": (resultado.messages if resultado else []) or [],
            "warnings": (resultado.warnings if resultado else []) or [],
            "errors": ([error] if error else []) + ((resultado.errors if resultado else []) or []),
        }})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)
