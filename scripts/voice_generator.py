
"""
Hindi Horror Story Automation
Gemini 3.8 Flash TTS Voice Generator

Generates cinematic Hindi narration, saves individual
audio chunks and combines them into one WAV file.
"""

import base64
import json
import logging
import re
import wave

from pathlib import Path

from google import genai

from scripts.utils import (
    OUTPUT_DIR,
    load_config,
    get_api_key,
    create_project_directories,
)


LOGGER = logging.getLogger(__name__)

OUTPUT_ROOT = Path(OUTPUT_DIR)
ANALYSIS_FILE = OUTPUT_ROOT / "story_analysis.json"

AUDIO_DIR = OUTPUT_ROOT / "audio"
CHUNKS_DIR = AUDIO_DIR / "chunks"

NARRATION_FILE = AUDIO_DIR / "narration.txt"
FINAL_AUDIO = AUDIO_DIR / "narration.wav"
REPORT_FILE = OUTPUT_ROOT / "voice_generation_report.json"

DEFAULT_MODEL = "gemini-3.8-flash-tts"
DEFAULT_VOICE = "Charon"

DEFAULT_STYLE = (
    "Narrate in natural Hindi with a deep, calm, "
    "cinematic documentary storyteller delivery. "
    "Create a mysterious and suspenseful atmosphere. "
    "Use controlled pacing, clear pronunciation, "
    "natural pauses and subtle emotional intensity. "
    "Do not sound robotic, exaggerated or overly dramatic. "
    "Preserve the exact written narration without "
    "adding, removing or changing any words."
)


def setup_logging():
    """Initialize logging."""

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                AUDIO_DIR / "voice_generator.log",
                encoding="utf-8",
            ),
        ],
    )


def load_story_analysis():
    """Read story analysis JSON."""

    if not ANALYSIS_FILE.exists():
        raise FileNotFoundError(
            f"Missing story analysis: {ANALYSIS_FILE}. "
            "Run story_analyzer.py first."
        )

    with open(ANALYSIS_FILE, "r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError("Story analysis must be a JSON object.")

    return data


def get_voice_config(config):
    """Read voice settings with safe defaults."""

    voice_settings = config.get("voice", {})
    audio_settings = config.get("audio", {})
    generation_settings = config.get("generation", {})

    if not isinstance(voice_settings, dict):
        voice_settings = {}

    if not isinstance(audio_settings, dict):
        audio_settings = {}

    if not isinstance(generation_settings, dict):
        generation_settings = {}

    model = (
        voice_settings.get("model")
        or audio_settings.get("tts_model")
        or generation_settings.get("tts_model")
        or DEFAULT_MODEL
    )

    voice = (
        voice_settings.get("voice")
        or audio_settings.get("voice")
        or DEFAULT_VOICE
    )

    style = (
        voice_settings.get("style")
        or audio_settings.get("style")
        or DEFAULT_STYLE
    )

    chunk_size = int(
        voice_settings.get("chunk_size", 2400)
    )

    if chunk_size < 100 or chunk_size > 6000:
        raise ValueError(
            "Voice chunk_size must be between 100 and 6000."
        )

    return {
        "model": model,
        "voice": voice,
        "style": style,
        "chunk_size": chunk_size,
    }


def clean_narration(text):
    """Clean narration without changing its actual words."""

    if not isinstance(text, str):
        return ""

    text = text.strip()

    # Remove common Markdown heading markers.
    text = re.sub(
        r"(?m)^\s*#{1,6}\s*",
        "",
        text
    )

    # Remove Markdown bold and italic markers.
    text = text.replace("**", "")
    text = text.replace("__", "")
    text = text.replace("*", "")

    # Remove empty lines at the beginning and end.
    return text.strip()


def extract_narration(analysis):
    """
    Extract narration from story analysis.

    Supports root narration fields and scene-based narration.
    """

    root_fields = [
        "narration_script",
        "full_narration",
        "hindi_narration",
        "narration",
        "voiceover_script",
        "script",
    ]

    for field in root_fields:

        value = analysis.get(field)

        if isinstance(value, str) and value.strip():
            return clean_narration(value)

        if isinstance(value, list):
            parts = []

            for item in value:
                if isinstance(item, str):
                    parts.append(item)

                elif isinstance(item, dict):
                    text = (
                        item.get("narration")
                        or item.get("text")
                        or item.get("content")
                    )

                    if isinstance(text, str) and text.strip():
                        parts.append(text)

            if parts:
                return clean_narration("\n\n".join(parts))

    # Fallback: collect narration from individual scenes.
    scenes = analysis.get("scenes", [])

    if isinstance(scenes, list):
        narration_parts = []

        for scene in scenes:

            if not isinstance(scene, dict):
                continue

            scene_text = (
                scene.get("hindi_narration")
                or scene.get("narration")
                or scene.get("narration_text")
                or scene.get("voiceover")
                or scene.get("voice_over")
            )

            if isinstance(scene_text, str) and scene_text.strip():
                narration_parts.append(scene_text.strip())

        if narration_parts:
            return clean_narration(
                "\n\n".join(narration_parts)
            )

    raise ValueError(
        "No narration text found in story_analysis.json. "
        "Expected narration_script or scene narration fields. "
        "The analyzer output schema must be checked."
    )


def save_narration_text(text):
    """Save exact narration text for subtitle generation."""

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    with open(
        NARRATION_FILE,
        "w",
        encoding="utf-8"
    ) as file:
        file.write(text)

    LOGGER.info("Narration text saved: %s", NARRATION_FILE)


def split_long_text(text, max_chars):
    """Split narration at sentence boundaries where possible."""

    paragraphs = [
        paragraph.strip()
        for paragraph in text.splitlines()
        if paragraph.strip()
    ]

    chunks = []
    current = ""

    for paragraph in paragraphs:

        if len(paragraph) <= max_chars:

            candidate = (
                current + "\n\n" + paragraph
                if current else paragraph
            )

            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    chunks.append(current)

                current = paragraph

            continue

        # Split unusually long paragraphs into sentences.
        sentences = re.split(
            r"(?<=[.!?।])\s+",
            paragraph
        )

        for sentence in sentences:

            sentence = sentence.strip()

            if not sentence:
                continue

            if len(sentence) > max_chars:
                raise ValueError(
                    "A single sentence exceeds the configured "
                    "chunk size. Increase chunk_size."
                )

            candidate = (
                current + " " + sentence
                if current else sentence
            )

            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    chunks.append(current)

                current = sentence

    if current:
        chunks.append(current)

    return chunks


def create_client():
    """Initialize Google Gen AI client."""

    api_key = get_api_key()

    if not api_key:
        raise ValueError("GEMINI_API_KEY is missing.")

    return genai.Client(api_key=api_key)


def generate_audio_chunk(
    client,
    text,
    output_path,
    model,
    voice,
    style,
):
    """
    Generate one WAV audio chunk using Gemini Interactions API.
    """

    LOGGER.info("Generating narration chunk: %s", output_path.name)

    interaction = client.interactions.create(
        model=model,
        input=[
            {
                "type": "user_input",
                "content": [
                    {
                        "type": "text",
                        "text": text,
                        "annotations": [
                            {
                                "type": "speech_metadata",
                                "style": style,
                            }
                        ],
                    }
                ],
            }
        ],
        response_format={
            "type": "audio"
        },
        generation_config={
            "speech_config": [
                {
                    "voice": voice
                }
            ]
        },
    )

    if not interaction.output_audio:
        raise RuntimeError(
            "Gemini returned no audio output."
        )

    audio_data = interaction.output_audio.data

    if not audio_data:
        raise RuntimeError(
            "Gemini returned empty audio data."
        )

    # Interactions API returns base64 audio data.
    audio_bytes = base64.b64decode(audio_data)

    if not audio_bytes.startswith(b"RIFF"):
        raise RuntimeError(
            "Expected WAV audio output, but received "
            "an unsupported audio format."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "wb") as file:
        file.write(audio_bytes)

    # Validate the saved WAV.
    with wave.open(str(output_path), "rb") as audio:
        if audio.getnframes() <= 0:
            raise RuntimeError("Generated WAV contains no audio.")

        duration = (
            audio.getnframes() / audio.getframerate()
        )

    LOGGER.info(
        "Audio chunk saved: %s | Duration: %.2f seconds",
        output_path,
        duration,
    )

    return {
        "path": str(output_path),
        "duration_seconds": round(duration, 2),
        "characters": len(text),
    }


def combine_audio_chunks(chunk_paths, output_path):
    """Combine WAV chunks while preserving their audio parameters."""

    if not chunk_paths:
        raise ValueError("No audio chunks available to combine.")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    reference_params = None
    total_frames = 0

    # Validate all WAV files before combining.
    for chunk_path in chunk_paths:

        with wave.open(str(chunk_path), "rb") as audio:

            params = audio.getparams()

            if reference_params is None:
                reference_params = params
            else:
                if (
                    params.nchannels != reference_params.nchannels
                    or params.sampwidth != reference_params.sampwidth
                    or params.framerate != reference_params.framerate
                    or params.comptype != reference_params.comptype
                ):
                    raise ValueError(
                        "Audio chunks have incompatible WAV "
                        "formats. Cannot safely combine them."
                    )

            total_frames += audio.getnframes()

    # Write combined WAV.
    with wave.open(str(output_path), "wb") as output:

        output.setnchannels(reference_params.nchannels)
        output.setsampwidth(reference_params.sampwidth)
        output.setframerate(reference_params.framerate)
        output.setcomptype(
            reference_params.comptype,
            reference_params.compname
        )

        for chunk_path in chunk_paths:

            with wave.open(str(chunk_path), "rb") as audio:

                output.writeframes(
                    audio.readframes(audio.getnframes())
                )

    duration = (
        total_frames / reference_params.framerate
    )

    LOGGER.info(
        "Combined narration saved: %s",
        output_path
    )

    return round(duration, 2)


def save_report(report):
    """Save voice generation report."""

    with open(
        REPORT_FILE,
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(
            report,
            file,
            ensure_ascii=False,
            indent=4
        )


def main():
    """Execute complete Hindi voice generation pipeline."""

    setup_logging()

    LOGGER.info("Starting Hindi voice generation.")

    create_project_directories()

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)

    config = load_config()
    analysis = load_story_analysis()

    voice_config = get_voice_config(config)

    narration = extract_narration(analysis)

    if not narration:
        raise ValueError("Extracted narration is empty.")

    save_narration_text(narration)

    chunks = split_long_text(
        narration,
        voice_config["chunk_size"]
    )

    LOGGER.info(
        "Total narration characters: %s",
        len(narration)
    )

    LOGGER.info(
        "Total audio chunks: %s",
        len(chunks)
    )

    client = create_client()

    generated_chunks = []
    chunk_paths = []

    for index, chunk in enumerate(chunks, start=1):

        output_path = (
            CHUNKS_DIR / f"narration_{index:03d}.wav"
        )

        result = generate_audio_chunk(
            client=client,
            text=chunk,
            output_path=output_path,
            model=voice_config["model"],
            voice=voice_config["voice"],
            style=voice_config["style"],
        )

        result["chunk_number"] = index

        generated_chunks.append(result)
        chunk_paths.append(output_path)

    total_duration = combine_audio_chunks(
        chunk_paths,
        FINAL_AUDIO
    )

    report = {
        "story_title": analysis.get("title", "Untitled"),
        "model": voice_config["model"],
        "voice": voice_config["voice"],
        "language": "Hindi",
        "style": voice_config["style"],
        "total_characters": len(narration),
        "total_chunks": len(generated_chunks),
        "total_duration_seconds": total_duration,
        "narration_file": str(NARRATION_FILE),
        "final_audio": str(FINAL_AUDIO),
        "chunks": generated_chunks,
        "status": "success",
    }

    save_report(report)

    LOGGER.info("-----------------------------------")
    LOGGER.info("VOICE GENERATION COMPLETED")
    LOGGER.info("Final audio: %s", FINAL_AUDIO)
    LOGGER.info("Duration: %.2f seconds", total_duration)
    LOGGER.info("-----------------------------------")


if __name__ == "__main__":
    try:
        main()

    except Exception:
        logging.exception(
            "Voice generation failed."
        )
        raise
