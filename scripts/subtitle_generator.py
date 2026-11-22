
"""
Hindi Horror Story Automation
Subtitle Generator

Creates SRT subtitles from Hindi narration text
and generated WAV audio.

Note:
Subtitle timestamps are estimated in V1.
They are not word-level forced alignment.
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


# Subtitle settings
MAX_CHARS_PER_LINE = 42
MAX_LINES_PER_SUBTITLE = 2
MIN_SUBTITLE_DURATION = 1.2
MAX_SUBTITLE_DURATION = 6.0


def setup_logging():
    """Configure subtitle generator logging."""

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
    )


def read_narration():
    """Read generated Hindi narration."""

    if not NARRATION_FILE.exists():
        raise FileNotFoundError(
            f"Narration text not found: {NARRATION_FILE}"
        )

    text = NARRATION_FILE.read_text(
        encoding="utf-8"
    ).strip()

    if not text:
        raise ValueError("Narration text is empty.")

    return text


def get_audio_duration():
    """Read actual WAV audio duration."""

    if not AUDIO_FILE.exists():
        raise FileNotFoundError(
            f"Narration audio not found: {AUDIO_FILE}"
        )

    with wave.open(str(AUDIO_FILE), "rb") as audio:

        frames = audio.getnframes()
        sample_rate = audio.getframerate()

        if sample_rate <= 0 or frames <= 0:
            raise ValueError("Invalid narration WAV file.")

        duration = frames / sample_rate

    LOGGER.info(
        "Audio duration: %.2f seconds",
        duration
    )

    return duration


def split_sentences(text):
    """Split narration into meaningful sentences."""

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


def wrap_text(text, max_chars=MAX_CHARS_PER_LINE):
    """Wrap Hindi text without breaking words."""

    words = text.split()

    lines = []
    current_line = ""

    for word in words:

        if len(word) > max_chars:

            if current_line:
                lines.append(current_line)
                current_line = ""

            # Preserve long words instead of deleting characters.
            lines.append(word)
            continue

        candidate = (
            current_line + " " + word
            if current_line else word
        )

        if len(candidate) <= max_chars:
            current_line = candidate

        else:
            if current_line:
                lines.append(current_line)

            current_line = word

    if current_line:
        lines.append(current_line)

    return lines


def create_subtitle_segments(text):
    """Create readable subtitle segments."""

    sentences = split_sentences(text)

    segments = []

    for sentence in sentences:

        lines = wrap_text(sentence)

        current_lines = []

        for line in lines:

            if len(current_lines) < MAX_LINES_PER_SUBTITLE:
                current_lines.append(line)

            else:
                segments.append("\n".join(current_lines))
                current_lines = [line]

        if current_lines:
            segments.append("\n".join(current_lines))

    if not segments:
        raise ValueError("No subtitle segments generated.")

    return segments


def calculate_weights(segments):
    """Calculate approximate speaking-time weights."""

    weights = []

    for segment in segments:

        visible_text = segment.replace("\n", " ").strip()

        # Character-based approximation, not speech alignment.
        weight = max(len(visible_text), 1)

        # Slight pause allowance for sentence endings.
        if re.search(r"[।.!?]$", visible_text):
            weight *= 1.12

        weights.append(weight)

    return weights


def allocate_durations(segments, total_duration):
    """Allocate estimated subtitle timing across audio."""

    if not segments:
        return []

    weights = calculate_weights(segments)
    total_weight = sum(weights)

    if total_weight <= 0:
        raise ValueError("Invalid subtitle weights.")

    count = len(segments)

    # Keep all timestamps inside actual audio duration.
    minimum = min(
        MIN_SUBTITLE_DURATION,
        total_duration / count
    )

    if minimum <= 0:
        raise ValueError("Invalid audio duration.")

    if count * minimum > total_duration:
        minimum = total_duration / count

    durations = []

    remaining = total_duration
    remaining_weight = total_weight

    for index, weight in enumerate(weights):

        items_left = count - index

        if items_left == 1:
            duration = remaining

        else:
            proportional = (
                remaining * weight / remaining_weight
            )

            minimum_remaining = (
                minimum * (items_left - 1)
            )

            maximum_allowed = (
                remaining - minimum_remaining
            )

            duration = max(
                minimum,
                min(proportional, maximum_allowed)
            )

        durations.append(duration)

        remaining -= duration
        remaining_weight -= weight

    return durations


def format_timestamp(seconds):
    """Convert seconds into SRT timestamp format."""

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


def build_srt(segments, durations):
    """Build complete SRT subtitle content."""

    entries = []
    current_time = 0.0

    for index, (segment, duration) in enumerate(
        zip(segments, durations),
        start=1
    ):

        start_time = current_time
        end_time = current_time + duration

        entry = (
            f"{index}\n"
            f"{format_timestamp(start_time)} --> "
            f"{format_timestamp(end_time)}\n"
            f"{segment}\n"
        )

        entries.append(entry)

        current_time = end_time

    return "\n".join(entries), current_time


def save_subtitles(srt_content):
    """Save final SRT subtitle file."""

    SUBTITLE_DIR.mkdir(parents=True, exist_ok=True)

    with open(
        SRT_FILE,
        "w",
        encoding="utf-8-sig"
    ) as file:
        file.write(srt_content)

    LOGGER.info("SRT saved: %s", SRT_FILE)


def save_report(report):
    """Save subtitle generation report."""

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


def main():
    """Execute subtitle generation."""

    setup_logging()

    LOGGER.info("Starting Hindi subtitle generation.")

    create_project_directories()

    SUBTITLE_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    narration = read_narration()
    audio_duration = get_audio_duration()

    segments = create_subtitle_segments(
        narration
    )

    durations = allocate_durations(
        segments,
        audio_duration
    )

    srt_content, final_time = build_srt(
        segments,
        durations
    )

    save_subtitles(srt_content)

    report = {
        "status": "success",
        "language": "Hindi",
        "audio_file": str(AUDIO_FILE),
        "subtitle_file": str(SRT_FILE),
        "audio_duration_seconds": round(
            audio_duration, 3
        ),
        "subtitle_end_seconds": round(
            final_time, 3
        ),
        "total_subtitles": len(segments),
        "max_chars_per_line": MAX_CHARS_PER_LINE,
        "max_lines_per_subtitle": MAX_LINES_PER_SUBTITLE,
        "timing_method": "estimated_character_weighted",
        "word_level_alignment": False,
    }

    save_report(report)

    LOGGER.info("-----------------------------------")
    LOGGER.info("SUBTITLE GENERATION COMPLETED")
    LOGGER.info("Total subtitles: %s", len(segments))
    LOGGER.info("Audio duration: %.2f seconds", audio_duration)
    LOGGER.info("Subtitle file: %s", SRT_FILE)
    LOGGER.info("-----------------------------------")


if __name__ == "__main__":

    try:
        main()

    except Exception:

        logging.exception(
            "Subtitle generation failed."
        )

        raise
