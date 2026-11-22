
import json
import os
import re
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
IMAGE_DIR = OUTPUT_DIR / "images"
THUMBNAIL_DIR = OUTPUT_DIR / "thumbnail"

THUMBNAIL_FILE = THUMBNAIL_DIR / "youtube_thumbnail.jpg"

WIDTH = 1280
HEIGHT = 720


def find_story_analysis():
    path = OUTPUT_DIR / "story_analysis.json"

    if not path.exists():
        return {}

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
    # Keep the thumbnail title short and readable.
    title = re.sub(r"\s+", " ", title).strip()

    if len(title) > 45:
        title = title[:42].rsplit(" ", 1)[0] + "..."

    return title


def find_best_image():
    if not IMAGE_DIR.exists():
        return None

    extensions = {".jpg", ".jpeg", ".png", ".webp"}

    images = sorted(
        [
            path for path in IMAGE_DIR.rglob("*")
            if path.is_file() and path.suffix.lower() in extensions
        ],
        key=lambda path: path.name
    )

    if not images:
        return None

    # Use the first scene image as a fallback background.
    return images[0]


def find_cover_image():
    candidates = [
        OUTPUT_DIR / "thumbnail_background.jpg",
        OUTPUT_DIR / "thumbnail_background.png",
        OUTPUT_DIR / "cover.jpg",
        OUTPUT_DIR / "cover.png",
        IMAGE_DIR / "thumbnail.jpg",
        IMAGE_DIR / "thumbnail.png"
    ]

    for path in candidates:
        if path.exists():
            return path

    return find_best_image()


def create_thumbnail():
    THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)

    analysis = find_story_analysis()
    title = create_thumbnail_text(find_title(analysis))
    image_path = find_cover_image()

    if image_path is None:
        raise FileNotFoundError(
            "No image found. Generate scene images first."
        )

    # Escape special characters for FFmpeg drawtext.
    safe_title = (
        title.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
        .replace(",", "\\,")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )

    draw_text = (
        f"drawtext=text='{safe_title}':"
        "fontcolor=white:"
        "fontsize=64:"
        "borderw=4:"
        "bordercolor=black:"
        "shadowcolor=black@0.8:"
        "shadowx=3:"
        "shadowy=3:"
        "x=(w-text_w)/2:"
        "y=h-text_h-65"
    )

    command = [
        "ffmpeg", "-y",
        "-i", str(image_path),
        "-vf",
        (
            f"scale={WIDTH}:{HEIGHT}:"
            "force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},"
            "eq=brightness=-0.08:contrast=1.15:saturation=0.8,"
            + draw_text
        ),
        "-frames:v", "1",
        "-q:v", "2",
        str(THUMBNAIL_FILE)
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Thumbnail generation failed:\n" +
            result.stderr[-3000:]
        )

    report = {
        "thumbnail": str(THUMBNAIL_FILE.relative_to(BASE_DIR)),
        "title": title,
        "resolution": f"{WIDTH}x{HEIGHT}",
        "status": "completed"
    }

    with open(
        THUMBNAIL_DIR / "thumbnail_report.json",
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(report, file, indent=4, ensure_ascii=False)

    print("THUMBNAIL CREATED SUCCESSFULLY")
    print(THUMBNAIL_FILE)

    return THUMBNAIL_FILE


if __name__ == "__main__":
    create_thumbnail()
