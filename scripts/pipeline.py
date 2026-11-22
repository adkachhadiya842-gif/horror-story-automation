
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"

STORY_FILE = INPUT_DIR / "story.txt"

PIPELINE_STEPS = [
    ("Story Analysis", "story_analyzer.py"),
    ("Image Generation", "image_generator.py"),
    ("Voice Generation", "voice_generator.py"),
    ("Subtitle Generation", "subtitle_generator.py"),
    ("Sound Design", "sound_designer.py"),
    ("Video Editing", "video_editor.py"),
    ("Thumbnail Generation", "thumbnail_generator.py"),
]


def save_report(report):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    report_path = OUTPUT_DIR / "pipeline_report.json"

    with open(report_path, "w", encoding="utf-8") as file:
        json.dump(report, file, indent=4, ensure_ascii=False)

    return report_path


def check_requirements():
    if not STORY_FILE.exists():
        raise FileNotFoundError(
            "Story file missing: input/story.txt"
        )

    story = STORY_FILE.read_text(encoding="utf-8").strip()

    if not story:
        raise ValueError(
            "input/story.txt is empty. Paste your horror story first."
        )

    if not (BASE_DIR / "requirements.txt").exists():
        raise FileNotFoundError("requirements.txt is missing.")

    for step_name, script_name in PIPELINE_STEPS:
        script_path = SCRIPTS_DIR / script_name

        if not script_path.exists():
            raise FileNotFoundError(
                f"Missing script for {step_name}: {script_path}"
            )


def run_step(step_name, script_name):
    script_path = SCRIPTS_DIR / script_name

    print("\n" + "=" * 60)
    print(f"STARTING: {step_name}")
    print("=" * 60)

    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=str(BASE_DIR),
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"{step_name} failed with exit code {result.returncode}"
        )

    print(f"\nCOMPLETED: {step_name}")


def main():
    started_at = datetime.now().isoformat(timespec="seconds")

    report = {
        "started_at": started_at,
        "status": "running",
        "completed_steps": [],
        "failed_step": None
    }

    print("\nHORROR STORY AUTOMATION")
    print("Starting complete production pipeline...\n")

    try:
        check_requirements()

        for step_name, script_name in PIPELINE_STEPS:
            run_step(step_name, script_name)
            report["completed_steps"].append(step_name)
            save_report(report)

        report["status"] = "completed"
        report["finished_at"] = datetime.now().isoformat(
            timespec="seconds"
        )

        save_report(report)

        print("\n" + "=" * 60)
        print("ALL PIPELINE STEPS COMPLETED SUCCESSFULLY")
        print("=" * 60)
        print("Check the output folder for your final video and thumbnail.")

    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        report["finished_at"] = datetime.now().isoformat(
            timespec="seconds"
        )

        save_report(report)

        print("\nPIPELINE FAILED")
        print(str(error))

        raise


if __name__ == "__main__":
    main()
