"""
Cinematic Horror Story - Image Generator
OpenRouter Recraft image generation.

Features:
- Validates story analysis and configuration before cleaning outputs.
- Generates scene images before optional reference sheets.
- Limits free-model requests per run (configurable).
- Stops immediately on HTTP 429 instead of wasting retries.
- Retries temporary network/server errors with Retry-After support.
- Validates and atomically saves image files.
- Writes a report compatible with pipeline.py (scene_images key).
"""

import base64
import email.utils
import io
import json
import logging
import os
import re
import shutil
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from scripts.utils import (
    OUTPUT_DIR,
    load_config,
    create_project_directories,
    save_json,
)


LOGGER = logging.getLogger(__name__)

DEFAULT_IMAGE_MODEL = "recraft/recraft-v4.1-flash-v2:free"
IMAGE_MODEL = os.getenv(
    "OPENROUTER_IMAGE_MODEL",
    DEFAULT_IMAGE_MODEL,
).strip() or DEFAULT_IMAGE_MODEL

IMAGE_API_URL = "https://openrouter.ai/api/v1/images"

OUTPUT_ROOT = Path(OUTPUT_DIR)
ANALYSIS_PATH = OUTPUT_ROOT / "story_analysis.json"
ASSET_ROOT = OUTPUT_ROOT / "assets"

CHARACTER_DIR = ASSET_ROOT / "characters"
LOCATION_DIR = ASSET_ROOT / "locations"
ENTITY_DIR = ASSET_ROOT / "entities"
SCENE_DIR = OUTPUT_ROOT / "images"

REPORT_PATH = OUTPUT_ROOT / "image_generation_report.json"

# The free model has request limits. Keep scene images as the priority.
# Override with generation.max_scene_images_per_run in config.json.
DEFAULT_MAX_SCENE_IMAGES_PER_RUN = 35
DEFAULT_MAX_REFERENCE_IMAGES_PER_RUN = 5

STYLE = (
    "Premium cinematic 2D illustrated documentary animation aesthetic, "
    "semi-realistic hand-painted 2D artwork, detailed clean ink outlines, "
    "natural human anatomy and believable facial expressions, "
    "professional historical documentary illustration, "
    "realistic Indian environments when appropriate, "
    "cinematic widescreen composition, rich painted backgrounds, "
    "subtle paper and brush texture, high visual detail. "
    "COLOR PALETTE: dominant dark navy blue (#071426) and steel blue "
    "(#162C45), deep crimson red (#8F1728) used only as a restrained accent, "
    "muted warm beige (#D5BDA0) for natural face and practical light tones. "
    "LIGHTING: dramatic low-key lighting, cool blue shadows, subtle crimson "
    "rim light only where motivated, soft volumetric light, strong but "
    "controlled contrast, natural skin tones. "
    "MOOD: serious, mysterious, suspenseful, cinematic documentary. "
    "Avoid photorealistic photography, 3D CGI, plastic cartoon rendering, "
    "childish bright colors, full-frame red or blue tint, excessive glow. "
    "Widescreen 16:9 composition. No text, captions, logos, or watermarks."
)

RETRYABLE_HTTP_CODES = {408, 425, 500, 502, 503, 504, 529}
NON_RETRYABLE_HTTP_CODES = {400, 401, 402, 403, 404, 405, 413, 415, 422}


class ImageRateLimitError(RuntimeError):
    """Raised when OpenRouter rejects an image request due to rate limits."""


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
    if not isinstance(config, dict):
        return default

    section_data = config.get(section, {})
    if not isinstance(section_data, dict):
        return default

    return section_data.get(key, default)


def as_text(value, default=""):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def as_name_list(value):
    if value is None:
        return []
    if isinstance(value, (str, dict)):
        value = [value]
    if not isinstance(value, list):
        return []

    names = []
    for item in value:
        if isinstance(item, dict):
            name = item.get("name", "")
        else:
            name = item
        name = str(name).strip()
        if name:
            names.append(name)
    return names


def clean_generated_folders():
    """Remove generated image folders from previous runs only."""
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

    LOGGER.info("Previous generated image folders cleaned.")


def load_analysis():
    if not ANALYSIS_PATH.exists():
        raise FileNotFoundError(
            f"Story analysis not found: {ANALYSIS_PATH}"
        )

    with open(ANALYSIS_PATH, "r", encoding="utf-8") as file:
        analysis = json.load(file)

    if not isinstance(analysis, dict):
        raise ValueError("Story analysis must be a JSON object.")

    scenes = analysis.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError(
            "Story analysis must contain a non-empty scenes list."
        )

    valid_scenes = [scene for scene in scenes if isinstance(scene, dict)]
    if not valid_scenes:
        raise ValueError(
            "Story analysis contains no valid scene objects."
        )

    analysis["scenes"] = valid_scenes
    return analysis


def get_api_key():
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise ValueError(
            "OPENROUTER_API_KEY is missing. "
            "Check GitHub Repository Secrets and workflow env."
        )
    return key


def positive_int_setting(config, section, key, default, minimum=1, maximum=10):
    raw_value = get_setting(config, section, key, default)
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        LOGGER.warning(
            "Invalid %s.%s=%r; using %s.",
            section,
            key,
            raw_value,
            default,
        )
        value = default

    return max(minimum, min(value, maximum))


def build_reference_maps(analysis):
    """Build text descriptions for character, location and entity continuity."""
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

            name = as_text(item.get("name"))
            if name:
                maps[collection_name][name.casefold()] = item

    return maps


def describe_character(item):
    return (
        f"Name: {as_text(item.get('name'), 'Unknown')}. "
        f"Age: {as_text(item.get('age'), 'Not specified')}. "
        f"Appearance: {as_text(item.get('appearance'))}. "
        f"Clothing: {as_text(item.get('clothing'))}. "
        f"Personality: {as_text(item.get('personality'))}. "
        f"Visual continuity: "
        f"{as_text(item.get('visual_consistency_notes'))}."
    )


def get_scene_continuity(scene, analysis, reference_maps):
    details = []

    for name in as_name_list(scene.get("characters_present")):
        character = reference_maps["characters"].get(name.casefold())
        if character:
            details.append(
                "CHARACTER CONTINUITY: " + describe_character(character)
            )
        else:
            details.append(
                f"CHARACTER: {name}. Preserve the established appearance "
                "from previous scenes."
            )

    location_value = scene.get("location", "")
    if isinstance(location_value, dict):
        location_name = as_text(location_value.get("name"))
    else:
        location_name = as_text(location_value)

    if location_name:
        location = reference_maps["locations"].get(
            location_name.casefold()
        )
        if location:
            details.append(
                "LOCATION CONTINUITY: "
                f"Name: {as_text(location.get('name'), location_name)}. "
                f"Description: {as_text(location.get('description'))}. "
                f"Atmosphere: {as_text(location.get('atmosphere'))}. "
                f"Lighting: {as_text(location.get('lighting'))}. "
                f"Visual notes: "
                f"{as_text(location.get('visual_consistency_notes'))}."
            )
        else:
            details.append(
                f"LOCATION: {location_name}. Keep its appearance "
                "consistent with earlier scenes."
            )

    for name in as_name_list(scene.get("entities_present")):
        entity = reference_maps["entities"].get(name.casefold())
        if entity:
            details.append(
                "ENTITY CONTINUITY: "
                f"Name: {as_text(entity.get('name'), name)}. "
                f"Description: {as_text(entity.get('description'))}. "
                f"Behavior: {as_text(entity.get('behavior'))}. "
                f"Visual notes: "
                f"{as_text(entity.get('visual_consistency_notes'))}."
            )
        else:
            details.append(
                f"ENTITY: {name}. Preserve its previously established "
                "appearance and supernatural characteristics."
            )

    return "\n".join(details)


def build_prompt(category, item, analysis=None, reference_maps=None):
    analysis = analysis if isinstance(analysis, dict) else {}
    item = item if isinstance(item, dict) else {}
    reference_maps = reference_maps or build_reference_maps(analysis)

    if category == "character":
        return f"""
Create a professional CHARACTER REFERENCE IMAGE
for a cinematic Hindi horror film.

CHARACTER NAME: {as_text(item.get('name'), 'Unknown')}
AGE: {as_text(item.get('age'), 'Not specified')}
APPEARANCE: {as_text(item.get('appearance'))}
CLOTHING: {as_text(item.get('clothing'))}
PERSONALITY: {as_text(item.get('personality'))}
ROLE: {as_text(item.get('role'))}
CONTINUITY: {as_text(item.get('visual_consistency_notes'))}

Show one clean character reference composition with
full-body front view, three-quarter view and side profile.
Keep facial identity, body proportions, hairstyle and
clothing consistent in every view.

Visual style: {STYLE}
"""

    if category == "location":
        return f"""
Create a cinematic LOCATION REFERENCE IMAGE.

LOCATION: {as_text(item.get('name'), 'Unknown')}
DESCRIPTION: {as_text(item.get('description'))}
ATMOSPHERE: {as_text(item.get('atmosphere'))}
LIGHTING: {as_text(item.get('lighting'))}
CONTINUITY: {as_text(item.get('visual_consistency_notes'))}

Show a detailed establishing view.
Preserve architecture, geography, weather and lighting.

Visual style: {STYLE}
"""

    if category == "entity":
        return f"""
Create a cinematic SUPERNATURAL ENTITY REFERENCE IMAGE.

ENTITY: {as_text(item.get('name'), 'Unknown')}
DESCRIPTION: {as_text(item.get('description'))}
BEHAVIOR: {as_text(item.get('behavior'))}
CONTINUITY: {as_text(item.get('visual_consistency_notes'))}

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
        characters = ", ".join(as_name_list(item.get("characters_present")))
        location = item.get("location", "")
        if isinstance(location, dict):
            location = location.get("name", "")

        return f"""
Create ONE cinematic widescreen still image
for a professional Hindi horror movie.

STORY TITLE:
{as_text(analysis.get('title'), 'Horror Story')}

SCENE NUMBER:
{as_text(item.get('scene_number'))}

STORY EVENT:
{as_text(item.get('story_event'))}

VISUAL DESCRIPTION:
{as_text(item.get('visual_description'))}

CHARACTERS PRESENT:
{characters}

LOCATION:
{as_text(location)}

CAMERA ANGLE:
{as_text(item.get('camera_angle'))}

LIGHTING:
{as_text(item.get('lighting'))}

ATMOSPHERE:
{as_text(item.get('atmosphere'))}

VISUAL CONTINUITY DETAILS:
{continuity}

ADDITIONAL SCENE NOTES:
{as_text(item.get('visual_consistency_notes'))}

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

    raise ValueError(f"Unknown image category: {category}")


def save_generated_image(image_bytes, output_path):
    """Decode, validate and atomically save a generated image as PNG."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(
        output_path.stem + ".tmp" + output_path.suffix
    )

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.load()

            if image.width < 256 or image.height < 256:
                raise ValueError(
                    f"Image resolution too small: "
                    f"{image.width}x{image.height}"
                )

            if image.width <= 0 or image.height <= 0:
                raise ValueError("Image has invalid dimensions.")

            rgb_image = image.convert("RGB")
            rgb_image.save(temporary_path, format="PNG")

        # Verify the written file can be opened before making it final.
        with Image.open(temporary_path) as check_image:
            check_image.verify()

        temporary_path.replace(output_path)

    except (UnidentifiedImageError, OSError, ValueError) as error:
        if temporary_path.exists():
            temporary_path.unlink()
        raise ValueError(
            f"Returned image could not be decoded or saved: {error}"
        ) from error
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()
        raise

    LOGGER.info("Image saved: %s", output_path)
    return str(output_path)


def get_retry_after_seconds(headers):
    if not headers:
        return None

    value = headers.get("Retry-After")
    if not value:
        return None

    try:
        return max(1, min(int(float(value)), 180))
    except (TypeError, ValueError):
        pass

    try:
        retry_date = email.utils.parsedate_to_datetime(value)
        if retry_date.tzinfo is None:
            retry_date = retry_date.replace(tzinfo=timezone.utc)
        seconds = (retry_date - datetime.now(timezone.utc)).total_seconds()
        return max(1, min(int(seconds), 180))
    except (TypeError, ValueError, OverflowError):
        return None


def generate_image(
    prompt,
    output_path,
    aspect_ratio="16:9",
    max_retries=3,
    retry_delay=15,
):
    api_key = get_api_key()

    try:
        max_retries = max(1, min(int(max_retries), 5))
        retry_delay = max(1, min(int(retry_delay), 120))
    except (TypeError, ValueError):
        max_retries = 3
        retry_delay = 15

    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Image prompt cannot be empty.")

    payload = {
        "model": IMAGE_MODEL,
        "prompt": prompt.strip(),
        "aspect_ratio": str(aspect_ratio or "16:9"),
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
                "Generating %s (attempt %s/%s, model=%s)",
                output_path,
                attempt,
                max_retries,
                IMAGE_MODEL,
            )

            request = urllib.request.Request(
                IMAGE_API_URL,
                data=request_data,
                headers=headers,
                method="POST",
            )

            with urllib.request.urlopen(request, timeout=180) as response:
                raw_body = response.read()

            try:
                result = json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise RuntimeError(
                    f"OpenRouter returned invalid JSON: {error}"
                ) from error

            if not isinstance(result, dict):
                raise RuntimeError("OpenRouter response was not a JSON object.")

            images = result.get("data")
            if not isinstance(images, list) or not images:
                raise RuntimeError(
                    "OpenRouter response did not contain a non-empty data array."
                )

            first_image = images[0]
            if not isinstance(first_image, dict):
                raise RuntimeError("OpenRouter image data was malformed.")

            encoded_image = first_image.get("b64_json")
            if not isinstance(encoded_image, str) or not encoded_image.strip():
                raise RuntimeError(
                    "OpenRouter response did not contain data[0].b64_json."
                )

            try:
                image_bytes = base64.b64decode(
                    encoded_image,
                    validate=True,
                )
            except (ValueError, base64.binascii.Error) as error:
                raise RuntimeError(
                    f"OpenRouter returned invalid base64 image data: {error}"
                ) from error

            if not image_bytes:
                raise RuntimeError("OpenRouter returned an empty image.")

            return save_generated_image(image_bytes, output_path)

        except urllib.error.HTTPError as error:
            error_body = error.read().decode(
                "utf-8",
                errors="replace",
            )
            error_message = error_body[:1000]

            LOGGER.error(
                "OpenRouter HTTP error %s: %s",
                error.code,
                error_message,
            )

            if error.code == 429:
                retry_after = get_retry_after_seconds(error.headers)
                suffix = (
                    f" Retry-After: {retry_after}s."
                    if retry_after else ""
                )
                raise ImageRateLimitError(
                    f"HTTP 429 rate limit reached: {error_message}{suffix}"
                ) from error

            if error.code in NON_RETRYABLE_HTTP_CODES:
                raise RuntimeError(
                    f"OpenRouter rejected the request: "
                    f"HTTP {error.code}. {error_message}"
                ) from error

            if error.code not in RETRYABLE_HTTP_CODES or attempt == max_retries:
                raise RuntimeError(
                    f"OpenRouter image request failed: "
                    f"HTTP {error.code}. {error_message}"
                ) from error

            wait_time = get_retry_after_seconds(error.headers)
            if wait_time is None:
                wait_time = min(retry_delay * attempt, 120)

        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            LOGGER.warning(
                "Temporary image API connection error (attempt %s/%s): %s",
                attempt,
                max_retries,
                error,
            )
            if attempt == max_retries:
                raise RuntimeError(
                    f"Image API connection failed after {max_retries} attempts: "
                    f"{error}"
                ) from error
            wait_time = min(retry_delay * attempt, 120)

        except (RuntimeError, ValueError) as error:
            # Invalid requests/data are not likely to improve by retrying.
            # Only malformed/empty provider responses are retried once.
            message = str(error).lower()
            retryable_response_error = (
                "invalid json" in message
                or "did not contain" in message
                or "malformed" in message
            )
            if not retryable_response_error or attempt == max_retries:
                raise
            wait_time = min(retry_delay * attempt, 120)

        LOGGER.info("Retrying image request after %s seconds.", wait_time)
        time.sleep(wait_time)

    raise RuntimeError(f"Image generation failed: {output_path}")


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

    max_retries = positive_int_setting(
        config, "generation", "max_retries", 3, 1, 5
    )
    retry_delay = positive_int_setting(
        config, "generation", "retry_delay_seconds", 15, 1, 120
    )
    aspect_ratio = get_setting(config, "images", "aspect_ratio", "16:9")
    reference_limit = positive_int_setting(
        config,
        "generation",
        "max_reference_images_per_run",
        DEFAULT_MAX_REFERENCE_IMAGES_PER_RUN,
        1,
        20,
    )

    attempted = 0
    rate_limited = False

    for collection_name, folder, category in collections:
        items = analysis.get(collection_name, [])
        if not isinstance(items, list):
            continue

        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue

            name = as_text(item.get("name")) or f"{category}_{index}"
            if attempted >= reference_limit or rate_limited:
                failures.append({
                    "category": category,
                    "name": name,
                    "error": "Reference image skipped due to per-run limit "
                             "or API rate limit.",
                })
                references[collection_name][name] = {
                    "path": None,
                    "status": "skipped",
                }
                continue

            filename = f"{index:03d}_{safe_filename(name)}.png"
            output_path = folder / filename
            attempted += 1

            try:
                path = generate_image(
                    prompt=build_prompt(category, item, analysis),
                    output_path=output_path,
                    aspect_ratio=aspect_ratio,
                    max_retries=max_retries,
                    retry_delay=retry_delay,
                )
                references[collection_name][name] = {
                    "path": path,
                    "status": "success",
                }

            except ImageRateLimitError as error:
                rate_limited = True
                LOGGER.warning(
                    "Reference generation stopped by API rate limit: %s",
                    error,
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

            except Exception as error:
                LOGGER.exception("Reference generation failed: %s", name)
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
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("No scenes are available for image generation.")

    try:
        image_count = int(image_count)
    except (TypeError, ValueError):
        raise ValueError("video.image_count must be a valid integer.")

    if image_count <= 0:
        raise ValueError("video.image_count must be positive.")

    if len(scenes) <= image_count:
        return scenes

    if image_count == 1:
        return [scenes[0]]

    indexes = [
        round(i * (len(scenes) - 1) / (image_count - 1))
        for i in range(image_count)
    ]

    # Keep indices unique in case rounding produces duplicates.
    unique_indexes = list(dict.fromkeys(indexes))
    return [scenes[index] for index in unique_indexes]


def generate_scene_images(analysis, config):
    scenes = analysis["scenes"]

    configured_image_count = int(
        get_setting(config, "video", "image_count", 96)
    )
    per_run_limit = positive_int_setting(
        config,
        "generation",
        "max_scene_images_per_run",
        DEFAULT_MAX_SCENE_IMAGES_PER_RUN,
        1,
        200,
    )

    effective_image_count = min(configured_image_count, per_run_limit)
    selected_scenes = select_scene_images(scenes, effective_image_count)

    if configured_image_count > per_run_limit:
        LOGGER.warning(
            "Scene image request capped from %s to %s this run. "
            "Change generation.max_scene_images_per_run to override.",
            configured_image_count,
            per_run_limit,
        )

    max_retries = positive_int_setting(
        config, "generation", "max_retries", 3, 1, 5
    )
    retry_delay = positive_int_setting(
        config, "generation", "retry_delay_seconds", 15, 1, 120
    )
    aspect_ratio = get_setting(config, "images", "aspect_ratio", "16:9")
    reference_maps = build_reference_maps(analysis)

    generated_scenes = []
    stop_after_rate_limit = False

    for index, scene in enumerate(selected_scenes, start=1):
        scene_number = scene.get("scene_number", index)
        filename = (
            f"scene_{index:03d}_story_{safe_filename(scene_number)}.png"
        )
        output_path = SCENE_DIR / filename

        if stop_after_rate_limit:
            generated_scenes.append({
                "image_index": index,
                "scene_number": scene_number,
                "story_event": as_text(scene.get("story_event")),
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
                "story_event": as_text(scene.get("story_event")),
                "image_path": image_path,
                "status": "success",
            })

        except ImageRateLimitError as error:
            LOGGER.error(
                "Image API rate limit reached. Remaining scenes will be skipped."
            )
            generated_scenes.append({
                "image_index": index,
                "scene_number": scene_number,
                "story_event": as_text(scene.get("story_event")),
                "image_path": None,
                "status": "failed",
                "error": str(error),
            })
            stop_after_rate_limit = True

        except Exception as error:
            LOGGER.exception("Scene image failed: %s", scene_number)
            generated_scenes.append({
                "image_index": index,
                "scene_number": scene_number,
                "story_event": as_text(scene.get("story_event")),
                "image_path": None,
                "status": "failed",
                "error": str(error),
            })

    return generated_scenes


def main():
    setup_logging()

    LOGGER.info("Starting OpenRouter image generation.")
    LOGGER.info("Image model: %s", IMAGE_MODEL)

    create_project_directories()

    config = load_config()
    analysis = load_analysis()

    # Check the API key before deleting any previous generated assets.
    get_api_key()

    aspect_ratio = get_setting(config, "images", "aspect_ratio", "16:9")
    if aspect_ratio not in {
        "1:1", "2:1", "1:2", "3:2", "2:3",
        "4:3", "3:4", "5:4", "4:5", "16:9", "9:16", "auto",
    }:
        raise ValueError(
            f"Unsupported image aspect ratio in config: {aspect_ratio}"
        )

    configured_image_count = int(
        get_setting(config, "video", "image_count", 96)
    )
    if configured_image_count <= 0:
        raise ValueError("video.image_count must be positive.")

    clean_generated_folders()

    LOGGER.info(
        "Story: %s | Valid scenes: %s",
        as_text(analysis.get("title"), "Untitled"),
        len(analysis["scenes"]),
    )

    # Scene images are the required assets for video editing.
    scene_results = generate_scene_images(analysis, config)

    successful = sum(
        1 for item in scene_results if item["status"] == "success"
    )
    failed = sum(
        1 for item in scene_results if item["status"] == "failed"
    )
    skipped = sum(
        1 for item in scene_results if item["status"] == "skipped"
    )

    # Reference sheets are optional and are generated after scene images,
    # so they cannot consume the scene-image request budget first.
    references = {
        "characters": {},
        "locations": {},
        "entities": {},
    }
    reference_failures = []

    reference_sheets_enabled = get_setting(
        config, "images", "reference_sheets", True
    )
    if reference_sheets_enabled:
        references, reference_failures = generate_references(
            analysis,
            config,
        )
    else:
        LOGGER.info("Reference-sheet generation disabled in config.")

    report = {
        "story_title": as_text(analysis.get("title"), "Untitled"),
        "image_model": IMAGE_MODEL,
        "provider": "OpenRouter",
        "configured_max_scene_images": configured_image_count,
        "max_scene_images_per_run": positive_int_setting(
            config,
            "generation",
            "max_scene_images_per_run",
            DEFAULT_MAX_SCENE_IMAGES_PER_RUN,
            1,
            200,
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

    save_json(report, REPORT_PATH)

    LOGGER.info(
        "Scene image results: %s successful, %s failed, %s skipped.",
        successful,
        failed,
        skipped,
    )
    LOGGER.info("Reference image failures: %s", len(reference_failures))
    LOGGER.info("Image report saved: %s", REPORT_PATH)

    if failed > 0 or skipped > 0 or successful == 0:
        raise RuntimeError(
            "Scene image generation incomplete: "
            f"{successful} successful, {failed} failed, {skipped} skipped."
        )

    LOGGER.info("All selected scene images generated successfully.")


if __name__ == "__main__":
    main()
