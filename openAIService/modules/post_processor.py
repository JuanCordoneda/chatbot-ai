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
from dataclasses import dataclass
from typing import Optional


@dataclass
class PostData:
    url: str
    shortcode: str
    caption: str
    comments: list[str]
    owner_username: str = ""
    owner_full_name: str = ""
    transcription: str = ""
    photo_description: str = ""
    is_video: bool = False
    image_b64: str = ""          # imagen del post en base64 (visión multimodal)
    image_media_type: str = ""   # ej "image/jpeg"
    # Motivo (para el vendedor) si la descripción por IA quedó vacía por un error
    # de la IA (saturada / sin crédito). "" si salió bien o no hubo imagen.
    descripcion_error: str = ""


def _motivo_desc_error(err: str) -> str:
    """Mensaje corto para el vendedor cuando la descripción por IA falla."""
    e = (err or "").lower()
    if "overloaded" in e or "529" in e:
        return "No se pudo describir la imagen: la IA está saturada. Reintentá en unos segundos."
    if "rate_limit" in e or "429" in e:
        return "No se pudo describir la imagen: demasiadas consultas seguidas. Reintentá en un momento."
    if "credit balance" in e or "billing" in e or "insufficient" in e:
        return "No se pudo describir la imagen: el servicio de IA se quedó sin crédito. Avisá al administrador."
    if "timeout" in e or "timed out" in e:
        return "No se pudo describir la imagen: la IA tardó demasiado. Reintentá."
    return "No se pudo describir la imagen (error de la IA). Reintentá en un momento."


def extract_shortcode(url: str) -> Optional[str]:
    match = re.search(r"instagram\.com/(?:p|reels?|tv)/([A-Za-z0-9_-]+)", url)
    return match.group(1) if match else None


def is_video_url(url: str) -> bool:
    return bool(re.search(r"instagram\.com/(?:reels?|tv)/", url))


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
# Idioma de los videos. Vacío ("") vuelve a la autodetección.
_WHISPER_LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "es") or None
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
            # language fijo: con audio de cancha / música, la autodetección de
            # idioma se equivocaba y devolvía frases inventadas en inglés sobre
            # un video hablado en español.
            # vad_filter: recorta los tramos sin voz. Sin esto, whisper "rellena"
            # el ruido ambiente con texto alucinado ("I don't know...") que después
            # se le mostraba al vendedor como si fuera lo que dice el video.
            segments, _ = model.transcribe(
                _preparar_audio(video_path),
                language=_WHISPER_LANGUAGE,
                vad_filter=True,
                condition_on_previous_text=False,   # corta el loop de repetir la última frase
                beam_size=_WHISPER_BEAM,
            )
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


def _load_ig_cookies() -> dict:
    """
    Carga las cookies de Instagram desde env vars o archivo de sesión.
    Prioridad: INSTAGRAM_COOKIES_JSON > INSTAGRAM_SESSION_B64 (formato instaloader pickle)
    """
    # Formato nuevo: JSON con las cookies directamente
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

    # Archivo de sesión local (docker-compose / dev)
    session_file = os.path.join(os.path.dirname(__file__), "..", "session-crowagency.ofc")
    if os.path.exists(session_file):
        try:
            with open(session_file, "rb") as f:
                data = pickle.load(f)
            return {
                "sessionid": data.get("sessionid", ""),
                "csrftoken": data.get("csrftoken", ""),
                "ds_user_id": data.get("ds_user_id", ""),
                "mid": data.get("mid", ""),
                "ig_did": data.get("ig_did", ""),
                "datr": data.get("datr", ""),
            }
        except Exception as e:
            print(f"[ig_cookies] error cargando session file: {e}", flush=True)

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


def _tile_px(n: int) -> int:
    """Px por celda del mosaico. Con pocas fotos agrandamos las celdas (se lee
    mejor el texto chico); con muchas achicamos para no inflar la imagen."""
    if n <= 4:
        return 768
    if n <= 9:
        return 512
    return 384


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

        buf = io.BytesIO()
        lienzo.save(buf, format="JPEG", quality=85)
        print(f"[mosaico] {len(fotos)} imágenes en grilla {cols}x{filas} "
              f"({len(buf.getvalue())} bytes)", flush=True)
        return base64.b64encode(buf.getvalue()).decode(), "image/jpeg"
    except Exception as e:
        print(f"[mosaico] error armando mosaico: {e}", flush=True)
        return "", ""


def _fetch_instagram_api(shortcode: str) -> dict:
    """
    Fetch via GraphQL doc_id de Instagram (la misma API que usa el navegador).
    Reemplaza instaloader que usa query_hash ya bloqueado por Instagram.
    """
    cookies = _load_ig_cookies()
    if not cookies.get("sessionid"):
        print("[ig_api] sin sesión disponible", flush=True)
        return {"_error": "no hay sesión de Instagram configurada en el server"}

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
            return {"_error": motivo}

        media = r.json().get("data", {}).get("xdt_shortcode_media")
        if not media:
            print(f"[ig_api] media null para {shortcode}", flush=True)
            return {"_error": "Instagram no devolvió los datos del post (puede ser privado, borrado, o la sesión venció)"}

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
            "photo_description": media.get("accessibility_caption", "") or "",
            "is_video": media.get("is_video", False),
            "video_url": media.get("video_url", "") or "",
            "display_url": display_url,
            "display_urls": display_urls,
        }
        print(f"[ig_api] ok — owner={result['owner_username']} is_video={result['is_video']}", flush=True)
        return result

    except Exception as e:
        print(f"[ig_api] error: {e}", flush=True)
        return {"_error": f"no se pudo contactar a Instagram ({type(e).__name__})"}


# Caché de posts ya scrapeados. "Cargar más" (y volver a pegar el mismo link)
# re-scrapeaba TODO de cero: bajaba el video otra vez, lo re-transcribía y volvía
# a describir la imagen. Eso multiplicaba la carga y los pedidos a Instagram sin
# aportar nada, porque el post es el mismo. Ahora se reusa por unos minutos.
_scrape_cache = {}                     # shortcode -> (timestamp, PostData)
_scrape_cache_lock = threading.Lock()
_SCRAPE_TTL = int(os.environ.get("SCRAPE_CACHE_TTL", "600"))    # 10 min
_SCRAPE_CACHE_MAX = 12                 # el image_b64 pesa: acotamos la memoria


def _cache_get(shortcode: str):
    with _scrape_cache_lock:
        hit = _scrape_cache.get(shortcode)
        if not hit:
            return None
        ts, data = hit
        if time.time() - ts > _SCRAPE_TTL:
            _scrape_cache.pop(shortcode, None)
            return None
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


def scrape_post(url: str, max_comments: int = 0) -> PostData:
    shortcode = extract_shortcode(url)
    if not shortcode:
        raise ValueError(f"No se pudo extraer el shortcode del link: {url}")

    # Solo se cachea lo que salió BIEN: si la transcripción falló, el próximo
    # intento tiene que volver a probar (si no, un rate limit puntual quedaba
    # pegado 10 minutos).
    cacheado = _cache_get(shortcode)
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
    if is_video and not video_url:
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
        image_b64 = base64.b64encode(descargadas[0][0]).decode()
        image_media_type = descargadas[0][1]
        n_imagenes = 1
        print(f"[image] imagen del post descargada ({len(descargadas[0][0])} bytes, {image_media_type})", flush=True)
    elif len(descargadas) > 1:
        image_b64, image_media_type = _armar_mosaico([c for c, _ in descargadas])
        if image_b64:
            n_imagenes = min(len(descargadas), _MOSAICO_MAX_IMAGENES)
        else:
            # Sin Pillow o mosaico fallido: al menos describimos la primera.
            import base64
            image_b64 = base64.b64encode(descargadas[0][0]).decode()
            image_media_type = descargadas[0][1]
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
                                               caption, n, es_video)
        except Exception as e:
            print(f"[describe] no disponible ({e})", flush=True)

    # En videos la descripción sale de un mosaico de capturas del propio video,
    # así que se lanza recién después de bajarlo (igual queda en paralelo con la
    # transcripción, que es lo lento). En fotos se lanza ya.
    if image_b64 and not (is_video and video_url):
        _lanzar_descripcion(image_b64, image_media_type, n_imagenes, False)

    if is_video and video_url:
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
        motivo = slow.get("_error") or "Instagram no devolvió el link del video"
        transcription = f"(transcripción no disponible: {motivo}. Probá de nuevo en un minuto.)"
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

    if not caption and not transcription and not image_b64:
        motivo = slow.get("_error") or "el post puede ser privado o el link estar mal"
        raise ValueError(f"No se pudo obtener el contenido del post: {motivo}.")

    resultado = PostData(
        url=url,
        shortcode=shortcode,
        caption=caption,
        comments=[],
        owner_username=owner_username,
        owner_full_name=owner_full_name,
        transcription=transcription,
        photo_description=photo_description,
        is_video=bool(is_video),
        image_b64=image_b64,
        image_media_type=image_media_type,
        descripcion_error=descripcion_error,
    )

    # Se cachea solo si el post salió completo: un video sin transcripción por un
    # error transitorio NO se guarda, así el siguiente intento vuelve a probar.
    transcripcion_fallada = transcription.startswith("(transcripción no disponible")
    if not transcripcion_fallada:
        _cache_put(shortcode, resultado)
    print(f"[TIMING] scrape_post total: {time.time()-t0:.2f}s", flush=True)
    return resultado
