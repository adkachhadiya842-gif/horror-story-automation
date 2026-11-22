
import json
import subprocess
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
IMAGE_DIRS = [
    OUTPUT_DIR / "images",
    OUTPUT_DIR / "scenes",
    OUTPUT_DIR / "visuals",
]
AUDIO_DIR = OUTPUT_DIR / "audio"
TEMP_DIR = OUTPUT_DIR / "temp_video"
FINAL_VIDEO = OUTPUT_DIR / "final_video.mp4"

WIDTH = 1920
HEIGHT = 1080
FPS = 24


def run_command(command):
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            "FFmpeg error:\n" + result.stderr[-4000:]
        )


def find_ffmpeg():
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )
    except Exception:
        raise RuntimeError(
            "FFmpeg is not installed or is not available in PATH."
        )


def find_story_analysis():
    possible_files = [
        OUTPUT_DIR / "story_analysis.json",
        OUTPUT_DIR / "analysis" / "story_analysis.json"
    ]

    for path in possible_files:
        if path.exists():
            with open(path, "r", encoding="utf-8") as file:
                return json.load(file)

    return {}


def find_images():
    images = []

    extensions = {".png", ".jpg", ".jpeg", ".webp"}

    for directory in IMAGE_DIRS:
        if directory.exists():
            images.extend(
                file for file in directory.rglob("*")
                if file.is_file() and file.suffix.lower() in extensions
            )

    # Avoid duplicate paths
    images = list(dict.fromkeys(images))

    def natural_key(path):
        return [
            int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", path.name)
        ]

    return sorted(images, key=natural_key)


def find_audio():
    candidates = [
        AUDIO_DIR / "final_audio.wav",
        AUDIO_DIR / "narration.wav",
        AUDIO_DIR / "final_audio.mp3",
        AUDIO_DIR / "narration.mp3"
    ]

    for path in candidates:
        if path.exists():
            return path

    raise FileNotFoundError(
        "Narration audio not found in output/audio/"
    )


def get_audio_duration(audio_path):
    command = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path)
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError("Could not read audio duration.")

    return float(result.stdout.strip())


def get_scene_text(scene):
    if isinstance(scene, str):
        return scene

    if not isinstance(scene, dict):
        return ""

    for key in [
        "narration",
        "voiceover",
        "text",
        "description",
        "script"
    ]:
        value = scene.get(key)
        if isinstance(value, str) and value.strip():
            return value

    return ""


def calculate_scene_durations(analysis, image_count, total_duration):
    scenes = analysis.get("scenes", [])

    if not isinstance(scenes, list) or not scenes:
        scenes = [{} for _ in range(image_count)]

    # Match available images to scenes.
    count = min(len(scenes), image_count)
    scenes = scenes[:count]

    # Use supplied scene durations if available.
    supplied = []

    for scene in scenes:
        duration = None

        if isinstance(scene, dict):
            for key in ["duration", "duration_seconds", "estimated_duration"]:
                try:
                    value = float(scene.get(key, 0))
                    if value > 0:
                        duration = value
                        break
                except (TypeError, ValueError):
                    pass

        supplied.append(duration)

    if all(value is not None for value in supplied):
        total_supplied = sum(supplied)
        return [
            value * total_duration / total_supplied
            for value in supplied
        ]

    # Otherwise distribute time according to narration length.
    weights = []

    for scene in scenes:
        text = get_scene_text(scene)
        weights.append(max(len(text.strip()), 1))

    total_weight = sum(weights)

    return [
        total_duration * weight / total_weight
        for weight in weights
    ]


def create_scene_clip(image_path, duration, output_path, index):
    frames = max(1, round(duration * FPS))

    # Gentle alternating zoom direction.
    if index % 2 == 0:
        zoom = "min(zoom+0.0005,1.08)"
    else:
        zoom = "if(lte(zoom,1.0),1.08,max(1.0,zoom-0.0005))"

    filter_string = (
        "scale=2200:1238:force_original_aspect_ratio=increase,"
        "crop=2200:1238,"
        f"zoompan=z='{zoom}':"
        "x='iw/2-(iw/zoom/2)':"
        "y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS},"
        "setsar=1,format=yuv420p"
    )

    command = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(image_path),
        "-vf", filter_string,
        "-frames:v", str(frames),
        "-an",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        str(output_path)
    ]

    run_command(command)


def create_video():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    find_ffmpeg()

    images = find_images()

    if not images:
        raise FileNotFoundError(
            "No scene images found. Expected images inside output/images/"
        )

    audio_path = find_audio()
    total_duration = get_audio_duration(audio_path)

    if total_duration <= 0:
        raise RuntimeError("Audio duration is invalid.")

    analysis = find_story_analysis()

    durations = calculate_scene_durations(
        analysis,
        len(images),
        total_duration
    )

    print(f"Found {len(images)} images.")
    print(f"Audio duration: {total_duration:.2f} seconds.")
    print("Creating cinematic scene clips...")

    clip_paths = []

    for index, (image, duration) in enumerate(
        zip(images[:len(durations)], durations)
    ):
        clip_path = TEMP_DIR / f"scene_{index + 1:04d}.mp4"

        print(
            f"Rendering scene {index + 1}/{len(durations)} "
            f"({duration:.1f}s)"
        )

        create_scene_clip(
            image,
            duration,
            clip_path,
            index
        )

        clip_paths.append(clip_path)

    concat_file = TEMP_DIR / "concat_list.txt"

    with open(concat_file, "w", encoding="utf-8") as file:
        for clip in clip_paths:
            safe_path = clip.resolve().as_posix().replace("'", "'\\''")
            file.write(f"file '{safe_path}'\n")

    silent_video = TEMP_DIR / "silent_video.mp4"

    run_command([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        str(silent_video)
    ])

    print("Combining video with narration and sound design...")

    run_command([
        "ffmpeg", "-y",
        "-i", str(silent_video),
        "-i", str(audio_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", str(total_duration),
        "-movflags", "+faststart",
        str(FINAL_VIDEO)
    ])

    report = {
        "video_file": str(FINAL_VIDEO.relative_to(BASE_DIR)),
        "resolution": f"{WIDTH}x{HEIGHT}",
        "fps": FPS,
        "image_count": len(images),
        "audio_file": str(audio_path.relative_to(BASE_DIR)),
        "duration_seconds": round(total_duration, 2),
        "status": "completed"
    }

    with open(
        OUTPUT_DIR / "video_report.json",
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(report, file, indent=4, ensure_ascii=False)

    print("\nVIDEO CREATED SUCCESSFULLY")
    print(f"Output: {FINAL_VIDEO}")

    return FINAL_VIDEO


if __name__ == "__main__":
    create_video()
