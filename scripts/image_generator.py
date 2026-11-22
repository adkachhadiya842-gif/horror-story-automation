
"""
Cinematic Horror Story - Image Generator
OpenRouter Recraft image generation.
Includes clean-run handling, visual continuity prompts,
retry handling and accurate generation reports.
"""

import base64
import io
import json
import logging
import os
import re
import shutil
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

OUTPUT_ROOT = Path(OUTPUT_DIR)
ANALYSIS_PATH = OUTPUT_ROOT / "story_analysis.json"
ASSET_ROOT = OUTPUT_ROOT / "assets"

CHARACTER_DIR = ASSET_ROOT / "characters"
LOCATION_DIR = ASSET_ROOT / "locations"
ENTITY_DIR = ASSET_ROOT / "entities"
SCENE_DIR = OUTPUT_ROOT / "images"

REPORT_PATH = OUTPUT_ROOT / "image_generation_report.json"

STYLE = (
    "Premium cinematic photorealistic 3D horror film aesthetic, "
    "extremely detailed realistic textures, natural human anatomy, "
    "cinematic composition, dramatic film lighting, "
    "realistic Indian environments when appropriate, "
    "professional horror movie cinematography, "
    "high visual fidelity, widescreen 16:9 composition. "
    "No text, no captions, no logos, no watermarks."
)


def setup_logging():
    log_dir = OUTPUT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                log_dir / "image_generator.log",
                encoding="utf-8",
            ),
        ],
        force=True,
    )


def safe_filename(value):
    value = str(value).strip().lower()
    value = re.sub(r"[^\w-]+", "_", value)
    return value.strip("_")[:70] or "unnamed"


def get_setting(config, section, key, default):
    section_data = config.get(section, {})
    if not isinstance(section_data, dict):
        return default
    return section_data.get(key, default)


def clean_generated_folders():
    """
    Remove generated images from previous runs.
    Never deletes input/story.txt or other input files.
    """

    folders = [
        SCENE_DIR,
        CHARACTER_DIR,
        LOCATION_DIR,
        ENTITY_DIR,
    ]

    for folder in folders:
        if folder.exists():
            shutil.rmtree(folder)

        folder.mkdir(parents=True, exist_ok=True)

    LOGGER.info(
        "Previous generated image folders cleaned."
    )


def load_analysis():
    if not ANALYSIS_PATH.exists():
        raise FileNotFoundError(
            f"Story analysis not found: {ANALYSIS_PATH}"
        )

    with open(ANALYSIS_PATH, "r", encoding="utf-8") as file:
        analysis = json.load(file)

    if not isinstance(analysis, dict):
        raise ValueError(
            "Story analysis must be a JSON object."
        )

    scenes = analysis.get("scenes")

    if not isinstance(scenes, list) or not scenes:
        raise ValueError(
            "Story analysis must contain a non-empty scenes list."
        )

    return analysis


def get_api_key():
    key = os.getenv("OPENROUTER_API_KEY", "").strip()

    if not key:
        raise ValueError(
            "OPENROUTER_API_KEY is missing. "
            "Check GitHub Repository Secrets and workflow env."
        )

    return key


def build_reference_maps(analysis):
    """
    Create lookup dictionaries for character, location and entity
    descriptions so every scene prompt can reuse consistent details.
    """

    maps = {
        "characters": {},
        "locations": {},
        "entities": {},
    }

    for collection_name in maps:
        items = analysis.get(collection_name, [])

        if not isinstance(items, list):
            continue

        for item in items:
            if not isinstance(item, dict):
                continue

            name = str(item.get("name", "")).strip()

            if name:
                maps[collection_name][name.lower()] = item

    return maps


def describe_character(item):
    return (
        f"Name: {item.get('name', 'Unknown')}. "
        f"Age: {item.get('age', 'Not specified')}. "
        f"Appearance: {item.get('appearance', '')}. "
        f"Clothing: {item.get('clothing', '')}. "
        f"Personality: {item.get('personality', '')}. "
        f"Visual continuity: "
        f"{item.get('visual_consistency_notes', '')}."
    )


def get_scene_continuity(scene, analysis, reference_maps):
    details = []

    characters_present = scene.get(
        "characters_present", []
    )

    if isinstance(characters_present, str):
        characters_present = [characters_present]

    for character_name in characters_present:
        if isinstance(character_name, dict):
            character_name = character_name.get("name", "")

        name = str(character_name).strip()

        if not name:
            continue

        character = reference_maps["characters"].get(
            name.lower()
        )

        if character:
            details.append(
                "CHARACTER CONTINUITY: "
                + describe_character(character)
            )
        else:
            details.append(
                f"CHARACTER: {name}. "
                "Preserve the established appearance from previous scenes."
            )

    location_value = scene.get("location", "")

    if isinstance(location_value, dict):
        location_name = location_value.get("name", "")
    else:
        location_name = str(location_value).strip()

    if location_name:
        location = reference_maps["locations"].get(
            location_name.lower()
        )

        if location:
            details.append(
                "LOCATION CONTINUITY: "
                f"Name: {location.get('name', location_name)}. "
                f"Description: {location.get('description', '')}. "
                f"Atmosphere: {location.get('atmosphere', '')}. "
                f"Lighting: {location.get('lighting', '')}. "
                f"Visual notes: "
                f"{location.get('visual_consistency_notes', '')}."
            )
        else:
            details.append(
                f"LOCATION: {location_name}. "
                "Keep its appearance consistent with earlier scenes."
            )

    entities_present = scene.get(
        "entities_present", []
    )

    if isinstance(entities_present, str):
        entities_present = [entities_present]

    for entity_name in entities_present:
        if isinstance(entity_name, dict):
            entity_name = entity_name.get("name", "")

        name = str(entity_name).strip()

        if not name:
            continue

        entity = reference_maps["entities"].get(
            name.lower()
        )

        if entity:
            details.append(
                "ENTITY CONTINUITY: "
                f"Name: {entity.get('name', name)}. "
                f"Description: {entity.get('description', '')}. "
                f"Behavior: {entity.get('behavior', '')}. "
                f"Visual notes: "
                f"{entity.get('visual_consistency_notes', '')}."
            )

    return "\n".join(details)


def build_prompt(category, item, analysis=None, reference_maps=None):
    analysis = analysis or {}
    reference_maps = reference_maps or build_reference_maps(
        analysis
    )

    if category == "character":
        return f"""
Create a professional CHARACTER REFERENCE IMAGE
for a cinematic Hindi horror film.

CHARACTER NAME: {item.get('name', 'Unknown')}
AGE: {item.get('age', 'Not specified')}
APPEARANCE: {item.get('appearance', '')}
CLOTHING: {item.get('clothing', '')}
PERSONALITY: {item.get('personality', '')}
ROLE: {item.get('role', '')}
CONTINUITY: {item.get('visual_consistency_notes', '')}

Show one clean character reference composition with
full-body front view, three-quarter view and side profile.
Keep facial identity, body proportions, hairstyle and
clothing consistent in every view.

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
Preserve its physical characteristics, face, clothing,
body proportions and supernatural details.

Visual style: {STYLE}
"""

    if category == "scene":
        continuity = get_scene_continuity(
            item,
            analysis,
            reference_maps,
        )

        return f"""
Create ONE cinematic widescreen still image
for a professional Hindi horror movie.

STORY TITLE:
{analysis.get('title', 'Horror Story')}

SCENE NUMBER:
{item.get('scene_number', '')}

STORY EVENT:
{item.get('story_event', '')}

VISUAL DESCRIPTION:
{item.get('visual_description', '')}

CHARACTERS PRESENT:
{', '.join(map(str, item.get('characters_present', [])))}

LOCATION:
{item.get('location', '')}

CAMERA ANGLE:
{item.get('camera_angle', '')}

LIGHTING:
{item.get('lighting', '')}

ATMOSPHERE:
{item.get('atmosphere', '')}

VISUAL CONTINUITY DETAILS:
{continuity}

ADDITIONAL SCENE NOTES:
{item.get('visual_consistency_notes', '')}

STRICT RULES:
- Follow the original story event exactly.
- Do not invent characters or events.
- Do not change character identities or clothing.
- Maintain the same faces and physical appearances
  wherever characters return.
- Maintain location architecture and geography.
- Do not combine multiple story moments.
- Show one cinematic film frame only.
- Do not add written text or subtitles in the image.

Visual style: {STYLE}
"""

    raise ValueError(
        f"Unknown image category: {category}"
    )


def save_generated_image(image_bytes, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.load()

            if image.width < 256 or image.height < 256:
                raise ValueError(
                    f"Image resolution too small: "
                    f"{image.width}x{image.height}"
                )

            image.convert("RGB").save(
                output_path,
                format="PNG",
            )

    except Exception as error:
        raise ValueError(
            f"Returned image could not be decoded: {error}"
        ) from error

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

    request_data = json.dumps(
        payload
    ).encode("utf-8")

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
                timeout=180,
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
                images[0]["b64_json"],
                validate=True,
            )

            return save_generated_image(
                image_bytes,
                output_path,
            )

        except urllib.error.HTTPError as error:
            error_body = error.read().decode(
                "utf-8",
                errors="replace",
            )

            LOGGER.error(
                "OpenRouter HTTP error %s: %s",
                error.code,
                error_body[:1200],
            )

            if error.code in (400, 401, 402, 403, 404):
                raise RuntimeError(
                    f"OpenRouter rejected the request: "
                    f"HTTP {error.code}. {error_body[:800]}"
                ) from error

            if attempt == max_retries:
                raise RuntimeError(
                    f"HTTP {error.code}: {error_body[:800]}"
                ) from error

        except Exception as error:
            LOGGER.warning(
                "Image generation attempt failed: %s",
                error,
            )

            if attempt == max_retries:
                raise

        wait_time = retry_delay * attempt

        LOGGER.info(
            "Retrying after %s seconds.",
            wait_time,
        )

        time.sleep(wait_time)

    raise RuntimeError(
        f"Image generation failed: {output_path}"
    )


def generate_references(analysis, config):
    references = {
        "characters": {},
        "locations": {},
        "entities": {},
    }

    failures = []

    collections = [
        ("characters", CHARACTER_DIR, "character"),
        ("locations", LOCATION_DIR, "location"),
        ("entities", ENTITY_DIR, "entity"),
    ]

    max_retries = max(
        1,
        int(get_setting(
            config,
            "generation",
            "max_retries",
            3,
        )),
    )

    retry_delay = max(
        1,
        int(get_setting(
            config,
            "generation",
            "retry_delay_seconds",
            15,
        )),
    )

    aspect_ratio = get_setting(
        config,
        "images",
        "aspect_ratio",
        "16:9",
    )

    for collection_name, folder, category in collections:
        items = analysis.get(collection_name, [])

        if not isinstance(items, list):
            continue

        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue

            name = str(
                item.get("name") or f"{category}_{index}"
            ).strip()

            filename = (
                f"{index:03d}_{safe_filename(name)}.png"
            )

            output_path = folder / filename

            try:
                path = generate_image(
                    prompt=build_prompt(
                        category,
                        item,
                        analysis,
                    ),
                    output_path=output_path,
                    aspect_ratio=aspect_ratio,
                    max_retries=max_retries,
                    retry_delay=retry_delay,
                )

                references[collection_name][name] = {
                    "path": path,
                    "status": "success",
                }

            except Exception as error:
                LOGGER.exception(
                    "Reference generation failed: %s",
                    name,
                )

                references[collection_name][name] = {
                    "path": None,
                    "status": "failed",
                    "error": str(error),
                }

                failures.append({
                    "category": category,
                    "name": name,
                    "error": str(error),
                })

    return references, failures


def select_scene_images(scenes, image_count):
    if image_count <= 0:
        raise ValueError(
            "video.image_count must be positive."
        )

    if len(scenes) <= image_count:
        return scenes

    if image_count == 1:
        return [scenes[0]]

    indexes = [
        round(
            i * (len(scenes) - 1) / (image_count - 1)
        )
        for i in range(image_count)
    ]

    return [
        scenes[index]
        for index in indexes
    ]


def generate_scene_images(analysis, config):
    scenes = analysis["scenes"]

    image_count = int(
        get_setting(
            config,
            "video",
            "image_count",
            96,
        )
    )

    selected_scenes = select_scene_images(
        scenes,
        image_count,
    )

    max_retries = max(
        1,
        int(get_setting(
            config,
            "generation",
            "max_retries",
            3,
        )),
    )

    retry_delay = max(
        1,
        int(get_setting(
            config,
            "generation",
            "retry_delay_seconds",
            15,
        )),
    )

    aspect_ratio = get_setting(
        config,
        "images",
        "aspect_ratio",
        "16:9",
    )

    reference_maps = build_reference_maps(analysis)

    generated_scenes = []
    stop_after_rate_limit = False

    for index, scene in enumerate(selected_scenes, start=1):
        if not isinstance(scene, dict):
            generated_scenes.append({
                "image_index": index,
                "scene_number": index,
                "image_path": None,
                "status": "failed",
                "error": "Scene data is not a JSON object.",
            })
            continue

        scene_number = scene.get(
            "scene_number",
            index,
        )

        filename = (
            f"scene_{index:03d}_story_{safe_filename(scene_number)}.png"
        )

        output_path = SCENE_DIR / filename

        if stop_after_rate_limit:
            generated_scenes.append({
                "image_index": index,
                "scene_number": scene_number,
                "story_event": scene.get("story_event", ""),
                "image_path": None,
                "status": "skipped",
                "error": "Skipped after image API rate limit.",
            })
            continue

        prompt = build_prompt(
            "scene",
            scene,
            analysis,
            reference_maps,
        )

        try:
            image_path = generate_image(
                prompt=prompt,
                output_path=output_path,
                aspect_ratio=aspect_ratio,
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
            error_message = str(error)

            LOGGER.exception(
                "Scene image failed: %s",
                scene_number,
            )

            generated_scenes.append({
                "image_index": index,
                "scene_number": scene_number,
                "story_event": scene.get("story_event", ""),
                "image_path": None,
                "status": "failed",
                "error": error_message,
            })

            if "HTTP 429" in error_message:
                LOGGER.error(
                    "Image API rate limit reached. "
                    "Remaining scenes will be marked skipped."
                )
                stop_after_rate_limit = True

    return generated_scenes


def main():
    setup_logging()

    LOGGER.info(
        "Starting OpenRouter image generation."
    )
    LOGGER.info(
        "Image model: %s",
        IMAGE_MODEL,
    )

    create_project_directories()

    config = load_config()
    analysis = load_analysis()

    # Clean old generated images only after validating
    # that the new analysis and config are available.
    clean_generated_folders()

    LOGGER.info(
        "Story: %s | Scenes available: %s",
        analysis.get("title", "Untitled"),
        len(analysis["scenes"]),
    )

    references, reference_failures = generate_references(
        analysis,
        config,
    )

    scene_results = generate_scene_images(
        analysis,
        config,
    )

    successful = sum(
        1
        for item in scene_results
        if item["status"] == "success"
    )

    failed = sum(
        1
        for item in scene_results
        if item["status"] == "failed"
    )

    skipped = sum(
        1
        for item in scene_results
        if item["status"] == "skipped"
    )

    report = {
        "story_title": analysis.get(
            "title",
            "Untitled",
        ),
        "image_model": IMAGE_MODEL,
        "provider": "OpenRouter",
        "configured_max_scene_images": int(
            get_setting(
                config,
                "video",
                "image_count",
                96,
            )
        ),
        "scenes_available": len(analysis["scenes"]),
        "scene_images_requested": len(scene_results),
        "successful_scene_images": successful,
        "failed_scene_images": failed,
        "skipped_scene_images": skipped,
        "reference_images": references,
        "reference_failures": reference_failures,
        "scene_images": scene_results,
    }

    save_json(
        report,
        REPORT_PATH,
    )

    LOGGER.info(
        "Scene image results: %s successful, "
        "%s failed, %s skipped.",
        successful,
        failed,
        skipped,
    )

    LOGGER.info(
        "Reference image failures: %s",
        len(reference_failures),
    )

    LOGGER.info(
        "Image report saved: %s",
        REPORT_PATH,
    )

    if failed > 0 or skipped > 0 or successful == 0:
        raise RuntimeError(
            "Scene image generation incomplete: "
            f"{successful} successful, "
            f"{failed} failed, "
            f"{skipped} skipped."
        )

    LOGGER.info(
        "All selected scene images generated successfully."
    )


if __name__ == "__main__":
    main()
