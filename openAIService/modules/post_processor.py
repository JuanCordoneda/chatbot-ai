import re
import os
import json
import html as html_lib
import subprocess
import tempfile
import time
import threading
import concurrent.futures
import requests as req
from dataclasses import dataclass, field
from typing import Optional

# Idioma del contenido (descripción visual + transcripción). Definición única en
# common/idioma.py, compartida con ai_generator.
from common import idioma as _idioma


@dataclass
class PostData:
    url: str
    shortcode: str
    caption: str
    comments: list[str]
    owner_username: str = ""
    owner_full_name: str = ""
    # @usuarios (minúsculas) de los colaboradores del post. Instagram los manda
    # en `coauthor_producers`. Los usa la detección de cliente: si el dueño no es
    # cliente nuestro pero un colaborador sí, el post es de ese cliente.
    collaborators: list[str] = field(default_factory=list)
    transcription: str = ""
    photo_description: str = ""
    is_video: bool = False
    image_b64: str = ""          # imagen del post en base64 (visión multimodal)
    image_media_type: str = ""   # ej "image/jpeg"
    # >1 => image_b64 es un MOSAICO (fotos de un carrusel, o capturas de un
    # video). Quien genere texto tiene que saberlo para no hablar de "la foto 3"
    # ni de la grilla, que es un armado nuestro y no algo que el post tenga.
    n_imagenes: int = 1
    # Motivo (para el vendedor) si la descripción por IA quedó vacía por un error
    # de la IA (saturada / sin crédito). "" si salió bien o no hubo imagen.
    descripcion_error: str = ""


def _motivo_desc_error(err: str) -> str:
    """Mensaje corto para el vendedor cuando la descripción por IA falla."""
    e = (err or "").lower()
    # Rechazo por políticas: no es un error transitorio, reintentar no cambia
    # nada. Se lo decimos así para que el vendedor no se quede apretando de nuevo.
    if "rechaz" in e or "refusal" in e:
        return ("La IA no quiso describir esta imagen. Generá igual: los "
                "comentarios se hacen con el texto del post.")
    if "overloaded" in e or "529" in e:
        return "No se pudo describir la imagen: la IA está saturada. Reintentá en unos segundos."
    if "rate_limit" in e or "429" in e:
        return "No se pudo describir la imagen: demasiadas consultas seguidas. Reintentá en un momento."
    if "credit balance" in e or "billing" in e or "insufficient" in e:
        return "No se pudo describir la imagen: el servicio de IA se quedó sin crédito. Avisá al administrador."
    if "timeout" in e or "timed out" in e:
        return "No se pudo describir la imagen: la IA tardó demasiado. Reintentá."
    return "No se pudo describir la imagen (error de la IA). Reintentá en un momento."


def _motivo_post_inaccesible(slow: dict, fast: dict) -> str:
    """Por qué no salió el post, distinguiendo el caso 'el post no existe' del
    caso 'la sesión no sirve'.

    Los dos llegaban al vendedor con el mismo texto ("privado, borrado, o la
    sesión venció"), que lo mandaba a sospechar de la sesión cuando casi siempre
    es el post. Se pueden separar porque el scrapeo va por dos caminos con
    identidades distintas: la API con sesión y el HTML público, anónimo. Si el
    anónimo TAMBIÉN vino vacío, no es la sesión: Instagram no entrega ese post
    para nadie."""
    error = slow.get("_error") or "Instagram no devolvió el link del video"
    # La sesión no sirve: NO es el post y NO se arregla reintentando. Antes esto
    # caía en el genérico "probá de nuevo en un minuto" y el vendedor reintentaba
    # el mismo link toda la mañana creyendo que el link estaba mal.
    if slow.get("_error_kind") == "sesion_muerta":
        # "sesión de Instagram del server" es la frase que mira el clasificador
        # para no ofrecer el botón de Reintentar: acá reintentar no arregla nada.
        return (f"{error}. No es el post: es la sesión de Instagram del server, "
                "avisá al administrador")
    if slow.get("_error_kind") != "media_null":
        # 429/403/timeout: el error ya dice lo que pasa y suele ser transitorio.
        return f"{error}. Probá de nuevo en un minuto"
    if not fast.get("caption") and not fast.get("owner_username"):
        return ("Instagram no entrega este post (borrado, restringido por edad o "
                "país, o el link no corresponde a un post existente). Para "
                "confirmarlo, abrilo en una ventana de incógnito: si ahí tampoco "
                "carga, no es un problema del sistema")
    # El público lo ve pero el logueado no: ahí sí la sesión es sospechosa.
    return ("la sesión de Instagram del server necesita renovarse (el post es "
            "público pero la cuenta no lo puede leer). Avisá al administrador")


def extract_shortcode(url: str) -> Optional[str]:
    # El @usuario puede venir antes del /p/ o /reel/ (instagram.com/user/reel/CODE/).
    match = re.search(
        r"instagram\.com/(?:[A-Za-z0-9._]+/)?(?:p|reels?|tv)/([A-Za-z0-9_-]+)", url
    )
    return match.group(1) if match else None


def is_video_url(url: str) -> bool:
    return bool(re.search(r"instagram\.com/(?:[A-Za-z0-9._]+/)?(?:reels?|tv)/", url))


_whisper_model = None
# Con varios posts a la vez, 3 hilos entraban juntos acá y cada uno cargaba SU
# propio modelo (x3 de RAM, y en el server chico eso mataba al proceso: "falla la
# transcripción"). El lock garantiza que se cargue UNA sola vez.
_whisper_load_lock = threading.Lock()
# faster-whisper no es thread-safe para transcribir en paralelo sobre la misma
# instancia, y además cada transcripción ya usa varios cores: dejamos pasar de a
# WHISPER_CONCURRENCIA (default 2). Los demás esperan turno en vez de saturar la CPU.
_whisper_sem = threading.BoundedSemaphore(int(os.environ.get("WHISPER_CONCURRENCIA", "2")))
# Cores por transcripción: acotado para que N transcripciones no se peleen por la CPU.
_WHISPER_THREADS = int(os.environ.get("WHISPER_THREADS", "4"))
# Idioma HABLADO en los videos (el de entrada, no el de salida). Vacío =
# autodetección, que es el default. Estaba fijo en "es" y con un cliente que
# habla inglés whisper decodificaba inglés como español y devolvía un engendro
# ("Si estás cansado de un mejor proceso de practicar, con vías o sellos") que
# después alimentaba los comentarios. Poner WHISPER_LANGUAGE=es|en solo si hace
# falta forzar una cuenta puntual.
_WHISPER_LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "").strip() or None
# Red de seguridad de la autodetección (el motivo por el que en su momento se fijó
# el idioma): con audio de cancha, música o viento la detección se va a cualquier
# lado y el modelo inventa frases. Si la confianza no llega a este piso, se
# transcribe en el idioma de respaldo en vez de creerle a la detección.
# El respaldo es el idioma de contenido (inglés de fábrica): un audio ruidoso de
# una cuenta en inglés se estaba decodificando como español porque el default
# acá era "es".
_WHISPER_LANG_MIN_PROB = float(os.environ.get("WHISPER_LANG_MIN_PROB", "0.5"))
_WHISPER_LANG_FALLBACK = (os.environ.get("WHISPER_LANG_FALLBACK", _idioma.IDIOMA_CONTENIDO)
                          .strip() or None)
# Salida SIEMPRE en el idioma de contenido: si el video se habla en otro idioma,
# whisper lo traduce (task="translate", que traduce solo hacia el inglés). Con un
# idioma de contenido que no sea inglés no hay traducción posible y la
# transcripción queda literal, en el idioma del video.
_WHISPER_TASK = "translate" if _idioma.TRADUCE_TRANSCRIPCION else "transcribe"
# Tamaño del modelo. "small" transcribe bastante mejor que "base" y ocupa ~500MB
# (vs ~150MB): si el server queda corto de RAM, WHISPER_MODEL=base.
_WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
# beam_size=5 vs 1: ~30% más lento pero con beam=1 el modelo devolvía frases
# inventadas que además pasaban el filtro de confianza. No bajar sin medir.
_WHISPER_BEAM = int(os.environ.get("WHISPER_BEAM", "5"))
# Segmentos con confianza muy baja: es ruido que el modelo "rellenó" con texto
# inventado. Preferimos no mostrarlos antes que mostrar algo falso.
_LOGPROB_MIN = float(os.environ.get("WHISPER_LOGPROB_MIN", "-1.2"))
_NOSPEECH_MAX = float(os.environ.get("WHISPER_NOSPEECH_MAX", "0.85"))


def _preparar_audio(video_path: str) -> str:
    """Extrae el audio del video y lo limpia antes de transcribir. En los reels
    el audio viene con cancha, música y viento encima de la voz, y whisper sobre
    ese ruido inventaba frases enteras. Filtrando fuera de la banda de la voz y
    normalizando el volumen, las alucinaciones desaparecen.
    Devuelve la ruta del wav, o el video original si ffmpeg falla."""
    destino = os.path.join(os.path.dirname(video_path), "audio.wav")
    try:
        subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", video_path,
             "-vn", "-ac", "1", "-ar", "16000",
             # highpass/lowpass: dejamos la banda de la voz. afftdn: quita ruido
             # de fondo constante. loudnorm: empareja el volumen (voz lejana).
             "-af", "highpass=f=100,lowpass=f=4000,afftdn=nf=-25,loudnorm",
             "-y", destino],
            capture_output=True, timeout=120,
        )
        if os.path.exists(destino) and os.path.getsize(destino) > 0:
            return destino
    except Exception as e:
        print(f"[whisper] no se pudo limpiar el audio ({e}), uso el video tal cual", flush=True)
    return video_path


def _get_whisper_model():
    global _whisper_model
    with _whisper_load_lock:
        if _whisper_model is None:
            from faster_whisper import WhisperModel
            print(f"[whisper] cargando modelo {_WHISPER_MODEL} (cpu_threads={_WHISPER_THREADS})...", flush=True)
            _whisper_model = WhisperModel(_WHISPER_MODEL, device="cpu", compute_type="int8",
                                          cpu_threads=_WHISPER_THREADS)
        return _whisper_model


def precargar_whisper():
    """Carga el modelo en segundo plano al arrancar el servicio. Si no, el
    primero que manda un reel espera ~13s a que se cargue, arriba de lo que
    tarda la transcripción."""
    def _cargar():
        try:
            _get_whisper_model()
            print("[whisper] modelo precargado", flush=True)
        except Exception as e:
            print(f"[whisper] precarga falló ({e}), se cargará en el primer uso", flush=True)
    threading.Thread(target=_cargar, daemon=True).start()


def _transcribe_video(video_path: str) -> str:
    try:
        try:
            model = _get_whisper_model()
        except Exception as e:
            # Si el modelo no carga (falta el binario, sin RAM, sin disco para
            # bajarlo), TODOS los videos fallan: que el mensaje lo diga.
            print(f"[whisper] no se pudo cargar el modelo: {e}", flush=True)
            return f"(transcripción no disponible: no se pudo cargar el motor de transcripción — {e})"
        t0 = time.time()
        with _whisper_sem:
            espera = time.time() - t0
            if espera > 0.5:
                print(f"[whisper] esperó {espera:.1f}s por turno (otra transcripción en curso)", flush=True)
            audio = _preparar_audio(video_path)
            # task: la transcripción sale SIEMPRE en el idioma de contenido. Con
            # "translate" whisper entrega en inglés lo que se habla en cualquier
            # idioma; el `language` de abajo sigue siendo el idioma HABLADO.
            # vad_filter: recorta los tramos sin voz. Sin esto, whisper "rellena"
            # el ruido ambiente con texto alucinado ("I don't know...") que después
            # se le mostraba al vendedor como si fuera lo que dice el video.
            opciones = dict(task=_WHISPER_TASK, vad_filter=True,
                            condition_on_previous_text=False,   # corta el loop de repetir la última frase
                            beam_size=_WHISPER_BEAM)
            segments, info = model.transcribe(audio, language=_WHISPER_LANGUAGE, **opciones)
            # Autodetección sin confianza (audio con cancha, música o viento): antes
            # de creerle y transcribir en un idioma inventado, caemos al de respaldo.
            # Los segments son perezosos: si todavía no los consumimos, esta segunda
            # llamada no rehace la transcripción, solo la detección.
            if _WHISPER_LANGUAGE is None:
                prob = getattr(info, "language_probability", 1.0) or 0.0
                print(f"[whisper] idioma detectado: {info.language} ({prob:.2f})", flush=True)
                if prob < _WHISPER_LANG_MIN_PROB and _WHISPER_LANG_FALLBACK:
                    print(f"[whisper] confianza baja, transcribo en "
                          f"{_WHISPER_LANG_FALLBACK}", flush=True)
                    segments, info = model.transcribe(
                        audio, language=_WHISPER_LANG_FALLBACK, **opciones)
            # Descartamos los segmentos que el propio modelo da por poco
            # confiables: ahí es donde aparecían las frases inventadas.
            partes, descartados = [], 0
            for s in segments:
                if s.avg_logprob < _LOGPROB_MIN or s.no_speech_prob > _NOSPEECH_MAX:
                    descartados += 1
                    print(f"[whisper] descartado (logprob={s.avg_logprob:.2f} "
                          f"nospeech={s.no_speech_prob:.2f}): {s.text.strip()[:60]}", flush=True)
                    continue
                partes.append(s.text)
            text = " ".join(partes).strip()
        print(f"[whisper] transcripción: {len(text)} chars en {time.time()-t0:.1f}s", flush=True)
        if not text:
            # Video sin voz (música, ambiente): no es un error, pero antes quedaba
            # un bloque vacío y parecía que había fallado.
            if descartados:
                return ("(sin transcripción: el audio está muy ruidoso y no se "
                        "entiende lo que se dice)")
            return "(sin transcripción: el video no tiene voz hablada — solo música o sonido ambiente)"
        return text
    except Exception as e:
        print(f"[whisper] error: {e}", flush=True)
        return f"(transcripción no disponible: {e})"


_FRAMES_VIDEO = int(os.environ.get("FRAMES_VIDEO", "6"))


def _duracion_video(video_path: str) -> float:
    """Duración en segundos vía ffprobe. 0.0 si no se pudo determinar."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=20,
        )
        return float((out.stdout or "0").strip() or 0)
    except Exception as e:
        print(f"[frames] ffprobe falló: {e}", flush=True)
        return 0.0


def _extraer_frames(video_path: str, n: int = _FRAMES_VIDEO) -> list[bytes]:
    """Saca n capturas repartidas a lo largo del video (a un % fijo de la
    duración cada una) para armar el mosaico. Antes solo se miraba la portada:
    un reel de 60s se describía por su primer frame, que muchas veces es una
    placa de título y no dice nada de lo que pasa después.
    Devuelve [] si no se pudo (el llamador cae al thumbnail de siempre)."""
    dur = _duracion_video(video_path)
    if dur <= 0:
        return []
    # Muestreamos en el centro de cada tramo: evita el primer frame (suele venir
    # negro o con fade) y el último (créditos / cierre).
    tiempos = [dur * (i + 0.5) / n for i in range(n)]
    frames = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for i, t in enumerate(tiempos):
            destino = os.path.join(tmpdir, f"f{i}.jpg")
            try:
                # -ss antes de -i = seek rápido (no decodifica desde el principio).
                subprocess.run(
                    ["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", f"{t:.2f}",
                     "-i", video_path, "-frames:v", "1", "-q:v", "3", "-y", destino],
                    capture_output=True, timeout=30,
                )
                if os.path.exists(destino) and os.path.getsize(destino) > 0:
                    with open(destino, "rb") as f:
                        frames.append(f.read())
            except Exception as e:
                print(f"[frames] error extrayendo frame {i} ({t:.1f}s): {e}", flush=True)
    print(f"[frames] {len(frames)}/{n} capturas de un video de {dur:.1f}s", flush=True)
    return frames


_cuenta_avisada = ""


def _avisar_cuenta(cookies: dict, origen: str) -> dict:
    """Deja en el log de qué cuenta es la sesión que se está usando (una sola vez).
    Cuando se cambia de cuenta, el síntoma de que quedó la vieja en algún lado es
    'media null' en todos los posts, que no dice nada. Con esto se ve al arrancar
    cuál está activa, sin exponer el sessionid."""
    global _cuenta_avisada
    marca = f"{origen}:{cookies.get('ds_user_id', '?')}"
    if marca != _cuenta_avisada:
        _cuenta_avisada = marca
        print(f"[ig_cookies] sesión desde {origen} (ds_user_id={cookies.get('ds_user_id') or 'sin ds_user_id'})",
              flush=True)
    return cookies


def _cookies_del_entorno() -> dict:
    """Las cookies que vienen por env var. Sigue siendo el fallback cuando no
    hay base de datos (docker local, tests) o cuando nadie cargó ninguna cuenta
    en el panel todavía.

    Prioridad: INSTAGRAM_COOKIES_JSON > INSTAGRAM_SESSION_B64 (pickle viejo de
    instaloader).
    """
    cookies_json = os.environ.get("INSTAGRAM_COOKIES_JSON")
    if cookies_json:
        try:
            return json.loads(cookies_json)
        except Exception as e:
            print(f"[ig_cookies] error parseando INSTAGRAM_COOKIES_JSON: {e}", flush=True)

    # Formato viejo: pickle de instaloader
    import pickle, base64
    session_b64 = os.environ.get("INSTAGRAM_SESSION_B64")
    if session_b64:
        try:
            data = pickle.loads(base64.b64decode(session_b64))
            return {
                "sessionid": data.get("sessionid", ""),
                "csrftoken": data.get("csrftoken", ""),
                "ds_user_id": data.get("ds_user_id", ""),
                "mid": data.get("mid", ""),
                "ig_did": data.get("ig_did", ""),
                "datr": data.get("datr", ""),
            }
        except Exception as e:
            print(f"[ig_cookies] error cargando INSTAGRAM_SESSION_B64: {e}", flush=True)

    # Antes había un tercer fallback: un pickle de instaloader commiteado al lado
    # de este archivo (session-crowagency.ofc). Se sacó al cambiar de cuenta: si
    # faltaba la env var, el scraper volvía en silencio a la sesión vieja y todo
    # fallaba con "media null", sin ninguna pista de por qué. Mejor quedarse sin
    # sesión y decirlo, que scrapear con la cuenta equivocada.
    return {}


def _sesiones_disponibles() -> list[dict]:
    """Las cuentas de Instagram a probar, en orden: [{id, cookies, origen}].

    Primero las que cargó el admin en el panel (tabla ig_sessions, ordenadas por
    prioridad y con las caídas al final), y al final SIEMPRE la env var como
    último recurso. Que la env var quede de última y no de única es lo que
    convierte una caída de cuenta en una rotación en vez de un corte.

    A propósito NO cachea: cuando alguien renueva la sesión desde el panel
    porque el servicio está caído, tiene que servir en el siguiente post, no en
    el siguiente reinicio.
    """
    candidatos = []
    try:
        from common import repository as _repo
        for s in _repo.ig_sessions_para_usar():
            if (s.get("cookies") or {}).get("sessionid"):
                candidatos.append({
                    "id": s["id"],
                    "cookies": s["cookies"],
                    "origen": f"@{s['username']}" if s.get("username") else f"ig_sessions#{s['id']}",
                })
    except Exception as e:
        # Sin DB (o con la DB caída) el scraper tiene que seguir andando con la
        # env var: es exactamente el escenario para el que existe el fallback.
        print(f"[ig_cookies] no pude leer las sesiones de la base: {e}", flush=True)

    delentorno = _cookies_del_entorno()
    if delentorno.get("sessionid"):
        candidatos.append({"id": None, "cookies": delentorno, "origen": "INSTAGRAM_COOKIES_JSON"})

    if not candidatos:
        print("[ig_cookies] sin sesión: no hay cuentas cargadas en el panel ni "
              "INSTAGRAM_COOKIES_JSON en el entorno", flush=True)
    return candidatos


def _load_ig_cookies() -> dict:
    """La sesión que se usaría ahora mismo. Queda para los llamadores que solo
    quieren las cookies (y para no romper los scripts de diagnóstico); el
    scrapeo usa _sesiones_disponibles(), que además sabe rotar."""
    for c in _sesiones_disponibles():
        return _avisar_cuenta(c["cookies"], c["origen"])
    return {}


def _fetch_full_name(username: str) -> str:
    """Fetch del nombre público del perfil via HTML público (og:title)."""
    try:
        r = req.get(
            f"https://www.instagram.com/{username}/",
            headers={
                "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=8,
        )
        if r.status_code == 200:
            m = re.search(r'<meta property="og:title" content="([^"]*)"', r.text)
            if m:
                raw = html_lib.unescape(m.group(1))
                name_m = re.match(r"(.+?)\s*\(@", raw)
                if name_m:
                    return name_m.group(1).strip()
    except Exception as e:
        print(f"[full_name] error: {e}", flush=True)
    return ""


def _fetch_fast(shortcode: str) -> dict:
    """Fetch rápido via HTML público con UA de Facebook."""
    result = {}
    try:
        r = req.get(
            f"https://www.instagram.com/p/{shortcode}/",
            headers={
                "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=8,
        )
        if r.status_code == 200:
            m = re.search(r'<meta property="og:description" content="([^"]*)"', r.text)
            if m:
                raw = html_lib.unescape(m.group(1))
                user_m = re.search(r'- ([A-Za-z0-9._]+) on [A-Za-z]+ \d+', raw)
                if user_m:
                    result["owner_username"] = user_m.group(1)
                caption_m = re.search(r':\s*"(.+)"', raw, re.DOTALL)
                if caption_m:
                    result["caption"] = caption_m.group(1)
                elif user_m:
                    result["caption"] = ""
                else:
                    result["caption"] = raw
                print(f"[fast_fetch] username={result.get('owner_username')} caption={result.get('caption','')[:60]}", flush=True)
            m3 = re.search(r'<meta property="og:type" content="([^"]*)"', r.text)
            if m3:
                result["is_video"] = "video" in m3.group(1).lower()
                print(f"[fast_fetch] og:type={m3.group(1)} is_video={result['is_video']}", flush=True)
            m_vid = re.search(r'<meta property="og:video(?::secure_url|:url)?" content="([^"]+)"', r.text)
            if m_vid:
                result["video_url"] = html_lib.unescape(m_vid.group(1))
                result["is_video"] = True
                print(f"[fast_fetch] og:video encontrado", flush=True)
    except Exception as e:
        print(f"[fast_fetch] failed: {e}", flush=True)
    return result


_MOSAICO_MAX_IMAGENES = int(os.environ.get("MOSAICO_MAX_IMAGENES", "10"))

# Lado máximo (px) de lo que le mandamos al modelo. El costo de una llamada de
# visión es proporcional a los píxeles (~ancho*alto/750 tokens), y la imagen no
# se manda una sola vez: va a la descripción Y a cada intento de generación de
# comentarios. Una foto de IG cruda (1080x1350) son ~1950 tokens; a 1024 de lado
# son ~1100, y para "qué se ve en esta foto" la descripción no cambia.
# Por env var para poder subirlo sin redeployar si algún cliente necesita leer
# texto muy chico.
_VISION_MAX_LADO = int(os.environ.get("VISION_MAX_LADO", "1024"))
_VISION_JPEG_QUALITY = int(os.environ.get("VISION_JPEG_QUALITY", "80"))


def _tile_px(n: int) -> int:
    """Px por celda del mosaico. Con pocas fotos agrandamos las celdas (se lee
    mejor el texto chico); con muchas achicamos para no inflar la imagen.
    El lienzo final igual se recorta a _VISION_MAX_LADO."""
    if n <= 4:
        return 768
    if n <= 9:
        return 512
    return 384


def _reducir_imagen(raw: bytes, media_type: str) -> tuple[bytes, str]:
    """Baja la imagen a _VISION_MAX_LADO de lado mayor y la reencoda en JPEG.
    Si Pillow no está o algo falla, devuelve la original (peor costo, no error)."""
    if _VISION_MAX_LADO <= 0:
        return raw, media_type
    try:
        import io
        from PIL import Image

        img = Image.open(io.BytesIO(raw))
        if max(img.size) <= _VISION_MAX_LADO and (media_type or "").endswith("jpeg"):
            return raw, media_type
        img = img.convert("RGB")
        img.thumbnail((_VISION_MAX_LADO, _VISION_MAX_LADO), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_VISION_JPEG_QUALITY, optimize=True)
        chico = buf.getvalue()
        print(f"[image] reducida a {img.width}x{img.height} "
              f"({len(raw)} -> {len(chico)} bytes)", flush=True)
        return chico, "image/jpeg"
    except Exception as e:
        print(f"[image] no se pudo reducir la imagen: {e}", flush=True)
        return raw, media_type


def _armar_mosaico(imagenes: list[bytes]) -> tuple[str, str]:
    """Arma UNA sola imagen (grilla) con todas las fotos del carrusel, numeradas.
    Así el modelo describe el carrusel completo en una única llamada de visión,
    en vez de una llamada (y un juego de tokens) por foto.
    Devuelve (base64, media_type). Ante cualquier problema devuelve ("", "")."""
    try:
        import base64
        import io
        import math
        from PIL import Image, ImageDraw, ImageFont
    except Exception as e:
        print(f"[mosaico] Pillow no disponible: {e}", flush=True)
        return "", ""

    try:
        fotos = []
        for raw in imagenes[:_MOSAICO_MAX_IMAGENES]:
            try:
                fotos.append(Image.open(io.BytesIO(raw)).convert("RGB"))
            except Exception:
                continue
        if not fotos:
            return "", ""

        tile = _tile_px(len(fotos))
        cols = math.ceil(math.sqrt(len(fotos)))
        filas = math.ceil(len(fotos) / cols)
        lienzo = Image.new("RGB", (cols * tile, filas * tile), (255, 255, 255))
        d = ImageDraw.Draw(lienzo)
        try:
            fuente = ImageFont.load_default(size=tile // 12)
        except Exception:
            fuente = ImageFont.load_default()   # Pillow viejo: sin size

        for i, foto in enumerate(fotos):
            # "contain" sobre fondo blanco: no recortamos nada del contenido.
            foto.thumbnail((tile, tile), Image.LANCZOS)
            x0, y0 = (i % cols) * tile, (i // cols) * tile
            lienzo.paste(foto, (x0 + (tile - foto.width) // 2, y0 + (tile - foto.height) // 2))
            # Número de orden bien visible: la descripción va foto por foto, así
            # que el modelo tiene que poder distinguir cuál es cuál sin dudar.
            # (Con la fuente default de PIL, 11px, en una celda de 512 el número
            # quedaba ilegible.)
            lado = tile // 8
            d.rectangle([x0 + 6, y0 + 6, x0 + 6 + lado, y0 + 6 + lado], fill=(0, 0, 0))
            d.text((x0 + 6 + lado // 2, y0 + 6 + lado // 2), str(i + 1),
                   fill=(255, 255, 255), font=fuente, anchor="mm")
            d.rectangle([x0, y0, x0 + tile - 1, y0 + tile - 1], outline=(0, 0, 0), width=2)

        # El lienzo se arma grande (los números y el texto chico se dibujan
        # nítidos) y recién al final se baja al lado máximo de visión: así
        # pagamos tokens por una imagen de 1024 y no de 1536.
        if _VISION_MAX_LADO > 0 and max(lienzo.size) > _VISION_MAX_LADO:
            lienzo.thumbnail((_VISION_MAX_LADO, _VISION_MAX_LADO), Image.LANCZOS)

        buf = io.BytesIO()
        lienzo.save(buf, format="JPEG", quality=_VISION_JPEG_QUALITY, optimize=True)
        print(f"[mosaico] {len(fotos)} imágenes en grilla {cols}x{filas} "
              f"-> {lienzo.width}x{lienzo.height} "
              f"({len(buf.getvalue())} bytes)", flush=True)
        return base64.b64encode(buf.getvalue()).decode(), "image/jpeg"
    except Exception as e:
        print(f"[mosaico] error armando mosaico: {e}", flush=True)
        return "", ""


def _coautores(media: dict) -> list[str]:
    """@usuarios de los colaboradores del post, en minúsculas y sin repetir.

    Instagram los devuelve en `coauthor_producers` (los "colaboradores" de un
    post publicado en conjunto). El dueño NO va en esta lista. Es tolerante con
    la forma del payload: si el campo no viene, o viene con otra estructura,
    devuelve vacío en vez de romper el scrape entero.
    """
    salida, vistos = [], set()
    for c in (media.get("coauthor_producers") or []):
        if not isinstance(c, dict):
            continue
        u = (c.get("username") or "").strip().lower()
        if u and u not in vistos:
            vistos.add(u)
            salida.append(u)
    return salida


def _fetch_instagram_api(shortcode: str) -> dict:
    """Trae el post por la API de Instagram, rotando de cuenta si hace falta.

    Prueba las sesiones cargadas en orden. Cuando Instagram RECHAZA una (401,
    checkpoint, página de deslogueado) la marca caída y sigue con la siguiente:
    el vendedor no ve nada raro, y el admin se entera por el aviso del monitor.
    No rota por cualquier error — un rate limit o un post privado no son culpa
    de la cuenta y cambiarla solo quemaría la de respaldo también.
    """
    candidatos = _sesiones_disponibles()
    if not candidatos:
        print("[ig_api] sin sesión disponible", flush=True)
        return {"_error": "no hay sesión de Instagram configurada en el server"}

    ultimo = {}
    # Qué cuenta rechazó Instagram EN ESTE fetch. Solo con esto se avisa una
    # rotación: que cambie la cuenta activa no alcanza como señal, porque
    # también cambia al cargar una sesión nueva desde el panel.
    rechazada_antes = ""
    # Cuentas que devolvieron el post VACÍO sobre un post que sí es público.
    # Se prueba la siguiente igual, pero no se las condena todavía: ver
    # _post_es_publico. Se confirman recién si otra cuenta trae el post.
    sospechosas = []
    for i, cand in enumerate(candidatos):
        _avisar_cuenta(cand["cookies"], cand["origen"])
        r = _fetch_instagram_api_con(shortcode, cand["cookies"])

        if r.get("_error_kind") == "sesion_muerta":
            _marcar_fila(cand, ok=False, detalle=r.get("_error", ""))
            rechazada_antes = rechazada_antes or cand["origen"]
            ultimo = r
            if i + 1 < len(candidatos):
                print(f"[ig_api] {cand['origen']} rechazada por Instagram — "
                      f"paso a {candidatos[i + 1]['origen']}", flush=True)
            continue

        # "media null": Instagram contesta 200 con los datos vacíos. Es la falla
        # AMBIGUA — puede ser que el post no exista o sea privado, o que la
        # sesión ya no sirva y Instagram no lo diga. Se desempata preguntando
        # por el camino anónimo: si de ahí el post SÍ viene, existe y es
        # público, así que el problema es nuestra sesión.
        if (r.get("_error_kind") == "media_null" and i + 1 < len(candidatos)
                and _post_es_publico(shortcode)):
            print(f"[ig_api] {cand['origen']} devolvió el post vacío pero el post "
                  f"es público — pruebo con {candidatos[i + 1]['origen']}", flush=True)
            sospechosas.append(cand)
            ultimo = r
            continue

        # Un 429 o un post privado no dicen nada sobre la cuenta: no la ascienden
        # a viva (sería mentirle al panel sobre cuándo anduvo por última vez) ni
        # la condenan. Solo un fetch limpio cuenta como prueba de vida.
        if not r.get("_error"):
            # Otra cuenta trajo el post que estas devolvieron vacío: ahí sí está
            # probado que el problema era de ellas y no del post.
            for mala in sospechosas:
                _marcar_fila(mala, ok=False,
                             detalle="Instagram devolvió el post vacío con esta "
                                     "sesión y completo con otra")
            rechazada_antes = rechazada_antes or (sospechosas[0]["origen"] if sospechosas else "")
            _marcar_fila(cand, ok=True, detalle="")
            # El estado del MONITOR se toca una sola vez por fetch y recién acá:
            # que Instagram rechace una cuenta no es una caída si la siguiente
            # anduvo. Avisarlo por sesión mandaba un "🔴 se cayeron TODAS"
            # seguido de un "🟢 volvió" en cada rotación exitosa.
            _avisar_monitor(True, "", cand["origen"], rotacion=rechazada_antes)
        return r

    if rechazada_antes:
        print("[ig_api] TODAS las sesiones de Instagram están caídas", flush=True)
        _avisar_monitor(False, ultimo.get("_error", ""), "")
    else:
        # Ninguna cuenta fue rechazada explícitamente: todas devolvieron vacío.
        # Sin prueba de que sean ellas, no se las condena ni se despierta a
        # nadie — un solo post raro no puede quemar todas las cuentas.
        print("[ig_api] ninguna sesión pudo traer el post, pero ninguna fue "
              "rechazada: no marco nada", flush=True)
    return ultimo


def _post_es_publico(shortcode: str) -> bool:
    """¿El post existe y se ve sin estar logueado?

    Desempata el "media null". Usa el camino anónimo (_fetch_fast, el mismo que
    ya se usa para el caption rápido), así que no gasta ninguna sesión. Cuesta
    un request de más, pero solo en el caso ambiguo, que es raro.
    """
    try:
        publico = _fetch_fast(shortcode)
        return bool(publico.get("caption") or publico.get("owner_username"))
    except Exception as e:
        # Sin respuesta clara nos quedamos con "no sé", que acá significa no
        # rotar: es la opción que no quema cuentas.
        print(f"[ig_api] no pude verificar si {shortcode} es público: {e}", flush=True)
        return False


def _marcar_fila(cand: dict, *, ok: bool, detalle: str) -> None:
    """Cómo le fue a ESTA cuenta, en su fila de la base: es lo que mira el panel
    y lo que hace que una rechazada salga de la cola."""
    try:
        if cand.get("id"):
            from common import repository as _repo
            _repo.marcar_ig_session(cand["id"], ok, detalle)
    except Exception as e:
        print(f"[ig_api] no pude marcar la sesión: {e}", flush=True)


def _avisar_monitor(ok: bool, detalle: str, origen: str, rotacion: str = "") -> None:
    """Cómo le fue al SERVICIO: una sola vez por fetch, ya sabiendo si alguna
    cuenta sirvió. De acá salen los avisos por WhatsApp."""
    try:
        from modules import ig_monitor
        ig_monitor.registrar(ok, detalle or ("anduvo" if ok else ""),
                             origen=origen, rotacion=rotacion)
    except Exception:
        # El monitor es opcional: que no ande no puede romper un scrapeo.
        pass


def _fetch_instagram_api_con(shortcode: str, cookies: dict) -> dict:
    """
    Fetch via GraphQL doc_id de Instagram (la misma API que usa el navegador).
    Reemplaza instaloader que usa query_hash ya bloqueado por Instagram.
    """
    try:
        r = req.post(
            "https://www.instagram.com/graphql/query",
            cookies=cookies,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
                "x-ig-app-id": "936619743392459",
                "x-csrftoken": cookies.get("csrftoken", ""),
                "content-type": "application/x-www-form-urlencoded",
                "Referer": f"https://www.instagram.com/p/{shortcode}/",
                "Origin": "https://www.instagram.com",
            },
            data={
                "doc_id": "10015901848480474",
                "variables": json.dumps({
                    "shortcode": shortcode,
                    "__relay_internal__pv__PolarisFeedShareMenurelayprovider": False,
                }),
            },
            timeout=15,
        )

        if r.status_code != 200:
            print(f"[ig_api] status {r.status_code}", flush=True)
            motivo = ("Instagram nos está limitando (rate limit)" if r.status_code == 429
                      else "la sesión de Instagram venció o fue bloqueada" if r.status_code in (401, 403)
                      else f"Instagram respondió {r.status_code}")
            kind = "sesion_muerta" if r.status_code in (401, 403) else ""
            if kind:
                print("[ig_api] SESIÓN MUERTA — renová INSTAGRAM_COOKIES_JSON", flush=True)
            return {"_error": motivo, "_error_kind": kind} if kind else {"_error": motivo}

        # Instagram contesta 200 con la página de deslogueado (HTML) cuando la
        # cookie ya no sirve. Eso reventaba en r.json() como un JSONDecodeError
        # pelado, que se leía igual que un problema de red pasajero.
        try:
            payload = r.json()
        except ValueError:
            texto = r.text or ""
            cuerpo = texto[:120].strip().replace("\n", " ")
            # Instagram devuelve la página web en vez de JSON en dos casos que se
            # arreglan distinto: la cuenta quedó trabada en un checkpoint (hay que
            # verificarla desde el navegador, ninguna cookie sirve hasta entonces)
            # o la cookie ya no vale (alcanza con renovarla).
            if "checkpoint" in texto or "challenge" in texto:
                print("[ig_api] CUENTA EN CHECKPOINT: Instagram le pide verificación "
                      "a la cuenta del scraper. Entrá con esa cuenta, confirmá, y "
                      "recién ahí renová INSTAGRAM_COOKIES_JSON", flush=True)
                return {"_error": "Instagram trabó la cuenta del server y le pide "
                                  "verificación: hay que entrar con esa cuenta y "
                                  "confirmar que es ella",
                        "_error_kind": "sesion_muerta"}
            print("[ig_api] SESIÓN MUERTA: Instagram no devolvió JSON — renová "
                  f"INSTAGRAM_COOKIES_JSON. Empieza con: {cuerpo!r}", flush=True)
            return {"_error": "la sesión de Instagram del server venció "
                              "(Instagram devolvió la página de deslogueado)",
                    "_error_kind": "sesion_muerta"}

        if payload.get("message") in ("checkpoint_required", "login_required"):
            print(f"[ig_api] SESIÓN MUERTA: {payload.get('message')} — renová "
                  "INSTAGRAM_COOKIES_JSON", flush=True)
            return {"_error": "Instagram le está pidiendo verificación a la cuenta "
                              "del server: hay que abrirla en el navegador y renovar "
                              "la cookie",
                    "_error_kind": "sesion_muerta"}

        media = (payload.get("data") or {}).get("xdt_shortcode_media")
        if not media:
            # 200 con media en null. Sin más contexto no se sabe si el post no
            # existe o si la sesión dejó de servir: quien llama lo resuelve
            # mirando si el camino público (_fetch_fast, anónimo) también falló.
            print(f"[ig_api] media null para {shortcode}", flush=True)
            return {"_error": "Instagram no devolvió los datos del post (puede ser privado, borrado, o la sesión venció)",
                    "_error_kind": "media_null"}

        # display_url = imagen del post (foto, o thumbnail del video). En carruseles
        # tomamos la del primer item.
        # display_url = imagen principal. En carruseles juntamos TODAS las
        # imágenes de los hijos: después se arma un mosaico y se describe de una
        # sola pasada (describir una por una multiplicaba los tokens de visión).
        display_urls = []
        for edge in (media.get("edge_sidecar_to_children", {}).get("edges") or []):
            node = edge.get("node", {}) or {}
            u = node.get("display_url", "") or ""
            if u:
                display_urls.append(u)

        display_url = media.get("display_url", "") or ""
        if not display_url and display_urls:
            display_url = display_urls[0]
        if not display_urls and display_url:
            display_urls = [display_url]

        result = {
            "caption": (media.get("edge_media_to_caption", {}).get("edges") or [{}])[0].get("node", {}).get("text", "") or "",
            "owner_username": media.get("owner", {}).get("username", "") or "",
            "owner_full_name": media.get("owner", {}).get("full_name", "") or "",
            "collaborators": _coautores(media),
            "photo_description": media.get("accessibility_caption", "") or "",
            "is_video": media.get("is_video", False),
            "video_url": media.get("video_url", "") or "",
            "display_url": display_url,
            "display_urls": display_urls,
        }
        print(f"[ig_api] ok — owner={result['owner_username']} "
              f"collabs={result['collaborators'] or '-'} is_video={result['is_video']}", flush=True)
        return result

    except Exception as e:
        print(f"[ig_api] error: {e}", flush=True)
        return {"_error": f"no se pudo contactar a Instagram ({type(e).__name__})"}


# Caché de posts ya scrapeados. "Cargar más" (y volver a pegar el mismo link)
# re-scrapeaba TODO de cero: bajaba el video otra vez, lo re-transcribía y volvía
# a describir la imagen. Eso multiplicaba la carga y los pedidos a Instagram sin
# aportar nada, porque el post es el mismo.
#
# Son DOS NIVELES:
#   L1 memoria — instantáneo, pero por proceso y se pierde al reiniciar.
#   L2 Postgres — compartido entre workers y sobrevive los deploys. Es el que
#      evita pagar de nuevo la visión y whisper cuando el vendedor vuelve al
#      mismo post mañana, o cuando el retry cae en otro worker.
#
# Un post no cambia: la imagen y la transcripción son las mismas la semana que
# viene. El TTL del L2 es largo a propósito y existe solo para que el caption y
# el dueño no queden viejos para siempre.
_scrape_cache = {}                     # shortcode -> (timestamp, PostData)
_scrape_cache_lock = threading.Lock()
_SCRAPE_TTL = int(os.environ.get("SCRAPE_CACHE_TTL", "600"))    # 10 min (L1)
_SCRAPE_CACHE_MAX = 12                 # el image_b64 pesa: acotamos la memoria
_SCRAPE_TTL_DB_H = int(os.environ.get("POST_CACHE_TTL_HORAS", "72"))   # 3 días (L2)

# Capa de datos opcional: sin DB, el caché queda solo en memoria como antes.
try:
    from common import repository as _repo
except Exception:
    _repo = None


def _cache_get(shortcode: str):
    with _scrape_cache_lock:
        hit = _scrape_cache.get(shortcode)
        if hit:
            ts, data = hit
            if time.time() - ts <= _SCRAPE_TTL:
                return data
            _scrape_cache.pop(shortcode, None)

    # L2: la DB. Un hit acá vale una llamada de visión + whisper + el scrape.
    if _repo is None:
        return None
    try:
        fila = _repo.post_cache_get(shortcode, ttl_horas=_SCRAPE_TTL_DB_H)
    except Exception as e:
        print(f"[cache] error consultando la DB: {e}", flush=True)
        return None
    if not fila:
        return None
    # Fila degradada: se guardó mientras el scrape estaba roto (sesión de IG
    # caída), así que no tiene ni imagen ni descripción. Servirla es peor que no
    # tener caché — el post sale pelado y el TTL la deja pegada tres días.
    # Tratarla como miss hace que el próximo intento la re-scrapee y la pise.
    if not (fila.get("image_b64") or fila.get("photo_description")):
        print(f"[cache] {shortcode} ignorado: la fila cacheada está vacía "
              f"(se guardó con el scrape roto), lo vuelvo a bajar", flush=True)
        return None

    data = PostData(comments=[], descripcion_error="",
                    **{k: v for k, v in fila.items() if k != "hits"})
    with _scrape_cache_lock:           # se sube a L1 para los próximos hits
        _scrape_cache[shortcode] = (time.time(), data)
    print(f"[cache] post {shortcode} recuperado de la DB "
          f"(sin scrape, sin whisper, sin visión)", flush=True)
    return data


def _cache_put(shortcode: str, data: "PostData"):
    with _scrape_cache_lock:
        _scrape_cache[shortcode] = (time.time(), data)
        # Purga: vencidos primero, y si igual sobra, el más viejo.
        ahora = time.time()
        for k in [k for k, (ts, _) in _scrape_cache.items() if ahora - ts > _SCRAPE_TTL]:
            _scrape_cache.pop(k, None)
        while len(_scrape_cache) > _SCRAPE_CACHE_MAX:
            _scrape_cache.pop(min(_scrape_cache, key=lambda k: _scrape_cache[k][0]), None)

    if _repo is None:
        return
    # A la DB NO va un post cuya descripción falló: si guardáramos eso, un 529
    # puntual de la IA quedaría pegado tres días y todos los vendedores verían el
    # post sin descripción. Sin la fila, el próximo intento vuelve a describirla.
    if data.descripcion_error:
        print(f"[cache] {shortcode} no se persiste: la descripción falló", flush=True)
        return
    try:
        _repo.post_cache_put({
            "shortcode": shortcode,
            "url": data.url,
            "caption": data.caption,
            "owner_username": data.owner_username,
            "owner_full_name": data.owner_full_name,
            "collaborators": data.collaborators,
            "transcription": data.transcription,
            "photo_description": data.photo_description,
            "is_video": data.is_video,
            "image_b64": data.image_b64,
            "image_media_type": data.image_media_type,
            "n_imagenes": data.n_imagenes,
        })
    except Exception as e:
        print(f"[cache] no se pudo persistir el post {shortcode}: {e}", flush=True)


def scrape_post(url: str, max_comments: int = 0, ligero: bool = False,
                account_id=None, user_id=None, client_id: str | None = None) -> PostData:
    """account_id/user_id/client_id: a quién se le imputa el gasto de la llamada
    de visión (la única de este módulo que quema tokens). Sin esto la descripción
    del post salía sin dueño y no aparecía en el panel de gasto por vendedor.

    ligero: modo keyword. Los comentarios son una sola palabra, así que no
    hace falta ni la imagen, ni la descripción visual, ni la transcripción del
    video: solo quién es el dueño del post (para la campaña) y el caption (para
    mostrarlo). Se saltea todo lo caro y lento — que es casi todo el scrape.
    No usa ni escribe el caché: el PostData que devuelve está incompleto a
    propósito y no sirve para una generación normal del mismo post."""
    shortcode = extract_shortcode(url)
    if not shortcode:
        raise ValueError(f"No se pudo extraer el shortcode del link: {url}")

    # Solo se cachea lo que salió BIEN: si la transcripción falló, el próximo
    # intento tiene que volver a probar (si no, un rate limit puntual quedaba
    # pegado 10 minutos).
    cacheado = _cache_get(shortcode) if not ligero else None
    if cacheado is not None:
        print(f"[cache] post {shortcode} reusado (sin re-scrapear ni re-transcribir)", flush=True)
        return cacheado

    t0 = time.time()

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    fast_future = executor.submit(_fetch_fast, shortcode)
    slow_future = executor.submit(_fetch_instagram_api, shortcode)

    fast = {}
    try:
        fast = fast_future.result(timeout=9)
        print(f"[fast_fetch] ok en {time.time()-t0:.2f}s: caption={'si' if fast.get('caption') else 'no'}", flush=True)
    except Exception as e:
        print(f"[fast_fetch] timeout/error: {e}", flush=True)

    slow = {}
    try:
        slow = slow_future.result()
        print(f"[ig_api] ok en {time.time()-t0:.2f}s", flush=True)
    except Exception as e:
        print(f"[ig_api] error: {e}", flush=True)
        if not fast.get("caption") and not fast.get("owner_username"):
            raise ValueError("No se pudo acceder al post. Verificá que el link sea público.")

    executor.shutdown(wait=False)

    caption = slow.get("caption") or fast.get("caption") or ""
    owner_username = slow.get("owner_username") or fast.get("owner_username") or ""
    owner_full_name = slow.get("owner_full_name") or ""
    # Solo la API los trae; el camino rápido/anónimo no expone colaboradores.
    collaborators = list(slow.get("collaborators") or [])
    if not owner_full_name and owner_username:
        owner_full_name = _fetch_full_name(owner_username)
    photo_description = slow.get("photo_description") or ""
    is_video = is_video_url(url) or slow.get("is_video") or fast.get("is_video", False)
    video_url = slow.get("video_url") or fast.get("video_url") or ""
    transcription = ""

    # Es un reel pero no tenemos el link del video: casi siempre es Instagram
    # fallando de forma transitoria (rate limit / sesión). Reintentamos la API un
    # par de veces con backoff antes de rendirnos — antes esto se rendía en el
    # primer intento y el usuario veía "sin transcripción" sin saber por qué.
    if is_video and not video_url and not ligero:
        for intento in (1, 2):
            print(f"[ig_api] reel sin video_url ({slow.get('_error') or 'sin motivo'}), "
                  f"reintento {intento}/2...", flush=True)
            time.sleep(1.5 * intento)
            retry = _fetch_instagram_api(shortcode)
            if retry.get("video_url"):
                slow = {**slow, **{k: v for k, v in retry.items() if v}}
                video_url = retry["video_url"]
                caption = caption or retry.get("caption") or ""
                owner_username = owner_username or retry.get("owner_username") or ""
                collaborators = collaborators or list(retry.get("collaborators") or [])
                display_url_retry = retry.get("display_url") or ""
                if display_url_retry:
                    slow["display_url"] = display_url_retry
                print("[ig_api] reintento OK: video_url recuperado", flush=True)
                break

    # Imagen del post para visión multimodal: foto (posts de imagen) o thumbnail
    # (reels/videos). La descargamos y la mandamos a Claude junto con el texto.
    # Si es un carrusel, bajamos TODAS las fotos y las unimos en un mosaico:
    # una sola llamada de visión describe el carrusel entero.
    display_url = slow.get("display_url") or ""
    display_urls = [u for u in (slow.get("display_urls") or []) if u]
    if display_url and display_url not in display_urls:
        display_urls.insert(0, display_url)
    if is_video:
        # En reels alcanza con la portada: el contenido lo aporta la transcripción.
        display_urls = display_urls[:1]
    if ligero:
        # Modo keyword: ni descargamos las imágenes. Sin imagen no hay llamada de
        # visión ni bloque multimodal más abajo.
        display_urls = []

    image_b64 = ""
    image_media_type = ""
    n_imagenes = 0
    descargadas = []
    for u in display_urls[:_MOSAICO_MAX_IMAGENES]:
        try:
            r_img = req.get(u, timeout=15)
            if r_img.status_code == 200 and r_img.content:
                ct = (r_img.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
                descargadas.append((r_img.content, ct if ct.startswith("image/") else "image/jpeg"))
        except Exception as e:
            print(f"[image] error descargando imagen: {e}", flush=True)

    if len(descargadas) == 1:
        import base64
        raw, ct = _reducir_imagen(*descargadas[0])
        image_b64 = base64.b64encode(raw).decode()
        image_media_type = ct
        n_imagenes = 1
        print(f"[image] imagen del post descargada ({len(raw)} bytes, {image_media_type})", flush=True)
    elif len(descargadas) > 1:
        image_b64, image_media_type = _armar_mosaico([c for c, _ in descargadas])
        if image_b64:
            n_imagenes = min(len(descargadas), _MOSAICO_MAX_IMAGENES)
        else:
            # Sin Pillow o mosaico fallido: al menos describimos la primera.
            import base64
            raw, ct = _reducir_imagen(*descargadas[0])
            image_b64 = base64.b64encode(raw).decode()
            image_media_type = ct
            n_imagenes = 1

    # Descripción visual para mostrarle al vendedor: la genera la IA mirando la
    # imagen real (foto, o portada/preview del video). El accessibility_caption de
    # Instagram queda solo como fallback si la llamada de visión falla.
    # Arranca ANTES de la transcripción para que en videos ambas corran en paralelo
    # y la descripción no sume latencia propia.
    desc_executor = None
    desc_future = None

    def _lanzar_descripcion(b64, media_type, n, es_video):
        nonlocal desc_executor, desc_future
        try:
            from modules.ai_generator import describir_imagen
            desc_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            desc_future = desc_executor.submit(describir_imagen, b64, media_type,
                                               caption, n, es_video, shortcode,
                                               account_id, user_id, client_id)
        except Exception as e:
            print(f"[describe] no disponible ({e})", flush=True)

    # En videos la descripción sale de un mosaico de capturas del propio video,
    # así que se lanza recién después de bajarlo (igual queda en paralelo con la
    # transcripción, que es lo lento). En fotos se lanza ya.
    if image_b64 and not (is_video and video_url):
        _lanzar_descripcion(image_b64, image_media_type, n_imagenes, False)

    if ligero:
        # Modo keyword: sin transcripción. Es lo más lento del scrape (bajar el
        # video + whisper, hasta un minuto) y no aporta nada a escribir una palabra.
        print("[scrape] modo keyword: salteo transcripción y visión", flush=True)
    elif is_video and video_url:
        print(f"[ig_api] descargando video para transcripción...", flush=True)
        # La descarga del video también se reintenta: un corte de red en el medio
        # dejaba el archivo trunco y whisper devolvía basura o error.
        ultimo_error = None
        for intento in (1, 2):
            try:
                with tempfile.TemporaryDirectory() as tmpdir:
                    video_path = os.path.join(tmpdir, f"{shortcode}.mp4")
                    r_vid = req.get(video_url, timeout=60)
                    if r_vid.status_code != 200 or not r_vid.content:
                        raise IOError(f"descarga del video devolvió {r_vid.status_code}")
                    with open(video_path, "wb") as f:
                        f.write(r_vid.content)

                    # Capturas repartidas a lo largo del video → mosaico → una
                    # sola llamada de visión que describe el reel entero, no solo
                    # la portada. Se lanza ANTES de transcribir para que corran
                    # en paralelo y no sume latencia.
                    if desc_future is None:
                        frames = _extraer_frames(video_path)
                        if len(frames) > 1:
                            m_b64, m_mt = _armar_mosaico(frames)
                            if m_b64:
                                image_b64, image_media_type = m_b64, m_mt
                                n_imagenes = min(len(frames), _MOSAICO_MAX_IMAGENES)
                        if image_b64:
                            _lanzar_descripcion(image_b64, image_media_type,
                                                n_imagenes, True)

                    transcription = _transcribe_video(video_path)
                ultimo_error = None
                break
            except Exception as e:
                ultimo_error = e
                print(f"[ig_api] error descargando video (intento {intento}/2): {e}", flush=True)
                time.sleep(1.5 * intento)
        if ultimo_error is not None:
            transcription = f"(transcripción no disponible: no se pudo descargar el video — {ultimo_error})"
        # No se pudo bajar el video (o no salió ningún frame): describimos la
        # portada, que es lo que se hacía antes de las capturas.
        if desc_future is None and image_b64:
            _lanzar_descripcion(image_b64, image_media_type, n_imagenes, True)
    elif is_video:
        # Reel sin video_url ni después de los reintentos: le decimos al usuario
        # POR QUÉ, en vez del genérico "sin transcripción disponible".
        motivo = _motivo_post_inaccesible(slow, fast)
        transcription = f"(transcripción no disponible: {motivo}.)"
        print(f"[ig_api] sin transcripción — {motivo}", flush=True)

    descripcion_error = ""
    if desc_future is not None:
        try:
            desc_ia = desc_future.result(timeout=30)
            if desc_ia:
                photo_description = desc_ia
                print(f"[describe] descripción IA lista ({len(desc_ia)} chars)", flush=True)
        except Exception as e:
            # Sólo avisamos si NO quedó ninguna descripción (ni el alt-text de IG):
            # si hay alt-text, hay algo que mostrar y no molestamos con el error.
            if not photo_description:
                descripcion_error = _motivo_desc_error(str(e))
            print(f"[describe] error/timeout, uso alt-text de Instagram ({e})", flush=True)
        finally:
            desc_executor.shutdown(wait=False)

    # En modo keyword alcanza con saber de quién es el post: no bajamos ni la
    # imagen ni el audio, así que exigir contenido lo haría fallar siempre.
    sin_contenido = (not owner_username and not caption) if ligero else (
        not caption and not transcription and not image_b64)
    if sin_contenido:
        motivo = _motivo_post_inaccesible(slow, fast)
        raise ValueError(f"No se pudo obtener el contenido del post: {motivo}.")

    resultado = PostData(
        url=url,
        shortcode=shortcode,
        caption=caption,
        comments=[],
        owner_username=owner_username,
        owner_full_name=owner_full_name,
        collaborators=collaborators,
        transcription=transcription,
        photo_description=photo_description,
        is_video=bool(is_video),
        image_b64=image_b64,
        image_media_type=image_media_type,
        n_imagenes=n_imagenes,
        descripcion_error=descripcion_error,
    )

    # Se cachea solo si el post salió completo: un video sin transcripción por un
    # error transitorio NO se guarda, así el siguiente intento vuelve a probar.
    transcripcion_fallada = transcription.startswith("(transcripción no disponible")
    # `transcripcion_fallada` no alcanza: si la API de Instagram falla, un reel
    # con URL /p/ queda marcado como foto (el og:type público dice "article"),
    # nunca entra en la rama de transcripción, y se cacheaba como una foto sin
    # nada adentro. Miramos el error de la API y la falta de imagen, que es lo
    # que de verdad indica que el scrape salió incompleto.
    scrape_degradado = bool(slow.get("_error")) or not image_b64
    if scrape_degradado:
        print(f"[cache] {shortcode} no se persiste: scrape incompleto "
              f"({slow.get('_error') or 'sin imagen'})", flush=True)
    if not transcripcion_fallada and not scrape_degradado and not ligero:
        _cache_put(shortcode, resultado)
    print(f"[TIMING] scrape_post total: {time.time()-t0:.2f}s", flush=True)
    return resultado
