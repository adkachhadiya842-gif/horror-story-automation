
"""
Hindi Horror Story Automation
Subtitle Generator - V4

Features:
- Uses the same audio priority as video_editor.py.
- Prefers final_audio.wav, then narration.wav and MP3 fallbacks.
- Reads subtitle text from narration.txt.
- Maximum 42 characters per line.
- Maximum 2 lines per subtitle.
- Maximum 6 seconds per subtitle.
- Targets approximately 16 characters per second.
- Adapts subtitle timing to available audio duration.
- Does not fail only because ideal subtitle durations exceed audio.
- Allows remaining audio to play without subtitles.
- Writes UTF-8 BOM SRT for Hindi compatibility.
- Validates timestamps and generates a detailed report.

Timing is estimated. This is not word-level forced alignment.
"""

import json
import logging
import math
import re
import shutil
import subprocess
import unicodedata

from pathlib import Path

from scripts.utils import OUTPUT_DIR, create_project_directories


# --------------------------------------------------
# PATHS
# --------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = Path(OUTPUT_DIR)

AUDIO_DIR = OUTPUT / "audio"
SUBTITLE_DIR = OUTPUT / "subtitles"

NARRATION_FILE = AUDIO_DIR / "narration.txt"

SRT_FILE = SUBTITLE_DIR / "narration.srt"
REPORT_FILE = SUBTITLE_DIR / "subtitle_report.json"

LOGGER = logging.getLogger("horror_subtitle_generator")


# --------------------------------------------------
# SETTINGS
# --------------------------------------------------

MAX_LINE_CHARS = 42
MAX_LINES = 2

MIN_SUBTITLE_DURATION = 1.2
MAX_SUBTITLE_DURATION = 6.0

TARGET_CHARACTERS_PER_SECOND = 16.0

SENTENCE_PAUSE_SECONDS = 0.25

MIN_RENDERABLE_DURATION_MS = 10

AUDIO_CANDIDATES = [
    AUDIO_DIR / "final_audio.wav",
    AUDIO_DIR / "narration.wav",
    AUDIO_DIR / "final_audio.mp3",
    AUDIO_DIR / "narration.mp3",
]


# --------------------------------------------------
# LOGGING
# --------------------------------------------------

def setup_logging():
    SUBTITLE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                SUBTITLE_DIR / "subtitle_generator.log",
                encoding="utf-8",
            ),
        ],
        force=True,
    )


# --------------------------------------------------
# COMMANDS
# --------------------------------------------------

def check_ffprobe():
    if not shutil.which("ffprobe"):
        raise RuntimeError(
            "ffprobe is unavailable. Install FFmpeg first."
        )


def run_ffprobe(audio_path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries",
            "format=duration:"
            "stream=codec_type,duration",
            "-of", "json",
            str(audio_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Could not inspect audio file: "
            + result.stderr[-3000:]
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "Invalid ffprobe response."
        ) from error

    audio_stream = next(
        (
            stream
            for stream in data.get("streams", [])
            if stream.get("codec_type") == "audio"
        ),
        None,
    )

    if not audio_stream:
        raise RuntimeError(
            f"No audio stream found in {audio_path.name}."
        )

    duration_value = audio_stream.get("duration")

    if duration_value in (None, "N/A"):
        duration_value = (
            data.get("format", {}).get("duration")
        )

    try:
        duration = float(duration_value)
    except (TypeError, ValueError):
        raise RuntimeError(
            f"Invalid audio duration: {audio_path.name}"
        )

    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError(
            f"Audio duration is invalid: {audio_path.name}"
        )

    return duration


# --------------------------------------------------
# AUDIO SELECTION
# --------------------------------------------------

def find_audio():
    """
    Must follow video_editor.py audio selection order.
    """

    for path in AUDIO_CANDIDATES:
        if path.is_file() and path.stat().st_size > 0:
            LOGGER.info(
                "Selected audio: %s",
                path.name,
            )
            return path

    raise FileNotFoundError(
        "No usable audio file found in output/audio/. "
        "Expected final_audio.wav or narration.wav."
    )


# --------------------------------------------------
# NARRATION TEXT
# --------------------------------------------------

def load_narration():
    if not NARRATION_FILE.is_file():
        raise FileNotFoundError(
            f"Narration text missing: {NARRATION_FILE}"
        )

    text = NARRATION_FILE.read_text(
        encoding="utf-8-sig"
    )

    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", " ", text)
    text = text.strip()

    if not text:
        raise ValueError(
            "Narration text file is empty."
        )

    return text


# --------------------------------------------------
# GRAPHEME HANDLING
# --------------------------------------------------

def split_graphemes(text):
    """
    Keep common Devanagari combining marks and virama
    sequences together when splitting unusually long words.
    """

    clusters = []
    current = ""

    for character in text:
        code = ord(character)

        is_combining = (
            unicodedata.combining(character) != 0
            or 0x0900 <= code <= 0x0903
            or 0x093A <= code <= 0x094F
            or 0x0951 <= code <= 0x0957
            or 0x0962 <= code <= 0x0963
        )

        if not current:
            current = character

        elif (
            is_combining
            or current.endswith("\u094d")
            or current.endswith("\u200d")
        ):
            current += character

        else:
            clusters.append(current)
            current = character

    if current:
        clusters.append(current)

    return clusters


def split_long_word(word, maximum):
    """
    Split a pathological long token without splitting
    common Devanagari combining sequences.
    """

    clusters = split_graphemes(word)

    parts = []
    current = ""

    for cluster in clusters:
        if len(current) + len(cluster) > maximum and current:
            parts.append(current)
            current = cluster
        else:
            current += cluster

    if current:
        parts.append(current)

    return parts


# --------------------------------------------------
# SENTENCE SPLITTING
# --------------------------------------------------

def split_sentences(text):
    """
    Preserve Hindi danda and common English punctuation.
    """

    parts = re.split(
        r"(?<=[।.!?])\s+",
        text,
    )

    return [
        part.strip()
        for part in parts
        if part.strip()
    ]


# --------------------------------------------------
# LINE WRAPPING
# --------------------------------------------------

def wrap_words(text):
    """
    Create lines that do not exceed MAX_LINE_CHARS.
    """

    words = text.split()
    expanded_words = []

    for word in words:
        if len(word) > MAX_LINE_CHARS:
            expanded_words.extend(
                split_long_word(
                    word,
                    MAX_LINE_CHARS,
                )
            )
        else:
            expanded_words.append(word)

    lines = []
    current = ""

    for word in expanded_words:
        candidate = (
            f"{current} {word}".strip()
        )

        if len(candidate) <= MAX_LINE_CHARS:
            current = candidate
        else:
            if current:
                lines.append(current)

            current = word

    if current:
        lines.append(current)

    return lines


# --------------------------------------------------
# SUBTITLE SEGMENTS
# --------------------------------------------------

def build_segments(text):
    """
    Create subtitle blocks with at most two lines.
    """

    sentences = split_sentences(text)

    segments = []

    current_lines = []

    for sentence in sentences:
        lines = wrap_words(sentence)

        for line in lines:
            if not current_lines:
                current_lines = [line]
                continue

            if (
                len(current_lines) < MAX_LINES
                and len(current_lines[-1]) + 1 + len(line)
                <= MAX_LINE_CHARS
            ):
                current_lines[-1] += " " + line

            elif len(current_lines) < MAX_LINES:
                current_lines.append(line)

            else:
                segments.append({
                    "text": "\n".join(current_lines),
                    "character_count": sum(
                        len(item)
                        for item in current_lines
                    ),
                    "line_count": len(current_lines),
                })

                current_lines = [line]

    if current_lines:
        segments.append({
            "text": "\n".join(current_lines),
            "character_count": sum(
                len(item)
                for item in current_lines
            ),
            "line_count": len(current_lines),
        })

    if not segments:
        raise ValueError(
            "Could not create subtitle segments."
        )

    return segments


# --------------------------------------------------
# NATURAL DURATION ESTIMATION
# --------------------------------------------------

def calculate_ideal_duration(segment):
    """
    Estimate duration from visible character count.

    This is a heuristic, not speech recognition.
    """

    characters = max(
        segment["character_count"],
        1,
    )

    duration = (
        characters / TARGET_CHARACTERS_PER_SECOND
    ) + SENTENCE_PAUSE_SECONDS

    duration = max(
        MIN_SUBTITLE_DURATION,
        duration,
    )

    duration = min(
        MAX_SUBTITLE_DURATION,
        duration,
    )

    return duration


# --------------------------------------------------
# TIMING ALLOCATION
# --------------------------------------------------

def allocate_durations(segments, audio_duration):
    """
    Assign subtitle durations without exceeding audio length.

    Rules:
    - Never exceed MAX_SUBTITLE_DURATION.
    - Prefer MIN_SUBTITLE_DURATION where possible.
    - If the audio is too short, compress durations and warn.
    - If audio is longer, expand captions up to the maximum.
    - Remaining audio may have no subtitles.
    """

    if audio_duration <= 0:
        raise ValueError("Audio duration must be positive.")

    count = len(segments)

    available_ms = max(
        int(round(audio_duration * 1000)),
        1,
    )

    maximum_ms = int(
        MAX_SUBTITLE_DURATION * 1000
    )

    minimum_ms = int(
        MIN_SUBTITLE_DURATION * 1000
    )

    if available_ms < count * MIN_RENDERABLE_DURATION_MS:
        raise ValueError(
            "Audio is too short to display every subtitle "
            "even at the minimum renderable timestamp."
        )

    ideal_ms = [
        int(round(
            calculate_ideal_duration(segment) * 1000
        ))
        for segment in segments
    ]

    durations = [
        min(value, maximum_ms)
        for value in ideal_ms
    ]

    natural_total_ms = sum(durations)

    warnings = []

    # Case 1: Captions need more time than the audio has.
    if natural_total_ms > available_ms:
        scale = available_ms / natural_total_ms

        durations = [
            max(
                MIN_RENDERABLE_DURATION_MS,
                int(round(value * scale)),
            )
            for value in durations
        ]

        warnings.append(
            "Ideal caption durations exceeded audio duration; "
            "durations were compressed."
        )

    # Case 2: More audio time is available.
    # Expand captions only up to MAX_SUBTITLE_DURATION.
    elif natural_total_ms < available_ms:
        remaining_ms = available_ms - natural_total_ms

        while remaining_ms > 0:
            eligible = [
                index
                for index, duration in enumerate(durations)
                if duration < maximum_ms
            ]

            if not eligible:
                break

            share = max(
                1,
                remaining_ms // len(eligible),
            )

            changed = 0

            for index in eligible:
                capacity = maximum_ms - durations[index]

                addition = min(
                    capacity,
                    share,
                    remaining_ms,
                )

                if addition <= 0:
                    continue

                durations[index] += addition
                remaining_ms -= addition
                changed += addition

                if remaining_ms <= 0:
                    break

            if changed == 0:
                break

    # Keep every timestamp renderable and inside the audio.
    total_allocated = sum(durations)

    if total_allocated > available_ms:
        overflow = total_allocated - available_ms

        for index in range(len(durations) - 1, -1, -1):
            reducible = max(
                0,
                durations[index] - MIN_RENDERABLE_DURATION_MS,
            )

            reduction = min(
                reducible,
                overflow,
            )

            durations[index] -= reduction
            overflow -= reduction

            if overflow <= 0:
                break

        if overflow > 0:
            raise RuntimeError(
                "Unable to fit subtitle timestamps into audio."
            )

    if any(
        duration > maximum_ms
        for duration in durations
    ):
        raise RuntimeError(
            "Maximum subtitle duration validation failed."
        )

    if any(
        duration <= 0
        for duration in durations
    ):
        raise RuntimeError(
            "Invalid subtitle duration generated."
        )

    actual_total_ms = sum(durations)

    if actual_total_ms < available_ms:
        warnings.append(
            "Some audio time remains without subtitles."
        )

    if any(
        duration < minimum_ms
        for duration in durations
    ):
        warnings.append(
            "Some subtitles are shorter than the preferred "
            "minimum duration because audio time is limited."
        )

    return durations, warnings


# --------------------------------------------------
# SRT FORMATTING
# --------------------------------------------------

def format_srt_time(milliseconds):
    milliseconds = max(
        0,
        int(milliseconds),
    )

    hours = milliseconds // 3_600_000
    milliseconds %= 3_600_000

    minutes = milliseconds // 60_000
    milliseconds %= 60_000

    seconds = milliseconds // 1000
    milliseconds %= 1000

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{seconds:02d},"
        f"{milliseconds:03d}"
    )


def build_srt(segments, durations, audio_duration):
    """
    Create sequential, non-overlapping subtitle entries.
    """

    blocks = []
    current_ms = 0

    audio_limit_ms = int(
        round(audio_duration * 1000)
    )

    for index, (segment, duration_ms) in enumerate(
        zip(segments, durations),
        start=1,
    ):
        start_ms = current_ms
        end_ms = start_ms + duration_ms

        if end_ms > audio_limit_ms:
            end_ms = audio_limit_ms

        if end_ms <= start_ms:
            raise RuntimeError(
                "Invalid subtitle timestamp generated."
            )

        block = (
            f"{index}\n"
            f"{format_srt_time(start_ms)} --> "
            f"{format_srt_time(end_ms)}\n"
            f"{segment['text']}"
        )

        blocks.append(block)
        current_ms = end_ms

    return "\n\n".join(blocks) + "\n"


# --------------------------------------------------
# SRT VALIDATION
# --------------------------------------------------

def parse_srt_timestamp(value):
    match = re.fullmatch(
        r"(\d{2,}):(\d{2}):(\d{2}),(\d{3})",
        value.strip(),
    )

    if not match:
        raise ValueError(
            f"Invalid SRT timestamp: {value}"
        )

    hours, minutes, seconds, milliseconds = map(
        int,
        match.groups(),
    )

    return (
        hours * 3_600_000
        + minutes * 60_000
        + seconds * 1000
        + milliseconds
    )


def validate_srt_content(srt_content, audio_duration):
    blocks = re.split(
        r"\n\s*\n",
        srt_content.strip(),
    )

    previous_end = 0
    maximum_observed_duration = 0
    maximum_observed_cps = 0.0

    audio_limit_ms = int(
        round(audio_duration * 1000)
    )

    for expected_index, block in enumerate(
        blocks,
        start=1,
    ):
        lines = block.splitlines()

        if len(lines) < 3:
            raise ValueError(
                f"Malformed SRT block: {expected_index}"
            )

        if lines[0].strip() != str(expected_index):
            raise ValueError(
                "SRT subtitle indexes are not sequential."
            )

        timestamps = lines[1].split("-->")

        if len(timestamps) != 2:
            raise ValueError(
                f"Invalid timestamp block: {expected_index}"
            )

        start_ms = parse_srt_timestamp(
            timestamps[0]
        )

        end_ms = parse_srt_timestamp(
            timestamps[1]
        )

        duration_ms = end_ms - start_ms

        if duration_ms <= 0:
            raise ValueError(
                f"Invalid subtitle duration: {expected_index}"
            )

        if duration_ms > MAX_SUBTITLE_DURATION * 1000:
            raise ValueError(
                f"Subtitle exceeds {MAX_SUBTITLE_DURATION} seconds: "
                f"{expected_index}"
            )

        if start_ms < previous_end:
            raise ValueError(
                f"Subtitle overlap detected: {expected_index}"
            )

        if end_ms > audio_limit_ms:
            raise ValueError(
                f"Subtitle exceeds audio duration: {expected_index}"
            )

        subtitle_lines = lines[2:]

        if len(subtitle_lines) > MAX_LINES:
            raise ValueError(
                f"Subtitle exceeds maximum line count: {expected_index}"
            )

        if not any(line.strip() for line in subtitle_lines):
            raise ValueError(
                f"Empty subtitle: {expected_index}"
            )

        for line in subtitle_lines:
            if len(line) > MAX_LINE_CHARS:
                raise ValueError(
                    f"Subtitle line exceeds character limit: "
                    f"{expected_index}"
                )

        character_count = sum(
            len(line)
            for line in subtitle_lines
        )

        cps = character_count / (duration_ms / 1000)

        maximum_observed_cps = max(
            maximum_observed_cps,
            cps,
        )

        maximum_observed_duration = max(
            maximum_observed_duration,
            duration_ms,
        )

        previous_end = end_ms

    return {
        "subtitle_count": len(blocks),
        "last_subtitle_end_ms": previous_end,
        "last_subtitle_end_seconds": round(
            previous_end / 1000,
            3,
        ),
        "unsubtitled_audio_seconds": round(
            max(0, audio_limit_ms - previous_end) / 1000,
            3,
        ),
        "maximum_subtitle_duration_seconds": round(
            maximum_observed_duration / 1000,
            3,
        ),
        "maximum_observed_characters_per_second": round(
            maximum_observed_cps,
            2,
        ),
        "line_limit_verified": True,
        "line_count_limit_verified": True,
        "maximum_duration_verified": True,
        "audio_boundary_verified": True,
    }


# --------------------------------------------------
# REPORT
# --------------------------------------------------

def save_report(data):
    SUBTITLE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    with open(
        REPORT_FILE,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=4,
        )


# --------------------------------------------------
# MAIN
# --------------------------------------------------

def main():
    setup_logging()
    create_project_directories()

    AUDIO_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    SUBTITLE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    LOGGER.info("Starting Subtitle Generator V4.")

    check_ffprobe()

    audio_path = find_audio()
    audio_duration = run_ffprobe(audio_path)

    narration_text = load_narration()

    segments = build_segments(narration_text)

    LOGGER.info(
        "Generated %d subtitle segments.",
        len(segments),
    )

    durations, allocation_warnings = allocate_durations(
        segments,
        audio_duration,
    )

    srt_content = build_srt(
        segments,
        durations,
        audio_duration,
    )

    validation = validate_srt_content(
        srt_content,
        audio_duration,
    )

    # UTF-8 BOM improves compatibility with Hindi subtitles.
    SRT_FILE.write_text(
        "\ufeff" + srt_content,
        encoding="utf-8",
    )

    report = {
        "status": "success",
        "version": "V4",
        "audio_file": str(audio_path),
        "audio_duration_seconds": round(
            audio_duration,
            3,
        ),
        "narration_text_file": str(NARRATION_FILE),
        "subtitle_file": str(SRT_FILE),
        "subtitle_count": validation["subtitle_count"],
        "max_line_characters": MAX_LINE_CHARS,
        "max_lines_per_subtitle": MAX_LINES,
        "minimum_preferred_duration_seconds": (
            MIN_SUBTITLE_DURATION
        ),
        "maximum_duration_seconds": (
            MAX_SUBTITLE_DURATION
        ),
        "target_characters_per_second": (
            TARGET_CHARACTERS_PER_SECOND
        ),
        "maximum_observed_characters_per_second": (
            validation[
                "maximum_observed_characters_per_second"
            ]
        ),
        "maximum_observed_duration_seconds": (
            validation[
                "maximum_subtitle_duration_seconds"
            ]
        ),
        "last_subtitle_end_seconds": (
            validation[
                "last_subtitle_end_seconds"
            ]
        ),
        "unsubtitled_audio_seconds": (
            validation[
                "unsubtitled_audio_seconds"
            ]
        ),
        "timing_method": (
            "estimated_character_weighted_duration"
        ),
        "word_level_alignment": False,
        "audio_selected_using_video_editor_priority": True,
        "line_limit_verified": True,
        "line_count_limit_verified": True,
        "maximum_duration_verified": True,
        "audio_boundary_verified": True,
        "warnings": allocation_warnings,
    }

    save_report(report)

    LOGGER.info("-----------------------------------")
    LOGGER.info("SUBTITLE GENERATION COMPLETED")
    LOGGER.info("Audio: %s", audio_path.name)
    LOGGER.info("Audio duration: %.2f seconds", audio_duration)
    LOGGER.info("Subtitle count: %d", len(segments))
    LOGGER.info(
        "Last subtitle ends: %.2f seconds",
        validation["last_subtitle_end_seconds"],
    )
    LOGGER.info(
        "Unsubtitled audio: %.2f seconds",
        validation["unsubtitled_audio_seconds"],
    )
    LOGGER.info("SRT: %s", SRT_FILE)
    LOGGER.info("Report: %s", REPORT_FILE)

    for warning in allocation_warnings:
        LOGGER.warning(warning)

    LOGGER.info("-----------------------------------")

    return SRT_FILE


if __name__ == "__main__":
    try:
        main()
    except Exception:
        LOGGER.exception(
            "Subtitle generation failed."
        )
        raise
