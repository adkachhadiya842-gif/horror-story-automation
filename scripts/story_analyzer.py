
import json
import os
import re
import time
import random
import urllib.request
import urllib.error

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


# ---------------------------------------------------------
# STORY ANALYSIS PROMPT
# ---------------------------------------------------------

def build_analysis_prompt(story):
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


# ---------------------------------------------------------
# JSON EXTRACTION AND VALIDATION
# ---------------------------------------------------------

def extract_json(text):
    if not text:
        raise ValueError("AI returned an empty response.")

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
        raise ValueError("AI did not return valid JSON.")

    return json.loads(text[start:end + 1])


def validate_analysis(analysis):
    if not isinstance(analysis, dict):
        raise ValueError("Analysis must be a JSON object.")

    if not isinstance(analysis.get("scenes"), list):
        raise ValueError("Analysis does not contain a valid scenes list.")

    if len(analysis["scenes"]) == 0:
        raise ValueError("Analysis contains zero scenes.")

    for index, scene in enumerate(analysis["scenes"], start=1):
        if not isinstance(scene, dict):
            raise ValueError(
                f"Scene {index} is not a valid JSON object."
            )

    analysis["total_scenes"] = len(analysis["scenes"])

    return analysis


# ---------------------------------------------------------
# RETRY CONFIGURATION
# ---------------------------------------------------------

def get_error_status(error):
    status = getattr(error, "code", None)

    if status is None:
        status = getattr(error, "status_code", None)

    if status is None:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)

    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def is_retryable_error(error):
    status = get_error_status(error)

    # Temporary server, rate-limit and network errors.
    if status in (408, 409, 425, 429, 500, 502, 503, 504):
        return True

    # Permanent authentication, permission or model errors.
    if status in (400, 401, 403, 404):
        return False

    # Invalid JSON / empty AI response can be retried.
    if isinstance(error, (ValueError, json.JSONDecodeError)):
        return True

    # Network errors without an HTTP status can be temporary.
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True

    return status is None


def wait_before_retry(attempt, base_delay):
    # Exponential backoff with small random jitter.
    delay = min(base_delay * (2 ** (attempt - 1)), 60)
    delay += random.uniform(0, 2)

    logger.warning(
        "Retrying after %.1f seconds...",
        delay
    )

    time.sleep(delay)


# ---------------------------------------------------------
# GEMINI PROVIDER
# ---------------------------------------------------------

def generate_with_gemini(
    prompt,
    api_key,
    model_name,
    max_retries,
    retry_delay
):
    client = genai.Client(api_key=api_key)

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                "Gemini story analysis attempt %s/%s",
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
            analysis = validate_analysis(analysis)

            logger.info(
                "Gemini analysis successful. Scenes: %s",
                analysis["total_scenes"]
            )

            return analysis

        except Exception as error:
            logger.exception(
                "Gemini attempt %s failed: %s",
                attempt,
                error
            )

            if attempt >= max_retries:
                break

            if not is_retryable_error(error):
                logger.warning(
                    "Gemini error is not retryable. Switching provider."
                )
                break

            wait_before_retry(attempt, retry_delay)

    raise RuntimeError(
        "Gemini failed after retries. OpenRouter fallback required."
    )


# ---------------------------------------------------------
# OPENROUTER PROVIDER
# ---------------------------------------------------------

def generate_with_openrouter(
    prompt,
    api_key,
    model_name,
    max_retries,
    retry_delay
):
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is missing. "
            "Add it in GitHub Repository Secrets."
        )

    url = "https://openrouter.ai/api/v1/chat/completions"

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                "OpenRouter fallback attempt %s/%s using %s",
                attempt,
                max_retries,
                model_name
            )

            payload = {
                "model": model_name,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are an expert Hindi horror film "
                            "director and screenplay analyst. "
                            "Follow the user's instructions exactly. "
                            "Return only valid JSON."
                        )
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.4,
                "response_format": {
                    "type": "json_object"
                }
            }

            request_data = json.dumps(payload).encode("utf-8")

            request = urllib.request.Request(
                url,
                data=request_data,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/",
                    "X-Title": "Horror Story Automation"
                },
                method="POST"
            )

            with urllib.request.urlopen(
                request,
                timeout=180
            ) as response:
                response_body = response.read().decode("utf-8")

            result = json.loads(response_body)

            choices = result.get("choices", [])

            if not choices:
                raise ValueError(
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

            if not content:
                raise ValueError(
                    "OpenRouter returned empty content."
                )

            analysis = extract_json(content)
            analysis = validate_analysis(analysis)

            logger.info(
                "OpenRouter analysis successful. Scenes: %s",
                analysis["total_scenes"]
            )

            return analysis

        except urllib.error.HTTPError as error:
            status = error.code
            details = error.read().decode(
                "utf-8",
                errors="replace"
            )

            logger.error(
                "OpenRouter HTTP error %s: %s",
                status,
                details[:1500]
            )

            if status in (400, 401, 403, 404):
                raise RuntimeError(
                    f"OpenRouter rejected the request (HTTP {status}). "
                    "Check the API key and configured model."
                ) from error

            if attempt >= max_retries:
                raise

            wait_before_retry(attempt, retry_delay)

        except Exception as error:
            logger.exception(
                "OpenRouter attempt %s failed: %s",
                attempt,
                error
            )

            if attempt >= max_retries:
                raise

            if not is_retryable_error(error):
                raise

            wait_before_retry(attempt, retry_delay)

    raise RuntimeError("OpenRouter failed after all retries.")


# ---------------------------------------------------------
# SAVE ANALYSIS
# ---------------------------------------------------------

def save_analysis(analysis):
    output_path = PROJECT_ROOT / "output" / "story_analysis.json"

    save_json(analysis, output_path)

    logger.info(
        "Story analysis saved successfully: %s",
        output_path
    )

    return analysis


# ---------------------------------------------------------
# MAIN ANALYSIS PIPELINE
# ---------------------------------------------------------

def analyze_story():
    config = load_config()
    story = read_story()

    if not story or not story.strip():
        raise ValueError("Story file is empty.")

    generation_config = config["generation"]

    model_name = generation_config["text_model"]

    max_retries = max(
        1,
        int(generation_config.get("max_retries", 3))
    )

    retry_delay = max(
        1,
        int(generation_config.get("retry_delay_seconds", 5))
    )

    openrouter_model = (
        generation_config.get("openrouter_text_model")
        or os.getenv(
            "OPENROUTER_MODEL",
            "openai/gpt-4o-mini"
        )
    )

    gemini_api_key = get_api_key()
    openrouter_api_key = os.getenv("OPENROUTER_API_KEY")

    prompt = build_analysis_prompt(story)

    logger.info("Starting story analysis.")
    logger.info("Primary provider: Gemini")
    logger.info("Fallback provider: OpenRouter")

    # First attempt: Gemini.
    try:
        analysis = generate_with_gemini(
            prompt=prompt,
            api_key=gemini_api_key,
            model_name=model_name,
            max_retries=max_retries,
            retry_delay=retry_delay
        )

        analysis["ai_provider"] = "Gemini"
        analysis["ai_model"] = model_name

        return save_analysis(analysis)

    except Exception as gemini_error:
        logger.error(
            "Gemini unavailable. Activating OpenRouter fallback. "
            "Reason: %s",
            gemini_error
        )

    # Second attempt: OpenRouter automatic fallback.
    try:
        analysis = generate_with_openrouter(
            prompt=prompt,
            api_key=openrouter_api_key,
            model_name=openrouter_model,
            max_retries=max_retries,
            retry_delay=retry_delay
        )

        analysis["ai_provider"] = "OpenRouter"
        analysis["ai_model"] = openrouter_model

        return save_analysis(analysis)

    except Exception as openrouter_error:
        logger.exception(
            "Both Gemini and OpenRouter failed."
        )

        raise RuntimeError(
            "Story analysis failed on both AI providers. "
            f"Gemini model: {model_name}. "
            f"OpenRouter model: {openrouter_model}. "
            f"OpenRouter error: {openrouter_error}"
        ) from openrouter_error


if __name__ == "__main__":
    analyze_story()
