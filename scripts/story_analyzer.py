
"""
Hindi Horror Story Automation
Optimized Story Analyzer - Visual Scene Breakdown

Rules:
- input/story.txt remains the ONLY narration source.
- AI is used only for visual analysis.
- Scene fields remain compatible with image_generator.py.
- Scene count adapts to story length.
- Gemini gets one attempt before OpenRouter fallback.
"""

import json
import logging
import math
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

# Require at least 75% of the requested scenes.
MIN_SCENE_COVERAGE = 0.75

OPENROUTER_TIMEOUT = 240
MAX_RETRY_WAIT = 120


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
    """Calculate visual scene target from original story length."""

    story_length = len(story.strip())

    estimated = round(story_length / 180)

    return max(
        MIN_TARGET_SCENES,
        min(estimated, MAX_TARGET_SCENES),
    )


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
        "retry_delay": max(
            5,
            int(generation.get("retry_delay_seconds", 5)),
        ),
    }


def build_analysis_prompt(story, target_scenes, retry=False):
    """
    Build a compact storyboard prompt.

    Short scene descriptions reduce output size while retaining
    the fields required by image_generator.py.
    """

    retry_instruction = ""

    if retry:
        retry_instruction = f"""
IMPORTANT RETRY REQUIREMENT:
The previous response did not satisfy the scene coverage requirement.
Return at least {math.ceil(target_scenes * MIN_SCENE_COVERAGE)}
valid scenes, aiming for exactly {target_scenes}.
Keep each scene description concise.
Do not omit important events or the ending.
"""

    return f"""
You are a professional Hindi horror film director,
storyboard artist and visual continuity supervisor.

Analyze the COMPLETE original Hindi horror story below.

YOUR TASK:
Create a chronological visual storyboard for cinematic image generation.

STORY PRESERVATION:
1. Preserve all original character names and relationships.
2. Preserve the exact chronological event order.
3. Preserve important events, reveals, dialogues and the ending.
4. Do not invent characters, locations, subplots or events.
5. Do not rewrite or shorten the original story.
6. Scene narration is only a visual reference.
7. The original input story independently supplies the voiceover.

SCENE REQUIREMENTS:
Target scene count: {target_scenes}
Aim for exactly {target_scenes} scenes.
Minimum acceptable scene count: {math.ceil(target_scenes * MIN_SCENE_COVERAGE)}.

Create one scene for each distinct important visual moment.
Cover the beginning, middle, suspenseful discoveries, supernatural
events, important reactions, twists and ending.

Do not combine unrelated events.
Do not create duplicate scenes merely to increase scene count.
Do not omit important story events to save output space.

OUTPUT EFFICIENCY:
- Keep each scene concise but visually useful.
- visual_description: approximately 1-2 sentences.
- image_prompt: approximately 30-60 English words.
- story_event: one concise sentence.
- narration: short visual reference, not voiceover.
- Avoid repeating long character descriptions in every scene.
- Use visual_consistency_notes to preserve recurring details.

CHARACTER CONTINUITY:
For every important recurring character provide:
name, age if known, appearance, clothing, personality if established,
role and visual_consistency_notes.

LOCATION CONTINUITY:
For every recurring location provide:
name, description, atmosphere, lighting and visual_consistency_notes.

ENTITY CONTINUITY:
For every supernatural entity provide:
name, description, behavior and visual_consistency_notes.

Do not assume two similar entities are identical unless the story
establishes that connection.
Do not invent details that are not supported by the story.

RETURN ONLY VALID JSON.
No Markdown fences. No explanations outside JSON.

Use this exact JSON structure:

{{
  "title": "Story title",
  "language": "Hindi",
  "genre": "Horror",
  "story_summary": "Short production summary",
  "ending_summary": "Actual original ending",
  "characters": [
    {{
      "name": "Exact name",
      "age": "Known age or Not specified",
      "appearance": "Physical appearance",
      "clothing": "Clothing",
      "personality": "Established personality",
      "role": "Story role",
      "visual_consistency_notes": "Recurring visual details"
    }}
  ],
  "locations": [
    {{
      "name": "Exact location",
      "description": "Environment and architecture",
      "atmosphere": "Mood",
      "lighting": "Lighting",
      "visual_consistency_notes": "Recurring details"
    }}
  ],
  "entities": [
    {{
      "name": "Entity name",
      "description": "Physical appearance",
      "behavior": "Behavior",
      "visual_consistency_notes": "Distinctive details"
    }}
  ],
  "scenes": [
    {{
      "scene_number": 1,
      "scene_title": "Short title",
      "story_event": "Actual event in the story",
      "narration": "Short visual reference",
      "visual_description": "Visible cinematic details",
      "image_prompt": "Detailed English image prompt",
      "characters_present": ["Exact character names"],
      "entities_present": ["Visible entities"],
      "location": "Exact location name",
      "camera_angle": "Camera angle",
      "lighting": "Lighting",
      "atmosphere": "Scene mood",
      "visual_consistency_notes": "Continuity details"
    }}
  ]
}}

FIELD RULES:
- Every scene must contain all fields shown above.
- characters_present and entities_present must be arrays.
- location and every other scene field must be a string.
- Use sequential scene_number values starting at 1.
- Use consistent spelling of character names.
- Describe only what is visually supported by the story.
- Unknown details should use neutral descriptions.
- Every important story event must receive visual coverage.
- Give the ending sufficient visual coverage.

{retry_instruction}

COMPLETE ORIGINAL STORY:

{story}

END OF ORIGINAL STORY.
"""


def extract_json(response_text):
    """Extract one valid JSON object from model response."""

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

    if start == -1:
        raise ValueError(
            "AI response does not contain a JSON object."
        )

    decoder = json.JSONDecoder()

    try:
        parsed, end_position = decoder.raw_decode(
            text[start:]
        )

    except json.JSONDecodeError as error:
        raise ValueError(
            "AI returned malformed or truncated JSON: "
            f"{error}"
        ) from error

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


def normalize_reference_items(items, item_type):
    """Normalize character, location and entity reference lists."""

    if not isinstance(items, list):
        items = []

    normalized = []

    for item in items:

        if not isinstance(item, dict):
            continue

        item = dict(item)

        name = normalize_string(item.get("name"))

        if not name:
            continue

        item["name"] = name

        if item_type == "character":

            if not item.get("appearance"):
                item["appearance"] = item.get(
                    "description",
                    "",
                )

            defaults = {
                "age": "Not specified",
                "clothing": "",
                "personality": "",
                "role": "",
                "visual_consistency_notes": "",
            }

        elif item_type == "location":

            defaults = {
                "description": "",
                "atmosphere": "",
                "lighting": "",
                "visual_consistency_notes": "",
            }

        else:

            defaults = {
                "description": "",
                "behavior": "",
                "visual_consistency_notes": "",
            }

        for key, default_value in defaults.items():
            item[key] = normalize_string(
                item.get(key, default_value)
            )

        normalized.append(item)

    return normalized


def normalize_analysis(analysis, target_scenes):
    """Normalize and validate the storyboard structure."""

    if not isinstance(analysis, dict):
        raise ValueError(
            "Analysis must be a JSON object."
        )

    scenes = analysis.get("scenes")

    if not isinstance(scenes, list) or not scenes:
        raise ValueError(
            "AI did not return any visual scenes."
        )

    normalized_scenes = []

    required_string_fields = (
        "scene_title",
        "story_event",
        "narration",
        "visual_description",
        "image_prompt",
        "location",
        "camera_angle",
        "lighting",
        "atmosphere",
        "visual_consistency_notes",
    )

    for scene in scenes:

        if not isinstance(scene, dict):
            continue

        scene = dict(scene)

        # Support older field names.
        if not scene.get("characters_present"):
            scene["characters_present"] = scene.get(
                "characters",
                [],
            )

        if not scene.get("entities_present"):
            scene["entities_present"] = scene.get(
                "entities",
                [],
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
            scene["image_prompt"] = scene.get(
                "visual_description",
                "",
            )

        if not scene.get("narration"):
            scene["narration"] = scene.get(
                "visual_description",
                "",
            )

        defaults = {
            "scene_title": "Visual scene",
            "story_event": "",
            "narration": "",
            "visual_description": "",
            "image_prompt": "",
            "location": "Not specified",
            "camera_angle": "Cinematic wide shot",
            "lighting": "Atmospheric cinematic lighting",
            "atmosphere": "Suspenseful horror atmosphere",
            "visual_consistency_notes": "",
        }

        for key, default_value in defaults.items():
            scene[key] = normalize_string(
                scene.get(key, default_value)
            )

        # Reject scenes without useful visual information.
        if (
            not scene["visual_description"]
            or not scene["image_prompt"]
        ):
            LOGGER.warning(
                "Skipping scene with missing visual description."
            )
            continue

        scene["characters_present"] = normalize_name_list(
            scene.get("characters_present")
        )

        scene["entities_present"] = normalize_name_list(
            scene.get("entities_present")
        )

        scene["scene_number"] = len(normalized_scenes) + 1

        normalized_scenes.append(scene)

    if not normalized_scenes:
        raise ValueError(
            "No valid visual scenes were returned."
        )

    minimum_scenes = math.ceil(
        target_scenes * MIN_SCENE_COVERAGE
    )

    if len(normalized_scenes) < minimum_scenes:
        raise ValueError(
            "Storyboard scene coverage is insufficient. "
            f"Target: {target_scenes}; "
            f"Minimum required: {minimum_scenes}; "
            f"Returned: {len(normalized_scenes)}."
        )

    characters = normalize_reference_items(
        analysis.get("characters", []),
        "character",
    )

    locations = normalize_reference_items(
        analysis.get("locations", []),
        "location",
    )

    entities = normalize_reference_items(
        analysis.get("entities", []),
        "entity",
    )

    result = dict(analysis)

    result["scenes"] = normalized_scenes
    result["total_scenes"] = len(normalized_scenes)
    result["target_scenes"] = target_scenes

    result["characters"] = characters
    result["locations"] = locations
    result["entities"] = entities

    result["title"] = normalize_string(
        result.get("title") or "Untitled Horror Story"
    )

    result["language"] = normalize_string(
        result.get("language") or "Hindi"
    )

    result["genre"] = normalize_string(
        result.get("genre") or "Horror"
    )

    result["story_summary"] = normalize_string(
        result.get("story_summary")
    )

    result["ending_summary"] = normalize_string(
        result.get("ending_summary")
    )

    LOGGER.info(
        "Storyboard validation passed: %s/%s scenes.",
        len(normalized_scenes),
        target_scenes,
    )

    return result


def generate_with_gemini(prompt, model):
    api_key = get_api_key()

    if not api_key:
        raise ValueError("GEMINI_API_KEY is missing.")

    client = genai.Client(api_key=api_key)

    LOGGER.info(
        "Sending one Gemini storyboard request using %s.",
        model,
    )

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


def get_http_status(error):
    if isinstance(error, urllib.error.HTTPError):
        return error.code

    status = getattr(error, "status_code", None)

    if status is None:
        status = getattr(error, "status", None)

    try:
        return int(status)
    except (ValueError, TypeError):
        pass

    match = re.search(
        r"\b(408|429|500|502|503|504)\b",
        str(error),
    )

    return int(match.group(1)) if match else None


def is_retryable_openrouter_error(error):
    status = get_http_status(error)

    if status in (408, 429, 500, 502, 503, 504):
        return True

    message = str(error).lower()

    return any(
        phrase in message
        for phrase in (
            "timed out",
            "timeout",
            "connection reset",
            "temporarily unavailable",
            "connection error",
        )
    )


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
                    "You are a professional Hindi horror storyboard artist. "
                    "Return only one complete valid JSON object. "
                    "Follow the requested schema exactly. "
                    "Create the requested number of concise scenes. "
                    "Preserve original story chronology and ending. "
                    "Never invent story events. "
                    "Do not truncate the JSON."
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
            timeout=OPENROUTER_TIMEOUT,
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
            f"{error_body[:1200]}"
        ) from error

    choices = response_data.get("choices", [])

    if not choices:
        raise RuntimeError(
            "OpenRouter returned no choices."
        )

    choice = choices[0]
    message = choice.get("message", {})
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

    finish_reason = choice.get("finish_reason")

    if finish_reason == "length":
        raise RuntimeError(
            "OpenRouter response was truncated by token limit."
        )

    return content


def save_analysis(analysis):
    """Save only a fully validated analysis."""

    OUTPUT_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_file = ANALYSIS_FILE.with_suffix(
        ".temporary.json"
    )

    with open(
        temporary_file,
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            analysis,
            file,
            ensure_ascii=False,
            indent=2,
        )

    # Atomic replacement prevents partial JSON files.
    temporary_file.replace(ANALYSIS_FILE)

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

    analysis = None
    provider_used = None
    model_used = None
    errors = []

    # -------------------------------------------------
    # STEP 1: GEMINI - ONE ATTEMPT
    # -------------------------------------------------

    try:
        LOGGER.info(
            "Starting Gemini analysis. Maximum attempts: 1"
        )

        prompt = build_analysis_prompt(
            original_story,
            target_scenes,
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

    except Exception as error:

        errors.append(
            f"Gemini: {error}"
        )

        LOGGER.warning(
            "Gemini analysis failed. Switching to OpenRouter: %s",
            error,
        )

    # -------------------------------------------------
    # STEP 2: OPENROUTER FALLBACK
    # -------------------------------------------------

    if analysis is None:

        LOGGER.info(
            "Starting OpenRouter fallback."
        )

        maximum_attempts = 2

        for attempt in range(
            1,
            maximum_attempts + 1,
        ):

            try:
                LOGGER.info(
                    "OpenRouter analysis attempt %s/%s",
                    attempt,
                    maximum_attempts,
                )

                retry_prompt = build_analysis_prompt(
                    original_story,
                    target_scenes,
                    retry=(attempt > 1),
                )

                response_text = generate_with_openrouter(
                    retry_prompt,
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

                errors.append(
                    f"OpenRouter attempt {attempt}: {error}"
                )

                LOGGER.warning(
                    "OpenRouter attempt %s failed: %s",
                    attempt,
                    error,
                )

                if attempt >= maximum_attempts:
                    break

                if is_retryable_openrouter_error(error):

                    status = get_http_status(error)

                    wait_seconds = min(
                        settings["retry_delay"] * (2 ** attempt),
                        MAX_RETRY_WAIT,
                    )

                    if status == 429:
                        wait_seconds = max(
                            wait_seconds,
                            30,
                        )

                    LOGGER.warning(
                        "Temporary OpenRouter error. "
                        "Retrying after %s seconds.",
                        wait_seconds,
                    )

                    time.sleep(wait_seconds)

                else:
                    # Retry once with the compact retry prompt.
                    LOGGER.info(
                        "Retrying OpenRouter with stricter "
                        "scene coverage instructions."
                    )

    # -------------------------------------------------
    # STEP 3: FINAL VALIDATION
    # -------------------------------------------------

    if analysis is None:
        raise RuntimeError(
            "Story analysis failed with all providers. "
            + " | ".join(errors)
        )

    # The original story remains the narration source.
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

    analysis["total_scenes"] = len(
        analysis["scenes"]
    )

    analysis["target_scenes"] = target_scenes

    save_analysis(analysis)

    LOGGER.info("--------------------------------------")
    LOGGER.info("STORY ANALYSIS COMPLETED")
    LOGGER.info("Title: %s", analysis.get("title"))
    LOGGER.info("Target scenes: %s", target_scenes)
    LOGGER.info("Actual scenes: %s", analysis["total_scenes"])
    LOGGER.info("Original characters: %s", len(original_story))
    LOGGER.info(
        "Narration characters: %s",
        len(analysis["narration_script"]),
    )
    LOGGER.info("Provider: %s", provider_used)
    LOGGER.info("Model: %s", model_used)
    LOGGER.info("--------------------------------------")

    return analysis


def main():
    setup_logging()

    LOGGER.info(
        "Starting optimized Hindi horror story analysis."
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
