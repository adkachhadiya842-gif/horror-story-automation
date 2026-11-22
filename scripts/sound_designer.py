
"""
Hindi Horror Story Automation
Sound Designer - V1

Creates scene-wise sound design plans and mixes:
- Original Hindi narration
- Background ambience
- Background music
- Scene-specific sound effects

Audio assets must be supplied by the user.
"""

import json
import logging
import re
from pathlib import Path

from pydub import AudioSegment

from scripts.utils import OUTPUT_DIR, create_project_directories


LOGGER = logging.getLogger(__name__)

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
    ".wav",
    ".mp3",
    ".ogg",
    ".flac",
    ".m4a",
}

# Mixing levels in decibels.
NARRATION_GAIN = 0
AMBIENCE_GAIN = -30
MUSIC_GAIN = -24
EFFECT_GAIN = -12

AUDIO_FADE_MS = 2000


def setup_logging():
    """Initialize sound designer logging."""

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
    )


def prepare_directories():
    """Create required project directories."""

    create_project_directories()

    for folder in [
        AUDIO_DIR,
        AMBIENCE_DIR,
        MUSIC_DIR,
        EFFECTS_DIR,
    ]:
        folder.mkdir(
            parents=True,
            exist_ok=True
        )


def load_json(path):
    """Read JSON file."""

    if not path.exists():
        raise FileNotFoundError(
            f"Required file not found: {path}"
        )

    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def save_json(data, path):
    """Save JSON report."""

    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=4
        )


def load_narration():
    """Load the original narration WAV."""

    if not NARRATION_FILE.exists():
        raise FileNotFoundError(
            f"Narration audio missing: {NARRATION_FILE}. "
            "Run voice_generator.py first."
        )

    audio = AudioSegment.from_file(
        str(NARRATION_FILE)
    )

    if len(audio) <= 0:
        raise ValueError("Narration audio is empty.")

    return audio


def get_scene_narration(scene):
    """Extract spoken narration from a scene."""

    fields = [
        "hindi_narration",
        "narration",
        "narration_text",
        "voiceover",
        "voice_over",
    ]

    for field in fields:

        value = scene.get(field)

        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


def get_scene_cues(scene):
    """Extract scene sound-effect instructions."""

    fields = [
        "sound_effects",
        "sound_design",
        "audio_cues",
        "sound_cues",
        "sfx",
    ]

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
                        cues.append(str(description))

    return cues


def build_scene_timeline(scenes, total_duration):
    """
    Estimate scene boundaries using narration length.

    Exact timing requires speech alignment, which is
    not yet implemented in V1.
    """

    if not scenes:
        raise ValueError("No scenes available for sound design.")

    weights = []

    for scene in scenes:

        narration = get_scene_narration(scene)

        weight = max(len(narration), 1)

        weights.append(weight)

    total_weight = sum(weights)

    timeline = []
    current_time = 0.0

    for index, (scene, weight) in enumerate(
        zip(scenes, weights),
        start=1
    ):

        if index == len(scenes):
            end_time = total_duration

        else:
            scene_duration = (
                total_duration * weight / total_weight
            )

            end_time = current_time + scene_duration

        timeline.append({
            "scene_number": scene.get(
                "scene_number",
                index
            ),
            "start_seconds": round(current_time, 3),
            "end_seconds": round(end_time, 3),
            "duration_seconds": round(
                end_time - current_time,
                3
            ),
            "sound_cues": get_scene_cues(scene),
            "visual_description": scene.get(
                "visual_description",
                ""
            ),
        })

        current_time = end_time

    return timeline


def find_audio_files(folder):
    """Find supported audio assets."""

    if not folder.exists():
        return []

    return sorted(
        [
            path
            for path in folder.rglob("*")
            if path.is_file()
            and path.suffix.lower() in SUPPORTED_FORMATS
        ]
    )


def load_audio_file(path):
    """Load an audio file safely."""

    try:
        audio = AudioSegment.from_file(str(path))

        if len(audio) <= 0:
            raise ValueError("Audio file is empty.")

        return audio

    except Exception as error:
        LOGGER.warning(
            "Could not load audio %s: %s",
            path,
            error
        )

        return None


def fit_audio(audio, target_duration):
    """Loop or trim audio to target duration."""

    if target_duration <= 0:
        return AudioSegment.empty()

    if len(audio) == 0:
        return AudioSegment.silent(
            duration=int(target_duration)
        )

    repetitions = (
        int(target_duration / len(audio)) + 1
    )

    result = audio * repetitions

    return result[:int(target_duration)]


def apply_fade(audio):
    """Apply safe beginning and ending fades."""

    if len(audio) <= 0:
        return audio

    fade_length = min(
        AUDIO_FADE_MS,
        len(audio) // 3
    )

    if fade_length <= 0:
        return audio

    return (
        audio.fade_in(fade_length)
        .fade_out(fade_length)
    )


def choose_background_track(folder, category):
    """Choose a default background track."""

    files = find_audio_files(folder)

    if not files:
        LOGGER.warning(
            "No %s audio files found in %s",
            category,
            folder
        )

        return None

    # Prefer an explicitly named default track.
    for path in files:

        if path.stem.lower() in {
            "default",
            "main",
            "background",
        }:
            return path

    return files[0]


def normalize_filename(value):
    """Normalize filenames for sound matching."""

    value = str(value).lower()

    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value
    )

    return set(value.split())


def find_matching_effect(cue, effect_files):
    """Find the closest effect using filename keywords."""

    cue_words = normalize_filename(cue)

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

    # Require at least one meaningful matching keyword.
    if best_score == 0:
        return None

    return best_match


def build_sound_plan(timeline, ambience, music, effect_files):
    """Build scene-wise sound design plan."""

    plan = []

    for scene in timeline:

        scene_effects = []

        for cue in scene["sound_cues"]:

            matching_file = find_matching_effect(
                cue,
                effect_files
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

        plan.append({
            **scene,
            "ambience_asset": (
                str(ambience)
                if ambience else None
            ),
            "music_asset": (
                str(music)
                if music else None
            ),
            "effects": scene_effects,
        })

    return plan


def mix_audio(
    narration,
    timeline,
    ambience_path,
    music_path,
    effect_files,
):
    """Mix narration, ambience, music and sound effects."""

    total_duration = len(narration)

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
                total_duration
            )

            ambience = (
                ambience
                .apply_gain(AMBIENCE_GAIN)
                .set_channels(2)
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
                total_duration
            )

            music = (
                music
                .apply_gain(MUSIC_GAIN)
                .set_channels(2)
            )

            final_mix = final_mix.overlay(
                music
            )

    # Scene-specific sound effects.
    matched_effects = []
    missing_effects = []

    for scene in timeline:

        start_ms = int(
            scene["start_seconds"] * 1000
        )

        end_ms = int(
            scene["end_seconds"] * 1000
        )

        scene_duration = end_ms - start_ms

        if scene_duration <= 0:
            continue

        for cue in scene["sound_cues"]:

            effect_path = find_matching_effect(
                cue,
                effect_files
            )

            if not effect_path:

                missing_effects.append({
                    "scene_number": scene["scene_number"],
                    "cue": cue,
                })

                continue

            effect = load_audio_file(effect_path)

            if not effect:
                continue

            # Effects start within their respective scene.
            effect = effect[:min(
                len(effect),
                scene_duration
            )]

            effect = apply_fade(effect)

            effect = (
                effect
                .apply_gain(EFFECT_GAIN)
                .set_channels(2)
            )

            final_mix = final_mix.overlay(
                effect,
                position=start_ms
            )

            matched_effects.append({
                "scene_number": scene["scene_number"],
                "cue": cue,
                "asset": str(effect_path),
                "start_ms": start_ms,
            })

    # Prevent clipping without changing the narration source.
    if final_mix.max_dBFS > -1.0:
        final_mix = final_mix.apply_gain(
            -1.0 - final_mix.max_dBFS
        )

    return (
        final_mix,
        matched_effects,
        missing_effects,
    )


def main():
    """Run complete sound design pipeline."""

    setup_logging()
    prepare_directories()

    LOGGER.info("Starting horror sound design.")

    analysis = load_json(ANALYSIS_FILE)

    scenes = analysis.get("scenes", [])

    if not isinstance(scenes, list) or not scenes:
        raise ValueError("Story analysis contains no scenes.")

    narration = load_narration()

    total_duration = len(narration)

    timeline = build_scene_timeline(
        scenes,
        total_duration
    )

    ambience_path = choose_background_track(
        AMBIENCE_DIR,
        "ambience"
    )

    music_path = choose_background_track(
        MUSIC_DIR,
        "music"
    )

    effect_files = find_audio_files(
        EFFECTS_DIR
    )

    LOGGER.info(
        "Available sound effects: %s",
        len(effect_files)
    )

    sound_plan = build_sound_plan(
        timeline,
        ambience_path,
        music_path,
        effect_files
    )

    save_json(
        sound_plan,
        PLAN_FILE
    )

    final_mix, matched, missing = mix_audio(
        narration,
        timeline,
        ambience_path,
        music_path,
        effect_files,
    )

    FINAL_AUDIO.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    final_mix.export(
        str(FINAL_AUDIO),
        format="wav",
    )

    report = {
        "status": "success",
        "total_duration_seconds": round(
            total_duration / 1000,
            2
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
        "matched_effects": matched,
        "missing_effects": missing,
        "matched_effect_count": len(matched),
        "missing_effect_count": len(missing),
        "final_audio": str(FINAL_AUDIO),
        "sound_design_plan": str(PLAN_FILE),
        "timing_method": "estimated_scene_timing",
    }

    save_json(
        report,
        REPORT_FILE
    )

    LOGGER.info("-----------------------------------")
    LOGGER.info("SOUND DESIGN COMPLETED")
    LOGGER.info("Final audio: %s", FINAL_AUDIO)
    LOGGER.info("Matched effects: %s", len(matched))
    LOGGER.info("Missing effects: %s", len(missing))
    LOGGER.info("-----------------------------------")


if __name__ == "__main__":

    try:
        main()

    except Exception:

        logging.exception(
            "Sound design failed."
        )

        raise
