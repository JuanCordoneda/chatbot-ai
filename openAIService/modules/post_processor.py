import instaloader
import re
import os
import html as html_lib
import tempfile
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
    transcription: str = ""
    photo_description: str = ""
    is_video: bool = False


def extract_shortcode(url: str) -> Optional[str]:
    match = re.search(r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)", url)
    return match.group(1) if match else None


def is_video_url(url: str) -> bool:
    return bool(re.search(r"instagram\.com/(?:reel|tv)/", url))


def _transcribe_video(video_path: str) -> str:
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("base", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(video_path)
        return " ".join(s.text for s in segments).strip()
    except Exception as e:
        return f"(transcripción no disponible: {e})"


def _fetch_fast(shortcode: str) -> dict:
    """Fetch post data quickly via public HTML (facebookexternalhit UA gets og tags)."""
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
                # Formato: "4,550 likes, 26 comments - peterjfournier on June 19, 2026: "caption""
                user_m = re.search(r'- ([A-Za-z0-9._]+) on [A-Za-z]+ \d+', raw)
                if user_m:
                    result["owner_username"] = user_m.group(1)
                caption_m = re.search(r':\s*"(.+)"$', raw, re.DOTALL)
                result["caption"] = caption_m.group(1) if caption_m else raw
                print(f"[fast_fetch] username={result.get('owner_username')} caption={result['caption'][:60]}", flush=True)
            # Detectar si es video por og:type
            m3 = re.search(r'<meta property="og:type" content="([^"]*)"', r.text)
            if m3:
                result["is_video"] = "video" in m3.group(1).lower()
                print(f"[fast_fetch] og:type={m3.group(1)} is_video={result['is_video']}", flush=True)
    except Exception as e:
        print(f"[fast_fetch] failed: {e}", flush=True)
    return result


def _fetch_instaloader(shortcode: str) -> dict:
    """Full fetch via instaloader (slower, more data)."""
    SESSION_FILE = os.path.join(os.path.dirname(__file__), "..", "session-crowagency.ofc")
    result = {}
    with tempfile.TemporaryDirectory() as tmpdir:
        L = instaloader.Instaloader(
            download_pictures=False,
            download_videos=False,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            compress_json=False,
            quiet=True,
            max_connection_attempts=1,
        )
        session_b64 = os.environ.get("INSTAGRAM_SESSION_B64")
        if session_b64:
            import base64, tempfile as _tf
            tmp = _tf.NamedTemporaryFile(delete=False, suffix=".ofc")
            tmp.write(base64.b64decode(session_b64))
            tmp.close()
            L.load_session_from_file("crowagency.ofc", tmp.name)
        elif os.path.exists(SESSION_FILE):
            L.load_session_from_file("crowagency.ofc", SESSION_FILE)

        post = instaloader.Post.from_shortcode(L.context, shortcode)
        result["caption"] = post.caption or ""
        result["owner_username"] = post.owner_username or ""
        result["photo_description"] = post.accessibility_caption or ""
        result["is_video"] = post.is_video

        if post.is_video:
            try:
                video_path = os.path.join(tmpdir, f"{shortcode}.mp4")
                r = req.get(post.video_url, timeout=60)
                with open(video_path, "wb") as f:
                    f.write(r.content)
                result["transcription"] = _transcribe_video(video_path)
            except Exception as e:
                result["transcription"] = f"(error descargando video: {e})"

    return result


def scrape_post(url: str, max_comments: int = 0) -> PostData:
    shortcode = extract_shortcode(url)
    if not shortcode:
        raise ValueError(f"No se pudo extraer el shortcode del link: {url}")

    import time
    t0 = time.time()

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    fast_future = executor.submit(_fetch_fast, shortcode)
    slow_future = executor.submit(_fetch_instaloader, shortcode)

    # Esperar fast fetch (máx 9s)
    fast = {}
    try:
        fast = fast_future.result(timeout=9)
        print(f"[fast_fetch] ok en {time.time()-t0:.2f}s: caption={'si' if fast.get('caption') else 'no'}", flush=True)
    except Exception as e:
        print(f"[fast_fetch] timeout/error: {e}", flush=True)

    # Si fast no trajo caption, esperar instaloader (requerimiento)
    # Si fast sí trajo caption, instaloader sigue en background para extras (photo_description, etc.)
    # pero no bloqueamos la generación — devolvemos ya con lo que tenemos
    # Instaloader siempre espera — la IA no arranca sin sus datos
    # (transcripción para videos, photo_description para fotos, caption limpio)
    slow = {}
    try:
        slow = slow_future.result()
        print(f"[instaloader] ok en {time.time()-t0:.2f}s", flush=True)
    except Exception as e:
        print(f"[instaloader] error: {e}", flush=True)
        if not fast.get("caption"):
            raise ValueError("No se pudo acceder al post. Verificá que el link sea público.")

    executor.shutdown(wait=False)

    caption = slow.get("caption") or fast.get("caption") or ""
    owner_username = slow.get("owner_username") or fast.get("owner_username") or ""
    photo_description = slow.get("photo_description") or ""
    transcription = slow.get("transcription") or ""
    # URL is the most reliable signal: /reel/ and /tv/ are always video
    is_video = is_video_url(url) or slow.get("is_video") or fast.get("is_video", False)

    if not caption:
        raise ValueError("No se pudo obtener el pie de página del post. Verificá que el link sea público.")

    return PostData(
        url=url,
        shortcode=shortcode,
        caption=caption,
        comments=[],
        owner_username=owner_username,
        transcription=transcription,
        photo_description=photo_description,
        is_video=bool(is_video),
    )
