
import json
import subprocess
import sys
import time

from pathlib import Path
from datetime import datetime, timezone

from PIL import Image

from scripts.utils import OUTPUT_DIR, logger
from scripts.validators import run_all_validations


BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"

VIDEO_FILE = OUTPUT_DIR / "final_video.mp4"
THUMBNAIL_FILE = OUTPUT_DIR / "thumbnail" / "youtube_thumbnail.jpg"

PIPELINE_STEPS = [
    ("Story Analysis", "story_analyzer.py"),
    ("Image Generation", "image_generator.py"),
    ("Voice Generation", "voice_generator.py"),
    ("Subtitle Generation", "subtitle_generator.py"),
    ("Sound Design", "sound_designer.py"),
    ("Video Editing", "video_editor.py"),
    ("Thumbnail Generation", "thumbnail_generator.py"),
]


def current_time():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_report(report):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    report_path = OUTPUT_DIR / "pipeline_report.json"

    with open(report_path, "w", encoding="utf-8") as file:
        json.dump(
            report,
            file,
            indent=4,
            ensure_ascii=False,
        )

    return report_path


def check_pipeline_files():
    for step_name, script_name in PIPELINE_STEPS:
        script_path = SCRIPTS_DIR / script_name

        if not script_path.is_file():
            raise FileNotFoundError(
                f"Missing script for {step_name}: {script_path}"
            )

    if not (BASE_DIR / "requirements.txt").is_file():
        raise FileNotFoundError("requirements.txt is missing.")

    logger.info("Pipeline file checks passed.")


def run_step(step_name, script_name, report):
    script_path = SCRIPTS_DIR / script_name

    print("\n" + "=" * 60)
    print(f"STARTING: {step_name}")
    print("=" * 60)

    step_report = {
        "step": step_name,
        "script": script_name,
        "status": "running",
        "started_at": current_time(),
    }

    report["current_step"] = step_name
    report["steps"].append(step_report)
    save_report(report)

    start = time.monotonic()

    try:
        result = subprocess.run(
            [sys.executable, "-u", str(script_path)],
            cwd=str(BASE_DIR),
            text=True,
            check=False,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"{step_name} failed with exit code "
                f"{result.returncode}"
            )

        step_report["status"] = "completed"
        step_report["finished_at"] = current_time()
        step_report["duration_seconds"] = round(
            time.monotonic() - start, 2
        )

        report["completed_steps"].append(step_name)
        report["current_step"] = None

        save_report(report)

        print(f"\nCOMPLETED: {step_name}")

    except Exception as error:
        step_report["status"] = "failed"
        step_report["error"] = str(error)
        step_report["finished_at"] = current_time()
        step_report["duration_seconds"] = round(
            time.monotonic() - start, 2
        )

        report["failed_step"] = step_name
        report["current_step"] = None

        save_report(report)

        raise


def verify_video():
    if not VIDEO_FILE.is_file():
        raise FileNotFoundError(
            f"Final video not found: {VIDEO_FILE}"
        )

    if VIDEO_FILE.stat().st_size < 100_000:
        raise RuntimeError(
            "Final video file is unexpectedly small."
        )

    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries",
            "stream=codec_type,width,height",
            "-show_entries",
            "format=duration",
            "-of", "json",
            str(VIDEO_FILE),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "FFprobe could not validate final video."
        )

    data = json.loads(result.stdout)

    streams = data.get("streams", [])
    video_streams = [
        stream for stream in streams
        if stream.get("codec_type") == "video"
    ]
    audio_streams = [
        stream for stream in streams
        if stream.get("codec_type") == "audio"
    ]

    if not video_streams:
        raise RuntimeError("Final video has no video stream.")

    if not audio_streams:
        raise RuntimeError("Final video has no audio stream.")

    video = video_streams[0]

    if video.get("width") != 1920 or video.get("height") != 1080:
        raise RuntimeError(
            "Final video resolution is not 1920x1080."
        )

    duration = float(data.get("format", {}).get("duration", 0))

    if duration <= 0:
        raise RuntimeError("Final video duration is invalid.")

    return {
        "file": str(VIDEO_FILE.relative_to(BASE_DIR)),
        "size_bytes": VIDEO_FILE.stat().st_size,
        "resolution": "1920x1080",
        "duration_seconds": round(duration, 2),
        "video_stream": True,
        "audio_stream": True,
        "status": "verified",
    }


def verify_thumbnail():
    if not THUMBNAIL_FILE.is_file():
        raise FileNotFoundError(
            f"Thumbnail not found: {THUMBNAIL_FILE}"
        )

    with Image.open(THUMBNAIL_FILE) as image:
        image.load()

        if image.size != (1280, 720):
            raise RuntimeError(
                f"Invalid thumbnail resolution: {image.size}"
            )

        if image.format != "JPEG":
            raise RuntimeError(
                f"Invalid thumbnail format: {image.format}"
            )

    if THUMBNAIL_FILE.stat().st_size < 10_000:
        raise RuntimeError(
            "Thumbnail file is unexpectedly small."
        )

    return {
        "file": str(THUMBNAIL_FILE.relative_to(BASE_DIR)),
        "size_bytes": THUMBNAIL_FILE.stat().st_size,
        "resolution": "1280x720",
        "format": "JPEG",
        "status": "verified",
    }


def verify_final_outputs():
    print("\nVerifying final production files...")

    video_report = verify_video()
    thumbnail_report = verify_thumbnail()

    return {
        "video": video_report,
        "thumbnail": thumbnail_report,
        "status": "verified",
    }


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    report = {
        "project": "horror-story-automation",
        "started_at": current_time(),
        "status": "running",
        "validation_status": "pending",
        "completed_steps": [],
        "failed_step": None,
        "current_step": None,
        "steps": [],
        "final_outputs": None,
    }

    print("\nHORROR STORY AUTOMATION")
    print("Starting complete production pipeline...\n")

    save_report(report)

    try:
        print("\nRunning project validations...")

        run_all_validations()
        check_pipeline_files()

        report["validation_status"] = "passed"
        report["validated_at"] = current_time()

        save_report(report)

        print("All validations passed.")

        for step_name, script_name in PIPELINE_STEPS:
            run_step(step_name, script_name, report)

        report["final_outputs"] = verify_final_outputs()

        report["status"] = "completed"
        report["finished_at"] = current_time()

        report["total_duration_seconds"] = round(
            sum(
                step.get("duration_seconds", 0)
                for step in report["steps"]
            ),
            2,
        )

        save_report(report)

        print("\n" + "=" * 60)
        print("ALL PIPELINE STEPS COMPLETED SUCCESSFULLY")
        print("=" * 60)
        print(f"Final video: {VIDEO_FILE}")
        print(f"Thumbnail: {THUMBNAIL_FILE}")

    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        report["finished_at"] = current_time()

        if report["validation_status"] == "pending":
            report["validation_status"] = "failed"

        save_report(report)

        logger.exception("Production pipeline failed.")

        print("\nPIPELINE FAILED")
        print(str(error))

        raise


if __name__ == "__main__":
    main()
