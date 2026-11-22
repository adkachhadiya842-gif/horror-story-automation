
"""
Cinematic Horror Story - YouTube Thumbnail Generator
Hindi Devanagari font support using Noto Sans Devanagari.
"""

import json
import re
import subprocess
from pathlib import Path

from PIL import Image


BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
IMAGE_DIR = OUTPUT_DIR / "images"
THUMBNAIL_DIR = OUTPUT_DIR / "thumbnail"

THUMBNAIL_FILE = THUMBNAIL_DIR / "youtube_thumbnail.jpg"
TEXT_FILE = THUMBNAIL_DIR / "thumbnail_title.txt"
REPORT_FILE = THUMBNAIL_DIR / "thumbnail_report.json"

WIDTH = 1280
HEIGHT = 720

FONT_PATHS = [
    Path("/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf"),
]


def run_command(command):
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Thumbnail FFmpeg error:\n"
            + result.stderr[-4000:]
        )

    return result


def find_font():
    for font_path in FONT_PATHS:
        if font_path.is_file():
            return font_path

    raise FileNotFoundError(
        "Hindi Devanagari font not found. "
        "Install Ubuntu package fonts-noto-core."
    )


def find_story_analysis():
    path = OUTPUT_DIR / "story_analysis.json"

    if not path.exists():
        raise FileNotFoundError(
            "Story analysis not found: "
            + str(path)
        )

    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def find_title(analysis):
    title = (
        analysis.get("title")
        or analysis.get("story_title")
        or analysis.get("name")
        or "एक खौफनाक कहानी"
    )

    return str(title).strip()


def create_thumbnail_text(title):
    title = re.sub(r"\s+", " ", title).strip()

    if len(title) > 38:
        title = title[:35].rsplit(" ", 1)[0] + "..."

    return title


def find_best_image():
    if not IMAGE_DIR.exists():
        return None

    extensions = {".jpg", ".jpeg", ".png", ".webp"}

    images = [
        path
        for path in IMAGE_DIR.iterdir()
        if path.is_file()
        and path.suffix.lower() in extensions
        and path.name.lower().startswith("scene_")
    ]

    def natural_key(path):
        numbers = re.findall(r"\d+", path.stem)
        return int(numbers[0]) if numbers else 999999

    images.sort(key=natural_key)

    if not images:
        return None

    # Use the first current-run scene image.
    return images[0]


def find_cover_image():
    candidates = [
        OUTPUT_DIR / "thumbnail_background.jpg",
        OUTPUT_DIR / "thumbnail_background.png",
        OUTPUT_DIR / "cover.jpg",
        OUTPUT_DIR / "cover.png",
    ]

    for path in candidates:
        if path.is_file():
            return path

    return find_best_image()


def verify_thumbnail():
    if not THUMBNAIL_FILE.is_file():
        raise RuntimeError(
            "Thumbnail file was not created."
        )

    with Image.open(THUMBNAIL_FILE) as image:
        image.load()

        if image.size != (WIDTH, HEIGHT):
            raise RuntimeError(
                "Thumbnail resolution mismatch: "
                f"{image.size}"
            )

        if image.format != "JPEG":
            raise RuntimeError(
                f"Expected JPEG but got {image.format}"
            )


def create_thumbnail():
    THUMBNAIL_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    font_path = find_font()

    analysis = find_story_analysis()

    title = create_thumbnail_text(
        find_title(analysis)
    )

    image_path = find_cover_image()

    if image_path is None:
        raise FileNotFoundError(
            "No current scene image found in output/images/."
        )

    # Save Hindi text as UTF-8 text file.
    # This avoids FFmpeg command-line escaping problems.
    TEXT_FILE.write_text(
        title,
        encoding="utf-8",
    )

    font_path_ffmpeg = font_path.as_posix()
    text_path_ffmpeg = TEXT_FILE.resolve().as_posix()

    draw_text = (
        f"drawtext="
        f"fontfile='{font_path_ffmpeg}':"
        f"textfile='{text_path_ffmpeg}':"
        "expansion=none:"
        "text_shaping=1:"
        "fontcolor=white:"
        "fontsize=62:"
        "line_spacing=12:"
        "borderw=3:"
        "bordercolor=black:"
        "shadowcolor=black@0.85:"
        "shadowx=3:"
        "shadowy=3:"
        "box=1:"
        "boxcolor=black@0.45:"
        "boxborderw=18:"
        "x=(w-text_w)/2:"
        "y=h-text_h-65"
    )

    video_filter = (
        f"scale={WIDTH}:{HEIGHT}:"
        "force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},"
        "eq=brightness=-0.08:"
        "contrast=1.15:"
        "saturation=0.85,"
        + draw_text
    )

    print("Creating Hindi thumbnail...")
    print(f"Title: {title}")
    print(f"Font: {font_path}")
    print(f"Background: {image_path}")

    run_command([
        "ffmpeg",
        "-y",
        "-i", str(image_path),
        "-vf", video_filter,
        "-frames:v", "1",
        "-q:v", "2",
        "-pix_fmt", "yuvj420p",
        str(THUMBNAIL_FILE),
    ])

    verify_thumbnail()

    report = {
        "thumbnail": str(
            THUMBNAIL_FILE.relative_to(BASE_DIR)
        ),
        "title": title,
        "font": font_path.name,
        "resolution": f"{WIDTH}x{HEIGHT}",
        "background_image": str(
            image_path.relative_to(BASE_DIR)
        ),
        "hindi_text_shaping": True,
        "status": "completed",
    }

    with open(
        REPORT_FILE,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            report,
            file,
            indent=4,
            ensure_ascii=False,
        )

    print("\nTHUMBNAIL CREATED AND VERIFIED")
    print(THUMBNAIL_FILE)

    return THUMBNAIL_FILE


if __name__ == "__main__":
    create_thumbnail()
