
import os

from scripts.utils import (
    logger,
    get_api_key,
    get_story_path,
    load_config,
    read_story,
)


def validate_story():
    """Check that the input story exists and is valid."""

    story_path = get_story_path()
    story = read_story()

    if len(story.strip()) < 100:
        raise ValueError(
            "Story is too short. Please provide a complete story."
        )

    logger.info(
        "Story validation passed: %s characters",
        len(story),
    )

    return story_path


def validate_config(config):
    """Validate project configuration."""

    required_sections = [
        "project",
        "video",
        "story",
        "images",
        "audio",
        "subtitles",
        "thumbnail",
        "generation",
    ]

    for section in required_sections:
        if not isinstance(config.get(section), dict):
            raise ValueError(
                f"Missing or invalid configuration section: {section}"
            )

    video = config["video"]

    for key in ["width", "height", "fps", "image_count"]:
        try:
            value = int(video.get(key, 0))
        except (TypeError, ValueError):
            raise ValueError(
                f"Invalid video setting: {key}"
            )

        if value <= 0:
            raise ValueError(
                f"Video setting {key} must be positive."
            )

    if video["width"] != 1920 or video["height"] != 1080:
        raise ValueError(
            "This production pipeline currently requires "
            "1920x1080 video resolution."
        )

    if video["fps"] != 24:
        raise ValueError(
            "This production pipeline currently requires 24 FPS."
        )

    if video["image_count"] > 96:
        raise ValueError(
            "Maximum configured scene image count is 96."
        )

    generation = config["generation"]

    if not generation.get("text_model"):
        raise ValueError(
            "Text model is not configured."
        )

    if not generation.get("openrouter_text_model"):
        raise ValueError(
            "OpenRouter text fallback model is not configured."
        )

    if not config["images"].get("aspect_ratio"):
        raise ValueError(
            "Image aspect ratio is not configured."
        )

    logger.info("Configuration validation passed.")

    return True


def validate_api_key():
    """Validate credentials for the configured providers."""

    gemini_key = os.getenv(
        "GEMINI_API_KEY",
        "",
    ).strip()

    openrouter_key = os.getenv(
        "OPENROUTER_API_KEY",
        "",
    ).strip()

    if not gemini_key:
        raise ValueError(
            "GEMINI_API_KEY is missing. "
            "Configure it in GitHub repository secrets."
        )

    if len(gemini_key) < 10:
        raise ValueError(
            "GEMINI_API_KEY appears invalid."
        )

    if not openrouter_key:
        raise ValueError(
            "OPENROUTER_API_KEY is missing. "
            "It is required for image generation and text fallback."
        )

    if len(openrouter_key) < 10:
        raise ValueError(
            "OPENROUTER_API_KEY appears invalid."
        )

    logger.info(
        "Gemini and OpenRouter credentials are available."
    )

    return True


def run_all_validations():
    """Run all project input validations."""

    logger.info("Starting project validation...")

    config = load_config()

    validate_story()
    validate_config(config)
    validate_api_key()

    logger.info(
        "All project validations passed successfully."
    )

    return config
