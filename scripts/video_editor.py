
"""
Cinematic Horror Story - Video Editor

Features:
- Uses only successful images from the current image generation report.
- Preserves chronological scene order.
- Matches total video frames to narration duration.
- Produces 1920x1080, 24 FPS MP4.
- Burns Hindi subtitles into the video when enabled.
- Verifies the final video using ffprobe.
- Saves a detailed video production report.
"""

import json
import math
import shutil
import subprocess

from pathlib import Path


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

WIDTH = 1920
HEIGHT = 1080
FPS = 24

DURATION_TOLERANCE = 0.15


def run_command(command, cwd=None):
    """Run an external command and report errors."""

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
            + result.stderr[-5000:]
        )

    return result


def find_ffmpeg():
    """Check FFmpeg and FFprobe availability."""

    for executable in ("ffmpeg", "ffprobe"):
        try:
            subprocess.run(
                [executable, "-version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
        except Exception as error:
            raise RuntimeError(
                f"{executable} is not installed or not in PATH."
            ) from error


def load_json(path):
    """Load and validate a JSON object."""

    if not path.exists():
        raise FileNotFoundError(
            f"Required file not found: {path}"
        )

    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError(
            f"Invalid JSON object: {path}"
        )

    return data


def should_burn_subtitles():
    """Check subtitle settings from config.json."""

    config = load_json(CONFIG_FILE)

    subtitle_settings = config.get("subtitles", {})

    enabled = subtitle_settings.get("enabled", False)
    burn = subtitle_settings.get("burn_into_video", False)

    return bool(enabled and burn)


def find_audio():
    """Find the best available final narration audio."""

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
        "Narration audio not found in output/audio/. "
        "Expected final_audio.wav or narration.wav."
    )


def get_audio_duration(audio_path):
    """Read actual audio duration using FFprobe."""

    result = run_command([
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ])

    try:
        duration = float(result.stdout.strip())
    except (ValueError, TypeError):
        raise RuntimeError(
            "Could not read valid audio duration."
        )

    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError(
            f"Invalid audio duration: {duration}"
        )

    return duration


def get_current_run_images():
    """
    Read successful image paths from the current image report.
    Old images from other folders are not included.
    """

    report = load_json(IMAGE_REPORT_FILE)

    scene_results = report.get("scene_images", [])

    if not isinstance(scene_results, list):
        raise ValueError(
            "Image report does not contain a valid scene_images list."
        )

    successful = [
        item
        for item in scene_results
        if isinstance(item, dict)
        and item.get("status") == "success"
        and item.get("image_path")
    ]

    successful.sort(
        key=lambda item: int(item.get("image_index", 0))
    )

    images = []

    for item in successful:
        image_path = Path(item["image_path"])

        if not image_path.is_absolute():
            image_path = BASE_DIR / image_path

        image_path = image_path.resolve()

        try:
            image_path.relative_to(IMAGE_DIR.resolve())
        except ValueError:
            raise ValueError(
                "Image report contains an image outside "
                f"output/images/: {image_path}"
            )

        if not image_path.is_file():
            raise FileNotFoundError(
                f"Reported scene image is missing: {image_path}"
            )

        images.append({
            "path": image_path,
            "scene_number": item.get("scene_number"),
            "image_index": item.get("image_index"),
            "story_event": item.get("story_event", ""),
        })

    if not images:
        raise FileNotFoundError(
            "No successful current-run scene images found. "
            "Check image_generation_report.json."
        )

    return images


def get_scene_weights(images, analysis):
    """
    Use storyboard event descriptions to distribute visual time.
    These descriptions are not used as voiceover.
    """

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

        weight = max(
            len(str(event_text).strip()),
            1,
        )

        weights.append(weight)

    return weights


def allocate_frames(weights, total_frames):
    """
    Allocate integer frame counts.
    The sum of all scene frames must equal total_frames.
    """

    if not weights:
        raise ValueError(
            "Cannot allocate frames without images."
        )

    if total_frames < len(weights):
        raise ValueError(
            "Audio duration is too short for the number of images "
            "at the selected FPS."
        )

    total_weight = sum(weights)

    if total_weight <= 0:
        weights = [1] * len(weights)
        total_weight = len(weights)

    exact_allocations = [
        (weight / total_weight) * total_frames
        for weight in weights
    ]

    allocations = [
        int(value)
        for value in exact_allocations
    ]

    remaining = total_frames - sum(allocations)

    remainders = sorted(
        range(len(weights)),
        key=lambda index: (
            exact_allocations[index] - allocations[index]
        ),
        reverse=True,
    )

    for index in remainders[:remaining]:
        allocations[index] += 1

    if sum(allocations) != total_frames:
        raise RuntimeError(
            "Frame allocation total does not match target."
        )

    if any(frames <= 0 for frames in allocations):
        raise RuntimeError(
            "At least one scene received zero frames."
        )

    return allocations


def create_scene_clip(
    image_path,
    frame_count,
    output_path,
    index,
):
    """Create one cinematic still-image video clip."""

    if index % 2 == 0:
        zoom = "min(zoom+0.0005,1.08)"
    else:
        zoom = (
            "if(lte(zoom,1.0),"
            "1.08,"
            "max(1.0,zoom-0.0005))"
        )

    filter_string = (
        "scale=2200:1238:"
        "force_original_aspect_ratio=increase,"
        "crop=2200:1238,"
        f"zoompan=z='{zoom}':"
        "x='iw/2-(iw/zoom/2)':"
        "y='ih/2-(ih/zoom/2)':"
        f"d={frame_count}:"
        f"s={WIDTH}x{HEIGHT}:"
        f"fps={FPS},"
        "setsar=1,"
        "format=yuv420p"
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
        "-r", str(FPS),
        str(output_path),
    ])


def escape_subtitle_path(path):
    """
    Escape a subtitle path for FFmpeg's subtitles filter.
    """

    value = str(path.resolve())

    value = value.replace("\\", "\\\\")
    value = value.replace(":", "\\:")
    value = value.replace("'", "\\'")

    return value


def burn_subtitles(input_video, output_video):
    """Burn Hindi SRT subtitles into the silent video."""

    if not SUBTITLE_FILE.is_file():
        raise FileNotFoundError(
            "Subtitle file missing: "
            "output/subtitles/narration.srt"
        )

    if SUBTITLE_FILE.stat().st_size == 0:
        raise ValueError(
            "Subtitle file is empty."
        )

    subtitle_path = escape_subtitle_path(
        SUBTITLE_FILE
    )

    subtitle_filter = (
        f"subtitles='{subtitle_path}':"
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

    print("Burning Hindi subtitles into video...")

    run_command([
        "ffmpeg",
        "-y",
        "-i", str(input_video),
        "-vf", subtitle_filter,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-r", str(FPS),
        "-an",
        str(output_video),
    ])

    if not output_video.is_file() or output_video.stat().st_size == 0:
        raise RuntimeError(
            "Subtitle-burned video was not created."
        )

    return output_video


def get_video_info(video_path):
    """Read final MP4 metadata using FFprobe."""

    result = run_command([
        "ffprobe",
        "-v", "error",
        "-show_entries",
        "format=duration:"
        "stream=codec_type,width,height,r_frame_rate,duration",
        "-of", "json",
        str(video_path),
    ])

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "Could not parse final video information."
        ) from error

    streams = data.get("streams", [])
    format_data = data.get("format", {})

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
            "Final MP4 does not contain a video stream."
        )

    if not audio_stream:
        raise RuntimeError(
            "Final MP4 does not contain an audio stream."
        )

    duration = float(
        format_data.get("duration", 0)
    )

    return {
        "duration": duration,
        "width": int(video_stream.get("width", 0)),
        "height": int(video_stream.get("height", 0)),
        "fps": video_stream.get("r_frame_rate"),
        "has_audio": True,
    }


def create_video():
    """Run the complete cinematic video editing process."""

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    find_ffmpeg()

    analysis = load_json(ANALYSIS_FILE)
    images = get_current_run_images()

    audio_path = find_audio()
    audio_duration = get_audio_duration(audio_path)

    burn_subtitles_enabled = should_burn_subtitles()

    total_frames = round(
        audio_duration * FPS
    )

    target_video_duration = total_frames / FPS

    if total_frames <= 0:
        raise RuntimeError(
            "Audio duration is too short to create a video."
        )

    weights = get_scene_weights(
        images,
        analysis,
    )

    frame_allocations = allocate_frames(
        weights,
        total_frames,
    )

    print("--------------------------------------")
    print("CINEMATIC VIDEO PRODUCTION")
    print("--------------------------------------")
    print(f"Scene images: {len(images)}")
    print(f"Audio duration: {audio_duration:.3f} seconds")
    print(f"Target video frames: {total_frames}")
    print(f"Target video duration: {target_video_duration:.3f} seconds")
    print(f"Resolution: {WIDTH}x{HEIGHT}")
    print(f"FPS: {FPS}")
    print(f"Burn subtitles: {burn_subtitles_enabled}")
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
        zip(images, frame_allocations),
        start=1,
    ):
        clip_path = TEMP_DIR / (
            f"scene_{index:04d}.mp4"
        )

        duration = frames / FPS

        print(
            f"Rendering scene {index}/{len(images)} "
            f"| {duration:.2f}s | {frames} frames"
        )

        create_scene_clip(
            image["path"],
            frames,
            clip_path,
            index,
        )

        clip_paths.append(clip_path)

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

    silent_video = TEMP_DIR / "silent_video.mp4"

    run_command([
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        "-an",
        str(silent_video),
    ], cwd=TEMP_DIR)

    video_for_audio = silent_video

    if burn_subtitles_enabled:
        captioned_video = TEMP_DIR / "captioned_video.mp4"

        video_for_audio = burn_subtitles(
            silent_video,
            captioned_video,
        )

    print(
        "Combining video with narration and sound design..."
    )

    run_command([
        "ffmpeg",
        "-y",
        "-i", str(video_for_audio),
        "-i", str(audio_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", f"{target_video_duration:.6f}",
        "-movflags", "+faststart",
        "-shortest",
        str(FINAL_VIDEO),
    ])

    if not FINAL_VIDEO.exists() or FINAL_VIDEO.stat().st_size == 0:
        raise RuntimeError(
            "Final video was not created correctly."
        )

    video_info = get_video_info(FINAL_VIDEO)

    if (
        video_info["width"] != WIDTH
        or video_info["height"] != HEIGHT
    ):
        raise RuntimeError(
            "Final video resolution mismatch: "
            f"{video_info['width']}x{video_info['height']}"
        )

    if video_info["fps"] != f"{FPS}/1":
        raise RuntimeError(
            f"Final video FPS mismatch: {video_info['fps']}"
        )

    duration_difference = abs(
        video_info["duration"] - audio_duration
    )

    if duration_difference > DURATION_TOLERANCE:
        raise RuntimeError(
            "Final video duration does not match narration. "
            f"Audio: {audio_duration:.3f}s, "
            f"Video: {video_info['duration']:.3f}s, "
            f"Difference: {duration_difference:.3f}s"
        )

    report = {
        "video_file": str(
            FINAL_VIDEO.relative_to(BASE_DIR)
        ),
        "resolution": f"{WIDTH}x{HEIGHT}",
        "fps": FPS,
        "scene_images_used": len(images),
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
        "duration_difference_seconds": round(
            duration_difference,
            3,
        ),
        "total_video_frames": total_frames,
        "subtitles_enabled": burn_subtitles_enabled,
        "subtitle_file": (
            str(SUBTITLE_FILE.relative_to(BASE_DIR))
            if burn_subtitles_enabled
            else None
        ),
        "status": "completed",
    }

    with open(
        VIDEO_REPORT,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            report,
            file,
            indent=4,
            ensure_ascii=False,
        )

    print("\nVIDEO CREATED AND VERIFIED")
    print(f"Output: {FINAL_VIDEO}")
    print(
        f"Final duration: {video_info['duration']:.3f} seconds"
    )
    print(
        f"Audio/video difference: {duration_difference:.3f} seconds"
    )
    print(
        f"Hindi subtitles burned: {burn_subtitles_enabled}"
    )

    return FINAL_VIDEO


if __name__ == "__main__":
    create_video()
