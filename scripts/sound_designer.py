
"""
Hindi Horror Story Automation
Sound Designer - V3

Features:
- Preserves exact narration duration.
- Uses optional ambience and background music.
- Detects sound cues from explicit scene fields and scene descriptions.
- Supports Hindi and English cue keywords.
- Matches the supplied WAV sound-effect filenames.
- Places effects within estimated scene boundaries.
- Creates sound design plan and detailed JSON report.

Timing is scene-based estimation, not exact speech alignment.
"""

import json
import logging
import re
import unicodedata
from pathlib import Path

from pydub import AudioSegment

from scripts.utils import OUTPUT_DIR, create_project_directories


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

SUPPORTED_FORMATS = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}

# Mixing levels in decibels.
NARRATION_GAIN = -1.0
AMBIENCE_GAIN = -32.0
MUSIC_GAIN = -28.0
EFFECT_GAIN = -18.0

AUDIO_FADE_MS = 1200
MIN_EFFECT_GAP_MS = 1800

# Each key corresponds to the supplied effect filename.
EFFECT_KEYWORDS = {
    "door_creak": [
        "door", "door creak", "door open", "door close",
        "दरवाजा", "दरवाज़ा", "दरवाजा खुला",
        "दरवाज़ा खुला", "दरवाजा बंद", "दरवाज़ा बंद",
        "दरवाजा खुलना", "दरवाज़ा खुलना",
        "दरवाजे की आवाज", "दरवाज़े की आवाज",
        "दरवाजा चरमराना", "किवाड़",
    ],
    "footsteps": [
        "footsteps", "footstep", "walking", "walk",
        "running footsteps", "steps",
        "कदम", "कदमों की आवाज", "पैरों की आवाज",
        "चलने की आवाज", "चलना", "दौड़ना",
        "किसी के चलने", "पैरों की आहट",
    ],
    "heartbeat": [
        "heartbeat", "heart beat", "heart beating",
        "fast heartbeat", "heart pounding",
        "दिल की धड़कन", "दिल धड़कना",
        "धड़कन", "तेज धड़कन", "दिल",
    ],
    "thunder": [
        "thunder", "thunderstorm", "lightning",
        "storm", "thunder clap",
        "बिजली", "बादलों की गरज", "गरज",
        "बादल गरजना", "आकाशीय बिजली",
        "तूफान", "आंधी",
    ],
    "train_whistle": [
        "train whistle", "train horn", "whistle",
        "train passing", "railway whistle",
        "ट्रेन की सीटी", "रेल की सीटी",
        "सीटी", "ट्रेन", "रेलगाड़ी", "रेल गाड़ी",
        "रेल", "भाप का इंजन",
    ],
}

# Explicit scene fields that may contain sound descriptions.
CUE_FIELDS = (
    "sound_effects",
    "sound_design",
    "audio_cues",
    "sound_cues",
    "sfx",
)

# Text fields used for fallback sound detection.
SCENE_TEXT_FIELDS = (
    "story_event",
    "visual_description",
    "scene_title",
    "image_prompt",
    "description",
    "narration",
    "dialogue",
)


def setup_logging():
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
    create_project_directories()

    for folder in (
        AUDIO_DIR,
        AMBIENCE_DIR,
        MUSIC_DIR,
        EFFECTS_DIR,
    ):
        folder.mkdir(parents=True, exist_ok=True)


def load_json(path):
    if not path.is_file():
        raise FileNotFoundError(f"Required file missing: {path}")

    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError(f"Invalid JSON object: {path}")

    return data


def save_json(data, path):
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)


def load_narration():
    if not NARRATION_FILE.is_file():
        raise FileNotFoundError(
            f"Narration missing: {NARRATION_FILE}"
        )

    audio = AudioSegment.from_file(str(NARRATION_FILE))

    if len(audio) <= 0:
        raise ValueError("Narration audio is empty.")

    return audio.set_channels(2)


def normalize_text(value):
    """Normalize English and Devanagari text for matching."""
    value = unicodedata.normalize("NFKC", str(value)).lower()
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def collect_explicit_cues(scene):
    """Read sound cues already present in the scene analysis."""
    cues = []

    for field in CUE_FIELDS:
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
                        or item.get("effect")
                    )

                    if description:
                        cues.append(str(description).strip())

    return list(dict.fromkeys(cues))


def collect_scene_text(scene):
    """Combine useful scene fields for fallback detection."""
    parts = []

    for field in SCENE_TEXT_FIELDS:
        value = scene.get(field)

        if isinstance(value, str) and value.strip():
            parts.append(value.strip())

    return " ".join(parts)


def detect_fallback_cues(scene_text):
    """Detect known sound effects from Hindi/English scene text."""
    normalized_scene = normalize_text(scene_text)
    detected = []

    for effect_name, keywords in EFFECT_KEYWORDS.items():
        for keyword in keywords:
            normalized_keyword = normalize_text(keyword)

            if not normalized_keyword:
                continue

            # Match complete phrases, not arbitrary substrings.
            padded_scene = f" {normalized_scene} "
            padded_keyword = f" {normalized_keyword} "

            if padded_keyword in padded_scene:
                detected.append(effect_name)
                break

    return detected


def get_scene_cues(scene):
    """Combine explicit cues with fallback text detection."""
    explicit = collect_explicit_cues(scene)
    detected = detect_fallback_cues(collect_scene_text(scene))

    return list(dict.fromkeys(explicit + detected))


def get_scene_weight(scene):
    text = collect_scene_text(scene)
    return max(len(text), 1)


def build_scene_timeline(scenes, total_duration_ms):
    if not scenes:
        raise ValueError("No scenes found in story analysis.")

    weights = [get_scene_weight(scene) for scene in scenes]
    total_weight = sum(weights) or len(scenes)

    timeline = []
    current_ms = 0

    for index, (scene, weight) in enumerate(
        zip(scenes, weights),
        start=1,
    ):
        if index == len(scenes):
            end_ms = total_duration_ms
        else:
            estimated = total_duration_ms * weight / total_weight
            end_ms = current_ms + round(estimated)

        end_ms = min(end_ms, total_duration_ms)

        timeline.append({
            "scene_number": scene.get("scene_number", index),
            "start_ms": current_ms,
            "end_ms": end_ms,
            "start_seconds": round(current_ms / 1000, 3),
            "end_seconds": round(end_ms / 1000, 3),
            "duration_seconds": round(
                (end_ms - current_ms) / 1000, 3
            ),
            "sound_cues": get_scene_cues(scene),
            "visual_description": scene.get(
                "visual_description", ""
            ),
        })

        current_ms = end_ms

    if current_ms != total_duration_ms:
        raise RuntimeError(
            "Scene timeline does not cover complete narration."
        )

    return timeline


def find_audio_files(folder):
    if not folder.exists():
        return []

    return sorted(
        path for path in folder.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_FORMATS
    )


def load_audio_file(path):
    try:
        audio = AudioSegment.from_file(str(path))

        if len(audio) <= 0:
            raise ValueError("Empty audio file.")

        return audio.set_channels(2)

    except Exception as error:
        LOGGER.warning("Could not load %s: %s", path, error)
        return None


def fit_audio(audio, target_duration_ms):
    if target_duration_ms <= 0:
        return AudioSegment.empty()

    if len(audio) <= 0:
        return AudioSegment.silent(
            duration=target_duration_ms,
            frame_rate=44100,
        ).set_channels(2)

    repetitions = (target_duration_ms // len(audio)) + 1
    return (audio * repetitions)[:target_duration_ms]


def apply_fade(audio):
    if len(audio) <= 0:
        return audio

    fade_length = min(AUDIO_FADE_MS, len(audio) // 3)

    if fade_length <= 0:
        return audio

    return audio.fade_in(fade_length).fade_out(fade_length)


def choose_background_track(folder, category):
    files = find_audio_files(folder)

    if not files:
        LOGGER.warning("No %s audio found in %s", category, folder)
        return None

    preferred_names = {"default", "main", "background"}

    for path in files:
        if path.stem.lower() in preferred_names:
            return path

    return files[0]


def find_matching_effect(cue, effect_files):
    """Match a cue to the best available audio filename."""
    normalized_cue = normalize_text(cue)

    # Strong match for known canonical effect names.
    for effect_name, keywords in EFFECT_KEYWORDS.items():
        if normalize_text(effect_name) == normalized_cue:
            for path in effect_files:
                if path.stem.lower() == effect_name:
                    return path

    best_match = None
    best_score = 0

    cue_words = set(normalized_cue.split())

    for path in effect_files:
        filename = normalize_text(path.stem)
        filename_words = set(filename.split())

        score = len(cue_words.intersection(filename_words))

        for effect_name, keywords in EFFECT_KEYWORDS.items():
            if path.stem.lower() == effect_name:
                for keyword in keywords:
                    keyword_text = normalize_text(keyword)

                    if keyword_text and keyword_text in normalized_cue:
                        score += 5

        if score > best_score:
            best_score = score
            best_match = path

    return best_match if best_score > 0 else None


def build_effect_matches(timeline, effect_files):
    matches = []

    for scene in timeline:
        scene_effects = []

        for cue in scene["sound_cues"]:
            matching_file = find_matching_effect(cue, effect_files)

            scene_effects.append({
                "cue": cue,
                "asset": str(matching_file) if matching_file else None,
                "status": "matched" if matching_file else "missing",
            })

        matches.append({
            "scene_number": scene["scene_number"],
            "effects": scene_effects,
        })

    return matches


def build_sound_plan(timeline, ambience_path, music_path, effect_matches):
    match_lookup = {
        str(item["scene_number"]): item["effects"]
        for item in effect_matches
    }

    plan = []

    for scene in timeline:
        plan.append({
            **scene,
            "ambience_asset": str(ambience_path) if ambience_path else None,
            "music_asset": str(music_path) if music_path else None,
            "effects": match_lookup.get(
                str(scene["scene_number"]), []
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
    total_duration = len(narration)

    final_mix = (
        narration
        .apply_gain(NARRATION_GAIN)
        .set_channels(2)
    )

    # Continuous ambience.
    if ambience_path:
        ambience = load_audio_file(ambience_path)

        if ambience:
            ambience = fit_audio(ambience, total_duration)
            ambience = apply_fade(ambience).apply_gain(AMBIENCE_GAIN)
            final_mix = final_mix.overlay(ambience)

    # Continuous background music.
    if music_path:
        music = load_audio_file(music_path)

        if music:
            music = fit_audio(music, total_duration)
            music = apply_fade(music).apply_gain(MUSIC_GAIN)
            final_mix = final_mix.overlay(music)

    matched_effects = []
    missing_effects = []
    last_effect_time = {}

    # Scene-based effect placement.
    for scene, match_group in zip(timeline, effect_matches):
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
                    "reason": "no_matching_asset",
                })
                continue

            effect_path = Path(asset)

            # Avoid repeating the same effect too frequently.
            effect_key = effect_path.stem.lower()
            previous_time = last_effect_time.get(effect_key, -MIN_EFFECT_GAP_MS)

            if start_ms - previous_time < MIN_EFFECT_GAP_MS:
                continue

            effect = load_audio_file(effect_path)

            if effect is None:
                missing_effects.append({
                    "scene_number": scene["scene_number"],
                    "cue": cue,
                    "reason": "asset_load_failed",
                })
                continue

            # Place effect around the middle of the scene.
            # This is estimated timing, not speech synchronization.
            offset_ms = start_ms + int(scene_duration * 0.35)
            available_duration = total_duration - offset_ms

            if available_duration <= 0:
                continue

            effect = effect[:min(scene_duration, available_duration)]
            effect = apply_fade(effect).apply_gain(EFFECT_GAIN).set_channels(2)

            if len(effect) <= 0:
                continue

            final_mix = final_mix.overlay(effect, position=offset_ms)
            last_effect_time[effect_key] = offset_ms

            matched_effects.append({
                "scene_number": scene["scene_number"],
                "cue": cue,
                "asset": str(effect_path),
                "start_ms": offset_ms,
                "duration_ms": len(effect),
            })

    # Prevent digital clipping while retaining headroom.
    if final_mix.max_dBFS > -1.0:
        safety_gain = -1.0 - final_mix.max_dBFS
        LOGGER.warning(
            "Applying %.2f dB safety reduction to final mix.",
            safety_gain,
        )
        final_mix = final_mix.apply_gain(safety_gain)

    final_mix = final_mix[:total_duration]

    if len(final_mix) != total_duration:
        raise RuntimeError(
            "Final audio duration differs from narration."
        )

    return final_mix, matched_effects, missing_effects


def main():
    setup_logging()
    prepare_directories()

    LOGGER.info("Starting Sound Designer V3.")

    analysis = load_json(ANALYSIS_FILE)
    scenes = analysis.get("scenes", [])

    if not isinstance(scenes, list) or not scenes:
        raise ValueError("Story analysis contains no valid scenes.")

    narration = load_narration()
    total_duration_ms = len(narration)

    timeline = build_scene_timeline(scenes, total_duration_ms)

    ambience_path = choose_background_track(
        AMBIENCE_DIR, "ambience"
    )
    music_path = choose_background_track(
        MUSIC_DIR, "music"
    )
    effect_files = find_audio_files(EFFECTS_DIR)

    LOGGER.info("Available sound effects: %d", len(effect_files))

    for path in effect_files:
        LOGGER.info("Found effect asset: %s", path.name)

    effect_matches = build_effect_matches(timeline, effect_files)

    sound_plan = build_sound_plan(
        timeline,
        ambience_path,
        music_path,
        effect_matches,
    )
    save_json(sound_plan, PLAN_FILE)

    final_mix, matched, missing = mix_audio(
        narration,
        timeline,
        ambience_path,
        music_path,
        effect_matches,
    )

    FINAL_AUDIO.parent.mkdir(parents=True, exist_ok=True)
    final_mix.export(str(FINAL_AUDIO), format="wav")

    verification = AudioSegment.from_file(str(FINAL_AUDIO))

    if len(verification) != total_duration_ms:
        raise RuntimeError(
            "Exported final audio duration does not match narration."
        )

    if verification.channels != 2:
        raise RuntimeError("Final audio must be stereo.")

    report = {
        "status": "success",
        "version": "V3",
        "total_duration_seconds": round(total_duration_ms / 1000, 3),
        "total_scenes": len(scenes),
        "ambience_asset": str(ambience_path) if ambience_path else None,
        "music_asset": str(music_path) if music_path else None,
        "available_effect_assets": len(effect_files),
        "matched_effect_count": len(matched),
        "missing_effect_count": len(missing),
        "matched_effects": matched,
        "missing_effects": missing,
        "final_audio": str(FINAL_AUDIO),
        "sound_design_plan": str(PLAN_FILE),
        "audio_channels": verification.channels,
        "audio_frame_rate": verification.frame_rate,
        "audio_peak_dbfs": round(verification.max_dBFS, 2),
        "timing_method": "estimated_scene_based_timing",
        "exact_speech_alignment": False,
        "duration_verified": True,
    }

    save_json(report, REPORT_FILE)

    LOGGER.info("-----------------------------------")
    LOGGER.info("SOUND DESIGN V3 COMPLETED")
    LOGGER.info("Final audio: %s", FINAL_AUDIO)
    LOGGER.info("Duration: %.2f seconds", len(verification) / 1000)
    LOGGER.info("Matched effects: %d", len(matched))
    LOGGER.info("Missing effects: %d", len(missing))
    LOGGER.info("-----------------------------------")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("Sound design failed.")
        raise
