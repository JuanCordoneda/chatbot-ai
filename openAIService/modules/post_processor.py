import re
import os
import json
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
    owner_full_name: str = ""
    transcription: str = ""
    photo_description: str = ""
    is_video: bool = False


def extract_shortcode(url: str) -> Optional[str]:
    match = re.search(r"instagram\.com/(?:p|reels?|tv)/([A-Za-z0-9_-]+)", url)
    return match.group(1) if match else None


def is_video_url(url: str) -> bool:
    return bool(re.search(r"instagram\.com/(?:reels?|tv)/", url))


_whisper_model = None

def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
    return _whisper_model


def _transcribe_video(video_path: str) -> str:
    try:
        model = _get_whisper_model()
        segments, _ = model.transcribe(video_path)
        text = " ".join(s.text for s in segments).strip()
        print(f"[whisper] transcripción: {len(text)} chars", flush=True)
        return text
    except Exception as e:
        print(f"[whisper] error: {e}", flush=True)
        return f"(transcripción no disponible: {e})"


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


def _fetch_instagram_api(shortcode: str) -> dict:
    """
    Fetch via GraphQL doc_id de Instagram (la misma API que usa el navegador).
    Reemplaza instaloader que usa query_hash ya bloqueado por Instagram.
    """
    cookies = _load_ig_cookies()
    if not cookies.get("sessionid"):
        print("[ig_api] sin sesión disponible", flush=True)
        return {}

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
            return {}

        media = r.json().get("data", {}).get("xdt_shortcode_media")
        if not media:
            print(f"[ig_api] media null para {shortcode}", flush=True)
            return {}

        result = {
            "caption": (media.get("edge_media_to_caption", {}).get("edges") or [{}])[0].get("node", {}).get("text", "") or "",
            "owner_username": media.get("owner", {}).get("username", "") or "",
            "owner_full_name": media.get("owner", {}).get("full_name", "") or "",
            "photo_description": media.get("accessibility_caption", "") or "",
            "is_video": media.get("is_video", False),
            "video_url": media.get("video_url", "") or "",
        }
        print(f"[ig_api] ok — owner={result['owner_username']} is_video={result['is_video']}", flush=True)
        return result

    except Exception as e:
        print(f"[ig_api] error: {e}", flush=True)
        return {}


def scrape_post(url: str, max_comments: int = 0) -> PostData:
    shortcode = extract_shortcode(url)
    if not shortcode:
        raise ValueError(f"No se pudo extraer el shortcode del link: {url}")

    import time
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

    if is_video and video_url:
        print(f"[ig_api] descargando video para transcripción...", flush=True)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                video_path = os.path.join(tmpdir, f"{shortcode}.mp4")
                r_vid = req.get(video_url, timeout=60)
                with open(video_path, "wb") as f:
                    f.write(r_vid.content)
                transcription = _transcribe_video(video_path)
        except Exception as e:
            print(f"[ig_api] error descargando video: {e}", flush=True)

    if not caption and not transcription:
        raise ValueError("No se pudo obtener el pie de página ni la transcripción del post. Verificá que el link sea público.")

    return PostData(
        url=url,
        shortcode=shortcode,
        caption=caption,
        comments=[],
        owner_username=owner_username,
        owner_full_name=owner_full_name,
        transcription=transcription,
        photo_description=photo_description,
        is_video=bool(is_video),
    )
