
"""
Hindi Horror Story Automation
Subtitle Generator

Creates readable SRT subtitles from Hindi narration text
and generated WAV audio.

Timing is estimated, not word-level forced alignment.
"""

import json
import logging
import re
import wave

from pathlib import Path

from scripts.utils import OUTPUT_DIR, create_project_directories


LOGGER = logging.getLogger(__name__)

OUTPUT_ROOT = Path(OUTPUT_DIR)

AUDIO_DIR = OUTPUT_ROOT / "audio"
SUBTITLE_DIR = OUTPUT_ROOT / "subtitles"

NARRATION_FILE = AUDIO_DIR / "narration.txt"
AUDIO_FILE = AUDIO_DIR / "narration.wav"

SRT_FILE = SUBTITLE_DIR / "narration.srt"
REPORT_FILE = SUBTITLE_DIR / "subtitle_report.json"


# --------------------------------------------------
# SETTINGS
# --------------------------------------------------

MAX_CHARS_PER_LINE = 42
MAX_LINES_PER_SUBTITLE = 2

MIN_SUBTITLE_DURATION = 1.2
MAX_SUBTITLE_DURATION = 6.0

TARGET_CHARACTERS_PER_SECOND = 16.0
SENTENCE_PAUSE_FACTOR = 1.12


# --------------------------------------------------
# LOGGING
# --------------------------------------------------

def setup_logging():
    SUBTITLE_DIR.mkdir(parents=True, exist_ok=True)

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
# INPUT
# --------------------------------------------------

def read_narration():
    if not NARRATION_FILE.exists():
        raise FileNotFoundError(
            f"Narration text not found: {NARRATION_FILE}"
        )

    text = NARRATION_FILE.read_text(
        encoding="utf-8-sig"
    ).strip()

    if not text:
        raise ValueError("Narration text is empty.")

    return re.sub(r"\s+", " ", text).strip()


def get_audio_duration():
    if not AUDIO_FILE.exists():
        raise FileNotFoundError(
            f"Narration audio not found: {AUDIO_FILE}"
        )

    try:
        with wave.open(str(AUDIO_FILE), "rb") as audio:
            frames = audio.getnframes()
            sample_rate = audio.getframerate()

            if frames <= 0 or sample_rate <= 0:
                raise ValueError("Invalid narration WAV file.")

            duration = frames / sample_rate

    except wave.Error as error:
        raise ValueError(
            f"Unable to read narration WAV: {error}"
        ) from error

    if duration <= 0:
        raise ValueError("Invalid audio duration.")

    LOGGER.info("Audio duration: %.2f seconds", duration)

    return duration


# --------------------------------------------------
# TEXT PROCESSING
# --------------------------------------------------

def split_sentences(text):
    text = re.sub(r"\s+", " ", text).strip()

    sentences = re.split(
        r"(?<=[।.!?])\s+",
        text
    )

    return [
        sentence.strip()
        for sentence in sentences
        if sentence.strip()
    ]


def wrap_text(text):
    words = text.split()

    lines = []
    current_line = ""

    for word in words:

        if len(word) > MAX_CHARS_PER_LINE:
            raise ValueError(
                "A single word exceeds the subtitle line limit: "
                f"{word[:60]}"
            )

        candidate = (
            f"{current_line} {word}"
            if current_line
            else word
        )

        if len(candidate) <= MAX_CHARS_PER_LINE:
            current_line = candidate

        else:
            if current_line:
                lines.append(current_line)

            current_line = word

    if current_line:
        lines.append(current_line)

    return lines


def create_subtitle_segments(text):
    sentences = split_sentences(text)

    if not sentences:
        raise ValueError("No sentences found.")

    segments = []

    for sentence in sentences:
        lines = wrap_text(sentence)

        for index in range(
            0,
            len(lines),
            MAX_LINES_PER_SUBTITLE
        ):
            selected_lines = lines[
                index:index + MAX_LINES_PER_SUBTITLE
            ]

            segments.append("\n".join(selected_lines))

    if not segments:
        raise ValueError("No subtitle segments generated.")

    return segments


# --------------------------------------------------
# TIMING WEIGHTS
# --------------------------------------------------

def get_visible_character_count(segment):
    return len(
        segment.replace("\n", " ").strip()
    )


def calculate_weights(segments):
    weights = []

    for segment in segments:
        count = get_visible_character_count(segment)

        if count <= 0:
            raise ValueError("Empty subtitle segment found.")

        weight = float(count)

        visible_text = segment.replace("\n", " ").strip()

        if re.search(r"[।.!?]$", visible_text):
            weight *= 1.12

        weights.append(weight)

    return weights


# --------------------------------------------------
# AUDIO-BASED DURATION ALLOCATION
# --------------------------------------------------

def allocate_durations(segments, total_duration):
    """
    Allocate all subtitle timestamps proportionally
    inside the actual narration duration.

    The previous version raised an error when the sum
    of minimum subtitle durations exceeded audio length.

    This version scales all durations proportionally
    to the available audio duration instead.

    If the resulting reading speed is too fast, it is
    reported as a warning rather than stopping generation.
    """

    if not segments:
        raise ValueError("No subtitle segments available.")

    if total_duration <= 0:
        raise ValueError("Audio duration must be positive.")

    weights = calculate_weights(segments)
    total_weight = sum(weights)

    if total_weight <= 0:
        raise ValueError("Invalid subtitle weights.")

    durations = []

    for weight in weights:
        duration = (
            total_duration * weight / total_weight
        )

        durations.append(duration)

    # Correct floating-point accumulation so the total
    # duration matches the WAV duration.
    if durations:
        durations[-1] += (
            total_duration - sum(durations)
        )

    short_count = sum(
        duration < MIN_SUBTITLE_DURATION
        for duration in durations
    )

    long_count = sum(
        duration > MAX_SUBTITLE_DURATION
        for duration in durations
    )

    if short_count:
        LOGGER.warning(
            "%s subtitle(s) are shorter than %.1f seconds. "
            "Audio duration has priority.",
            short_count,
            MIN_SUBTITLE_DURATION
        )

    if long_count:
        LOGGER.warning(
            "%s subtitle(s) exceed %.1f seconds. "
            "Check subtitle segmentation.",
            long_count,
            MAX_SUBTITLE_DURATION
        )

    return durations


# --------------------------------------------------
# TIMESTAMPS
# --------------------------------------------------

def format_timestamp(seconds):
    if seconds < 0:
        raise ValueError("Negative timestamp.")

    milliseconds = round(seconds * 1000)

    hours = milliseconds // 3600000
    milliseconds %= 3600000

    minutes = milliseconds // 60000
    milliseconds %= 60000

    seconds_part = milliseconds // 1000
    milliseconds %= 1000

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{seconds_part:02d},"
        f"{milliseconds:03d}"
    )


# --------------------------------------------------
# SRT BUILD
# --------------------------------------------------

def build_srt(segments, durations, audio_duration):
    if len(segments) != len(durations):
        raise ValueError(
            "Subtitle segments and durations do not match."
        )

    entries = []
    current_time_ms = 0
    audio_limit_ms = round(audio_duration * 1000)

    for index, (segment, duration) in enumerate(
        zip(segments, durations),
        start=1
    ):
        start_time_ms = current_time_ms
        duration_ms = round(duration * 1000)

        if duration_ms <= 0:
            raise ValueError(
                f"Subtitle {index} has zero duration."
            )

        end_time_ms = start_time_ms + duration_ms

        # Protect against rounding overflow.
        if end_time_ms > audio_limit_ms:
            end_time_ms = audio_limit_ms

        if end_time_ms <= start_time_ms:
            raise ValueError(
                f"Invalid timestamp for subtitle {index}."
            )

        entry = (
            f"{index}\n"
            f"{format_timestamp(start_time_ms / 1000)} --> "
            f"{format_timestamp(end_time_ms / 1000)}\n"
            f"{segment}\n"
        )

        entries.append(entry)

        current_time_ms = end_time_ms

    return "\n".join(entries), current_time_ms / 1000


# --------------------------------------------------
# VALIDATION
# --------------------------------------------------

def validate_subtitles(
    segments,
    durations,
    audio_duration,
    final_time
):
    if not segments:
        raise ValueError("No subtitles generated.")

    if len(segments) != len(durations):
        raise ValueError("Subtitle count mismatch.")

    previous_end_ms = 0
    audio_limit_ms = round(audio_duration * 1000)

    for index, (segment, duration) in enumerate(
        zip(segments, durations),
        start=1
    ):
        lines = segment.split("\n")

        if len(lines) > MAX_LINES_PER_SUBTITLE:
            raise ValueError(
                f"Subtitle {index} has too many lines."
            )

        for line in lines:
            if len(line) > MAX_CHARS_PER_LINE:
                raise ValueError(
                    f"Subtitle {index} exceeds line length."
                )

        duration_ms = round(duration * 1000)

        start_ms = previous_end_ms
        end_ms = start_ms + duration_ms

        if duration_ms <= 0:
            raise ValueError(
                f"Subtitle {index} has invalid duration."
            )

        if end_ms > audio_limit_ms:
            raise ValueError(
                f"Subtitle {index} exceeds audio duration."
            )

        previous_end_ms = end_ms

    if final_time > audio_duration + 0.001:
        raise ValueError(
            "Final subtitle exceeds narration duration."
        )

    return True


# --------------------------------------------------
# SAVE FILES
# --------------------------------------------------

def save_subtitles(srt_content):
    SUBTITLE_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    SRT_FILE.write_text(
        srt_content,
        encoding="utf-8-sig"
    )

    if not SRT_FILE.exists() or SRT_FILE.stat().st_size == 0:
        raise RuntimeError("SRT file was not saved.")

    LOGGER.info("SRT saved: %s", SRT_FILE)


def save_report(report):
    with open(
        REPORT_FILE,
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(
            report,
            file,
            ensure_ascii=False,
            indent=4
        )

    LOGGER.info("Report saved: %s", REPORT_FILE)


# --------------------------------------------------
# MAIN
# --------------------------------------------------

def main():
    setup_logging()

    LOGGER.info("Starting Hindi subtitle generation.")

    create_project_directories()

    SUBTITLE_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    narration = read_narration()
    audio_duration = get_audio_duration()

    segments = create_subtitle_segments(narration)

    durations = allocate_durations(
        segments,
        audio_duration
    )

    srt_content, final_time = build_srt(
        segments,
        durations,
        audio_duration
    )

    validate_subtitles(
        segments,
        durations,
        audio_duration,
        final_time
    )

    save_subtitles(srt_content)

    character_counts = [
        get_visible_character_count(segment)
        for segment in segments
    ]

    actual_speeds = [
        count / duration
        for count, duration in zip(
            character_counts,
            durations
        )
    ]

    short_count = sum(
        duration < MIN_SUBTITLE_DURATION
        for duration in durations
    )

    long_count = sum(
        duration > MAX_SUBTITLE_DURATION
        for duration in durations
    )

    fast_count = sum(
        speed > TARGET_CHARACTERS_PER_SECOND
        for speed in actual_speeds
    )

    report = {
        "status": "success",
        "language": "Hindi",
        "audio_file": str(AUDIO_FILE),
        "subtitle_file": str(SRT_FILE),
        "audio_duration_seconds": round(audio_duration, 3),
        "subtitle_end_seconds": round(final_time, 3),
        "unsubtitled_audio_seconds": round(
            max(0.0, audio_duration - final_time),
            3
        ),
        "total_subtitles": len(segments),
        "max_chars_per_line": MAX_CHARS_PER_LINE,
        "max_lines_per_subtitle": MAX_LINES_PER_SUBTITLE,
        "minimum_target_duration": MIN_SUBTITLE_DURATION,
        "maximum_target_duration": MAX_SUBTITLE_DURATION,
        "target_characters_per_second": TARGET_CHARACTERS_PER_SECOND,
        "maximum_actual_characters_per_second": round(
            max(actual_speeds),
            2
        ),
        "subtitles_below_minimum_duration": short_count,
        "subtitles_above_maximum_duration": long_count,
        "subtitles_above_target_reading_speed": fast_count,
        "timing_method": "audio_duration_proportional_character_weighted",
        "word_level_alignment": False,
        "exact_speech_synchronization": False,
        "reading_speed_target_checked": True,
        "audio_duration_fit": True,
        "status_note": (
            "Estimated timing. Durations are proportionally "
            "allocated within the audio. Short durations or "
            "high reading speed are reported, not treated as "
            "fatal errors."
        ),
    }

    save_report(report)

    LOGGER.info("-----------------------------------")
    LOGGER.info("SUBTITLE GENERATION COMPLETED")
    LOGGER.info("Total subtitles: %s", len(segments))
    LOGGER.info("Audio duration: %.2f seconds", audio_duration)
    LOGGER.info("Subtitle end: %.2f seconds", final_time)
    LOGGER.info("Short subtitles: %s", short_count)
    LOGGER.info("Fast reading subtitles: %s", fast_count)
    LOGGER.info("SRT file: %s", SRT_FILE)
    LOGGER.info("-----------------------------------")


if __name__ == "__main__":
    try:
        main()

    except Exception:
        logging.exception(
            "Subtitle generation failed."
        )
        raise
