
import json
import subprocess
import sys
import time

from pathlib import Path
from datetime import datetime, timezone

from scripts.utils import OUTPUT_DIR, logger
from scripts.validators import run_all_validations


BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"

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
        json.dump(report, file, indent=4, ensure_ascii=False)

    return report_path


def check_pipeline_files():
    """Check that all required production scripts exist."""

    for step_name, script_name in PIPELINE_STEPS:
        script_path = SCRIPTS_DIR / script_name

        if not script_path.is_file():
            raise FileNotFoundError(
                f"Missing script for {step_name}: {script_path}"
            )

    requirements_file = BASE_DIR / "requirements.txt"

    if not requirements_file.is_file():
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
        "started_at": current_time()
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
            check=False
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
        "steps": []
    }

    print("\nHORROR STORY AUTOMATION")
    print("Starting complete production pipeline...\n")

    save_report(report)

    try:
        # Step 1: Validate story, config and API credentials.
        print("\nRunning project validations...")

        run_all_validations()
        check_pipeline_files()

        report["validation_status"] = "passed"
        report["validated_at"] = current_time()
        save_report(report)

        print("All validations passed.")

        # Step 2: Run production scripts sequentially.
        for step_name, script_name in PIPELINE_STEPS:
            run_step(step_name, script_name, report)

        report["status"] = "completed"
        report["finished_at"] = current_time()

        total_duration = sum(
            step.get("duration_seconds", 0)
            for step in report["steps"]
        )

        report["total_duration_seconds"] = round(
            total_duration, 2
        )

        save_report(report)

        print("\n" + "=" * 60)
        print("ALL PIPELINE STEPS COMPLETED SUCCESSFULLY")
        print("=" * 60)
        print("Check the output folder for your final video and thumbnail.")

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
