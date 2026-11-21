
import json
import re
import time
from pathlib import Path

from google import genai
from google.genai import types

from scripts.utils import (
    PROJECT_ROOT,
    logger,
    get_api_key,
    load_config,
    read_story,
    save_json
)


def build_analysis_prompt(story):
    """Create instructions for cinematic horror story analysis."""

    return f"""
You are an expert Hindi horror film director, screenplay analyst,
storyboard artist and cinematic visual designer.

Analyze the complete story provided below.

STRICT RULES:
1. Preserve the original plot and event sequence.
2. Never change character names or relationships.
3. Preserve important dialogues and story events.
4. Never change the original ending.
5. Do not invent new characters or subplots.
6. Do not remove important suspense or horror moments.
7. Keep all descriptions faithful to the original story.
8. Write narration in natural, cinematic Hindi.
9. Keep the original story's language and cultural context.
10. Every scene must follow the original story order.

OUTPUT:
Return ONLY valid JSON. Do not include Markdown or explanations.

Use this exact JSON structure:

{{
  "title": "Story title",
  "language": "Hindi",
  "genre": ["Horror", "Suspense", "Thriller"],
  "story_summary": "Complete story summary",
  "ending_summary": "Original ending description",
  "characters": [
    {{
      "name": "Character name",
      "age": "Age if known",
      "appearance": "Visible physical appearance",
      "clothing": "Clothing description",
      "personality": "Personality",
      "role": "Role in story",
      "visual_consistency_notes": "Features that must remain consistent"
    }}
  ],
  "locations": [
    {{
      "name": "Location name",
      "description": "Visual description",
      "atmosphere": "Mood and atmosphere",
      "lighting": "Lighting details",
      "visual_consistency_notes": "Details to preserve"
    }}
  ],
  "entities": [
    {{
      "name": "Entity name",
      "description": "Appearance and characteristics",
      "behavior": "Behavior in the story",
      "visual_consistency_notes": "Details to preserve"
    }}
  ],
  "scenes": [
    {{
      "scene_number": 1,
      "story_event": "Original event in this scene",
      "characters_present": [],
      "location": "Location name",
      "visual_description": "Detailed cinematic visual",
      "camera_angle": "Camera angle and movement",
      "lighting": "Lighting design",
      "atmosphere": "Mood and suspense",
      "narration": "Hindi narration for this scene",
      "dialogue": [],
      "sound_effects": [],
      "continuity_notes": "Visual continuity requirements"
    }}
  ]
}}

IMPORTANT:
- Include every important event from the original story.
- Use as many scenes as necessary.
- Do not force the story into a fixed runtime.
- Do not create repetitive or filler scenes.
- Keep character and location descriptions consistent.
- Do not invent unknown ages, appearances or facts.
- Use null or "Not specified in story" when information is unavailable.

COMPLETE ORIGINAL STORY:

{story}
"""


def extract_json(text):
    """Extract JSON if the model adds extra text."""

    text = text.strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        raise ValueError("Gemini did not return valid JSON.")

    return json.loads(text[start:end + 1])


def analyze_story():
    """Analyze the story using the Gemini API."""

    config = load_config()
    story = read_story()
    api_key = get_api_key()

    model_name = config["generation"]["text_model"]
    max_retries = config["generation"].get("max_retries", 3)
    retry_delay = config["generation"].get(
        "retry_delay_seconds", 5
    )

    client = genai.Client(api_key=api_key)
    prompt = build_analysis_prompt(story)

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                "Starting story analysis. Attempt %s/%s",
                attempt,
                max_retries
            )

            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.4
                )
            )

            if not response.text:
                raise ValueError("Gemini returned an empty response.")

            analysis = extract_json(response.text)

            if not isinstance(analysis, dict):
                raise ValueError("Analysis output must be a JSON object.")

            if not analysis.get("scenes"):
                raise ValueError("No scenes found in analysis.")

            analysis["total_scenes"] = len(analysis["scenes"])

            output_path = PROJECT_ROOT / "output" / "story_analysis.json"

            save_json(analysis, output_path)

            logger.info(
                "Story analysis completed. Total scenes: %s",
                analysis["total_scenes"]
            )

            return analysis

        except Exception as error:
            logger.exception("Story analysis attempt failed: %s", error)

            if attempt == max_retries:
                raise

            time.sleep(retry_delay * attempt)

    raise RuntimeError("Story analysis failed.")


if __name__ == "__main__":
    analyze_story()
