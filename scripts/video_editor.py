
"""
Cinematic Horror Story - Video Editor

Features:
- Uses successful current-run images only.
- Preserves chronological scene order.
- Reads video settings from config.json.
- Matches video duration to selected narration audio.
- FFmpeg cinematic crossfade transitions.
- Alternating zoom and directional pan.
- Hindi subtitle rendering.
- Validates subtitles against selected audio.
- Verifies final video/audio streams.
- Saves a detailed video report.
"""

import json
import math
import re
import shutil
import subprocess

from pathlib import Path


# --------------------------------------------------
# PATHS
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"

IMAGE_DIR = OUTPUT_DIR / "images"
AUDIO_DIR = OUTPUT_DIR / "audio"
SUBTITLE_DIR = OUTPUT_DIR / "subtitles"

TEMP_DIR = OUTPUT_DIR / "temp_video"

CONFIG_FILE = BASE_DIR / "config" / "config.json"
ANALYSIS_FILE = OUTPUT_DIR / "story_analysis.json"
IMAGE_REPORT_FILE = OUTPUT_DIR / "image_generation_report.json"
SUBTITLE_FILE = SUBTITLE_DIR / "narration.srt"

FINAL_VIDEO = OUTPUT_DIR / "final_video.mp4"
VIDEO_REPORT = OUTPUT_DIR / "video_report.json"


# --------------------------------------------------
# DEFAULT SETTINGS
# --------------------------------------------------

DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_FPS = 24

DEFAULT_TRANSITION_DURATION = 0.8
DURATION_TOLERANCE = 0.20

SUPPORTED_IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
}


# --------------------------------------------------
# COMMAND EXECUTION
# --------------------------------------------------

def run_command(command, cwd=None):
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Command failed:\n"
            + result.stderr[-6000:]
        )

    return result


def check_executable(executable):
    try:
        subprocess.run(
            [executable, "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except Exception as error:
        raise RuntimeError(
            f"{executable} is missing or unavailable."
        ) from error


def find_ffmpeg():
    check_executable("ffmpeg")
    check_executable("ffprobe")


# --------------------------------------------------
# JSON
# --------------------------------------------------

def load_json(path):
    if not path.is_file():
        raise FileNotFoundError(
            f"Required file not found: {path}"
        )

    with open(
        path,
        "r",
        encoding="utf-8-sig",
    ) as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError(
            f"Expected a JSON object: {path}"
        )

    return data


def save_json(path, data):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with open(
        path,
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
# CONFIGURATION
# --------------------------------------------------

def load_video_settings():
    config = load_json(CONFIG_FILE)

    video = config.get("video", {})

    if not isinstance(video, dict):
        raise ValueError(
            "Invalid video section in config.json."
        )

    width = int(video.get("width", DEFAULT_WIDTH))
    height = int(video.get("height", DEFAULT_HEIGHT))
    fps = int(video.get("fps", DEFAULT_FPS))

    if width <= 0 or height <= 0 or fps <= 0:
        raise ValueError(
            "Video width, height and FPS must be positive."
        )

    if width % 2 != 0 or height % 2 != 0:
        raise ValueError(
            "Video width and height must be even numbers."
        )

    transition_duration = float(
        video.get(
            "transition_duration",
            DEFAULT_TRANSITION_DURATION,
        )
    )

    if (
        not math.isfinite(transition_duration)
        or transition_duration < 0
    ):
        raise ValueError(
            "Invalid transition duration."
        )

    zoom_effect = video.get("zoom_effect", True)
    pan_effect = video.get("pan_effect", True)

    if not isinstance(zoom_effect, bool):
        raise ValueError(
            "zoom_effect must be true or false."
        )

    if not isinstance(pan_effect, bool):
        raise ValueError(
            "pan_effect must be true or false."
        )

    subtitles = config.get("subtitles", {})

    if not isinstance(subtitles, dict):
        raise ValueError(
            "Invalid subtitles configuration."
        )

    enabled = subtitles.get("enabled", False)
    burn = subtitles.get("burn_into_video", False)

    if (
        not isinstance(enabled, bool)
        or not isinstance(burn, bool)
    ):
        raise ValueError(
            "Subtitle settings must be booleans."
        )

    return {
        "width": width,
        "height": height,
        "fps": fps,
        "transition_duration": transition_duration,
        "zoom_effect": zoom_effect,
        "pan_effect": pan_effect,
        "subtitles_enabled": enabled and burn,
    }


# --------------------------------------------------
# AUDIO
# --------------------------------------------------

def find_audio():
    candidates = [
        AUDIO_DIR / "final_audio.wav",
        AUDIO_DIR / "narration.wav",
        AUDIO_DIR / "final_audio.mp3",
        AUDIO_DIR / "narration.mp3",
    ]

    for path in candidates:
        if path.is_file() and path.stat().st_size > 0:
            return path

    raise FileNotFoundError(
        "Narration audio not found in output/audio/."
    )


def get_audio_info(audio_path):
    result = run_command([
        "ffprobe",
        "-v", "error",
        "-show_entries",
        "format=duration:"
        "stream=codec_type,duration,sample_rate,channels",
        "-of", "json",
        str(audio_path),
    ])

    data = json.loads(result.stdout)

    streams = data.get("streams", [])

    audio_stream = next(
        (
            stream for stream in streams
            if stream.get("codec_type") == "audio"
        ),
        None,
    )

    if not audio_stream:
        raise RuntimeError(
            "Selected audio file has no audio stream."
        )

    raw_duration = audio_stream.get("duration")

    if raw_duration in (None, "N/A"):
        raw_duration = data.get(
            "format", {}
        ).get("duration", 0)

    duration = float(raw_duration)

    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError(
            "Could not determine valid audio duration."
        )

    return {
        "duration": duration,
        "sample_rate": audio_stream.get("sample_rate"),
        "channels": audio_stream.get("channels"),
    }


# --------------------------------------------------
# IMAGE REPORT
# --------------------------------------------------

def get_current_run_images():
    """
    Read only successful scene images from current report.
    """

    report = load_json(IMAGE_REPORT_FILE)

    scene_results = report.get("scene_images", [])

    if not isinstance(scene_results, list):
        raise ValueError(
            "Invalid scene_images list in image report."
        )

    successful = []

    for item in scene_results:
        if not isinstance(item, dict):
            continue

        if item.get("status") != "success":
            continue

        if not item.get("image_path"):
            continue

        successful.append(item)

    def sort_key(item):
        try:
            return int(item.get("image_index", 0))
        except (TypeError, ValueError):
            return 0

    successful.sort(key=sort_key)

    images = []

    for item in successful:
        image_path = Path(item["image_path"])

        if not image_path.is_absolute():
            image_path = BASE_DIR / image_path

        image_path = image_path.resolve()

        try:
            image_path.relative_to(
                IMAGE_DIR.resolve()
            )
        except ValueError as error:
            raise ValueError(
                f"Image outside output/images/: {image_path}"
            ) from error

        if not image_path.is_file():
            raise FileNotFoundError(
                f"Image is missing: {image_path}"
            )

        if (
            image_path.suffix.lower()
            not in SUPPORTED_IMAGE_EXTENSIONS
        ):
            raise ValueError(
                f"Unsupported image format: {image_path}"
            )

        if image_path.stat().st_size == 0:
            raise ValueError(
                f"Image is empty: {image_path}"
            )

        images.append({
            "path": image_path,
            "scene_number": item.get("scene_number"),
            "image_index": item.get("image_index"),
            "story_event": item.get("story_event", ""),
        })

    if not images:
        raise FileNotFoundError(
            "No successful current-run images found."
        )

    return images


# --------------------------------------------------
# SCENE WEIGHTS
# --------------------------------------------------

def get_scene_weights(images, analysis):
    scenes = analysis.get("scenes", [])

    scene_lookup = {}

    if isinstance(scenes, list):
        for scene in scenes:
            if not isinstance(scene, dict):
                continue

            scene_number = scene.get("scene_number")

            if scene_number is not None:
                scene_lookup[str(scene_number)] = scene

    weights = []

    for image in images:
        scene = scene_lookup.get(
            str(image.get("scene_number")),
            {},
        )

        event_text = (
            scene.get("story_event")
            or image.get("story_event")
            or scene.get("visual_description")
            or scene.get("scene_title")
            or ""
        )

        weights.append(
            max(len(str(event_text).strip()), 1)
        )

    return weights


# --------------------------------------------------
# FRAME ALLOCATION
# --------------------------------------------------

def allocate_frames(weights, total_frames):
    if not weights:
        raise ValueError(
            "No scene weights available."
        )

    if total_frames < len(weights):
        raise ValueError(
            "Narration is too short for scene images."
        )

    weights = [
        max(int(weight), 1)
        for weight in weights
    ]

    total_weight = sum(weights)

    exact = [
        weight / total_weight * total_frames
        for weight in weights
    ]

    allocations = [
        int(value)
        for value in exact
    ]

    remaining = total_frames - sum(allocations)

    order = sorted(
        range(len(weights)),
        key=lambda index: (
            exact[index] - allocations[index]
        ),
        reverse=True,
    )

    for index in order[:remaining]:
        allocations[index] += 1

    if sum(allocations) != total_frames:
        raise RuntimeError(
            "Frame allocation mismatch."
        )

    if any(value <= 0 for value in allocations):
        raise RuntimeError(
            "A scene received zero frames."
        )

    return allocations


# --------------------------------------------------
# TRANSITION SETTINGS
# --------------------------------------------------

def calculate_transition_frames(
    base_allocations,
    requested_duration,
    fps,
):
    if len(base_allocations) < 2:
        return 0

    requested_frames = round(
        requested_duration * fps
    )

    if requested_frames <= 0:
        return 0

    shortest_scene = min(base_allocations)

    safe_maximum = shortest_scene - 1

    if safe_maximum <= 0:
        return 0

    actual_frames = min(
        requested_frames,
        safe_maximum,
    )

    if actual_frames != requested_frames:
        print(
            "WARNING: Transition duration reduced "
            "to fit the shortest scene."
        )

    return actual_frames


# --------------------------------------------------
# CAMERA MOVEMENT - FIXED
# --------------------------------------------------

def create_camera_filter(
    frame_count,
    index,
    width,
    height,
    fps,
    zoom_enabled,
    pan_enabled,
):
    """
    Safe FFmpeg zoompan expressions.

    Alternating zoom and directional pan.
    Uses d+1 to avoid invalid max() expression parsing.
    """

    if frame_count <= 0:
        raise ValueError(
            "Camera frame count must be positive."
        )

    if not zoom_enabled and not pan_enabled:
        return ",".join([
            (
                f"scale={width}:{height}:"
                "force_original_aspect_ratio=increase"
            ),
            f"crop={width}:{height}",
            "setsar=1",
            "format=yuv420p",
        ])

    scale_width = round(width * 1.18)
    scale_height = round(height * 1.18)

    filters = [
        (
            f"scale={scale_width}:{scale_height}:"
            "force_original_aspect_ratio=increase"
        ),
        f"crop={scale_width}:{scale_height}",
    ]

    if zoom_enabled:
        if index % 2 == 0:
            zoom_expression = (
                "if(eq(on,0),1.0,"
                "min(zoom+0.0007,1.10))"
            )
        else:
            zoom_expression = (
                "if(eq(on,0),1.10,"
                "max(zoom-0.0007,1.0))"
            )
    elif pan_enabled:
        zoom_expression = "1.08"
    else:
        zoom_expression = "1.0"

    if pan_enabled:
        direction = index % 4

        if direction == 0:
            x_expression = (
                "(iw-iw/zoom)*on/(d+1)"
            )
            y_expression = (
                "ih/2-ih/zoom/2"
            )

        elif direction == 1:
            x_expression = (
                "(iw-iw/zoom)*(1-on/(d+1))"
            )
            y_expression = (
                "ih/2-ih/zoom/2"
            )

        elif direction == 2:
            x_expression = (
                "iw/2-iw/zoom/2"
            )
            y_expression = (
                "(ih-ih/zoom)*on/(d+1)"
            )

        else:
            x_expression = (
                "iw/2-iw/zoom/2"
            )
            y_expression = (
                "(ih-ih/zoom)*(1-on/(d+1))"
            )

    else:
        x_expression = (
            "iw/2-iw/zoom/2"
        )
        y_expression = (
            "ih/2-ih/zoom/2"
        )

    filters.append(
        f"zoompan=z='{zoom_expression}':"
        f"x='{x_expression}':"
        f"y='{y_expression}':"
        f"d={frame_count}:"
        f"s={width}x{height}:"
        f"fps={fps}"
    )

    filters.extend([
        "setsar=1",
        "format=yuv420p",
    ])

    return ",".join(filters)


# --------------------------------------------------
# SCENE CLIP
# --------------------------------------------------

def create_scene_clip(
    image_path,
    frame_count,
    output_path,
    index,
    settings,
):
    filter_string = create_camera_filter(
        frame_count=frame_count,
        index=index,
        width=settings["width"],
        height=settings["height"],
        fps=settings["fps"],
        zoom_enabled=settings["zoom_effect"],
        pan_enabled=settings["pan_effect"],
    )

    run_command([
        "ffmpeg",
        "-y",
        "-loop", "1",
        "-i", str(image_path),
        "-vf", filter_string,
        "-frames:v", str(frame_count),
        "-an",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-r", str(settings["fps"]),
        "-video_track_timescale", "90000",
        str(output_path),
    ])

    if (
        not output_path.is_file()
        or output_path.stat().st_size == 0
    ):
        raise RuntimeError(
            f"Scene clip failed: {output_path}"
        )


# --------------------------------------------------
# CINEMATIC TRANSITIONS
# --------------------------------------------------

def create_transition_video(
    clip_paths,
    base_allocations,
    transition_frames,
    fps,
    output_path,
):
    if not clip_paths:
        raise ValueError(
            "No scene clips supplied."
        )

    if len(clip_paths) != len(base_allocations):
        raise ValueError(
            "Clip count and frame allocation mismatch."
        )

    if len(clip_paths) == 1 or transition_frames <= 0:
        concat_file = TEMP_DIR / "concat_list.txt"

        with open(
            concat_file,
            "w",
            encoding="utf-8",
        ) as file:
            for clip in clip_paths:
                file.write(
                    f"file '{clip.name}'\n"
                )

        run_command([
            "ffmpeg",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-an",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-r", str(fps),
            str(output_path),
        ], cwd=TEMP_DIR)

        return {
            "transition_type": "none",
            "transition_count": 0,
            "transition_duration_seconds": 0,
        }

    transition_seconds = transition_frames / fps

    command = ["ffmpeg", "-y"]

    for clip in clip_paths:
        command.extend([
            "-i",
            str(clip),
        ])

    filters = []

    for index in range(len(clip_paths)):
        filters.append(
            f"[{index}:v]"
            f"settb=AVTB,"
            f"setpts=PTS-STARTPTS,"
            f"fps={fps},"
            f"format=yuv420p"
            f"[v{index}]"
        )

    previous_label = "v0"
    cumulative_frames = 0

    for index in range(1, len(clip_paths)):
        cumulative_frames += base_allocations[index - 1]

        offset = cumulative_frames / fps

        output_label = f"x{index}"

        filters.append(
            f"[{previous_label}][v{index}]"
            f"xfade=transition=fade:"
            f"duration={transition_seconds:.6f}:"
            f"offset={offset:.6f}"
            f"[{output_label}]"
        )

        previous_label = output_label

    filters.append(
        f"[{previous_label}]"
        f"fps={fps},"
        f"format=yuv420p"
        f"[finalv]"
    )

    command.extend([
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[finalv]",
        "-an",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-movflags", "+faststart",
        str(output_path),
    ])

    run_command(command)

    if (
        not output_path.is_file()
        or output_path.stat().st_size == 0
    ):
        raise RuntimeError(
            "Transition video was not created."
        )

    return {
        "transition_type": "fade",
        "transition_count": len(clip_paths) - 1,
        "transition_duration_seconds": round(
            transition_seconds,
            3,
        ),
    }


# --------------------------------------------------
# SUBTITLE VALIDATION
# --------------------------------------------------

def parse_srt_time(value):
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
        hours * 3600
        + minutes * 60
        + seconds
        + milliseconds / 1000
    )


def validate_srt(audio_duration):
    if not SUBTITLE_FILE.is_file():
        raise FileNotFoundError(
            f"Subtitle file missing: {SUBTITLE_FILE}"
        )

    content = SUBTITLE_FILE.read_text(
        encoding="utf-8-sig"
    ).strip()

    if not content:
        raise ValueError(
            "Subtitle file is empty."
        )

    blocks = re.split(
        r"\n\s*\n",
        content,
    )

    previous_end = 0.0
    subtitle_count = 0

    for block in blocks:
        lines = block.splitlines()

        if len(lines) < 3:
            raise ValueError(
                "Malformed SRT subtitle block."
            )

        if not lines[0].strip().isdigit():
            raise ValueError(
                "Invalid SRT subtitle index."
            )

        times = lines[1].split("-->")

        if len(times) != 2:
            raise ValueError(
                "Invalid SRT time range."
            )

        start = parse_srt_time(times[0])
        end = parse_srt_time(times[1])

        if end <= start:
            raise ValueError(
                "Subtitle has invalid duration."
            )

        if start < previous_end - 0.001:
            raise ValueError(
                "SRT subtitles overlap or are out of order."
            )

        if end > audio_duration + 0.25:
            raise ValueError(
                "Subtitle timestamps exceed audio duration."
            )

        subtitle_text = lines[2:]

        if not any(
            line.strip()
            for line in subtitle_text
        ):
            raise ValueError(
                "Empty subtitle text."
            )

        previous_end = end
        subtitle_count += 1

    if subtitle_count == 0:
        raise ValueError(
            "No valid SRT entries found."
        )

    return {
        "subtitle_count": subtitle_count,
        "subtitle_end_seconds": round(previous_end, 3),
        "audio_duration_seconds": round(audio_duration, 3),
        "timing_difference_seconds": round(
            abs(audio_duration - previous_end),
            3,
        ),
    }


# --------------------------------------------------
# HINDI SUBTITLE SUPPORT
# --------------------------------------------------

def check_subtitle_support():
    filters_result = run_command([
        "ffmpeg",
        "-hide_banner",
        "-filters",
    ])

    if not re.search(
        r"\bsubtitles\b",
        filters_result.stdout,
    ):
        raise RuntimeError(
            "FFmpeg subtitles filter unavailable. "
            "Install FFmpeg with libass support."
        )

    font_result = run_command([
        "fc-match",
        "-f",
        "%{file}",
        "Noto Sans Devanagari",
    ])

    font_path = font_result.stdout.strip()

    if (
        not font_path
        or not Path(font_path).is_file()
    ):
        raise RuntimeError(
            "Noto Sans Devanagari font not found."
        )

    return font_path


def escape_subtitle_path(path):
    value = str(path.resolve())

    value = value.replace("\\", "\\\\")
    value = value.replace(":", "\\:")
    value = value.replace("'", "\\'")
    value = value.replace(",", "\\,")

    return value


def burn_subtitles(input_video, output_video, settings):
    audio_info = get_audio_info(find_audio())

    srt_info = validate_srt(
        audio_info["duration"]
    )

    font_path = check_subtitle_support()

    subtitle_path = escape_subtitle_path(
        SUBTITLE_FILE
    )

    subtitle_filter = (
        f"subtitles=filename='{subtitle_path}':"
        "charenc=UTF-8:"
        "force_style='FontName=Noto Sans Devanagari,"
        "FontSize=24,"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,"
        "BorderStyle=1,"
        "Outline=2,"
        "Shadow=1,"
        "Alignment=2,"
        "MarginV=45'"
    )

    print("Burning Hindi subtitles...")

    run_command([
        "ffmpeg",
        "-y",
        "-i", str(input_video),
        "-vf", subtitle_filter,
        "-an",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-r", str(settings["fps"]),
        str(output_video),
    ])

    if (
        not output_video.is_file()
        or output_video.stat().st_size == 0
    ):
        raise RuntimeError(
            "Hindi subtitle rendering failed."
        )

    return {
        "subtitle_count": srt_info["subtitle_count"],
        "subtitle_end_seconds": srt_info["subtitle_end_seconds"],
        "font_path": font_path,
        "font_family": "Noto Sans Devanagari",
        "subtitle_filter": "available_and_rendered",
    }


# --------------------------------------------------
# FINAL VIDEO INFORMATION
# --------------------------------------------------

def get_video_info(video_path):
    result = run_command([
        "ffprobe",
        "-v", "error",
        "-show_entries",
        "format=duration:"
        "stream=codec_type,width,height,r_frame_rate,duration",
        "-of", "json",
        str(video_path),
    ])

    data = json.loads(result.stdout)

    streams = data.get("streams", [])

    video_stream = next(
        (
            stream for stream in streams
            if stream.get("codec_type") == "video"
        ),
        None,
    )

    audio_stream = next(
        (
            stream for stream in streams
            if stream.get("codec_type") == "audio"
        ),
        None,
    )

    if not video_stream:
        raise RuntimeError(
            "Final video has no video stream."
        )

    if not audio_stream:
        raise RuntimeError(
            "Final video has no audio stream."
        )

    duration = float(
        data.get("format", {}).get("duration", 0)
    )

    def stream_duration(stream):
        value = stream.get("duration")

        if value in (None, "N/A"):
            return duration

        return float(value)

    video_duration = stream_duration(video_stream)
    audio_duration = stream_duration(audio_stream)

    fps_value = video_stream.get("r_frame_rate")

    return {
        "duration": duration,
        "video_duration": video_duration,
        "audio_duration": audio_duration,
        "width": int(video_stream.get("width", 0)),
        "height": int(video_stream.get("height", 0)),
        "fps": fps_value,
        "has_video": True,
        "has_audio": True,
    }


# --------------------------------------------------
# MAIN VIDEO PRODUCTION
# --------------------------------------------------

def create_video():
    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    find_ffmpeg()

    settings = load_video_settings()

    analysis = load_json(ANALYSIS_FILE)
    images = get_current_run_images()

    audio_path = find_audio()
    audio_info = get_audio_info(audio_path)

    audio_duration = audio_info["duration"]
    fps = settings["fps"]

    total_frames = round(
        audio_duration * fps
    )

    target_duration = total_frames / fps

    if total_frames < len(images):
        raise RuntimeError(
            "Audio is too short for scene images."
        )

    weights = get_scene_weights(
        images,
        analysis,
    )

    base_allocations = allocate_frames(
        weights,
        total_frames,
    )

    transition_frames = calculate_transition_frames(
        base_allocations,
        settings["transition_duration"],
        fps,
    )

    clip_allocations = list(base_allocations)

    if transition_frames > 0:
        for index in range(len(clip_allocations) - 1):
            clip_allocations[index] += transition_frames

    print("--------------------------------------")
    print("CINEMATIC VIDEO PRODUCTION")
    print("--------------------------------------")
    print(f"Scene images: {len(images)}")
    print(f"Audio duration: {audio_duration:.3f}s")
    print(f"Target duration: {target_duration:.3f}s")
    print(
        f"Resolution: {settings['width']}x{settings['height']}"
    )
    print(f"FPS: {fps}")
    print(f"Transition frames: {transition_frames}")
    print(
        f"Transition duration: {transition_frames / fps:.3f}s"
    )
    print(f"Zoom effect: {settings['zoom_effect']}")
    print(f"Pan effect: {settings['pan_effect']}")
    print(
        f"Hindi subtitles: {settings['subtitles_enabled']}"
    )
    print("--------------------------------------")

    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)

    TEMP_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    if FINAL_VIDEO.exists():
        FINAL_VIDEO.unlink()

    clip_paths = []

    for index, (image, frames) in enumerate(
        zip(images, clip_allocations),
        start=1,
    ):
        clip_path = TEMP_DIR / f"scene_{index:04d}.mp4"

        print(
            f"Rendering scene {index}/{len(images)} "
            f"| {frames} frames"
        )

        create_scene_clip(
            image_path=image["path"],
            frame_count=frames,
            output_path=clip_path,
            index=index,
            settings=settings,
        )

        clip_paths.append(clip_path)

    silent_video = TEMP_DIR / "transition_video.mp4"

    transition_info = create_transition_video(
        clip_paths=clip_paths,
        base_allocations=base_allocations,
        transition_frames=transition_frames,
        fps=fps,
        output_path=silent_video,
    )

    video_for_audio = silent_video
    subtitle_info = None

    if settings["subtitles_enabled"]:
        captioned_video = TEMP_DIR / "captioned_video.mp4"

        subtitle_info = burn_subtitles(
            silent_video,
            captioned_video,
            settings,
        )

        video_for_audio = captioned_video

    print(
        "Combining video and final narration audio..."
    )

    run_command([
        "ffmpeg",
        "-y",
        "-i", str(video_for_audio),
        "-i", str(audio_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", f"{target_duration:.6f}",
        "-movflags", "+faststart",
        "-shortest",
        str(FINAL_VIDEO),
    ])

    if (
        not FINAL_VIDEO.is_file()
        or FINAL_VIDEO.stat().st_size == 0
    ):
        raise RuntimeError(
            "Final video was not created."
        )

    video_info = get_video_info(FINAL_VIDEO)

    if (
        video_info["width"] != settings["width"]
        or video_info["height"] != settings["height"]
    ):
        raise RuntimeError(
            "Final resolution mismatch: "
            f"{video_info['width']}x{video_info['height']}"
        )

    expected_fps = f"{fps}/1"

    if video_info["fps"] != expected_fps:
        raise RuntimeError(
            f"Final FPS mismatch: {video_info['fps']}"
        )

    duration_difference = abs(
        video_info["duration"] - audio_duration
    )

    allowed_difference = max(
        DURATION_TOLERANCE,
        1.0 / fps + 0.05,
    )

    if duration_difference > allowed_difference:
        raise RuntimeError(
            "Final video duration does not match audio. "
            f"Audio: {audio_duration:.3f}s, "
            f"Video: {video_info['duration']:.3f}s, "
            f"Difference: {duration_difference:.3f}s"
        )

    audio_video_difference = abs(
        video_info["video_duration"]
        - video_info["audio_duration"]
    )

    if audio_video_difference > allowed_difference:
        raise RuntimeError(
            "Video and audio stream duration mismatch."
        )

    report = {
        "video_file": str(
            FINAL_VIDEO.relative_to(BASE_DIR)
        ),
        "resolution": (
            f"{settings['width']}x{settings['height']}"
        ),
        "fps": fps,
        "scene_images_used": len(images),
        "image_order": "chronological_image_index",
        "audio_file": str(
            audio_path.relative_to(BASE_DIR)
        ),
        "audio_duration_seconds": round(
            audio_duration,
            3,
        ),
        "video_duration_seconds": round(
            video_info["duration"],
            3,
        ),
        "video_stream_duration_seconds": round(
            video_info["video_duration"],
            3,
        ),
        "audio_stream_duration_seconds": round(
            video_info["audio_duration"],
            3,
        ),
        "duration_difference_seconds": round(
            duration_difference,
            3,
        ),
        "video_audio_stream_difference_seconds": round(
            audio_video_difference,
            3,
        ),
        "total_video_frames": total_frames,
        "camera_movement": {
            "zoom_enabled": settings["zoom_effect"],
            "pan_enabled": settings["pan_effect"],
            "movement": "alternating_directional_zoom_and_pan",
        },
        "transitions": transition_info,
        "subtitles_enabled": settings["subtitles_enabled"],
        "subtitle_file": (
            str(SUBTITLE_FILE.relative_to(BASE_DIR))
            if settings["subtitles_enabled"]
            else None
        ),
        "subtitle_render_report": subtitle_info,
        "video_stream_verified": video_info["has_video"],
        "audio_stream_verified": video_info["has_audio"],
        "status": "completed",
    }

    save_json(
        VIDEO_REPORT,
        report,
    )

    print("\n--------------------------------------")
    print("VIDEO CREATED AND VERIFIED")
    print("--------------------------------------")
    print(f"Output: {FINAL_VIDEO}")
    print(
        f"Duration: {video_info['duration']:.3f}s"
    )
    print(
        f"Resolution: "
        f"{video_info['width']}x{video_info['height']}"
    )
    print(f"FPS: {video_info['fps']}")
    print(
        f"Audio/video difference: "
        f"{duration_difference:.3f}s"
    )
    print(
        f"Transitions: "
        f"{transition_info['transition_count']}"
    )
    print(
        f"Hindi subtitles: "
        f"{settings['subtitles_enabled']}"
    )
    print("--------------------------------------")

    return FINAL_VIDEO


if __name__ == "__main__":
    create_video()
