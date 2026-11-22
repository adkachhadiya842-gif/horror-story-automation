
"""
Hindi Horror Story Automation
Sound Designer - V2

Features:
- Preserves original narration duration.
- Adds optional ambience and background music.
- Matches sound effects using Hindi and English filenames.
- Creates scene-wise sound design plans.
- Prevents sound effects from extending beyond narration.
- Uses safer audio mixing levels.
- Generates detailed JSON reports.

Audio assets must be supplied by the user.
"""

import json
import logging
import re
import unicodedata
from pathlib import Path

from pydub import AudioSegment

from scripts.utils import (
    OUTPUT_DIR,
    create_project_directories,
)


LOGGER = logging.getLogger("horror_sound_designer")

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = Path(OUTPUT_DIR)

ANALYSIS_FILE = OUTPUT / "story_analysis.json"
NARRATION_FILE = OUTPUT / "audio" / "narration.wav"

SOUND_ROOT = ROOT / "input" / "sounds"

AMBIENCE_DIR = SOUND_ROOT / "ambience"
MUSIC_DIR = SOUND_ROOT / "music"
EFFECTS_DIR = SOUND_ROOT / "effects"

AUDIO_DIR = OUTPUT / "audio"

PLAN_FILE = AUDIO_DIR / "sound_design_plan.json"
REPORT_FILE = AUDIO_DIR / "sound_design_report.json"
FINAL_AUDIO = AUDIO_DIR / "final_audio.wav"

SUPPORTED_FORMATS = {
    ".wav", ".mp3", ".ogg", ".flac", ".m4a"
}

# Mixing levels.
NARRATION_GAIN = -1.0
AMBIENCE_GAIN = -32
MUSIC_GAIN = -28
EFFECT_GAIN = -18

AUDIO_FADE_MS = 1500

# Common Hindi-to-English sound filename aliases.
SOUND_ALIASES = {
    "दरवाजा": ["door", "door_creak", "door_open"],
    "दरवाज़ा": ["door", "door_creak", "door_open"],
    "दरवाजा खुलना": ["door_open", "door"],
    "दरवाजा बंद": ["door_close", "door_slam"],
    "कदम": ["footsteps", "footstep", "walking"],
    "पैरों की आवाज": ["footsteps", "walking"],
    "चलना": ["footsteps", "walking"],
    "हवा": ["wind", "howling_wind"],
    "आंधी": ["storm", "wind"],
    "बारिश": ["rain", "heavy_rain"],
    "बिजली": ["thunder", "lightning"],
    "गरज": ["thunder", "storm"],
    "चीख": ["scream", "horror_scream"],
    "चिल्लाना": ["scream", "shout"],
    "हंसी": ["evil_laugh", "laugh"],
    "हँसी": ["evil_laugh", "laugh"],
    "रोना": ["crying", "sobbing"],
    "सांस": ["breathing", "heavy_breathing"],
    "साँस": ["breathing", "heavy_breathing"],
    "दिल": ["heartbeat", "heart_beat"],
    "धड़कन": ["heartbeat", "heart_beat"],
    "घड़ी": ["clock", "ticking_clock"],
    "ट्रेन": ["train", "train_passing"],
    "सीटी": ["whistle", "train_whistle"],
    "खिड़की": ["window", "window_creak"],
    "कांच": ["glass", "glass_break"],
    "शीशा": ["glass", "glass_break"],
    "पानी": ["water", "dripping_water"],
    "बूंद": ["water_drip", "dripping"],
    "ताला": ["lock", "door_lock"],
    "जंजीर": ["chain", "chains"],
    "घंटी": ["bell", "church_bell"],
    "फुसफुसाना": ["whisper", "whispering"],
    "फुसफुसाहट": ["whisper", "whispering"],
    "धमाका": ["impact", "explosion", "boom"],
    "गिरना": ["fall", "impact", "body_fall"],
    "मशीन": ["machine", "mechanical"],
    "इंजन": ["engine", "train_engine"],
    "सन्नाटा": ["silence", "dark_ambience"],
}


def setup_logging():
    """Initialize logging."""

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                AUDIO_DIR / "sound_designer.log",
                encoding="utf-8",
            ),
        ],
        force=True,
    )


def prepare_directories():
    """Create required directories."""

    create_project_directories()

    for folder in (
        AUDIO_DIR,
        AMBIENCE_DIR,
        MUSIC_DIR,
        EFFECTS_DIR,
    ):
        folder.mkdir(parents=True, exist_ok=True)


def load_json(path):
    """Load JSON file."""

    if not path.is_file():
        raise FileNotFoundError(
            f"Required file missing: {path}"
        )

    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError(
            f"Invalid JSON object: {path}"
        )

    return data


def save_json(data, path):
    """Save JSON report."""

    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=4,
        )


def load_narration():
    """Load original narration audio."""

    if not NARRATION_FILE.is_file():
        raise FileNotFoundError(
            f"Narration missing: {NARRATION_FILE}"
        )

    audio = AudioSegment.from_file(str(NARRATION_FILE))

    if len(audio) <= 0:
        raise ValueError("Narration audio is empty.")

    return audio.set_channels(2)


def get_scene_cues(scene):
    """Extract sound-effect cues from scene data."""

    fields = (
        "sound_effects",
        "sound_design",
        "audio_cues",
        "sound_cues",
        "sfx",
    )

    cues = []

    for field in fields:
        value = scene.get(field)

        if isinstance(value, str) and value.strip():
            cues.append(value.strip())

        elif isinstance(value, list):
            for item in value:

                if isinstance(item, str) and item.strip():
                    cues.append(item.strip())

                elif isinstance(item, dict):
                    description = (
                        item.get("description")
                        or item.get("sound")
                        or item.get("name")
                    )

                    if description:
                        cues.append(str(description).strip())

    # Remove duplicate cue descriptions.
    return list(dict.fromkeys(cues))


def get_scene_weight(scene):
    """Estimate visual scene importance."""

    fields = (
        "story_event",
        "visual_description",
        "scene_title",
        "image_prompt",
    )

    for field in fields:
        value = scene.get(field)

        if isinstance(value, str) and value.strip():
            return max(len(value.strip()), 1)

    return 1


def build_scene_timeline(scenes, total_duration_ms):
    """
    Estimate scene boundaries.

    V2 uses visual event descriptions because exact speech
    alignment is not yet implemented.
    """

    if not scenes:
        raise ValueError("No scenes found in story analysis.")

    weights = [
        get_scene_weight(scene)
        for scene in scenes
    ]

    total_weight = sum(weights)

    if total_weight <= 0:
        weights = [1] * len(scenes)
        total_weight = len(scenes)

    timeline = []
    current_ms = 0

    for index, (scene, weight) in enumerate(
        zip(scenes, weights),
        start=1,
    ):

        if index == len(scenes):
            end_ms = total_duration_ms

        else:
            estimated_duration = (
                total_duration_ms * weight / total_weight
            )

            end_ms = current_ms + round(
                estimated_duration
            )

        end_ms = min(end_ms, total_duration_ms)

        timeline.append({
            "scene_number": scene.get("scene_number", index),
            "start_ms": current_ms,
            "end_ms": end_ms,
            "start_seconds": round(current_ms / 1000, 3),
            "end_seconds": round(end_ms / 1000, 3),
            "duration_seconds": round(
                (end_ms - current_ms) / 1000,
                3,
            ),
            "sound_cues": get_scene_cues(scene),
            "visual_description": scene.get(
                "visual_description",
                "",
            ),
        })

        current_ms = end_ms

    if current_ms != total_duration_ms:
        raise RuntimeError(
            "Scene timeline does not cover the complete narration."
        )

    return timeline


def find_audio_files(folder):
    """Find supported audio files."""

    if not folder.exists():
        return []

    return sorted(
        path
        for path in folder.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_FORMATS
    )


def load_audio_file(path):
    """Load audio safely."""

    try:
        audio = AudioSegment.from_file(str(path))

        if len(audio) <= 0:
            raise ValueError("Empty audio file.")

        return audio.set_channels(2)

    except Exception as error:
        LOGGER.warning(
            "Could not load audio %s: %s",
            path,
            error,
        )

        return None


def fit_audio(audio, target_duration_ms):
    """Loop or trim audio to exact target duration."""

    if target_duration_ms <= 0:
        return AudioSegment.empty()

    if len(audio) <= 0:
        return AudioSegment.silent(
            duration=target_duration_ms,
            frame_rate=44100,
        ).set_channels(2)

    repetitions = (
        target_duration_ms // len(audio)
    ) + 1

    return (audio * repetitions)[:target_duration_ms]


def apply_fade(audio):
    """Apply safe fades."""

    if len(audio) <= 0:
        return audio

    fade_length = min(
        AUDIO_FADE_MS,
        len(audio) // 3,
    )

    if fade_length <= 0:
        return audio

    return (
        audio
        .fade_in(fade_length)
        .fade_out(fade_length)
    )


def choose_background_track(folder, category):
    """Choose a background track."""

    files = find_audio_files(folder)

    if not files:
        LOGGER.warning(
            "No %s assets found in %s",
            category,
            folder,
        )
        return None

    preferred_names = {
        "default",
        "main",
        "background",
    }

    for path in files:
        if path.stem.lower() in preferred_names:
            return path

    return files[0]


def normalize_filename(value):
    """
    Normalize Hindi and English text.

    Unlike the previous version, this preserves Unicode
    letters so Devanagari filenames can be matched.
    """

    value = unicodedata.normalize(
        "NFKC",
        str(value).lower(),
    )

    value = "".join(
        char if char.isalnum() else " "
        for char in value
    )

    return set(value.split())


def expand_cue_keywords(cue):
    """Expand Hindi cue keywords into English aliases."""

    keywords = normalize_filename(cue)

    for hindi_term, aliases in SOUND_ALIASES.items():

        if hindi_term in cue:
            for alias in aliases:
                keywords.update(
                    normalize_filename(alias)
                )

    return keywords


def find_matching_effect(cue, effect_files):
    """Find the best matching effect asset."""

    cue_words = expand_cue_keywords(cue)

    best_match = None
    best_score = 0

    for path in effect_files:

        filename_words = normalize_filename(
            path.stem
        )

        score = len(
            cue_words.intersection(filename_words)
        )

        if score > best_score:
            best_score = score
            best_match = path

    if best_score <= 0:
        return None

    return best_match


def build_effect_matches(timeline, effect_files):
    """Match sound effects once and reuse the result."""

    matches = []

    for scene in timeline:

        scene_effects = []

        for cue in scene["sound_cues"]:

            matching_file = find_matching_effect(
                cue,
                effect_files,
            )

            scene_effects.append({
                "cue": cue,
                "asset": (
                    str(matching_file)
                    if matching_file else None
                ),
                "status": (
                    "matched"
                    if matching_file else "missing"
                ),
            })

        matches.append({
            "scene_number": scene["scene_number"],
            "effects": scene_effects,
        })

    return matches


def build_sound_plan(
    timeline,
    ambience_path,
    music_path,
    effect_matches,
):
    """Build detailed scene-wise sound plan."""

    match_lookup = {
        str(item["scene_number"]): item["effects"]
        for item in effect_matches
    }

    plan = []

    for scene in timeline:

        plan.append({
            **scene,
            "ambience_asset": (
                str(ambience_path)
                if ambience_path else None
            ),
            "music_asset": (
                str(music_path)
                if music_path else None
            ),
            "effects": match_lookup.get(
                str(scene["scene_number"]),
                [],
            ),
        })

    return plan


def mix_audio(
    narration,
    timeline,
    ambience_path,
    music_path,
    effect_matches,
):
    """Mix narration, background layers and sound effects."""

    total_duration = len(narration)

    # Keep narration as the main foreground layer.
    narration_track = (
        narration
        .apply_gain(NARRATION_GAIN)
        .set_channels(2)
    )

    final_mix = narration_track

    # Background ambience.
    if ambience_path:

        ambience = load_audio_file(ambience_path)

        if ambience:

            ambience = fit_audio(
                ambience,
                total_duration,
            )

            ambience = (
                apply_fade(ambience)
                .apply_gain(AMBIENCE_GAIN)
            )

            final_mix = final_mix.overlay(
                ambience
            )

    # Background music.
    if music_path:

        music = load_audio_file(music_path)

        if music:

            music = fit_audio(
                music,
                total_duration,
            )

            music = (
                apply_fade(music)
                .apply_gain(MUSIC_GAIN)
            )

            final_mix = final_mix.overlay(
                music
            )

    matched_effects = []
    missing_effects = []

    # Scene-specific effects.
    for scene, match_group in zip(
        timeline,
        effect_matches,
    ):

        start_ms = scene["start_ms"]
        end_ms = scene["end_ms"]

        scene_duration = end_ms - start_ms

        if scene_duration <= 0:
            continue

        for effect_info in match_group["effects"]:

            cue = effect_info["cue"]
            asset = effect_info["asset"]

            if not asset:

                missing_effects.append({
                    "scene_number": scene["scene_number"],
                    "cue": cue,
                })

                continue

            effect_path = Path(asset)

            effect = load_audio_file(effect_path)

            if effect is None:
                missing_effects.append({
                    "scene_number": scene["scene_number"],
                    "cue": cue,
                    "reason": "asset_load_failed",
                })
                continue

            # Effects are kept inside their scene boundary.
            effect = effect[:scene_duration]

            effect = (
                apply_fade(effect)
                .apply_gain(EFFECT_GAIN)
                .set_channels(2)
            )

            # Ensure the effect cannot extend past narration.
            available_duration = total_duration - start_ms

            effect = effect[:available_duration]

            if len(effect) <= 0:
                continue

            final_mix = final_mix.overlay(
                effect,
                position=start_ms,
            )

            matched_effects.append({
                "scene_number": scene["scene_number"],
                "cue": cue,
                "asset": str(effect_path),
                "start_ms": start_ms,
                "duration_ms": len(effect),
            })

    # Safety headroom. This can slightly lower all layers
    # only when the combined mix would otherwise clip.
    if final_mix.max_dBFS > -1.0:
        safety_gain = -1.0 - final_mix.max_dBFS

        LOGGER.warning(
            "Mix peak was high. Applying %.2f dB safety reduction.",
            safety_gain,
        )

        final_mix = final_mix.apply_gain(
            safety_gain
        )

    # Guarantee exact narration duration.
    final_mix = final_mix[:total_duration]

    if len(final_mix) != total_duration:
        raise RuntimeError(
            "Final audio duration differs from narration."
        )

    return (
        final_mix,
        matched_effects,
        missing_effects,
    )


def main():
    """Run the complete sound design pipeline."""

    setup_logging()
    prepare_directories()

    LOGGER.info("Starting Sound Designer V2.")

    analysis = load_json(ANALYSIS_FILE)

    scenes = analysis.get("scenes", [])

    if not isinstance(scenes, list) or not scenes:
        raise ValueError(
            "Story analysis contains no valid scenes."
        )

    narration = load_narration()

    total_duration_ms = len(narration)

    timeline = build_scene_timeline(
        scenes,
        total_duration_ms,
    )

    ambience_path = choose_background_track(
        AMBIENCE_DIR,
        "ambience",
    )

    music_path = choose_background_track(
        MUSIC_DIR,
        "music",
    )

    effect_files = find_audio_files(
        EFFECTS_DIR
    )

    LOGGER.info(
        "Available sound effects: %d",
        len(effect_files),
    )

    effect_matches = build_effect_matches(
        timeline,
        effect_files,
    )

    sound_plan = build_sound_plan(
        timeline,
        ambience_path,
        music_path,
        effect_matches,
    )

    save_json(
        sound_plan,
        PLAN_FILE,
    )

    final_mix, matched, missing = mix_audio(
        narration,
        timeline,
        ambience_path,
        music_path,
        effect_matches,
    )

    FINAL_AUDIO.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    final_mix.export(
        str(FINAL_AUDIO),
        format="wav",
    )

    # Verify exported audio.
    verification = AudioSegment.from_file(
        str(FINAL_AUDIO)
    )

    if len(verification) != total_duration_ms:
        raise RuntimeError(
            "Exported final audio duration does not match narration."
        )

    if verification.channels != 2:
        raise RuntimeError(
            "Final audio must be stereo."
        )

    report = {
        "status": "success",
        "version": "V2",
        "total_duration_seconds": round(
            total_duration_ms / 1000,
            3,
        ),
        "total_scenes": len(scenes),
        "ambience_asset": (
            str(ambience_path)
            if ambience_path else None
        ),
        "music_asset": (
            str(music_path)
            if music_path else None
        ),
        "available_effect_assets": len(effect_files),
        "matched_effect_count": len(matched),
        "missing_effect_count": len(missing),
        "matched_effects": matched,
        "missing_effects": missing,
        "final_audio": str(FINAL_AUDIO),
        "sound_design_plan": str(PLAN_FILE),
        "audio_channels": verification.channels,
        "audio_frame_rate": verification.frame_rate,
        "audio_peak_dbfs": round(
            verification.max_dBFS,
            2,
        ),
        "timing_method": "estimated_visual_scene_timing",
        "exact_speech_alignment": False,
        "duration_verified": True,
    }

    save_json(
        report,
        REPORT_FILE,
    )

    LOGGER.info("-----------------------------------")
    LOGGER.info("SOUND DESIGN V2 COMPLETED")
    LOGGER.info("Final audio: %s", FINAL_AUDIO)
    LOGGER.info(
        "Duration: %.2f seconds",
        len(verification) / 1000,
    )
    LOGGER.info("Matched effects: %d", len(matched))
    LOGGER.info("Missing effects: %d", len(missing))
    LOGGER.info("-----------------------------------")


if __name__ == "__main__":

    try:
        main()

    except Exception:
        logging.exception(
            "Sound design failed."
        )
        raise
