
"""
Cinematic 2D Documentary Image Generator
Hybrid 2D Cut-Out / Puppet Animation preparation.

Features:
- OpenRouter image generation (Recraft free model by default).
- Non-destructive asset generation.
- Character, location, entity and character-pose references.
- Cinematic 2D scene illustrations.
- RGBA preservation when the provider returns transparency.
- Retry handling with Retry-After support.
- Immediate stop on HTTP 429.
- Atomic image saving and validation.
- Pipeline-compatible scene_images report.
- Animation-ready scene metadata.

Important:
A generated flat scene image is NOT a fully rigged puppet.
Real limb animation requires separate body-part assets or multiple poses.
This module prepares and stores those assets where possible.
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


# ============================================================
# CONFIGURATION
# ============================================================

DEFAULT_IMAGE_MODEL = "recraft/recraft-v4.1-flash-v2:free"

OPENROUTER_IMAGE_MODEL = os.getenv(
    "OPENROUTER_IMAGE_MODEL",
    DEFAULT_IMAGE_MODEL
)

IMAGE_API_URL = "https://openrouter.ai/api/v1/images"

BASE_DIR = Path(__file__).resolve().parent.parent

ANALYSIS_FILE = OUTPUT_DIR / "story_analysis.json"

ASSET_DIR = OUTPUT_DIR / "assets"

CHARACTER_DIR = ASSET_DIR / "characters"
LOCATION_DIR = ASSET_DIR / "locations"
ENTITY_DIR = ASSET_DIR / "entities"
SCENE_ASSET_DIR = ASSET_DIR / "scenes"

IMAGE_DIR = OUTPUT_DIR / "images"

REPORT_FILE = OUTPUT_DIR / "image_generation_report.json"

STAGING_DIR = OUTPUT_DIR / "image_staging"

RETRYABLE_HTTP_CODES = {
    408, 425, 500, 502, 503, 504, 529
}

NON_RETRYABLE_HTTP_CODES = {
    400, 401, 402, 403, 404, 405, 413, 415, 422
}

MIN_IMAGE_SIZE = 256

MAX_IMAGE_DIMENSION = 8192

DEFAULT_TIMEOUT = 120

DEFAULT_REFERENCE_LIMIT = 5

DEFAULT_POSE_LIMIT = 2

DEFAULT_SCENE_LIMIT = 35


# ============================================================
# LOGGING
# ============================================================

LOG_DIR = OUTPUT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("image_generator")
logger.setLevel(logging.INFO)

if not logger.handlers:

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s"
    )

    file_handler = logging.FileHandler(
        LOG_DIR / "image_generator.log",
        encoding="utf-8"
    )

    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)


# ============================================================
# EXCEPTIONS
# ============================================================

class ImageRateLimitError(Exception):
    """Raised when OpenRouter returns HTTP 429."""


class ImageGenerationError(Exception):
    """Raised when image generation fails."""


# ============================================================
# BASIC HELPERS
# ============================================================

def safe_filename(value, max_length=70):

    value = str(value or "").strip().lower()

    value = re.sub(
        r"[^a-z0-9]+",
        "_",
        value
    )

    value = value.strip("_")

    if not value:
        value = "asset"

    return value[:max_length]


def get_setting(config, section, key, default=None):

    section_data = config.get(section, {})

    if not isinstance(section_data, dict):
        return default

    return section_data.get(key, default)


def as_text(value):

    if value is None:
        return ""

    if isinstance(value, str):
        return value.strip()

    if isinstance(value, (int, float)):
        return str(value)

    return str(value).strip()


def as_name_list(value):

    if not isinstance(value, list):
        return []

    result = []

    for item in value:

        if isinstance(item, dict):

            name = (
                item.get("name")
                or item.get("character_name")
                or item.get("location_name")
                or item.get("entity_name")
            )

        else:
            name = item

        name = as_text(name)

        if name and name not in result:
            result.append(name)

    return result


def positive_int_setting(
    config,
    section,
    key,
    default,
    minimum=1,
    maximum=100
):

    value = get_setting(
        config,
        section,
        key,
        default
    )

    try:
        value = int(value)

    except (TypeError, ValueError):
        value = default

    return max(
        minimum,
        min(value, maximum)
    )


def ensure_directories():

    directories = [
        OUTPUT_DIR,
        ASSET_DIR,
        CHARACTER_DIR,
        LOCATION_DIR,
        ENTITY_DIR,
        SCENE_ASSET_DIR,
        IMAGE_DIR,
        STAGING_DIR,
        LOG_DIR,
    ]

    for directory in directories:
        directory.mkdir(
            parents=True,
            exist_ok=True
        )

    # Preserve nested folders created by utils.py.

    for name in [
        "poses",
        "body_parts",
    ]:

        (CHARACTER_DIR / name).mkdir(
            parents=True,
            exist_ok=True
        )

    for name in [
        "backgrounds",
        "midgrounds",
        "foregrounds",
    ]:

        (SCENE_ASSET_DIR / name).mkdir(
            parents=True,
            exist_ok=True
        )


# ============================================================
# STORY ANALYSIS
# ============================================================

def load_analysis():

    if not ANALYSIS_FILE.exists():

        raise FileNotFoundError(
            f"Story analysis not found: {ANALYSIS_FILE}"
        )

    with open(
        ANALYSIS_FILE,
        "r",
        encoding="utf-8"
    ) as file:

        analysis = json.load(file)

    if not isinstance(analysis, dict):

        raise ValueError(
            "story_analysis.json must contain a JSON object."
        )

    scenes = analysis.get("scenes")

    if not isinstance(scenes, list) or not scenes:

        raise ValueError(
            "No valid scenes found in story_analysis.json."
        )

    return analysis


def get_api_key():

    api_key = os.getenv("OPENROUTER_API_KEY")

    if not api_key or len(api_key.strip()) < 10:

        raise RuntimeError(
            "OPENROUTER_API_KEY is missing or invalid."
        )

    return api_key.strip()


# ============================================================
# CONTINUITY HELPERS
# ============================================================

def build_reference_maps(analysis):

    characters = {}
    locations = {}
    entities = {}

    for item in analysis.get("characters", []):

        if isinstance(item, dict):

            name = as_text(item.get("name"))

            if name:
                characters[name] = item

    for item in analysis.get("locations", []):

        if isinstance(item, dict):

            name = as_text(item.get("name"))

            if name:
                locations[name] = item

    for item in analysis.get("entities", []):

        if isinstance(item, dict):

            name = as_text(item.get("name"))

            if name:
                entities[name] = item

    return characters, locations, entities


def describe_character(name, characters):

    data = characters.get(name, {})

    if not isinstance(data, dict):
        return ""

    fields = [
        "description",
        "appearance",
        "visual_description",
        "clothing",
        "personality",
        "age",
    ]

    details = []

    for field in fields:

        value = as_text(data.get(field))

        if value:
            details.append(value)

    return ", ".join(details)


def get_scene_continuity(scene, characters, locations):

    character_names = as_name_list(
        scene.get("characters", [])
    )

    location_name = as_text(
        scene.get("location")
        or scene.get("location_name")
    )

    character_details = []

    for name in character_names:

        description = describe_character(
            name,
            characters
        )

        if description:

            character_details.append(
                f"{name}: {description}"
            )

    location_details = ""

    if location_name:

        location_data = locations.get(
            location_name,
            {}
        )

        if isinstance(location_data, dict):

            location_details = (
                as_text(location_data.get("description"))
                or as_text(location_data.get("visual_description"))
            )

    return (
        character_names,
        location_name,
        character_details,
        location_details
    )


# ============================================================
# VISUAL STYLE
# ============================================================

VISUAL_STYLE = """
Premium cinematic 2D illustrated documentary animation.

Visual identity:
- Deep dark navy #071426.
- Steel blue #162C45.
- Crimson red #8F1728.
- Warm beige #D5BDA0.
- Professional graphic novel illustration.
- Semi-realistic hand-painted 2D artwork.
- Detailed ink outlines.
- Cinematic lighting.
- Rich environmental detail.
- Natural human anatomy.
- Realistic body proportions.
- Strong foreground and background separation.
- Controlled dramatic shadows.
- Atmospheric depth.
- Documentary storytelling composition.

Avoid:
- 3D CGI.
- Plastic 3D characters.
- Toy-like characters.
- Chibi style.
- Cute cartoon style.
- Photorealistic photography.
- Watermarks.
- Logos.
- Random text.
- Unwanted typography.
- Distorted hands.
- Extra fingers.
- Deformed faces.
"""


NEGATIVE_STYLE = """
No 3D rendering, no photorealistic image,
no glossy plastic skin, no toy appearance,
no anime exaggeration, no chibi,
no distorted anatomy, no extra limbs,
no random letters, no watermark,
no logo, no blurry faces.
"""


# ============================================================
# PROMPT GENERATION
# ============================================================

def build_character_prompt(name, data):

    description = describe_character(
        name,
        {name: data}
    )

    if not description:
        description = "A cinematic documentary character."

    return f"""
Create a professional character reference sheet
for a cinematic 2D animated documentary.

CHARACTER NAME:
{name}

CHARACTER DESCRIPTION:
{description}

Required views:
1. Full body front view.
2. Full body three-quarter view.
3. Full body side view.

All three views must depict exactly the same person.

Keep consistent:
- Face.
- Hairstyle.
- Skin tone.
- Body proportions.
- Clothing.
- Accessories.
- Age.
- Character identity.

Neutral standing pose.
Full body visible.
Arms slightly separated from torso.
Clear silhouette.
Plain neutral background.
Professional character design sheet.

{VISUAL_STYLE}

{NEGATIVE_STYLE}
"""


def build_pose_prompt(name, data, pose):

    description = describe_character(
        name,
        {name: data}
    )

    pose_descriptions = {

        "standing":
            "Neutral full-body standing pose.",

        "walking":
            "Natural walking pose, one leg forward.",

        "running":
            "Dynamic running pose with natural body balance.",

        "looking_back":
            "Standing pose with head turned backward.",

        "sitting":
            "Natural seated pose.",

        "gesture":
            "Natural expressive hand gesture.",
    }

    action = pose_descriptions.get(
        pose,
        "Natural full-body documentary pose."
    )

    return f"""
Create a separate full-body 2D character pose asset.

Character:
{name}

Character identity:
{description}

Pose:
{action}

Important:
The character must remain visually identical
to the supplied character description.

Full body visible.
Clear silhouette.
Arms and legs clearly separated where possible.
Neutral simple background.
No extra characters.
No captions.
No text.

{VISUAL_STYLE}

{NEGATIVE_STYLE}
"""


def build_location_prompt(name, data):

    description = (
        as_text(data.get("description"))
        or as_text(data.get("visual_description"))
        or name
    )

    return f"""
Create a cinematic 2D illustrated environment
reference image for a documentary animation.

LOCATION:
{name}

ENVIRONMENT:
{description}

Composition:
Wide establishing view.
Detailed architecture.
Strong depth separation.
Clear foreground.
Detailed midground.
Atmospheric background.
Cinematic lighting.
No people unless essential to the location.

{VISUAL_STYLE}

{NEGATIVE_STYLE}
"""


def build_entity_prompt(name, data):

    description = (
        as_text(data.get("description"))
        or as_text(data.get("visual_description"))
        or name
    )

    return f"""
Create a detailed 2D illustrated prop reference
for a cinematic documentary animation.

OBJECT:
{name}

DESCRIPTION:
{description}

Show the object clearly.
Centered composition.
Readable silhouette.
Detailed material and surface.
Simple neutral background.
No unrelated objects.
No text.

{VISUAL_STYLE}

{NEGATIVE_STYLE}
"""


def build_scene_prompt(
    scene,
    characters,
    locations,
    entities
):

    scene_number = scene.get(
        "scene_number",
        scene.get("number", "unknown")
    )

    description = (
        as_text(scene.get("visual_description"))
        or as_text(scene.get("description"))
        or as_text(scene.get("story_event"))
    )

    narration = as_text(
        scene.get("narration")
        or scene.get("voiceover")
    )

    character_names = as_name_list(
        scene.get("characters", [])
    )

    location_name = as_text(
        scene.get("location")
        or scene.get("location_name")
    )

    (
        character_names,
        location_name,
        character_details,
        location_details
    ) = get_scene_continuity(
        scene,
        characters,
        locations
    )

    entity_names = as_name_list(
        scene.get("entities", [])
    )

    entity_details = []

    for name in entity_names:

        data = entities.get(name, {})

        if isinstance(data, dict):

            detail = (
                as_text(data.get("description"))
                or as_text(data.get("visual_description"))
            )

            if detail:
                entity_details.append(
                    f"{name}: {detail}"
                )

    return f"""
Create one cinematic 2D illustrated documentary scene.

SCENE NUMBER:
{scene_number}

STORY EVENT:
{description}

NARRATION CONTEXT:
{narration[:1200]}

CHARACTERS:
{", ".join(character_names) or "No specific character"}

CHARACTER VISUAL DETAILS:
{"; ".join(character_details)}

LOCATION:
{location_name}

LOCATION DETAILS:
{location_details}

IMPORTANT OBJECTS:
{"; ".join(entity_details)}

VISUAL DIRECTION:

Create a carefully composed cinematic frame.
The scene must visually communicate the story event.
Use expressive but natural character poses.
Maintain consistent character identity.
Maintain consistent clothing and environment.
Use dramatic documentary lighting.
Create clear foreground, midground and background depth.

Composition must be suitable for later 2D cut-out animation
and layered parallax movement.

Keep important subjects away from extreme frame edges.
Use a widescreen 16:9 composition.
No captions.
No typography.
No watermarks.

{VISUAL_STYLE}

{NEGATIVE_STYLE}
"""


# ============================================================
# ANIMATION METADATA
# ============================================================

def build_animation_metadata(scene):

    description = (
        as_text(scene.get("visual_description"))
        or as_text(scene.get("description"))
        or as_text(scene.get("story_event"))
    )

    text = description.lower()

    camera_motion = "slow_push_in"

    if any(
        word in text
        for word in [
            "walking",
            "running",
            "chasing",
            "moving",
            "traveling",
        ]
    ):

        camera_motion = "tracking"

    elif any(
        word in text
        for word in [
            "city",
            "building",
            "landscape",
            "street",
            "location",
        ]
    ):

        camera_motion = "slow_pan"

    elif any(
        word in text
        for word in [
            "reveal",
            "secret",
            "discovery",
            "evidence",
        ]
    ):

        camera_motion = "dramatic_reveal"

    return {

        "animation_engine": "hybrid_2d_cutout",

        "scene_type": "cinematic_2d_documentary",

        "camera_motion": camera_motion,

        "layers_required": [
            "background",
            "midground",
            "character",
            "foreground",
            "atmosphere",
            "overlay",
        ],

        "effects": [
            "subtle_parallax",
            "film_grain",
            "cinematic_vignette",
        ],

        "character_animation": {
            "idle_breathing": True,
            "head_movement": True,
            "eye_blink": True,
            "arm_gestures": False,
            "body_movement": False,
            "requires_separate_pose_assets": True,
        },

        "source_image_is_flat": True,

        "full_rig_available": False,
    }


# ============================================================
# RETRY HANDLING
# ============================================================

def get_retry_after_seconds(headers):

    if not headers:
        return None

    value = headers.get("Retry-After")

    if not value:
        return None

    try:

        return max(
            0,
            float(value)
        )

    except (TypeError, ValueError):
        pass

    try:

        retry_date = email.utils.parsedate_to_datetime(
            value
        )

        if retry_date.tzinfo is None:

            retry_date = retry_date.replace(
                tzinfo=timezone.utc
            )

        return max(
            0,
            (
                retry_date
                - datetime.now(timezone.utc)
            ).total_seconds()
        )

    except Exception:
        return None


def get_retry_delay(attempt):

    return min(
        5 * (2 ** attempt),
        60
    )


# ============================================================
# IMAGE VALIDATION AND SAVING
# ============================================================

def validate_image_bytes(image_bytes):

    if not image_bytes:
        raise ImageGenerationError(
            "Empty image data received."
        )

    try:

        image = Image.open(
            io.BytesIO(image_bytes)
        )

        image.verify()

        image = Image.open(
            io.BytesIO(image_bytes)
        )

        width, height = image.size

        if width < MIN_IMAGE_SIZE:
            raise ImageGenerationError(
                f"Image width too small: {width}"
            )

        if height < MIN_IMAGE_SIZE:
            raise ImageGenerationError(
                f"Image height too small: {height}"
            )

        if width > MAX_IMAGE_DIMENSION:
            raise ImageGenerationError(
                f"Image width too large: {width}"
            )

        if height > MAX_IMAGE_DIMENSION:
            raise ImageGenerationError(
                f"Image height too large: {height}"
            )

        return image

    except (
        UnidentifiedImageError,
        OSError
    ) as error:

        raise ImageGenerationError(
            f"Invalid image data: {error}"
        ) from error


def save_generated_image(image_bytes, destination):

    destination = Path(destination)

    destination.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    image = validate_image_bytes(
        image_bytes
    )

    # Preserve transparency if the provider returns it.
    if image.mode in ("RGBA", "LA"):
        output_image = image.convert("RGBA")

    elif image.mode == "P" and "transparency" in image.info:
        output_image = image.convert("RGBA")

    else:
        output_image = image.convert("RGB")

    temporary_file = destination.with_suffix(
        destination.suffix + ".tmp"
    )

    try:

        output_image.save(
            temporary_file,
            format="PNG",
            optimize=True
        )

        with Image.open(temporary_file) as check:

            check.verify()

        os.replace(
            temporary_file,
            destination
        )

    finally:

        if temporary_file.exists():
            temporary_file.unlink()

    return str(destination)


# ============================================================
# OPENROUTER IMAGE GENERATION
# ============================================================

def generate_image(
    prompt,
    destination,
    api_key,
    config
):

    max_retries = positive_int_setting(
        config,
        "generation",
        "max_retries",
        3,
        1,
        5
    )

    retry_delay = positive_int_setting(
        config,
        "generation",
        "retry_delay_seconds",
        5,
        1,
        60
    )

    timeout = positive_int_setting(
        config,
        "generation",
        "image_timeout_seconds",
        DEFAULT_TIMEOUT,
        30,
        300
    )

    image_settings = config.get(
        "images",
        {}
    )

    aspect_ratio = (
        image_settings.get("aspect_ratio")
        or "16:9"
    )

    payload = {

        "model": OPENROUTER_IMAGE_MODEL,

        "prompt": prompt,

        "n": 1,

        "aspect_ratio": aspect_ratio,
    }

    request_data = json.dumps(
        payload
    ).encode("utf-8")

    headers = {

        "Authorization": f"Bearer {api_key}",

        "Content-Type": "application/json",

        "HTTP-Referer": "https://github.com/",

        "X-Title": "Cinematic 2D Horror Automation",
    }

    request = urllib.request.Request(
        IMAGE_API_URL,
        data=request_data,
        headers=headers,
        method="POST"
    )

    for attempt in range(max_retries):

        try:

            logger.info(
                "Generating image using %s",
                OPENROUTER_IMAGE_MODEL
            )

            with urllib.request.urlopen(
                request,
                timeout=timeout
            ) as response:

                response_body = response.read()

            result = json.loads(
                response_body.decode("utf-8")
            )

            data = result.get("data", [])

            if not data:

                raise ImageGenerationError(
                    "Provider returned no image data."
                )

            image_item = data[0]

            encoded_image = image_item.get(
                "b64_json"
            )

            if not encoded_image:

                raise ImageGenerationError(
                    "Provider response does not contain b64_json."
                )

            image_bytes = base64.b64decode(
                encoded_image,
                validate=True
            )

            return save_generated_image(
                image_bytes,
                destination
            )

        except urllib.error.HTTPError as error:

            status = error.code

            response_text = ""

            try:
                response_text = error.read().decode(
                    "utf-8",
                    errors="replace"
                )

            except Exception:
                pass

            logger.error(
                "OpenRouter HTTP %s: %s",
                status,
                response_text[:1000]
            )

            if status == 429:

                retry_after = get_retry_after_seconds(
                    error.headers
                )

                raise ImageRateLimitError(
                    "OpenRouter rate limit reached. "
                    f"Retry-After: {retry_after}"
                ) from error

            if status in NON_RETRYABLE_HTTP_CODES:
                raise ImageGenerationError(
                    f"Non-retryable HTTP error: {status}"
                ) from error

            if status not in RETRYABLE_HTTP_CODES:
                raise ImageGenerationError(
                    f"Unexpected HTTP error: {status}"
                ) from error

            if attempt == max_retries - 1:
                raise ImageGenerationError(
                    f"Image generation failed after retries: HTTP {status}"
                ) from error

            delay = get_retry_after_seconds(
                error.headers
            )

            if delay is None:
                delay = max(
                    retry_delay,
                    get_retry_delay(attempt)
                )

            logger.warning(
                "Retrying after HTTP %s in %.1f seconds",
                status,
                delay
            )

            time.sleep(delay)

        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError
        ) as error:

            if attempt == max_retries - 1:

                raise ImageGenerationError(
                    f"Network failure: {error}"
                ) from error

            delay = max(
                retry_delay,
                get_retry_delay(attempt)
            )

            logger.warning(
                "Network error. Retry in %s seconds: %s",
                delay,
                error
            )

            time.sleep(delay)

        except (
            json.JSONDecodeError,
            ValueError,
            ImageGenerationError
        ) as error:

            if isinstance(error, ImageGenerationError):
                raise

            if attempt == max_retries - 1:
                raise ImageGenerationError(
                    f"Invalid provider response: {error}"
                ) from error

            time.sleep(
                get_retry_delay(attempt)
            )

    raise ImageGenerationError(
        "Image generation exhausted all attempts."
    )


# ============================================================
# ASSET GENERATION
# ============================================================

def generate_asset(
    prompt,
    destination,
    api_key,
    config,
    asset_type,
    asset_name
):

    logger.info(
        "Generating %s asset: %s",
        asset_type,
        asset_name
    )

    try:

        image_path = generate_image(
            prompt,
            destination,
            api_key,
            config
        )

        return {

            "name": asset_name,

            "asset_type": asset_type,

            "image_path": image_path,

            "status": "success",
        }

    except ImageRateLimitError:
        raise

    except Exception as error:

        logger.exception(
            "Asset generation failed: %s",
            asset_name
        )

        return {

            "name": asset_name,

            "asset_type": asset_type,

            "image_path": None,

            "status": "failed",

            "error": str(error),
        }


def generate_references(
    analysis,
    api_key,
    config,
    report
):

    characters, locations, entities = build_reference_maps(
        analysis
    )

    reference_limit = positive_int_setting(
        config,
        "generation",
        "max_reference_images_per_run",
        DEFAULT_REFERENCE_LIMIT,
        0,
        50
    )

    pose_limit = positive_int_setting(
        config,
        "generation",
        "max_character_poses_per_run",
        DEFAULT_POSE_LIMIT,
        0,
        20
    )

    generated_count = 0

    # --------------------------------------------------------
    # CHARACTER REFERENCES
    # --------------------------------------------------------

    for name, data in characters.items():

        if generated_count >= reference_limit:
            break

        destination = (
            CHARACTER_DIR
            / f"{safe_filename(name)}_reference.png"
        )

        result = generate_asset(
            build_character_prompt(name, data),
            destination,
            api_key,
            config,
            "character_reference",
            name
        )

        report["reference_assets"].append(
            result
        )

        if result["status"] == "success":
            generated_count += 1

        if result["status"] == "failed":
            continue

    # --------------------------------------------------------
    # CHARACTER POSES
    # --------------------------------------------------------

    pose_names = [
        "standing",
        "walking",
        "looking_back",
        "running",
        "sitting",
        "gesture",
    ]

    generated_poses = 0

    for name, data in characters.items():

        for pose in pose_names:

            if generated_poses >= pose_limit:
                break

            destination = (
                CHARACTER_DIR
                / "poses"
                / f"{safe_filename(name)}_{pose}.png"
            )

            result = generate_asset(
                build_pose_prompt(
                    name,
                    data,
                    pose
                ),
                destination,
                api_key,
                config,
                "character_pose",
                f"{name}_{pose}"
            )

            report["pose_assets"].append(
                result
            )

            if result["status"] == "success":
                generated_poses += 1

        if generated_poses >= pose_limit:
            break

    # --------------------------------------------------------
    # LOCATION REFERENCES
    # --------------------------------------------------------

    for name, data in locations.items():

        if generated_count >= reference_limit:
            break

        destination = (
            LOCATION_DIR
            / f"{safe_filename(name)}_reference.png"
        )

        result = generate_asset(
            build_location_prompt(name, data),
            destination,
            api_key,
            config,
            "location_reference",
            name
        )

        report["reference_assets"].append(
            result
        )

        if result["status"] == "success":
            generated_count += 1

    # --------------------------------------------------------
    # ENTITY REFERENCES
    # --------------------------------------------------------

    for name, data in entities.items():

        if generated_count >= reference_limit:
            break

        destination = (
            ENTITY_DIR
            / f"{safe_filename(name)}_reference.png"
        )

        result = generate_asset(
            build_entity_prompt(name, data),
            destination,
            api_key,
            config,
            "entity_reference",
            name
        )

        report["reference_assets"].append(
            result
        )

        if result["status"] == "success":
            generated_count += 1


# ============================================================
# SCENE SELECTION
# ============================================================

def select_scene_images(scenes, image_count):

    if image_count >= len(scenes):
        return list(enumerate(scenes))

    if image_count <= 0:
        return []

    if image_count == 1:
        return [(0, scenes[0])]

    selected_indexes = []

    for index in range(image_count):

        position = round(
            index * (len(scenes) - 1)
            / (image_count - 1)
        )

        if position not in selected_indexes:
            selected_indexes.append(position)

    return [
        (index, scenes[index])
        for index in selected_indexes
    ]


# ============================================================
# SCENE IMAGE GENERATION
# ============================================================

def generate_scene_images(
    analysis,
    api_key,
    config,
    report
):

    scenes = analysis.get(
        "scenes",
        []
    )

    characters, locations, entities = build_reference_maps(
        analysis
    )

    configured_image_count = positive_int_setting(
        config,
        "video",
        "image_count",
        len(scenes),
        1,
        500
    )

    max_scene_images = positive_int_setting(
        config,
        "generation",
        "max_scene_images_per_run",
        DEFAULT_SCENE_LIMIT,
        1,
        500
    )

    target_count = min(
        configured_image_count,
        max_scene_images,
        len(scenes)
    )

    selected_scenes = select_scene_images(
        scenes,
        target_count
    )

    logger.info(
        "Generating %s scene images out of %s scenes.",
        len(selected_scenes),
        len(scenes)
    )

    for image_index, (scene_index, scene) in enumerate(
        selected_scenes,
        start=1
    ):

        scene_number = scene.get(
            "scene_number",
            scene.get("number", scene_index + 1)
        )

        story_event = (
            as_text(scene.get("story_event"))
            or as_text(scene.get("description"))
            or f"Scene {scene_number}"
        )

        filename = (
            f"scene_{int(scene_index + 1):04d}.png"
        )

        destination = IMAGE_DIR / filename

        prompt = build_scene_prompt(
            scene,
            characters,
            locations,
            entities
        )

        try:

            image_path = generate_image(
                prompt,
                destination,
                api_key,
                config
            )

            scene_result = {

                "image_index": image_index,

                "scene_number": scene_number,

                "story_event": story_event,

                "image_path": image_path,

                "status": "success",

                "animation_plan": build_animation_metadata(
                    scene
                ),
            }

            logger.info(
                "Scene %s generated successfully.",
                scene_number
            )

        except ImageRateLimitError:
            raise

        except Exception as error:

            logger.exception(
                "Scene %s generation failed.",
                scene_number
            )

            scene_result = {

                "image_index": image_index,

                "scene_number": scene_number,

                "story_event": story_event,

                "image_path": None,

                "status": "failed",

                "error": str(error),

                "animation_plan": build_animation_metadata(
                    scene
                ),
            }

        report["scene_images"].append(
            scene_result
        )


# ============================================================
# REPORT
# ============================================================

def save_report(report):

    report["updated_at"] = datetime.now(
        timezone.utc
    ).isoformat()

    save_json(
        report,
        REPORT_FILE
    )

    logger.info(
        "Image generation report saved: %s",
        REPORT_FILE
    )


def create_initial_report():

    return {

        "status": "running",

        "model": OPENROUTER_IMAGE_MODEL,

        "scene_images": [],

        "reference_assets": [],

        "pose_assets": [],

        "animation_engine": "hybrid_2d_cutout",

        "notes": [
            "Scene images are flat illustrated frames.",
            "Character pose assets are separate generated images.",
            "Full skeletal puppet rigging is not included.",
            "Transparent output depends on provider support.",
            "Reference images are not automatically used as image-conditioning inputs.",
        ],

        "created_at": datetime.now(
            timezone.utc
        ).isoformat(),
    }


# ============================================================
# MAIN
# ============================================================

def main():

    logger.info(
        "Starting cinematic 2D image generation."
    )

    ensure_directories()

    config = load_config()

    analysis = load_analysis()

    api_key = get_api_key()

    report = create_initial_report()

    save_report(report)

    try:

        # Generate scene images first.
        # This prioritizes the main video output.

        generate_scene_images(
            analysis,
            api_key,
            config,
            report
        )

        # Optional references and poses.

        images_config = config.get(
            "images",
            {}
        )

        generate_reference_sheets = (
            images_config.get(
                "reference_sheets",
                True
            )
        )

        if generate_reference_sheets:

            generate_references(
                analysis,
                api_key,
                config,
                report
            )

        successful_scenes = sum(
            1
            for item in report["scene_images"]
            if item.get("status") == "success"
        )

        failed_scenes = sum(
            1
            for item in report["scene_images"]
            if item.get("status") == "failed"
        )

        if successful_scenes == 0:

            raise ImageGenerationError(
                "No scene images were generated successfully."
            )

        report["successful_scene_images"] = (
            successful_scenes
        )

        report["failed_scene_images"] = (
            failed_scenes
        )

        report["status"] = (
            "completed"
            if failed_scenes == 0
            else "completed_with_errors"
        )

        save_report(report)

        logger.info(
            "Image generation finished. Successful: %s | Failed: %s",
            successful_scenes,
            failed_scenes
        )

        if failed_scenes:
            raise ImageGenerationError(
                f"{failed_scenes} scene image(s) failed."
            )

    except ImageRateLimitError as error:

        report["status"] = "rate_limited"

        report["error"] = str(error)

        save_report(report)

        logger.error(
            "Image generation stopped due to rate limit."
        )

        raise

    except Exception as error:

        report["status"] = "failed"

        report["error"] = str(error)

        save_report(report)

        logger.exception(
            "Image generation failed."
        )

        raise


if __name__ == "__main__":
    main()
