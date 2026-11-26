
"""
Hybrid 2D Cinematic Video Renderer
-----------------------------------

Creates a cinematic 2D documentary video from generated scene images.

Features:
- Reads image_generation_report.json.
- Renders scene images into individual video clips.
- Smooth zoom and pan camera movement.
- Supports scene-specific camera motion metadata.
- Synchronizes scene durations with narration length.
- Concatenates scenes using FFmpeg.
- Adds final narration audio.
- Burns Hindi subtitles using libass.
- Exports H.264 MP4 at 1920x1080 / 24 FPS.
- Creates a detailed production report.

Limitations:
- Flat images cannot provide genuine skeletal character animation.
- True layered parallax requires separate background, character
  and foreground assets.
"""

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


# ============================================================
# PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

VIDEO_FILE = OUTPUT_DIR / "final_video.mp4"

RAW_VIDEO_FILE = OUTPUT_DIR / "rendered_scenes.mp4"

AUDIO_VIDEO_FILE = OUTPUT_DIR / "video_with_audio.mp4"

SUBTITLED_VIDEO_FILE = OUTPUT_DIR / "final_video_subtitled.mp4"

THUMBNAIL_FILE = OUTPUT_DIR / "thumbnail" / "thumbnail.png"

SUBTITLE_FILE = OUTPUT_DIR / "subtitles" / "narration.srt"

FINAL_AUDIO_FILE = OUTPUT_DIR / "audio" / "final_audio.wav"

IMAGE_REPORT_FILE = OUTPUT_DIR / "image_generation_report.json"

REPORT_FILE = OUTPUT_DIR / "video_edit_report.json"

SEGMENT_DIR = OUTPUT_DIR / "render_segments"

CONCAT_FILE = SEGMENT_DIR / "concat_list.txt"


# ============================================================
# VIDEO SETTINGS
# ============================================================

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080

VIDEO_FPS = 24

VIDEO_CODEC = "libx264"

AUDIO_CODEC = "aac"

AUDIO_BITRATE = "192k"

VIDEO_PRESET = "medium"

VIDEO_CRF = 20

FONT_FAMILY = "Noto Sans Devanagari"

FONT_SIZE = 24

SUBTITLE_OUTLINE = 2

SUBTITLE_SHADOW = 1

SUBTITLE_MARGIN = 45

DEFAULT_ZOOM = 1.12

MAX_ZOOM = 1.18

MIN_ZOOM = 1.0

RENDER_TIMEOUT = 3600


# ============================================================
# COMMAND EXECUTION
# ============================================================

def run_command(command, description="Command", timeout=RENDER_TIMEOUT):

    logger.info(description)

    try:

        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )

        return result

    except subprocess.TimeoutExpired as error:

        logger.error("%s timed out.", description)

        raise RuntimeError(
            f"{description} timed out."
        ) from error

    except subprocess.CalledProcessError as error:

        stderr = error.stderr or ""
        stdout = error.stdout or ""

        logger.error(
            "%s failed.\nSTDOUT:\n%s\nSTDERR:\n%s",
            description,
            stdout[-3000:],
            stderr[-5000:],
        )

        raise RuntimeError(
            f"{description} failed: {stderr[-3000:]}"
        ) from error


# ============================================================
# FFmpeg CHECKS
# ============================================================

def check_ffmpeg():

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

    lines = result.stdout.strip().splitlines()

    if not lines:

        raise RuntimeError(
            "Could not detect Noto Sans Devanagari font."
        )

    font_path = Path(lines[0]).resolve()

    if not font_path.is_file():

        raise RuntimeError(
            f"Hindi font file does not exist: {font_path}"
        )

    logger.info("Hindi font detected: %s", font_path)

    return font_path


def check_subtitle_support():

    check_ffmpeg()

    result = run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-filters",
        ],
        "Checking FFmpeg filters",
    )

    if not re.search(
        r"\bsubtitles\b",
        result.stdout,
    ):

        raise RuntimeError(
            "FFmpeg subtitles filter is unavailable. "
            "Install FFmpeg with libass support."
        )

    return get_font_file()


# ============================================================
# MEDIA INFORMATION
# ============================================================

def get_media_info(media_path):

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


def get_media_duration(media_path):

    result = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ],
        "Reading media duration",
    )

    try:

        duration = float(result.stdout.strip())

        if duration <= 0:
            raise ValueError("Duration must be positive.")

        return duration

    except (ValueError, TypeError) as error:

        raise RuntimeError(
            f"Could not read media duration: {media_path}"
        ) from error


def get_video_duration(video_path):

    return get_media_duration(video_path)


def get_audio_duration(audio_path):

    return get_media_duration(audio_path)


# ============================================================
# SRT VALIDATION
# ============================================================

def timestamp_to_seconds(timestamp):

    hours, minutes, remaining = timestamp.split(":")

    seconds, milliseconds = remaining.split(",")

    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(milliseconds) / 1000
    )


def normalize_hindi_text(text):

    if not isinstance(text, str):
        return ""

    return unicodedata.normalize(
        "NFC",
        text,
    )


def validate_srt_file(subtitle_path):

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

    for match in timestamp_pattern.finditer(content):

        start, end = match.group(0).split("-->")

        start_seconds = timestamp_to_seconds(
            start.strip()
        )

        end_seconds = timestamp_to_seconds(
            end.strip()
        )

        if end_seconds <= start_seconds:

            raise RuntimeError(
                "Invalid subtitle timing: end must be after start."
            )

    logger.info(
        "SRT validation passed. Blocks: %s | Hindi characters: %s",
        len(timestamps),
        hindi_characters,
    )

    return True


# ============================================================
# INPUT VALIDATION
# ============================================================

def validate_media_files():

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


def load_image_report():

    if not IMAGE_REPORT_FILE.is_file():

        raise FileNotFoundError(
            f"Image generation report not found: {IMAGE_REPORT_FILE}"
        )

    with open(
        IMAGE_REPORT_FILE,
        "r",
        encoding="utf-8",
    ) as file:

        report = json.load(file)

    if not isinstance(report, dict):

        raise RuntimeError(
            "Image report must contain a JSON object."
        )

    scenes = report.get("scene_images", [])

    if not isinstance(scenes, list) or not scenes:

        raise RuntimeError(
            "No scene images found in image generation report."
        )

    valid_scenes = []

    for scene in scenes:

        if not isinstance(scene, dict):
            continue

        if scene.get("status") != "success":
            continue

        image_path = scene.get("image_path")

        if not image_path:
            continue

        path = Path(image_path)

        if not path.is_absolute():
            path = BASE_DIR / path

        if not path.is_file():

            logger.warning(
                "Scene image missing: %s",
                path
            )

            continue

        valid_scene = dict(scene)

        valid_scene["resolved_image_path"] = str(
            path.resolve()
        )

        valid_scenes.append(valid_scene)

    if not valid_scenes:

        raise RuntimeError(
            "No usable scene image files were found."
        )

    logger.info(
        "Loaded %s valid scene images.",
        len(valid_scenes)
    )

    return valid_scenes


# ============================================================
# CAMERA MOVEMENT
# ============================================================

def get_camera_motion(scene, index):

    animation_plan = scene.get(
        "animation_plan",
        {}
    )

    if not isinstance(animation_plan, dict):
        animation_plan = {}

    motion = animation_plan.get(
        "camera_motion",
        ""
    )

    if motion in {
        "slow_push_in",
        "slow_pan",
        "tracking",
        "dramatic_reveal",
    }:

        return motion

    motions = [
        "slow_push_in",
        "slow_pan",
        "tracking",
        "dramatic_reveal",
    ]

    return motions[index % len(motions)]


def build_zoompan_filter(
    duration,
    camera_motion,
):

    frames = max(
        1,
        round(duration * VIDEO_FPS)
    )

    zoom_limit = min(
        DEFAULT_ZOOM,
        MAX_ZOOM
    )

    zoom_increment = (
        (zoom_limit - MIN_ZOOM)
        / max(frames, 1)
    )

    zoom_expression = (
        f"min(1+{zoom_increment:.8f}*on,{zoom_limit:.4f})"
    )

    if camera_motion == "slow_pan":

        x_expression = (
            "(iw-iw/zoom)*on/"
            f"{max(frames - 1, 1)}"
        )

        y_expression = (
            "ih/2-(ih/zoom/2)"
        )

    elif camera_motion == "tracking":

        x_expression = (
            "(iw-iw/zoom)*"
            f"(0.5+0.5*sin(on/{max(frames, 1)}*PI))"
        )

        y_expression = (
            "(ih-ih/zoom)*"
            f"(0.5+0.5*cos(on/{max(frames, 1)}*PI))"
        )

    elif camera_motion == "dramatic_reveal":

        zoom_expression = (
            f"min(1+{zoom_increment * 1.25:.8f}*on,1.16)"
        )

        x_expression = (
            "(iw-iw/zoom)*0.5"
        )

        y_expression = (
            "(ih-ih/zoom)*0.5"
        )

    else:

        x_expression = (
            "(iw-iw/zoom)*0.5"
        )

        y_expression = (
            "(ih-ih/zoom)*0.5"
        )

    return (
        "scale=3840:2160:force_original_aspect_ratio=increase,"
        "crop=3840:2160,"
        f"zoompan=z='{zoom_expression}':"
        f"x='{x_expression}':"
        f"y='{y_expression}':"
        f"d={frames}:"
        f"s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:"
        f"fps={VIDEO_FPS},"
        "setsar=1,"
        "format=yuv420p"
    )


# ============================================================
# SCENE RENDERING
# ============================================================

def render_scene(
    scene,
    index,
    duration,
):

    image_path = Path(
        scene["resolved_image_path"]
    )

    scene_number = scene.get(
        "scene_number",
        index + 1
    )

    camera_motion = get_camera_motion(
        scene,
        index
    )

    segment_path = (
        SEGMENT_DIR
        / f"scene_{index + 1:04d}.mp4"
    )

    segment_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    video_filter = build_zoompan_filter(
        duration,
        camera_motion
    )

    frame_count = max(
        1,
        round(duration * VIDEO_FPS)
    )

    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-loop",
        "1",
        "-framerate",
        str(VIDEO_FPS),
        "-i",
        str(image_path),
        "-vf",
        video_filter,
        "-frames:v",
        str(frame_count),
        "-an",
        "-c:v",
        VIDEO_CODEC,
        "-preset",
        VIDEO_PRESET,
        "-crf",
        str(VIDEO_CRF),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(segment_path),
    ]

    run_command(
        command,
        f"Rendering scene {scene_number}",
    )

    if not segment_path.is_file():

        raise RuntimeError(
            f"Scene render output missing: {segment_path}"
        )

    if segment_path.stat().st_size == 0:

        raise RuntimeError(
            f"Scene render output is empty: {segment_path}"
        )

    return {
        "scene_number": scene_number,
        "image_path": str(image_path),
        "segment_path": str(segment_path),
        "duration_seconds": duration,
        "camera_motion": camera_motion,
        "status": "success",
    }


def calculate_scene_durations(scenes, total_duration):

    if not scenes:

        raise RuntimeError(
            "Cannot calculate duration without scenes."
        )

    # The current image report does not contain reliable
    # narration timestamps for every scene.
    # Distribute audio duration evenly across available images.

    scene_count = len(scenes)

    base_duration = total_duration / scene_count

    durations = [
        base_duration
        for _ in scenes
    ]

    # Correct floating point drift on the last scene.

    durations[-1] = (
        total_duration
        - sum(durations[:-1])
    )

    return durations


def render_all_scenes():

    scenes = load_image_report()

    total_audio_duration = get_audio_duration(
        FINAL_AUDIO_FILE
    )

    durations = calculate_scene_durations(
        scenes,
        total_audio_duration
    )

    SEGMENT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    # Remove only old temporary scene segments.
    # Generated image assets are not touched.

    for old_segment in SEGMENT_DIR.glob("scene_*.mp4"):

        old_segment.unlink()

    rendered_segments = []

    for index, scene in enumerate(scenes):

        duration = durations[index]

        result = render_scene(
            scene,
            index,
            duration
        )

        rendered_segments.append(result)

        logger.info(
            "Rendered scene %s/%s",
            index + 1,
            len(scenes)
        )

    return rendered_segments


# ============================================================
# CONCATENATION
# ============================================================

def create_concat_file(segments):

    if not segments:

        raise RuntimeError(
            "No rendered scene segments available."
        )

    lines = []

    for segment in segments:

        path = Path(
            segment["segment_path"]
        ).resolve()

        escaped_path = (
            str(path)
            .replace("\\", "/")
            .replace("'", "'\\''")
        )

        lines.append(
            f"file '{escaped_path}'"
        )

    CONCAT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    CONCAT_FILE.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8"
    )

    return CONCAT_FILE


def concatenate_scenes(segments):

    concat_file = create_concat_file(
        segments
    )

    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-c:v",
        "copy",
        "-an",
        "-movflags",
        "+faststart",
        str(RAW_VIDEO_FILE),
    ]

    run_command(
        command,
        "Concatenating rendered scenes",
    )

    if not RAW_VIDEO_FILE.is_file():

        raise RuntimeError(
            "Concatenated video was not created."
        )

    return RAW_VIDEO_FILE


# ============================================================
# AUDIO MERGING
# ============================================================

def merge_audio_video(
    video_path,
    audio_path,
    output_path,
):

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
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

    if not output_path.is_file():

        raise RuntimeError(
            "Audio/video merge output missing."
        )

    return output_path


# ============================================================
# HINDI SUBTITLES
# ============================================================

def escape_subtitle_path(path):

    path = str(path)

    path = path.replace("\\", "\\\\")
    path = path.replace(":", "\\:")
    path = path.replace("'", "\\'")
    path = path.replace(",", "\\,")
    path = path.replace("[", "\\[")
    path = path.replace("]", "\\]")

    return path


def create_subtitle_filter(
    subtitle_path,
    font_path,
):

    subtitle_path = escape_subtitle_path(
        subtitle_path
    )

    font_directory = escape_subtitle_path(
        font_path.parent
    )

    return (
        f"subtitles=filename='{subtitle_path}':"
        f"fontsdir='{font_directory}':"
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


def burn_subtitles(
    video_path,
    subtitle_path,
    output_path,
):

    logger.info(
        "Starting Hindi subtitle rendering."
    )

    font_path = check_subtitle_support()

    subtitle_filter = create_subtitle_filter(
        subtitle_path,
        font_path
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
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
            "Subtitle rendering failed."
        )

    if output_path.stat().st_size == 0:

        raise RuntimeError(
            "Subtitle rendering produced an empty video."
        )

    return output_path


# ============================================================
# FINAL VIDEO VALIDATION
# ============================================================

def validate_final_video(video_path):

    if not video_path.is_file():

        raise FileNotFoundError(
            f"Final video not found: {video_path}"
        )

    if video_path.stat().st_size == 0:

        raise RuntimeError(
            "Final video is empty."
        )

    info = get_media_info(video_path)

    video_stream = None
    audio_stream = None

    for stream in info.get("streams", []):

        if stream.get("codec_type") == "video":
            video_stream = stream

        elif stream.get("codec_type") == "audio":
            audio_stream = stream

    if not video_stream:

        raise RuntimeError(
            "Final video has no video stream."
        )

    if not audio_stream:

        raise RuntimeError(
            "Final video has no audio stream."
        )

    width = int(
        video_stream.get("width", 0)
    )

    height = int(
        video_stream.get("height", 0)
    )

    if width != VIDEO_WIDTH or height != VIDEO_HEIGHT:

        raise RuntimeError(
            f"Unexpected resolution: {width}x{height}"
        )

    duration = get_video_duration(
        video_path
    )

    if duration <= 0:

        raise RuntimeError(
            "Final video duration is invalid."
        )

    return {
        "duration_seconds": duration,
        "width": width,
        "height": height,
        "video_codec": video_stream.get("codec_name"),
        "audio_codec": audio_stream.get("codec_name"),
        "fps": video_stream.get("r_frame_rate"),
    }


# ============================================================
# REPORT
# ============================================================

def create_video_report(
    video_path,
    scene_results,
):

    media = validate_final_video(
        video_path
    )

    report = {

        "status": "completed",

        "created_at": datetime.now(
            timezone.utc
        ).isoformat(),

        "video_file": str(video_path),

        "file_size_mb": round(
            video_path.stat().st_size / (1024 * 1024),
            2
        ),

        "duration_seconds": media["duration_seconds"],

        "resolution": (
            f"{media['width']}x{media['height']}"
        ),

        "fps": media["fps"],

        "video_codec": media["video_codec"],

        "audio_codec": media["audio_codec"],

        "subtitle_file": str(SUBTITLE_FILE),

        "subtitle_font": FONT_FAMILY,

        "subtitle_rendering": "FFmpeg libass",

        "animation_engine": "hybrid_2d_camera_motion",

        "rendering_method": (
            "Image-based cinematic 2D motion rendering"
        ),

        "true_character_rigging": False,

        "true_layered_parallax": False,

        "scene_count": len(scene_results),

        "scenes": scene_results,

        "limitations": [
            "Flat scene images do not contain independent character body parts.",
            "True puppet rigging is not included.",
            "True multi-layer parallax requires separate layer assets.",
            "Scene timing is distributed evenly because per-scene narration timestamps are not currently available.",
        ],
    }

    REPORT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    REPORT_FILE.write_text(
        json.dumps(
            report,
            indent=4,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    logger.info(
        "Video report saved: %s",
        REPORT_FILE
    )

    return report


# ============================================================
# MAIN VIDEO PIPELINE
# ============================================================

def edit_video():

    logger.info(
        "Starting hybrid 2D video rendering pipeline."
    )

    validate_media_files()

    check_ffmpeg()

    check_subtitle_support()

    try:

        run_all_validations()

    except Exception as error:

        logger.warning(
            "General validation warning: %s",
            error
        )

    # Step 1: Render all scene images.

    logger.info(
        "STEP 1: Rendering cinematic scene clips."
    )

    scene_results = render_all_scenes()

    # Step 2: Concatenate scenes.

    logger.info(
        "STEP 2: Concatenating scene clips."
    )

    concatenate_scenes(
        scene_results
    )

    # Step 3: Add narration audio.

    logger.info(
        "STEP 3: Adding narration audio."
    )

    merge_audio_video(
        RAW_VIDEO_FILE,
        FINAL_AUDIO_FILE,
        AUDIO_VIDEO_FILE
    )

    # Step 4: Burn Hindi subtitles.

    logger.info(
        "STEP 4: Burning Hindi subtitles."
    )

    burn_subtitles(
        AUDIO_VIDEO_FILE,
        SUBTITLE_FILE,
        SUBTITLED_VIDEO_FILE
    )

    # Step 5: Validate final output.

    logger.info(
        "STEP 5: Validating final video."
    )

    validation = validate_final_video(
        SUBTITLED_VIDEO_FILE
    )

    # Replace final output only after successful validation.

    VIDEO_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    shutil.copy2(
        SUBTITLED_VIDEO_FILE,
        VIDEO_FILE
    )

    report = create_video_report(
        VIDEO_FILE,
        scene_results
    )

    logger.info(
        "Hybrid 2D video rendering completed."
    )

    logger.info(
        "Final video: %s",
        VIDEO_FILE
    )

    logger.info(
        "Final duration: %.2f seconds",
        validation["duration_seconds"]
    )

    return report


# ============================================================
# COMMAND LINE ENTRY POINT
# ============================================================

def main():

    try:

        report = edit_video()

        print(
            "\n========== VIDEO COMPLETE =========="
        )

        print(
            json.dumps(
                report,
                indent=4,
                ensure_ascii=False
            )
        )

        print(
            "\nFinal video:",
            VIDEO_FILE
        )

        return 0

    except Exception as error:

        logger.exception(
            "Video editing failed: %s",
            error
        )

        print(
            f"\nVIDEO EDITING FAILED: {error}",
            file=sys.stderr
        )

        return 1


if __name__ == "__main__":

    sys.exit(main())
