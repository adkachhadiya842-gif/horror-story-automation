
from scripts.utils import (
    logger,
    get_api_key,
    get_story_path,
    load_config,
    read_story
)


def validate_story():
    """Check that the input story exists and is not empty."""

    story_path = get_story_path()
    story = read_story()

    if len(story) < 100:
        raise ValueError(
            "Story is too short. Please provide a complete story."
        )

    logger.info(
        "Story validation passed: %s characters",
        len(story)
    )

    return story_path


def validate_config(config):
    """Validate required video and generation settings."""

    required_sections = [
        "project",
        "video",
        "story",
        "images",
        "audio",
        "subtitles",
        "thumbnail",
        "generation"
    ]

    for section in required_sections:
        if section not in config:
            raise ValueError(
                f"Missing configuration section: {section}"
            )

    video = config["video"]

    if video.get("width", 0) <= 0:
        raise ValueError("Video width must be positive.")

    if video.get("height", 0) <= 0:
        raise ValueError("Video height must be positive.")

    if video.get("fps", 0) <= 0:
        raise ValueError("Video FPS must be positive.")

    image_count = video.get("image_count", 0)

    if image_count not in [72, 96, 120]:
        raise ValueError(
            "Image count must be 72, 96, or 120."
        )

    generation = config["generation"]

    if not generation.get("text_model"):
        raise ValueError("Text model is not configured.")

    if not generation.get("image_model"):
        raise ValueError("Image model is not configured.")

    logger.info("Configuration validation passed.")

    return True


def validate_api_key():
    """Check whether Gemini API key is available."""

    api_key = get_api_key()

    if len(api_key) < 10:
        raise ValueError("Gemini API key appears invalid.")

    logger.info("Gemini API key is available.")

    return True


def run_all_validations():
    """Run all project input validations."""

    logger.info("Starting project validation...")

    config = load_config()

    validate_story()
    validate_config(config)
    validate_api_key()

    logger.info("All validations passed successfully.")

    return config
