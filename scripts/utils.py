
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv


# ============================================================
# PROJECT PATHS
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

CONFIG_DIR = PROJECT_ROOT / "config"
INPUT_DIR = PROJECT_ROOT / "input"
OUTPUT_DIR = PROJECT_ROOT / "output"

ASSET_DIR = OUTPUT_DIR / "assets"
CHARACTER_DIR = ASSET_DIR / "characters"
LOCATION_DIR = ASSET_DIR / "locations"
ENTITY_DIR = ASSET_DIR / "entities"
SCENE_ASSET_DIR = ASSET_DIR / "scenes"

ANIMATION_DIR = OUTPUT_DIR / "animation"
ANIMATION_LAYERS_DIR = ANIMATION_DIR / "layers"
ANIMATION_FRAMES_DIR = ANIMATION_DIR / "frames"
ANIMATION_SCENES_DIR = ANIMATION_DIR / "scenes"
ANIMATION_CACHE_DIR = ANIMATION_DIR / "cache"

AUDIO_DIR = OUTPUT_DIR / "audio"
AUDIO_CHUNKS_DIR = AUDIO_DIR / "chunks"

SUBTITLE_DIR = OUTPUT_DIR / "subtitles"
LOG_DIR = OUTPUT_DIR / "logs"

PROJECT_CACHE_DIR = PROJECT_ROOT / "cache"


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv(PROJECT_ROOT / ".env")


# ============================================================
# LOGGING
# ============================================================

def setup_logging():
    """Configure application logging."""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    return logging.getLogger("horror_automation")


logger = setup_logging()


# ============================================================
# CONFIGURATION
# ============================================================

def load_config():
    """Load project configuration from config/config.json."""

    config_path = CONFIG_DIR / "config.json"

    if not config_path.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {config_path}"
        )

    try:
        with open(config_path, "r", encoding="utf-8") as file:
            config = json.load(file)

        if not isinstance(config, dict):
            raise ValueError(
                "Configuration must contain a JSON object."
            )

        logger.info("Configuration loaded successfully.")
        return config

    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid JSON configuration: {error}"
        ) from error


# ============================================================
# API KEYS
# ============================================================

def get_api_key():
    """Get Gemini API key from environment variables."""

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key or not api_key.strip():
        raise RuntimeError(
            "GEMINI_API_KEY is missing. "
            "Add it to GitHub Actions Secrets."
        )

    return api_key.strip()


def get_openrouter_api_key():
    """Get OpenRouter API key from environment variables."""

    api_key = os.getenv("OPENROUTER_API_KEY")

    if not api_key or not api_key.strip():
        raise RuntimeError(
            "OPENROUTER_API_KEY is missing. "
            "Add it to GitHub Actions Secrets."
        )

    return api_key.strip()


# ============================================================
# PROJECT DIRECTORIES
# ============================================================

def create_project_directories():
    """Create folders required by the video automation pipeline."""

    directories = [
        # Main assets
        CHARACTER_DIR,
        LOCATION_DIR,
        ENTITY_DIR,
        SCENE_ASSET_DIR,

        # Character animation assets
        CHARACTER_DIR / "poses",
        CHARACTER_DIR / "body_parts",

        # Scene layers
        SCENE_ASSET_DIR / "backgrounds",
        SCENE_ASSET_DIR / "midgrounds",
        SCENE_ASSET_DIR / "foregrounds",

        # Animation output
        ANIMATION_DIR,
        ANIMATION_LAYERS_DIR,
        ANIMATION_FRAMES_DIR,
        ANIMATION_SCENES_DIR,
        ANIMATION_CACHE_DIR,

        # Audio
        AUDIO_DIR,
        AUDIO_CHUNKS_DIR,

        # Subtitles and logs
        SUBTITLE_DIR,
        LOG_DIR,

        # Project cache
        PROJECT_CACHE_DIR
    ]

    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Project directories are ready. "
        "Animation asset folders are included."
    )


# ============================================================
# STORY INPUT
# ============================================================

def get_story_path():
    """Return the input story file path."""

    story_path = INPUT_DIR / "story.txt"

    if not story_path.exists():
        raise FileNotFoundError(
            f"Story file not found: {story_path}"
        )

    return story_path


def read_story():
    """Read the complete story from input/story.txt."""

    story_path = get_story_path()
    story = story_path.read_text(encoding="utf-8").strip()

    if not story:
        raise ValueError("The story file is empty.")

    logger.info("Story loaded successfully.")
    return story


# ============================================================
# JSON UTILITIES
# ============================================================

def save_json(data, file_path):
    """
    Save Python data as UTF-8 JSON safely.

    Writes to a temporary file first and replaces the
    destination only after the JSON has been written.
    This reduces the risk of incomplete report files.
    """

    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = file_path.with_name(
        file_path.name + ".tmp"
    )

    try:
        with open(temp_path, "w", encoding="utf-8") as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2
            )

            file.flush()
            os.fsync(file.fileno())

        os.replace(temp_path, file_path)

        logger.info("Saved JSON: %s", file_path)

    except Exception:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            logger.warning(
                "Could not remove temporary JSON file: %s",
                temp_path
            )

        raise


def load_json(file_path):
    """Load JSON data from a file."""

    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(
            f"JSON file not found: {file_path}"
        )

    try:
        with open(file_path, "r", encoding="utf-8") as file:
            return json.load(file)

    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid JSON file: {file_path}. Error: {error}"
        ) from error
