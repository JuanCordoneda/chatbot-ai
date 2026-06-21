import instaloader
import re
import os
import tempfile
from dataclasses import dataclass, field
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


def extract_shortcode(url: str) -> Optional[str]:
    match = re.search(r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)", url)
    return match.group(1) if match else None


def _transcribe_video(video_path: str) -> str:
    try:
        import whisper
        model = whisper.load_model("base")
        result = model.transcribe(video_path, fp16=False)
        return result.get("text", "").strip()
    except Exception as e:
        return f"(transcripción no disponible: {e})"


def scrape_post(url: str, max_comments: int = 20) -> PostData:
    shortcode = extract_shortcode(url)
    if not shortcode:
        raise ValueError(f"No se pudo extraer el shortcode del link: {url}")

    SESSION_FILE = os.path.join(os.path.dirname(__file__), "..", "session-crowagency.ofc")

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

        if os.path.exists(SESSION_FILE):
            L.load_session_from_file("crowagency.ofc", SESSION_FILE)

        post = instaloader.Post.from_shortcode(L.context, shortcode)
        caption = post.caption or ""
        owner_username = post.owner_username or ""
        photo_description = post.accessibility_caption or ""

        comments = []

        # Descargar y transcribir video manualmente
        transcription = ""
        if post.is_video:
            try:
                import requests
                video_url = post.video_url
                video_path = os.path.join(tmpdir, f"{shortcode}.mp4")
                r = requests.get(video_url, timeout=60)
                with open(video_path, "wb") as f:
                    f.write(r.content)
                transcription = _transcribe_video(video_path)
            except Exception as e:
                transcription = f"(error descargando video: {e})"

    return PostData(
        url=url,
        shortcode=shortcode,
        caption=caption,
        comments=comments,
        owner_username=owner_username,
        transcription=transcription,
        photo_description=photo_description,
    )
