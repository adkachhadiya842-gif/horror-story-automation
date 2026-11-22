
"""
Hindi Horror Story Automation
Story Analyzer - Visual Scene Breakdown

Rules:
- Original story remains the exact narration source.
- AI is used only for visual analysis.
- Scene fields are compatible with image_generator.py.
- Scene count target adapts to story length.
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
MAX_TARGET_SCENES = 60
MIN_TARGET_SCENES = 12
OUTPUT_TOKENS = 16000


def setup_logging():
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
    """Read original story without rewriting or modifying it."""

    if not STORY_FILE.exists():
        raise FileNotFoundError(
            f"Story file not found: {STORY_FILE}"
        )

    with open(
        STORY_FILE,
        "r",
        encoding="utf-8-sig",
    ) as file:
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


def calculate_scene_target(story):
    """
    Estimate a useful number of visual scenes from story length.

    This is a target for AI analysis, not a guarantee.
    """

    story_length = len(story.strip())

    estimated = round(story_length / 180)

    target = max(
        MIN_TARGET_SCENES,
        min(estimated, MAX_TARGET_SCENES),
    )

    return target


def get_generation_settings(config):
    generation = config.get("generation", {})

    if not isinstance(generation, dict):
        generation = {}

    return {
        "gemini_model": (
            generation.get("text_model")
            or DEFAULT_GEMINI_MODEL
        ),
        "openrouter_model": (
            generation.get("openrouter_text_model")
            or DEFAULT_OPENROUTER_MODEL
        ),
        "max_retries": max(
            1,
            min(int(generation.get("max_retries", 3)), 5),
        ),
        "retry_delay": max(
            1,
            int(generation.get("retry_delay_seconds", 5)),
        ),
    }


def build_analysis_prompt(story, target_scenes):
    """Create the complete visual analysis prompt."""

    return f"""
You are a professional Hindi horror film director,
storyboard artist and visual continuity supervisor.

Analyze the COMPLETE original Hindi horror story below.

YOUR JOB:
Create a detailed chronological visual storyboard
for cinematic image generation.

IMPORTANT STORY PRESERVATION RULES:

1. Preserve every original character name.
2. Preserve all character relationships.
3. Preserve the exact chronological story order.
4. Preserve all important events and reveals.
5. Do not invent characters.
6. Do not invent locations or subplots.
7. Do not change the ending.
8. Do not change the meaning of any dialogue.
9. Do not add events that are not in the original story.
10. Do not rewrite or shorten the original story.
11. Do not generate the final voiceover.
12. Scene narration is only a visual reference.
13. The original story will independently be used for voiceover.

SCENE BREAKDOWN:

TARGET SCENE COUNT: approximately {target_scenes} scenes.

Create approximately {target_scenes} chronological visual scenes.
A reasonable variation is acceptable when the story requires it.

Do not force duplicate scenes just to reach the target.

Create a new scene whenever an important visual moment changes, such as:

- A new location or time.
- A character entering or leaving.
- An important action.
- A significant facial expression or reaction.
- A suspenseful discovery.
- A supernatural appearance.
- An important object being revealed.
- A major dialogue moment that has a distinct visual.
- A change in danger, emotion or atmosphere.
- A major twist or ending reveal.

Do not combine several unrelated events into one image.

Each scene must represent ONE clear visual moment.

For every scene:
- Write a detailed visual description.
- Specify characters physically visible.
- Specify the location.
- Specify the camera angle.
- Specify lighting and atmosphere.
- Preserve recurring character appearances.
- Preserve recurring location details.
- Do not invent visual facts that are not supported by the story.

CHARACTER CONTINUITY:

For each recurring character, provide:
- Exact name.
- Age only if stated or clearly established.
- Physical appearance.
- Hairstyle and facial details when supported.
- Clothing.
- Role in the original story.
- Visual consistency notes.

LOCATION CONTINUITY:

For each recurring location, provide:
- Exact location name.
- Architecture and environment.
- Atmosphere.
- Lighting.
- Recurring visual details.

ENTITY CONTINUITY:

For each supernatural entity, provide:
- Name or story-supported identity.
- Physical appearance.
- Clothing if described.
- Behavior.
- Distinctive visual characteristics.

IMPORTANT:
Do not assume that two similar-looking entities are the same
unless the story establishes that connection.

OUTPUT FORMAT:

Return ONLY valid JSON.
Do not use Markdown code fences.
Do not add explanations outside JSON.

Use this exact structure:

{{
  "title": "Original story title",
  "language": "Hindi",
  "genre": "Horror",
  "story_summary": "Short production reference summary",
  "ending_summary": "Summary of the actual original ending",

  "characters": [
    {{
      "name": "Exact character name",
      "age": "Age if known",
      "appearance": "Physical appearance",
      "clothing": "Clothing details",
      "personality": "Personality if established",
      "role": "Role in the story",
      "visual_consistency_notes": "Details to preserve"
    }}
  ],

  "locations": [
    {{
      "name": "Exact location name",
      "description": "Environment and architecture",
      "atmosphere": "Mood and environment",
      "lighting": "Lighting details",
      "visual_consistency_notes": "Recurring location details"
    }}
  ],

  "entities": [
    {{
      "name": "Entity name",
      "description": "Physical appearance",
      "behavior": "Behavior in the story",
      "visual_consistency_notes": "Details to preserve"
    }}
  ],

  "scenes": [
    {{
      "scene_number": 1,
      "scene_title": "Short scene title",
      "story_event": "Exact event represented in this scene",
      "narration": "Short visual reference only",
      "visual_description": "Detailed cinematic visual description",
      "image_prompt": "Detailed English image-generation prompt",
      "characters_present": ["Exact character names"],
      "entities_present": ["Entity names if visible"],
      "location": "Exact location name",
      "camera_angle": "Cinematic camera angle",
      "lighting": "Lighting details",
      "atmosphere": "Scene atmosphere",
      "visual_consistency_notes": "Details to preserve"
    }}
  ]
}}

FIELD RULES:

- characters_present must be a JSON array.
- entities_present must be a JSON array.
- location must be a string.
- story_event must describe the actual story event.
- visual_description must describe only what is visible.
- Do not use invented character names.
- Use the same spelling of character names in every scene.
- Keep scene numbers sequential starting from 1.
- Every important story event must appear in the storyboard.
- The ending must receive sufficient visual coverage.

If a detail is unknown, use a neutral description.
Do not invent specific factual details.

COMPLETE ORIGINAL STORY STARTS:

{story}

COMPLETE ORIGINAL STORY ENDS.
"""


def extract_json(response_text):
    if (
        not isinstance(response_text, str)
        or not response_text.strip()
    ):
        raise ValueError("AI returned an empty response.")

    text = response_text.strip()

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
        raise ValueError(
            "AI response does not contain a JSON object."
        )

    parsed = json.loads(text[start:end + 1])

    if not isinstance(parsed, dict):
        raise ValueError(
            "AI analysis must be a JSON object."
        )

    return parsed


def normalize_string(value):
    if value is None:
        return ""

    if isinstance(value, dict):
        return str(value.get("name", "")).strip()

    if isinstance(value, list):
        return ", ".join(
            str(item).strip()
            for item in value
            if item
        )

    return str(value).strip()


def normalize_name_list(value):
    if value is None:
        return []

    if isinstance(value, str):
        value = [value]

    if not isinstance(value, list):
        return []

    names = []

    for item in value:
        name = normalize_string(item)

        if name and name not in names:
            names.append(name)

    return names


def normalize_analysis(analysis, target_scenes):
    """Normalize AI response to match image_generator.py."""

    if not isinstance(analysis, dict):
        raise ValueError(
            "Analysis must be a JSON object."
        )

    scenes = analysis.get("scenes")

    if not isinstance(scenes, list) or not scenes:
        raise ValueError(
            "AI did not return any visual scenes."
        )

    # Normalize character reference data.
    characters = analysis.get("characters", [])

    if not isinstance(characters, list):
        characters = []

    normalized_characters = []

    for character in characters:
        if not isinstance(character, dict):
            continue

        name = normalize_string(character.get("name"))

        if not name:
            continue

        character["name"] = name

        if not character.get("appearance"):
            character["appearance"] = (
                character.get("description", "")
            )

        character.setdefault("age", "Not specified")
        character.setdefault("clothing", "")
        character.setdefault("personality", "")
        character.setdefault("role", "")
        character.setdefault("visual_consistency_notes", "")

        normalized_characters.append(character)

    # Normalize locations.
    locations = analysis.get("locations", [])

    if not isinstance(locations, list):
        locations = []

    normalized_locations = []

    for location in locations:
        if not isinstance(location, dict):
            continue

        name = normalize_string(location.get("name"))

        if not name:
            continue

        location["name"] = name
        location.setdefault("description", "")
        location.setdefault("atmosphere", "")
        location.setdefault("lighting", "")
        location.setdefault("visual_consistency_notes", "")

        normalized_locations.append(location)

    # Normalize entities.
    entities = analysis.get("entities", [])

    if not isinstance(entities, list):
        entities = []

    normalized_entities = []

    for entity in entities:
        if not isinstance(entity, dict):
            continue

        name = normalize_string(entity.get("name"))

        if not name:
            continue

        entity["name"] = name
        entity.setdefault("description", "")
        entity.setdefault("behavior", "")
        entity.setdefault("visual_consistency_notes", "")

        normalized_entities.append(entity)

    normalized_scenes = []

    for index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            LOGGER.warning(
                "Ignoring invalid scene at position %s",
                index,
            )
            continue

        scene["scene_number"] = index

        # Support the previous analyzer's field names.
        if not scene.get("characters_present"):
            scene["characters_present"] = (
                scene.get("characters", [])
            )

        scene["characters_present"] = normalize_name_list(
            scene.get("characters_present")
        )

        scene["entities_present"] = normalize_name_list(
            scene.get("entities_present")
            or scene.get("entities")
        )

        scene["location"] = normalize_string(
            scene.get("location")
        )

        if not scene.get("story_event"):
            scene["story_event"] = (
                scene.get("scene_title")
                or scene.get("narration")
                or scene.get("visual_description")
                or ""
            )

        if not scene.get("visual_description"):
            scene["visual_description"] = (
                scene.get("description")
                or scene.get("visual")
                or scene.get("image_prompt")
                or ""
            )

        if not scene.get("image_prompt"):
            scene["image_prompt"] = (
                scene.get("visual_description", "")
            )

        if not scene.get("narration"):
            scene["narration"] = (
                scene.get("visual_description", "")
            )

        scene.setdefault("scene_title", f"Scene {index}")
        scene.setdefault("camera_angle", "Cinematic wide shot")
        scene.setdefault("lighting", "Atmospheric cinematic lighting")
        scene.setdefault("atmosphere", "Suspenseful horror atmosphere")
        scene.setdefault("visual_consistency_notes", "")

        normalized_scenes.append(scene)

    if not normalized_scenes:
        raise ValueError(
            "No valid visual scenes were returned."
        )

    analysis["scenes"] = normalized_scenes
    analysis["total_scenes"] = len(normalized_scenes)
    analysis["target_scenes"] = target_scenes

    analysis["characters"] = normalized_characters
    analysis["locations"] = normalized_locations
    analysis["entities"] = normalized_entities

    analysis.setdefault("title", "Untitled Horror Story")
    analysis.setdefault("language", "Hindi")
    analysis.setdefault("genre", "Horror")
    analysis.setdefault("story_summary", "")
    analysis.setdefault("ending_summary", "")

    if len(normalized_scenes) < target_scenes * 0.65:
        LOGGER.warning(
            "AI returned fewer scenes than expected. "
            "Target: %s | Returned: %s",
            target_scenes,
            len(normalized_scenes),
        )

    return analysis


def generate_with_gemini(prompt, model):
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
            max_output_tokens=OUTPUT_TOKENS,
        ),
    )

    if not response or not response.text:
        raise RuntimeError(
            "Gemini returned an empty response."
        )

    return response.text


def generate_with_openrouter(prompt, model):
    api_key = os.getenv(
        "OPENROUTER_API_KEY",
        "",
    ).strip()

    if not api_key:
        raise ValueError(
            "OPENROUTER_API_KEY is missing."
        )

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a professional horror storyboard artist. "
                    "Return only valid JSON. Follow the exact requested "
                    "schema. Preserve the original story and chronology. "
                    "Do not invent story events."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "response_format": {
            "type": "json_object",
        },
        "temperature": 0.2,
        "max_tokens": OUTPUT_TOKENS,
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
            timeout=300,
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
            f"OpenRouter HTTP {error.code}: "
            f"{error_body[:1500]}"
        ) from error

    choices = response_data.get("choices", [])

    if not choices:
        raise RuntimeError(
            "OpenRouter returned no choices."
        )

    message = choices[0].get("message", {})
    content = message.get("content")

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

    finish_reason = choices[0].get("finish_reason")

    if finish_reason == "length":
        raise RuntimeError(
            "OpenRouter response was truncated by token limit."
        )

    return content


def save_analysis(analysis):
    OUTPUT_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

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
    create_project_directories()

    config = load_config()
    settings = get_generation_settings(config)

    original_story = read_story()

    target_scenes = calculate_scene_target(
        original_story
    )

    LOGGER.info(
        "Target visual scenes: %s",
        target_scenes,
    )

    prompt = build_analysis_prompt(
        original_story,
        target_scenes,
    )

    analysis = None
    provider_used = None
    model_used = None
    last_error = None

    # First provider: Gemini.
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

            parsed = extract_json(response_text)

            analysis = normalize_analysis(
                parsed,
                target_scenes,
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
                time.sleep(
                    settings["retry_delay"]
                )

    # Second provider: OpenRouter fallback.
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

                parsed = extract_json(response_text)

                analysis = normalize_analysis(
                    parsed,
                    target_scenes,
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
                    time.sleep(
                        settings["retry_delay"]
                    )

    if analysis is None:
        raise RuntimeError(
            "Both AI providers failed to analyze the story. "
            f"Last error: {last_error}"
        )

    # Original story is always the source of narration.
    # AI-generated summaries never replace this text.
    analysis["original_story"] = original_story
    analysis["narration_script"] = original_story
    analysis["narration_source"] = "input/story.txt"

    analysis["original_story_characters"] = len(
        original_story
    )

    analysis["narration_characters"] = len(
        original_story
    )

    analysis["scene_narration_is_voiceover"] = False

    analysis["analysis_provider"] = provider_used
    analysis["analysis_model"] = model_used

    save_analysis(analysis)

    LOGGER.info("--------------------------------------")
    LOGGER.info("STORY ANALYSIS COMPLETED")
    LOGGER.info("Title: %s", analysis.get("title"))
    LOGGER.info("Target scenes: %s", target_scenes)
    LOGGER.info("Actual scenes: %s", analysis.get("total_scenes"))
    LOGGER.info("Original characters: %s", len(original_story))
    LOGGER.info("Narration characters: %s", len(analysis["narration_script"]))
    LOGGER.info("Provider: %s", provider_used)
    LOGGER.info("Model: %s", model_used)
    LOGGER.info("--------------------------------------")

    return analysis


def main():
    setup_logging()

    LOGGER.info(
        "Starting Hindi horror story analysis."
    )

    analyze_story()


if __name__ == "__main__":
    try:
        main()

    except Exception:
        logging.exception(
            "Story analysis failed."
        )
        raise
