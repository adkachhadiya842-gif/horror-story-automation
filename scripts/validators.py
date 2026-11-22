
import os

from scripts.utils import (
    logger,
    get_story_path,
    load_config,
    read_story,
)


def require_positive_number(section, key, config):
    """Validate a positive numeric configuration value."""

    value = config.get(section, {}).get(key)

    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"Invalid configuration: {section}.{key}"
        )

    if number <= 0:
        raise ValueError(
            f"{section}.{key} must be greater than zero."
        )

    return number


def validate_story():
    """Validate the input story."""

    story_path = get_story_path()
    story = read_story()

    if not story or len(story.strip()) < 100:
        raise ValueError(
            "Story is too short. Minimum 100 characters required."
        )

    logger.info(
        "Story validation passed: %s characters",
        len(story)
    )

    return story_path


def validate_config(config):
    """Validate all required configuration sections."""

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

    width = int(require_positive_number("video", "width", config))
    height = int(require_positive_number("video", "height", config))
    fps = int(require_positive_number("video", "fps", config))
    image_count = int(
        require_positive_number("video", "image_count", config)
    )

    if width != 1920 or height != 1080:
        raise ValueError(
            "Current video editor requires 1920x1080 resolution."
        )

    if fps != 24:
        raise ValueError(
            "Current video editor requires 24 FPS."
        )

    if image_count > 96:
        raise ValueError(
            "Maximum configured scene image count is 96."
        )

    scene_duration = require_positive_number(
        "video", "scene_duration_seconds", config
    )

    transition = video.get("transition_duration", 0)

    try:
        transition = float(transition)
    except (TypeError, ValueError):
        raise ValueError(
            "video.transition_duration must be numeric."
        )

    if transition < 0:
        raise ValueError(
            "video.transition_duration cannot be negative."
        )

    if not config["images"].get("aspect_ratio"):
        raise ValueError(
            "Image aspect ratio is missing."
        )

    generation = config["generation"]

    for key in [
        "text_model",
        "openrouter_text_model",
        "image_model",
    ]:
        if not generation.get(key):
            raise ValueError(
                f"Missing generation setting: {key}"
            )

    retries = generation.get("max_retries", 3)

    if not isinstance(retries, int) or retries < 1:
        raise ValueError(
            "generation.max_retries must be a positive integer."
        )

    retry_delay = generation.get("retry_delay_seconds", 5)

    if not isinstance(retry_delay, (int, float)) or retry_delay < 0:
        raise ValueError(
            "generation.retry_delay_seconds cannot be negative."
        )

    # Audio configuration
    audio = config["audio"]

    for key in [
        "voice_generation",
        "background_ambience",
        "background_music",
        "sound_effects",
    ]:
        if key not in audio or not isinstance(audio[key], bool):
            raise ValueError(
                f"Invalid audio setting: audio.{key}"
            )

    # Subtitle configuration
    subtitles = config["subtitles"]

    if "enabled" not in subtitles:
        raise ValueError(
            "Subtitle enabled setting is missing."
        )

    if not isinstance(subtitles["enabled"], bool):
        raise ValueError(
            "subtitles.enabled must be true or false."
        )

    if subtitles.get("format", "").lower() != "srt":
        raise ValueError(
            "Current subtitle generator requires SRT format."
        )

    if subtitles.get("burn_into_video") not in [True, False]:
        raise ValueError(
            "subtitles.burn_into_video must be true or false."
        )

    # Thumbnail configuration
    thumbnail = config["thumbnail"]

    if not isinstance(thumbnail.get("enabled"), bool):
        raise ValueError(
            "thumbnail.enabled must be true or false."
        )

    thumb_width = int(
        require_positive_number("thumbnail", "width", config)
    )
    thumb_height = int(
        require_positive_number("thumbnail", "height", config)
    )

    if (thumb_width, thumb_height) != (1280, 720):
        raise ValueError(
            "Current thumbnail generator requires 1280x720."
        )

    logger.info("Configuration validation passed.")

    return True


def validate_api_key():
    """Check that required API credentials are available."""

    gemini_key = os.getenv(
        "GEMINI_API_KEY", ""
    ).strip()

    openrouter_key = os.getenv(
        "OPENROUTER_API_KEY", ""
    ).strip()

    if not gemini_key:
        raise ValueError(
            "GEMINI_API_KEY is missing. "
            "Add it to GitHub repository secrets."
        )

    if len(gemini_key) < 10:
        raise ValueError(
            "GEMINI_API_KEY appears invalid."
        )

    if not openrouter_key:
        raise ValueError(
            "OPENROUTER_API_KEY is missing. "
            "Add it to GitHub repository secrets."
        )

    if len(openrouter_key) < 10:
        raise ValueError(
            "OPENROUTER_API_KEY appears invalid."
        )

    logger.info("Required API credentials are available.")

    return True


def run_all_validations():
    """Run all pre-production validations."""

    logger.info("Starting project validation...")

    config = load_config()

    validate_story()
    validate_config(config)
    validate_api_key()

    logger.info(
        "All pre-production validations passed."
    )

    return config


if __name__ == "__main__":
    run_all_validations()
