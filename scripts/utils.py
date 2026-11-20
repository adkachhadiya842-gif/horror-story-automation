
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv


# Project root directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Important project directories
CONFIG_DIR = PROJECT_ROOT / "config"
INPUT_DIR = PROJECT_ROOT / "input"
OUTPUT_DIR = PROJECT_ROOT / "output"

# Load environment variables from .env if available
load_dotenv(PROJECT_ROOT / ".env")


def setup_logging():
    """Configure application logging."""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    return logging.getLogger("horror_automation")


logger = setup_logging()


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

        logger.info("Configuration loaded successfully.")
        return config

    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid JSON configuration: {error}"
        ) from error


def get_api_key():
    """Get Gemini API key from environment variables."""

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing. "
            "Add it to GitHub Actions Secrets."
        )

    return api_key.strip()


def create_project_directories():
    """Create required folders for generated assets."""

    directories = [
        OUTPUT_DIR / "assets" / "characters",
        OUTPUT_DIR / "assets" / "locations",
        OUTPUT_DIR / "assets" / "entities",
        OUTPUT_DIR / "assets" / "scenes",
        OUTPUT_DIR / "audio" / "chunks",
        OUTPUT_DIR / "subtitles",
        OUTPUT_DIR / "logs",
        PROJECT_ROOT / "cache"
    ]

    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)

    logger.info("Project directories are ready.")


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


def save_json(data, file_path):
    """Save Python data as a UTF-8 JSON file."""

    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2
        )

    logger.info("Saved JSON: %s", file_path)


def load_json(file_path):
    """Load JSON data from a file."""

    file_path = Path(file_path)

    with open(file_path, "r", encoding="utf-8") as file:
        return json.load(file)
