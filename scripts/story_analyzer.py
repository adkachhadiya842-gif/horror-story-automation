
"""
Hindi Horror Story Automation
Story Analyzer

IMPORTANT:
- Original story is preserved locally and verbatim.
- AI is used ONLY for visual story analysis.
- AI-generated scene summaries are NOT used as voiceover.
"""

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request

from pathlib import Path

from google import genai
from google.genai import types

from scripts.utils import (
    OUTPUT_DIR,
    load_config,
    get_api_key,
    create_project_directories,
)


LOGGER = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STORY_FILE = PROJECT_ROOT / "input" / "story.txt"

OUTPUT_ROOT = Path(OUTPUT_DIR)
ANALYSIS_FILE = OUTPUT_ROOT / "story_analysis.json"

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
DEFAULT_OPENROUTER_MODEL = "openai/gpt-4o-mini"

MIN_STORY_CHARACTERS = 300


def setup_logging():
    """Initialize logging."""

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                OUTPUT_ROOT / "story_analyzer.log",
                encoding="utf-8",
            ),
        ],
        force=True,
    )


def read_story():
    """Read the complete original story without rewriting it."""

    if not STORY_FILE.exists():
        raise FileNotFoundError(
            f"Story file not found: {STORY_FILE}"
        )

    with open(STORY_FILE, "r", encoding="utf-8-sig") as file:
        story = file.read()

    if not story or not story.strip():
        raise ValueError("input/story.txt is empty.")

    if len(story.strip()) < MIN_STORY_CHARACTERS:
        raise ValueError(
            "Story is too short. Please check input/story.txt."
        )

    LOGGER.info(
        "Original story loaded: %s characters",
        len(story),
    )

    return story


def get_generation_settings(config):
    """Read text model settings from config."""

    if not isinstance(config, dict):
        config = {}

    generation = config.get("generation", {})

    if not isinstance(generation, dict):
        generation = {}

    gemini_model = (
        generation.get("text_model")
        or DEFAULT_GEMINI_MODEL
    )

    openrouter_model = (
        generation.get("openrouter_text_model")
        or DEFAULT_OPENROUTER_MODEL
    )

    max_retries = int(
        generation.get("max_retries", 3)
    )

    retry_delay = int(
        generation.get("retry_delay_seconds", 5)
    )

    return {
        "gemini_model": gemini_model,
        "openrouter_model": openrouter_model,
        "max_retries": max(1, min(max_retries, 5)),
        "retry_delay": max(1, retry_delay),
    }


def build_analysis_prompt(story):
    """
    Ask AI to analyze the story for visual production.

    The original story itself is saved separately by Python.
    AI must not rewrite the narration.
    """

    return f"""
You are a professional Hindi horror film director,
screenwriter, storyboard artist and visual continuity supervisor.

Analyze the COMPLETE original Hindi horror story below.

YOUR TASK:
Create a detailed visual production analysis in valid JSON.

CRITICAL STORY RULES:
1. Preserve every original character name.
2. Preserve character relationships.
3. Preserve the original story order.
4. Do not remove important events.
5. Do not invent new characters.
6. Do not invent new locations or subplots.
7. Do not change the ending.
8. Do not change the meaning of any dialogue.
9. Do not add events that are not in the original story.
10. Do not rewrite or shorten the original story.
11. Do not generate the final voiceover.
12. Scene narration is ONLY a short visual reference, NOT voiceover.

VISUAL SCENE BREAKDOWN:
- Divide the complete story into sequential cinematic visual scenes.
- Include every important event and reveal.
- Use separate scenes when the location, action, camera framing,
  character emotion or important visual detail changes.
- For a long story, aim for approximately 25-45 visual scenes
  when the story length supports it.
- Do not artificially add scenes or repeat the same moment.
- Keep all scenes in the exact chronological order.
- Each scene should describe one clear visual moment.
- Write detailed image prompts suitable for cinematic 16:9 horror images.
- Maintain consistent appearance of recurring characters.
- Maintain consistent appearance of recurring locations and entities.
- Do not put multiple unrelated moments into one scene.

OUTPUT:
Return ONLY valid JSON. No Markdown fences and no explanation.

Use this exact overall structure:

{{
  "title": "Original story title",
  "language": "Hindi",
  "genre": "Horror",
  "story_summary": "Short summary for production reference",
  "ending_summary": "Summary of the original ending",
  "characters": [
    {{
      "name": "Exact character name",
      "description": "Visual appearance and age if stated",
      "clothing": "Clothing as supported by the story",
      "role": "Role in the original story"
    }}
  ],
  "locations": [
    {{
      "name": "Location name",
      "description": "Visual environment",
      "atmosphere": "Lighting and mood"
    }}
  ],
  "entities": [
    {{
      "name": "Entity name",
      "description": "Visual appearance",
      "behavior": "Behavior shown in the story"
    }}
  ],
  "scenes": [
    {{
      "scene_number": 1,
      "scene_title": "Short scene title",
      "narration": "Short visual reference only. Never use this as the final voiceover.",
      "visual_description": "Detailed description of exactly what is visible in this moment",
      "image_prompt": "Complete cinematic image-generation prompt in English. Preserve character and location consistency. 16:9, no text, no watermark.",
      "characters": ["Character names appearing in this scene"],
      "location": "Location name",
      "mood": "Horror atmosphere",
      "camera_angle": "Suggested cinematic camera angle"
    }}
  ]
}}

If a detail is not specified in the original story, do not invent
a specific factual detail. Use a visually neutral description.

COMPLETE ORIGINAL STORY STARTS BELOW:

{story}

COMPLETE ORIGINAL STORY ENDS ABOVE.
"""


def extract_json(response_text):
    """Extract JSON safely from model output."""

    if not isinstance(response_text, str) or not response_text.strip():
        raise ValueError("AI returned an empty response.")

    text = response_text.strip()

    # Remove optional Markdown code fences.
    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\s*```$",
        "",
        text,
    )

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end <= start:
        raise ValueError("AI response does not contain valid JSON.")

    parsed = json.loads(text[start:end + 1])

    if not isinstance(parsed, dict):
        raise ValueError("AI analysis must be a JSON object.")

    return parsed


def validate_analysis(analysis):
    """Validate and normalize the visual analysis."""

    if not isinstance(analysis, dict):
        raise ValueError("Analysis must be a JSON object.")

    scenes = analysis.get("scenes")

    if not isinstance(scenes, list) or not scenes:
        raise ValueError("AI did not return any visual scenes.")

    normalized_scenes = []

    for index, scene in enumerate(scenes, start=1):

        if not isinstance(scene, dict):
            continue

        scene["scene_number"] = index

        # Support common alternate field names.
        if not scene.get("visual_description"):
            scene["visual_description"] = (
                scene.get("description")
                or scene.get("visual")
                or ""
            )

        if not scene.get("image_prompt"):
            scene["image_prompt"] = (
                scene.get("prompt")
                or scene.get("visual_description")
                or ""
            )

        if not scene.get("narration"):
            scene["narration"] = (
                scene.get("visual_description")
                or scene.get("scene_title")
                or ""
            )

        if not isinstance(scene.get("characters"), list):
            scene["characters"] = []

        normalized_scenes.append(scene)

    if not normalized_scenes:
        raise ValueError("No valid visual scenes were returned.")

    analysis["scenes"] = normalized_scenes
    analysis["total_scenes"] = len(normalized_scenes)

    # Ensure expected top-level fields exist.
    analysis.setdefault("title", "Untitled Horror Story")
    analysis.setdefault("language", "Hindi")
    analysis.setdefault("genre", "Horror")
    analysis.setdefault("story_summary", "")
    analysis.setdefault("ending_summary", "")
    analysis.setdefault("characters", [])
    analysis.setdefault("locations", [])
    analysis.setdefault("entities", [])

    return analysis


def generate_with_gemini(prompt, model):
    """Generate visual analysis using Gemini."""

    api_key = get_api_key()

    if not api_key:
        raise ValueError("GEMINI_API_KEY is missing.")

    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )

    if not response or not response.text:
        raise RuntimeError("Gemini returned an empty response.")

    return response.text


def generate_with_openrouter(prompt, model):
    """Generate visual analysis using OpenRouter fallback."""

    api_key = os.getenv("OPENROUTER_API_KEY")

    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is missing.")

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a professional horror storyboard artist. "
                    "Return only valid JSON. Follow the user's schema "
                    "and preserve the original story."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "response_format": {
            "type": "json_object"
        },
        "temperature": 0.2,
        "max_tokens": 12000,
    }

    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/",
            "X-Title": "Hindi Horror Story Automation",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=240,
        ) as response:
            response_data = json.loads(
                response.read().decode("utf-8")
            )

    except urllib.error.HTTPError as error:
        error_body = error.read().decode(
            "utf-8",
            errors="replace",
        )

        raise RuntimeError(
            f"OpenRouter HTTP {error.code}: {error_body[:1500]}"
        ) from error

    choices = response_data.get("choices", [])

    if not choices:
        raise RuntimeError(
            "OpenRouter returned no choices."
        )

    content = choices[0].get("message", {}).get("content")

    if isinstance(content, list):
        content = "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict)
        )

    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(
            "OpenRouter returned empty analysis content."
        )

    return content


def save_analysis(analysis):
    """Save final analysis JSON."""

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    with open(
        ANALYSIS_FILE,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            analysis,
            file,
            ensure_ascii=False,
            indent=2,
        )

    LOGGER.info(
        "Story analysis saved: %s",
        ANALYSIS_FILE,
    )


def analyze_story():
    """Run complete story analysis while preserving original narration."""

    create_project_directories()

    config = load_config()
    settings = get_generation_settings(config)

    # Read the original story once.
    original_story = read_story()

    prompt = build_analysis_prompt(original_story)

    analysis = None
    provider_used = None
    model_used = None
    last_error = None

    # Try Gemini first.
    for attempt in range(
        1,
        settings["max_retries"] + 1,
    ):
        try:
            LOGGER.info(
                "Gemini analysis attempt %s/%s",
                attempt,
                settings["max_retries"],
            )

            response_text = generate_with_gemini(
                prompt,
                settings["gemini_model"],
            )

            analysis = validate_analysis(
                extract_json(response_text)
            )

            provider_used = "Gemini"
            model_used = settings["gemini_model"]
            break

        except Exception as error:
            last_error = error

            LOGGER.warning(
                "Gemini attempt %s failed: %s",
                attempt,
                error,
            )

            if attempt < settings["max_retries"]:
                time.sleep(settings["retry_delay"])

    # Use OpenRouter if Gemini did not succeed.
    if analysis is None:

        LOGGER.warning(
            "Gemini failed. Trying OpenRouter fallback."
        )

        for attempt in range(
            1,
            settings["max_retries"] + 1,
        ):
            try:
                LOGGER.info(
                    "OpenRouter attempt %s/%s",
                    attempt,
                    settings["max_retries"],
                )

                response_text = generate_with_openrouter(
                    prompt,
                    settings["openrouter_model"],
                )

                analysis = validate_analysis(
                    extract_json(response_text)
                )

                provider_used = "OpenRouter"
                model_used = settings["openrouter_model"]
                break

            except Exception as error:
                last_error = error

                LOGGER.warning(
                    "OpenRouter attempt %s failed: %s",
                    attempt,
                    error,
                )

                if attempt < settings["max_retries"]:
                    time.sleep(settings["retry_delay"])

    if analysis is None:
        raise RuntimeError(
            "Both AI providers failed to analyze the story. "
            f"Last error: {last_error}"
        )

    # CRITICAL:
    # These fields come directly from input/story.txt.
    # The AI response is never allowed to replace them.
    analysis["original_story"] = original_story
    analysis["narration_script"] = original_story
    analysis["narration_source"] = "input/story.txt"

    analysis["original_story_characters"] = len(original_story)
    analysis["narration_characters"] = len(original_story)

    analysis["analysis_provider"] = provider_used
    analysis["analysis_model"] = model_used

    # Explicitly mark scene narration as visual reference only.
    analysis["scene_narration_is_voiceover"] = False

    save_analysis(analysis)

    LOGGER.info("--------------------------------------")
    LOGGER.info("STORY ANALYSIS COMPLETED")
    LOGGER.info("Title: %s", analysis.get("title"))
    LOGGER.info("Scenes: %s", analysis.get("total_scenes"))
    LOGGER.info("Original story characters: %s", len(original_story))
    LOGGER.info("Narration characters: %s", len(analysis["narration_script"]))
    LOGGER.info("Provider: %s", provider_used)
    LOGGER.info("Model: %s", model_used)
    LOGGER.info("--------------------------------------")

    return analysis


def main():
    """Main entry point."""

    setup_logging()

    LOGGER.info("Starting Hindi horror story analysis.")

    analyze_story()


if __name__ == "__main__":
    try:
        main()

    except Exception:
        logging.exception("Story analysis failed.")
        raise
