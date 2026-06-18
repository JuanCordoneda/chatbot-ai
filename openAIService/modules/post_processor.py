import instaloader
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class PostData:
    url: str
    shortcode: str
    caption: str
    comments: list[str]


def extract_shortcode(url: str) -> Optional[str]:
    match = re.search(r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)", url)
    return match.group(1) if match else None


def scrape_post(url: str, max_comments: int = 20) -> PostData:
    shortcode = extract_shortcode(url)
    if not shortcode:
        raise ValueError(f"No se pudo extraer el shortcode del link: {url}")

    L = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=True,
        save_metadata=False,
        compress_json=False,
        quiet=True,
    )

    post = instaloader.Post.from_shortcode(L.context, shortcode)

    caption = post.caption or ""

    comments = []
    try:
        for comment in post.get_comments():
            if len(comments) >= max_comments:
                break
            text = comment.text.strip()
            if text:
                comments.append(text)
    except Exception:
        pass

    return PostData(
        url=url,
        shortcode=shortcode,
        caption=caption,
        comments=comments,
    )
