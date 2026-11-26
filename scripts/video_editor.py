
import json
import re
import shutil
import subprocess
import sys
import unicodedata

from datetime import datetime, timezone
from pathlib import Path

from scripts.utils import OUTPUT_DIR, logger
from scripts.validators import run_all_validations


BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"

VIDEO_FILE = OUTPUT_DIR / "final_video.mp4"
THUMBNAIL_FILE = OUTPUT_DIR / "thumbnail" / "thumbnail.png"
SUBTITLE_FILE = OUTPUT_DIR / "subtitles" / "narration.srt"
FINAL_AUDIO_FILE = OUTPUT_DIR / "audio" / "final_audio.wav"

REPORT_FILE = OUTPUT_DIR / "video_edit_report.json"

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
VIDEO_FPS = 24
VIDEO_CODEC = "libx264"
AUDIO_CODEC = "aac"
AUDIO_BITRATE = "192k"

FONT_FAMILY = "Noto Sans Devanagari"
FONT_SIZE = 24

VIDEO_PRESET = "medium"
VIDEO_CRF = 20

SUBTITLE_OUTLINE = 2
SUBTITLE_SHADOW = 1
SUBTITLE_MARGIN = 45


def run_command(command, description="Command"):
    """Execute a system command and return its output."""

    logger.info(description)

    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        return result

    except subprocess.CalledProcessError as error:
        stderr = error.stderr or ""
        stdout = error.stdout or ""

        logger.error(
            f"{description} failed.\n"
            f"STDOUT:\n{stdout}\n"
            f"STDERR:\n{stderr}"
        )

        raise RuntimeError(
            f"{description} failed: {stderr[-3000:]}"
        ) from error


def check_ffmpeg():
    """Check whether FFmpeg and FFprobe are installed."""

    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg is not installed.")

    if not shutil.which("ffprobe"):
        raise RuntimeError("FFprobe is not installed.")

    result = run_command(
        ["ffmpeg", "-version"],
        "Checking FFmpeg installation",
    )

    logger.info(result.stdout.splitlines()[0])

    return True


def get_font_file():
    """
    Find the actual Noto Sans Devanagari font file
    through fontconfig.
    """

    if not shutil.which("fc-match"):
        raise RuntimeError(
            "fontconfig is missing. Install fontconfig and Noto fonts."
        )

    result = run_command(
        [
            "fc-match",
            "-f",
            "%{file}\\n",
            FONT_FAMILY,
        ],
        "Detecting Hindi subtitle font",
    )

    font_path_text = result.stdout.strip().splitlines()

    if not font_path_text:
        raise RuntimeError(
            "Could not detect Noto Sans Devanagari font."
        )

    font_path = Path(font_path_text[0]).resolve()

    if not font_path.is_file():
        raise RuntimeError(
            f"Hindi font file does not exist: {font_path}"
        )

    logger.info(f"Hindi font detected: {font_path}")

    return font_path


def check_subtitle_support():
    """
    Verify FFmpeg subtitle filter and Hindi font support.
    Return the discovered font file.
    """

    check_ffmpeg()

    filter_result = run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-filters",
        ],
        "Checking FFmpeg filters",
    )

    if not re.search(
        r"\bsubtitles\b",
        filter_result.stdout,
    ):
        raise RuntimeError(
            "FFmpeg subtitles filter is unavailable. "
            "Install FFmpeg with libass support."
        )

    font_path = get_font_file()

    logger.info(
        "Subtitle filter and Hindi font checks passed."
    )

    return font_path


def escape_subtitle_path(path):
    """
    Escape paths for FFmpeg filter syntax.
    """

    path = str(path)

    path = path.replace("\\", "\\\\")
    path = path.replace(":", "\\:")
    path = path.replace("'", "\\'")
    path = path.replace(",", "\\,")
    path = path.replace("[", "\\[")
    path = path.replace("]", "\\]")

    return path


def get_video_duration(video_path):
    """Get media duration using FFprobe."""

    result = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        "Reading video duration",
    )

    try:
        return float(result.stdout.strip())

    except (ValueError, TypeError):
        raise RuntimeError(
            f"Could not read video duration: {video_path}"
        )


def get_audio_duration(audio_path):
    """Get audio duration using FFprobe."""

    result = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        "Reading audio duration",
    )

    try:
        return float(result.stdout.strip())

    except (ValueError, TypeError):
        raise RuntimeError(
            f"Could not read audio duration: {audio_path}"
        )


def get_media_info(media_path):
    """Return video and audio stream information."""

    result = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(media_path),
        ],
        "Reading media information",
    )

    try:
        return json.loads(result.stdout)

    except json.JSONDecodeError as error:
        raise RuntimeError(
            "FFprobe returned invalid JSON."
        ) from error


def validate_srt_file(subtitle_path):
    """
    Validate SRT encoding, timestamps and subtitle text.
    """

    if not subtitle_path.is_file():
        raise FileNotFoundError(
            f"Subtitle file not found: {subtitle_path}"
        )

    try:
        content = subtitle_path.read_text(
            encoding="utf-8-sig"
        )

    except UnicodeDecodeError as error:
        raise RuntimeError(
            "Subtitle file is not valid UTF-8."
        ) from error

    if not content.strip():
        raise RuntimeError(
            "Subtitle file is empty."
        )

    timestamp_pattern = re.compile(
        r"(\d{2}):(\d{2}):(\d{2}),(\d{3})"
        r"\s*-->\s*"
        r"(\d{2}):(\d{2}):(\d{2}),(\d{3})"
    )

    timestamps = timestamp_pattern.findall(content)

    if not timestamps:
        raise RuntimeError(
            "No valid SRT timestamps found."
        )

    hindi_characters = sum(
        1
        for character in content
        if "\u0900" <= character <= "\u097F"
    )

    if hindi_characters == 0:
        logger.warning(
            "No Devanagari characters detected in subtitles."
        )

    for match in timestamp_pattern.finditer(content):
        start = match.group(0).split("-->")[0].strip()
        end = match.group(0).split("-->")[1].strip()

        start_seconds = timestamp_to_seconds(start)
        end_seconds = timestamp_to_seconds(end)

        if end_seconds <= start_seconds:
            raise RuntimeError(
                "Invalid subtitle timing: end must be after start."
            )

    logger.info(
        f"SRT validation passed. "
        f"Subtitle blocks: {len(timestamps)}, "
        f"Hindi characters: {hindi_characters}"
    )

    return True


def timestamp_to_seconds(timestamp):
    """Convert SRT timestamp to seconds."""

    hours, minutes, remaining = timestamp.split(":")

    seconds, milliseconds = remaining.split(",")

    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(milliseconds) / 1000
    )


def normalize_hindi_text(text):
    """
    Normalize Unicode text without changing its meaning.
    """

    if not isinstance(text, str):
        return ""

    return unicodedata.normalize(
        "NFC",
        text,
    )


def validate_media_files():
    """Check required media files."""

    if not VIDEO_FILE.is_file():
        raise FileNotFoundError(
            f"Video file not found: {VIDEO_FILE}"
        )

    if not FINAL_AUDIO_FILE.is_file():
        raise FileNotFoundError(
            f"Final audio file not found: {FINAL_AUDIO_FILE}"
        )

    if not SUBTITLE_FILE.is_file():
        raise FileNotFoundError(
            f"Subtitle file not found: {SUBTITLE_FILE}"
        )

    validate_srt_file(SUBTITLE_FILE)

    return True


def create_subtitle_filter(subtitle_path, font_path):
    """
    Create FFmpeg libass subtitle filter.

    Important:
    - Uses Noto Sans Devanagari.
    - Explicitly provides fontsdir.
    - Uses UTF-8 encoding.
    - Does not use unsupported shaping=complex option.
    """

    subtitle_path = escape_subtitle_path(
        subtitle_path
    )

    font_directory = font_path.parent

    escaped_font_directory = escape_subtitle_path(
        font_directory
    )

    subtitle_filter = (
        f"subtitles=filename='{subtitle_path}':"
        f"fontsdir='{escaped_font_directory}':"
        "charenc=UTF-8:"
        "force_style='"
        "FontName=Noto Sans Devanagari,"
        f"FontSize={FONT_SIZE},"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,"
        "BackColour=&H80000000,"
        "BorderStyle=1,"
        f"Outline={SUBTITLE_OUTLINE},"
        f"Shadow={SUBTITLE_SHADOW},"
        "Alignment=2,"
        f"MarginV={SUBTITLE_MARGIN}"
        "'"
    )

    return subtitle_filter


def burn_subtitles(
    video_path,
    subtitle_path,
    output_path,
):
    """
    Burn Hindi subtitles into the video using FFmpeg libass.
    """

    logger.info(
        "Starting Hindi subtitle rendering."
    )

    font_path = check_subtitle_support()

    subtitle_filter = create_subtitle_filter(
        subtitle_path,
        font_path,
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        subtitle_filter,
        "-c:v",
        VIDEO_CODEC,
        "-preset",
        VIDEO_PRESET,
        "-crf",
        str(VIDEO_CRF),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    run_command(
        command,
        "Burning Hindi subtitles into video",
    )

    if not output_path.is_file():
        raise RuntimeError(
            "Subtitle rendering failed: output video not created."
        )

    if output_path.stat().st_size == 0:
        raise RuntimeError(
            "Subtitle rendering produced an empty video."
        )

    logger.info(
        "Hindi subtitle rendering completed."
    )

    return output_path


def merge_audio_video(
    video_path,
    audio_path,
    output_path,
):
    """
    Merge final narration audio with video.
    """

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        AUDIO_CODEC,
        "-b:a",
        AUDIO_BITRATE,
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    run_command(
        command,
        "Merging narration audio and video",
    )

    return output_path


def convert_video_format(
    input_path,
    output_path,
):
    """Convert video to standard MP4 H.264 format."""

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        (
            f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
            "(ow-iw)/2:(oh-ih)/2,"
            "setsar=1"
        ),
        "-r",
        str(VIDEO_FPS),
        "-c:v",
        VIDEO_CODEC,
        "-preset",
        VIDEO_PRESET,
        "-crf",
        str(VIDEO_CRF),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        AUDIO_CODEC,
        "-b:a",
        AUDIO_BITRATE,
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    run_command(
        command,
        "Converting video to standard MP4",
    )

    return output_path


def create_video_report(video_path):
    """Create a production report for the final video."""

    info = get_media_info(video_path)

    video_stream = None
    audio_stream = None

    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            video_stream = stream

        elif stream.get("codec_type") == "audio":
            audio_stream = stream

    report = {
        "created_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "video_file": str(video_path),
        "file_size_mb": round(
            video_path.stat().st_size / (1024 * 1024),
            2,
        ),
        "duration_seconds": get_video_duration(
            video_path
        ),
        "resolution": None,
        "fps": None,
        "video_codec": None,
        "audio_codec": None,
        "subtitle_file": str(SUBTITLE_FILE),
        "subtitle_font": FONT_FAMILY,
        "subtitle_rendering": "FFmpeg libass",
    }

    if video_stream:
        report["resolution"] = (
            f"{video_stream.get('width')}x"
            f"{video_stream.get('height')}"
        )

        report["fps"] = video_stream.get(
            "r_frame_rate"
        )

        report["video_codec"] = video_stream.get(
            "codec_name"
        )

    if audio_stream:
        report["audio_codec"] = audio_stream.get(
            "codec_name"
        )

    REPORT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    REPORT_FILE.write_text(
        json.dumps(
            report,
            indent=4,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    logger.info(
        f"Video report saved: {REPORT_FILE}"
    )

    return report


def edit_video():
    """
    Main video editing function.

    This function validates the generated video,
    burns Hindi subtitles and creates the final report.
    """

    logger.info(
        "Starting final video editing pipeline."
    )

    validate_media_files()

    check_subtitle_support()

    logger.info(
        "Running production validations."
    )

    try:
        run_all_validations()

    except Exception as error:
        logger.warning(
            f"General validation warning: {error}"
        )

    temporary_video = (
        OUTPUT_DIR / "video_before_subtitles.mp4"
    )

    if VIDEO_FILE.resolve() == temporary_video.resolve():
        raise RuntimeError(
            "Temporary video path conflicts with final video path."
        )

    logger.info(
        "Preparing video for subtitle rendering."
    )

    shutil.copy2(
        VIDEO_FILE,
        temporary_video,
    )

    try:
        burn_subtitles(
            video_path=temporary_video,
            subtitle_path=SUBTITLE_FILE,
            output_path=VIDEO_FILE,
        )

    finally:
        if temporary_video.exists():
            temporary_video.unlink()

    report = create_video_report(
        VIDEO_FILE
    )

    logger.info(
        "Final video editing pipeline completed."
    )

    logger.info(
        f"Final video: {VIDEO_FILE}"
    )

    return report


def main():
    """Command-line entry point."""

    try:
        report = edit_video()

        print("\n========== VIDEO COMPLETE ==========")

        print(
            json.dumps(
                report,
                indent=4,
                ensure_ascii=False,
            )
        )

        print("\nHindi subtitle rendering completed.")

        return 0

    except Exception as error:
        logger.exception(
            f"Video editing failed: {error}"
        )

        print(
            f"\nVIDEO EDITING FAILED: {error}",
            file=sys.stderr,
        )

        return 1


if __name__ == "__main__":
    sys.exit(main())
