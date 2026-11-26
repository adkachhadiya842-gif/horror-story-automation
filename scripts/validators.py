
import os

from scripts.utils import (
    logger,
    get_story_path,
    load_config,
    read_story,
)


# ============================================================
# BASIC VALIDATION HELPERS
# ============================================================

def require_positive_number(section, key, config):
    """Validate a positive numeric configuration value."""

    value = config.get(section, {}).get(key)

    if isinstance(value, bool):
        raise ValueError(
            f"Invalid configuration: {section}.{key}"
        )

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


def require_boolean(section, key, config):
    """Validate a boolean setting."""

    value = config.get(section, {}).get(key)

    if not isinstance(value, bool):
        raise ValueError(
            f"{section}.{key} must be true or false."
        )

    return value


def require_dict(parent, key, name):
    """Validate a nested configuration dictionary."""

    value = parent.get(key)

    if not isinstance(value, dict):
        raise ValueError(
            f"Missing or invalid configuration section: {name}"
        )

    return value


# ============================================================
# STORY VALIDATION
# ============================================================

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


# ============================================================
# VIDEO VALIDATION
# ============================================================

def validate_video_config(config):
    """Validate video rendering configuration."""

    video = config["video"]

    width = int(
        require_positive_number("video", "width", config)
    )

    height = int(
        require_positive_number("video", "height", config)
    )

    fps = int(
        require_positive_number("video", "fps", config)
    )

    image_count = int(
        require_positive_number("video", "image_count", config)
    )

    if width != 1920 or height != 1080:
        raise ValueError(
            "Current video system requires 1920x1080 resolution."
        )

    if fps != 24:
        raise ValueError(
            "Current video system requires 24 FPS."
        )

    if image_count > 96:
        raise ValueError(
            "Maximum configured scene image count is 96."
        )

    require_positive_number(
        "video",
        "scene_duration_seconds",
        config
    )

    transition = video.get("transition_duration", 0)

    if isinstance(transition, bool):
        raise ValueError(
            "video.transition_duration must be numeric."
        )

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

    if not isinstance(video.get("zoom_effect"), bool):
        raise ValueError(
            "video.zoom_effect must be true or false."
        )

    if not isinstance(video.get("pan_effect"), bool):
        raise ValueError(
            "video.pan_effect must be true or false."
        )

    return True


# ============================================================
# IMAGE VALIDATION
# ============================================================

def validate_image_config(config):
    """Validate image generation settings."""

    images = config["images"]

    if not images.get("aspect_ratio"):
        raise ValueError(
            "Image aspect ratio is missing."
        )

    for key in [
        "reference_sheets",
        "character_consistency",
        "location_consistency",
        "entity_consistency",
    ]:
        if not isinstance(images.get(key), bool):
            raise ValueError(
                f"images.{key} must be true or false."
            )

    return True


# ============================================================
# GENERATION VALIDATION
# ============================================================

def validate_generation_config(config):
    """Validate AI generation settings."""

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

    if (
        isinstance(retries, bool)
        or not isinstance(retries, int)
        or retries < 1
    ):
        raise ValueError(
            "generation.max_retries must be a positive integer."
        )

    retry_delay = generation.get("retry_delay_seconds", 5)

    if (
        isinstance(retry_delay, bool)
        or not isinstance(retry_delay, (int, float))
        or retry_delay < 0
    ):
        raise ValueError(
            "generation.retry_delay_seconds cannot be negative."
        )

    return True


# ============================================================
# AUDIO VALIDATION
# ============================================================

def validate_audio_config(config):
    """Validate audio configuration."""

    audio = config["audio"]

    for key in [
        "voice_generation",
        "background_ambience",
        "background_music",
        "sound_effects",
    ]:
        if not isinstance(audio.get(key), bool):
            raise ValueError(
                f"Invalid audio setting: audio.{key}"
            )

    return True


# ============================================================
# SUBTITLE VALIDATION
# ============================================================

def validate_subtitle_config(config):
    """Validate subtitle configuration."""

    subtitles = config["subtitles"]

    if not isinstance(subtitles.get("enabled"), bool):
        raise ValueError(
            "subtitles.enabled must be true or false."
        )

    if str(subtitles.get("format", "")).lower() != "srt":
        raise ValueError(
            "Current subtitle generator requires SRT format."
        )

    if not isinstance(subtitles.get("burn_into_video"), bool):
        raise ValueError(
            "subtitles.burn_into_video must be true or false."
        )

    return True


# ============================================================
# THUMBNAIL VALIDATION
# ============================================================

def validate_thumbnail_config(config):
    """Validate thumbnail configuration."""

    thumbnail = config["thumbnail"]

    if not isinstance(thumbnail.get("enabled"), bool):
        raise ValueError(
            "thumbnail.enabled must be true or false."
        )

    width = int(
        require_positive_number("thumbnail", "width", config)
    )

    height = int(
        require_positive_number("thumbnail", "height", config)
    )

    if (width, height) != (1280, 720):
        raise ValueError(
            "Current thumbnail generator requires 1280x720."
        )

    return True


# ============================================================
# ANIMATION VALIDATION
# ============================================================

def validate_animation_config(config):
    """Validate hybrid 2D animation configuration."""

    animation = require_dict(
        config,
        "animation",
        "animation"
    )

    if not isinstance(animation.get("enabled"), bool):
        raise ValueError(
            "animation.enabled must be true or false."
        )

    if not animation["enabled"]:
        logger.warning(
            "Animation is disabled in configuration."
        )
        return True

    if animation.get("engine") != "hybrid_2d_cutout":
        raise ValueError(
            "Unsupported animation engine. "
            "Expected: hybrid_2d_cutout"
        )

    if animation.get("style") != "cinematic_2d_documentary":
        raise ValueError(
            "Unsupported animation style."
        )

    if animation.get("resolution") != "1920x1080":
        raise ValueError(
            "Animation resolution must be 1920x1080."
        )

    animation_fps = animation.get("fps")

    if (
        isinstance(animation_fps, bool)
        or not isinstance(animation_fps, int)
        or animation_fps != config["video"]["fps"]
    ):
        raise ValueError(
            "animation.fps must match video.fps."
        )

    # --------------------------------------------------------
    # Layer settings
    # --------------------------------------------------------

    layers = require_dict(
        animation,
        "layers",
        "animation.layers"
    )

    for key in [
        "enabled",
        "background",
        "midground",
        "characters",
        "foreground",
        "atmosphere",
        "overlays",
    ]:
        if not isinstance(layers.get(key), bool):
            raise ValueError(
                f"animation.layers.{key} must be true or false."
            )

    depth_levels = layers.get("depth_levels")

    if (
        isinstance(depth_levels, bool)
        or not isinstance(depth_levels, int)
        or depth_levels < 1
        or depth_levels > 10
    ):
        raise ValueError(
            "animation.layers.depth_levels must be between 1 and 10."
        )

    # --------------------------------------------------------
    # Character animation settings
    # --------------------------------------------------------

    character = require_dict(
        animation,
        "character_animation",
        "animation.character_animation"
    )

    for key in [
        "enabled",
        "character_consistency",
        "prefer_separate_body_parts",
        "support_multiple_poses",
        "idle_breathing",
        "head_movement",
        "eye_blink",
        "arm_gestures",
        "body_movement",
        "fallback_to_static_pose",
    ]:
        if not isinstance(character.get(key), bool):
            raise ValueError(
                f"animation.character_animation.{key} "
                "must be true or false."
            )

    # --------------------------------------------------------
    # Camera settings
    # --------------------------------------------------------

    camera = require_dict(
        animation,
        "camera",
        "animation.camera"
    )

    for key in [
        "enabled",
        "pan",
        "zoom",
        "tracking",
        "parallax",
        "smooth_motion",
    ]:
        if not isinstance(camera.get(key), bool):
            raise ValueError(
                f"animation.camera.{key} must be true or false."
            )

    max_zoom = camera.get("max_zoom")
    max_pan = camera.get("max_pan_percent")

    if (
        isinstance(max_zoom, bool)
        or not isinstance(max_zoom, (int, float))
        or not 1.0 <= max_zoom <= 2.0
    ):
        raise ValueError(
            "animation.camera.max_zoom must be between 1.0 and 2.0."
        )

    if (
        isinstance(max_pan, bool)
        or not isinstance(max_pan, (int, float))
        or not 0 <= max_pan <= 25
    ):
        raise ValueError(
            "animation.camera.max_pan_percent must be between 0 and 25."
        )

    # --------------------------------------------------------
    # Effects settings
    # --------------------------------------------------------

    effects = require_dict(
        animation,
        "effects",
        "animation.effects"
    )

    for key in [
        "enabled",
        "fog",
        "smoke",
        "rain",
        "dust_particles",
        "light_flicker",
        "shadows",
        "vignette",
        "film_grain",
    ]:
        if not isinstance(effects.get(key), bool):
            raise ValueError(
                f"animation.effects.{key} must be true or false."
            )

    # --------------------------------------------------------
    # Motion graphics
    # --------------------------------------------------------

    motion_graphics = require_dict(
        animation,
        "motion_graphics",
        "animation.motion_graphics"
    )

    for key in [
        "enabled",
        "maps",
        "timelines",
        "location_labels",
        "evidence_boards",
        "documentary_text",
    ]:
        if not isinstance(motion_graphics.get(key), bool):
            raise ValueError(
                f"animation.motion_graphics.{key} "
                "must be true or false."
            )

    # --------------------------------------------------------
    # Timeline
    # --------------------------------------------------------

    timeline = require_dict(
        animation,
        "timeline",
        "animation.timeline"
    )

    if timeline.get("mode") not in [
        "narration_weighted",
        "fixed_duration",
    ]:
        raise ValueError(
            "Invalid animation.timeline.mode."
        )

    for key in [
        "sync_with_audio",
        "scene_transition",
        "avoid_unnecessary_gaps",
    ]:
        if not isinstance(timeline.get(key), bool):
            raise ValueError(
                f"animation.timeline.{key} must be true or false."
            )

    # --------------------------------------------------------
    # Transitions
    # --------------------------------------------------------

    transitions = require_dict(
        animation,
        "transitions",
        "animation.transitions"
    )

    if not isinstance(transitions.get("enabled"), bool):
        raise ValueError(
            "animation.transitions.enabled must be true or false."
        )

    for key in [
        "crossfade",
        "fade_to_black",
        "cinematic_wipe",
    ]:
        if not isinstance(transitions.get(key), bool):
            raise ValueError(
                f"animation.transitions.{key} must be true or false."
            )

    transition_duration = transitions.get("duration_seconds")

    if (
        isinstance(transition_duration, bool)
        or not isinstance(transition_duration, (int, float))
        or transition_duration < 0
        or transition_duration > 5
    ):
        raise ValueError(
            "animation.transitions.duration_seconds "
            "must be between 0 and 5."
        )

    # --------------------------------------------------------
    # Rendering
    # --------------------------------------------------------

    rendering = require_dict(
        animation,
        "rendering",
        "animation.rendering"
    )

    if rendering.get("format") != "mp4":
        raise ValueError(
            "Animation rendering format must be mp4."
        )

    if rendering.get("codec") != "libx264":
        raise ValueError(
            "Animation rendering codec must be libx264."
        )

    if rendering.get("pixel_format") != "yuv420p":
        raise ValueError(
            "Animation rendering pixel format must be yuv420p."
        )

    if rendering.get("audio_codec") != "aac":
        raise ValueError(
            "Animation audio codec must be aac."
        )

    crf = rendering.get("crf")

    if (
        isinstance(crf, bool)
        or not isinstance(crf, int)
        or not 0 <= crf <= 51
    ):
        raise ValueError(
            "animation.rendering.crf must be between 0 and 51."
        )

    logger.info(
        "Hybrid 2D animation configuration validation passed."
    )

    return True


# ============================================================
# MAIN CONFIG VALIDATION
# ============================================================

def validate_config(config):
    """Validate all project configuration sections."""

    required_sections = [
        "project",
        "video",
        "story",
        "images",
        "audio",
        "subtitles",
        "thumbnail",
        "generation",
        "animation",
    ]

    for section in required_sections:
        if not isinstance(config.get(section), dict):
            raise ValueError(
                f"Missing or invalid configuration section: {section}"
            )

    validate_video_config(config)
    validate_image_config(config)
    validate_generation_config(config)
    validate_audio_config(config)
    validate_subtitle_config(config)
    validate_thumbnail_config(config)
    validate_animation_config(config)

    logger.info("Configuration validation passed.")

    return True


# ============================================================
# API KEY VALIDATION
# ============================================================

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

    logger.info(
        "Required API credentials are available."
    )

    return True


# ============================================================
# COMPLETE PRE-PRODUCTION VALIDATION
# ============================================================

def run_all_validations():
    """Run all pre-production validations."""

    logger.info(
        "Starting project validation..."
    )

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
