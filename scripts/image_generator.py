
"""
Cinematic Horror Story - Image Generator
Uses OpenRouter Recraft free image model.
"""

import base64
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

from scripts.utils import (
    OUTPUT_DIR,
    load_config,
    create_project_directories,
    save_json,
)

LOGGER = logging.getLogger(__name__)

IMAGE_MODEL = "recraft/recraft-v4.1-flash-v2:free"
IMAGE_API_URL = "https://openrouter.ai/api/v1/images"

ANALYSIS_PATH = Path(OUTPUT_DIR) / "story_analysis.json"
ASSET_ROOT = Path(OUTPUT_DIR) / "assets"

CHARACTER_DIR = ASSET_ROOT / "characters"
LOCATION_DIR = ASSET_ROOT / "locations"
ENTITY_DIR = ASSET_ROOT / "entities"

# Video editor expects scene images in output/images/
SCENE_DIR = Path(OUTPUT_DIR) / "images"

STYLE = (
    "Premium cinematic photorealistic 3D horror film aesthetic, "
    "extremely detailed realistic textures, natural anatomy, "
    "cinematic composition, dramatic film lighting, "
    "realistic Indian environments when appropriate, "
    "professional horror movie cinematography, "
    "high visual fidelity, widescreen 16:9 composition. "
    "No text, no captions, no logos, no watermarks."
)


def setup_logging():
    log_dir = Path(OUTPUT_DIR) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                log_dir / "image_generator.log",
                encoding="utf-8"
            ),
        ],
    )


def safe_filename(value):
    value = str(value).strip().lower()
    value = re.sub(r"[^\w-]+", "_", value)
    return value.strip("_")[:70] or "unnamed"


def get_setting(config, section, key, default):
    return config.get(section, {}).get(key, default)


def load_analysis():
    if not ANALYSIS_PATH.exists():
        raise FileNotFoundError(
            f"Story analysis not found: {ANALYSIS_PATH}"
        )

    with open(ANALYSIS_PATH, "r", encoding="utf-8") as file:
        analysis = json.load(file)

    if not isinstance(analysis, dict):
        raise ValueError("Story analysis must be a JSON object.")

    if not isinstance(analysis.get("scenes"), list):
        raise ValueError("Story analysis must contain scenes.")

    if not analysis["scenes"]:
        raise ValueError("No scenes found in story analysis.")

    return analysis


def get_api_key():
    key = os.getenv("OPENROUTER_API_KEY", "").strip()

    if not key:
        raise ValueError(
            "OPENROUTER_API_KEY is missing. "
            "Check GitHub Repository Secrets and workflow env."
        )

    return key


def build_prompt(category, item, analysis=None):
    analysis = analysis or {}

    if category == "character":
        return f"""
Create a professional CHARACTER REFERENCE IMAGE for a
cinematic Hindi horror film.

CHARACTER NAME: {item.get('name', 'Unknown')}
AGE: {item.get('age', 'Not specified')}
APPEARANCE: {item.get('appearance', '')}
CLOTHING: {item.get('clothing', '')}
PERSONALITY: {item.get('personality', '')}
ROLE: {item.get('role', '')}
CONTINUITY: {item.get('visual_consistency_notes', '')}

Show full body, front view, three-quarter view and side profile.
Keep facial identity, body proportions and clothing consistent.

Visual style: {STYLE}
"""

    if category == "location":
        return f"""
Create a cinematic LOCATION REFERENCE IMAGE.

LOCATION: {item.get('name', 'Unknown')}
DESCRIPTION: {item.get('description', '')}
ATMOSPHERE: {item.get('atmosphere', '')}
LIGHTING: {item.get('lighting', '')}
CONTINUITY: {item.get('visual_consistency_notes', '')}

Show a detailed establishing view.
Preserve architecture, geography, weather and lighting.

Visual style: {STYLE}
"""

    if category == "entity":
        return f"""
Create a cinematic SUPERNATURAL ENTITY REFERENCE IMAGE.

ENTITY: {item.get('name', 'Unknown')}
DESCRIPTION: {item.get('description', '')}
BEHAVIOR: {item.get('behavior', '')}
CONTINUITY: {item.get('visual_consistency_notes', '')}

Create a frightening but visually coherent entity.
Preserve its physical characteristics.

Visual style: {STYLE}
"""

    if category == "scene":
        return f"""
Create ONE cinematic widescreen still image for a Hindi
horror movie.

STORY TITLE: {analysis.get('title', 'Horror Story')}
SCENE NUMBER: {item.get('scene_number', '')}

STORY EVENT:
{item.get('story_event', '')}

CHARACTERS PRESENT:
{', '.join(map(str, item.get('characters_present', [])))}

LOCATION:
{item.get('location', '')}

VISUAL DESCRIPTION:
{item.get('visual_description', '')}

CAMERA ANGLE:
{item.get('camera_angle', '')}

LIGHTING:
{item.get('lighting', '')}

ATMOSPHERE:
{item.get('atmosphere', '')}

CONTINUITY:
Maintain the same character descriptions, clothing,
facial features and location details wherever they recur.
Follow the original story event exactly.
Do not invent characters or events.
Do not combine multiple story moments.
Show one cinematic film frame only.

Visual style: {STYLE}
"""

    raise ValueError(f"Unknown image category: {category}")


def save_generated_image(image_bytes, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(__import__("io").BytesIO(image_bytes)) as image:
        image.load()
        image.convert("RGB").save(output_path, format="PNG")

    LOGGER.info("Image saved: %s", output_path)
    return str(output_path)


def generate_image(
    prompt,
    output_path,
    aspect_ratio="16:9",
    max_retries=3,
    retry_delay=15,
):
    api_key = get_api_key()

    payload = {
        "model": IMAGE_MODEL,
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "n": 1,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/",
        "X-Title": "Horror Story Automation",
    }

    request_data = json.dumps(payload).encode("utf-8")

    for attempt in range(1, max_retries + 1):
        try:
            LOGGER.info(
                "Generating %s (attempt %s/%s)",
                output_path,
                attempt,
                max_retries,
            )

            request = urllib.request.Request(
                IMAGE_API_URL,
                data=request_data,
                headers=headers,
                method="POST",
            )

            with urllib.request.urlopen(
                request,
                timeout=180
            ) as response:
                result = json.loads(
                    response.read().decode("utf-8")
                )

            images = result.get("data", [])

            if not images or not images[0].get("b64_json"):
                raise RuntimeError(
                    "OpenRouter returned no image data."
                )

            image_bytes = base64.b64decode(
                images[0]["b64_json"]
            )

            return save_generated_image(
                image_bytes,
                output_path
            )

        except urllib.error.HTTPError as error:
            error_body = error.read().decode(
                "utf-8",
                errors="replace"
            )

            LOGGER.error(
                "OpenRouter HTTP error %s: %s",
                error.code,
                error_body[:1500],
            )

            # Do not retry authentication or invalid model errors.
            if error.code in (400, 401, 402, 403, 404):
                raise RuntimeError(
                    f"OpenRouter rejected the request: HTTP {error.code}. "
                    f"{error_body[:1000]}"
                ) from error

            if attempt == max_retries:
                raise

        except Exception as error:
            LOGGER.warning(
                "Image generation failed: %s",
                error
            )

            if attempt == max_retries:
                raise

        wait_time = retry_delay * attempt
        LOGGER.info("Retrying after %s seconds.", wait_time)
        time.sleep(wait_time)

    return None


def generate_references(analysis, config):
    references = {
        "characters": {},
        "locations": {},
        "entities": {},
    }

    collections = [
        ("characters", CHARACTER_DIR, "character"),
        ("locations", LOCATION_DIR, "location"),
        ("entities", ENTITY_DIR, "entity"),
    ]

    max_retries = int(
        get_setting(config, "generation", "max_retries", 3)
    )

    retry_delay = int(
        get_setting(config, "generation", "retry_delay_seconds", 15)
    )

    for collection_name, folder, category in collections:
        items = analysis.get(collection_name, [])

        if not isinstance(items, list):
            continue

        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue

            name = item.get("name") or f"{category}_{index}"
            filename = f"{index:03d}_{safe_filename(name)}.png"
            output_path = folder / filename

            try:
                path = generate_image(
                    prompt=build_prompt(category, item, analysis),
                    output_path=output_path,
                    max_retries=max_retries,
                    retry_delay=retry_delay,
                )

                references[collection_name][name] = path

            except Exception:
                LOGGER.exception(
                    "Reference generation failed: %s",
                    name
                )

    return references


def select_scene_images(scenes, image_count):
    if image_count <= 0:
        raise ValueError("image_count must be positive.")

    if len(scenes) <= image_count:
        return scenes

    if image_count == 1:
        return [scenes[0]]

    indexes = [
        round(i * (len(scenes) - 1) / (image_count - 1))
        for i in range(image_count)
    ]

    return [scenes[index] for index in indexes]


def generate_scene_images(analysis, config):
    scenes = analysis["scenes"]

    image_count = int(
        get_setting(config, "video", "image_count", 96)
    )

    selected_scenes = select_scene_images(
        scenes,
        image_count
    )

    max_retries = int(
        get_setting(config, "generation", "max_retries", 3)
    )

    retry_delay = int(
        get_setting(config, "generation", "retry_delay_seconds", 15)
    )

    generated_scenes = []

    for index, scene in enumerate(selected_scenes, start=1):
        if not isinstance(scene, dict):
            continue

        scene_number = scene.get("scene_number", index)

        filename = (
            f"scene_{index:03d}_story_{scene_number}.png"
        )

        output_path = SCENE_DIR / filename

        prompt = build_prompt(
            "scene",
            scene,
            analysis
        )

        try:
            image_path = generate_image(
                prompt=prompt,
                output_path=output_path,
                max_retries=max_retries,
                retry_delay=retry_delay,
            )

            generated_scenes.append({
                "image_index": index,
                "scene_number": scene_number,
                "story_event": scene.get("story_event", ""),
                "image_path": image_path,
                "status": "success",
            })

        except Exception as error:
            LOGGER.exception(
                "Scene image failed: %s",
                scene_number
            )

            generated_scenes.append({
                "image_index": index,
                "scene_number": scene_number,
                "story_event": scene.get("story_event", ""),
                "image_path": None,
                "status": "failed",
                "error": str(error),
            })

            # Stop early if free API is unavailable or rate-limited.
            if "HTTP 429" in str(error):
                LOGGER.error(
                    "Free image model rate limit reached. "
                    "Stopping further image requests."
                )
                break

    return generated_scenes


def main():
    setup_logging()

    LOGGER.info("Starting OpenRouter image generation.")
    LOGGER.info("Image model: %s", IMAGE_MODEL)

    create_project_directories()

    for folder in [
        CHARACTER_DIR,
        LOCATION_DIR,
        ENTITY_DIR,
        SCENE_DIR,
    ]:
        folder.mkdir(parents=True, exist_ok=True)

    config = load_config()
    analysis = load_analysis()

    LOGGER.info(
        "Story: %s | Scenes available: %s",
        analysis.get("title", "Untitled"),
        len(analysis["scenes"]),
    )

    references = generate_references(
        analysis,
        config
    )

    scene_results = generate_scene_images(
        analysis,
        config
    )

    successful = sum(
        1 for item in scene_results
        if item["status"] == "success"
    )

    failed = sum(
        1 for item in scene_results
        if item["status"] == "failed"
    )

    report = {
        "story_title": analysis.get("title", "Untitled"),
        "image_model": IMAGE_MODEL,
        "provider": "OpenRouter",
        "reference_images": references,
        "scene_images": scene_results,
        "total_scenes_requested": len(scene_results),
        "successful_scene_images": successful,
        "failed_scene_images": failed,
    }

    report_path = (
        Path(OUTPUT_DIR) / "image_generation_report.json"
    )

    save_json(report, report_path)

    LOGGER.info(
        "Image generation result: %s successful, %s failed.",
        successful,
        failed,
    )

    LOGGER.info("Report saved: %s", report_path)

    if failed > 0 or successful == 0:
        raise RuntimeError(
            f"Image generation incomplete: "
            f"{successful} successful, {failed} failed."
        )

    LOGGER.info("All scene images generated successfully.")


if __name__ == "__main__":
    main()
