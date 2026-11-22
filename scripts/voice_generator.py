
"""
Hindi Horror Story Automation
Gemini Hindi TTS Voice Generator

Uses the complete original narration_script saved by story_analyzer.py.
Scene summaries are NEVER used as fallback voiceover.
"""

import base64
import json
import logging
import re
import time
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
    "cinematic horror storyteller delivery. "
    "Create a mysterious and suspenseful atmosphere. "
    "Use controlled pacing, clear pronunciation, "
    "natural pauses and subtle emotional intensity. "
    "Do not sound robotic, exaggerated or overly dramatic. "
    "Preserve the exact written narration without "
    "adding, removing or changing any words."
)

MIN_NARRATION_CHARACTERS = 300


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
        force=True,
    )


def load_story_analysis():
    """Read story analysis JSON."""

    if not ANALYSIS_FILE.exists():
        raise FileNotFoundError(
            f"Missing story analysis: {ANALYSIS_FILE}. "
            "Run story_analyzer.py first."
        )

    with open(
        ANALYSIS_FILE,
        "r",
        encoding="utf-8",
    ) as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError(
            "Story analysis must be a JSON object."
        )

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

    max_retries = int(
        generation_settings.get("max_retries", 3)
    )

    retry_delay = int(
        generation_settings.get("retry_delay_seconds", 5)
    )

    return {
        "model": model,
        "voice": voice,
        "style": style,
        "chunk_size": chunk_size,
        "max_retries": max(1, min(max_retries, 5)),
        "retry_delay": max(1, retry_delay),
    }


def clean_narration(text):
    """Remove formatting markers without rewriting story words."""

    if not isinstance(text, str):
        return ""

    text = text.strip()

    # Remove Markdown heading markers only.
    text = re.sub(
        r"(?m)^\s*#{1,6}\s*",
        "",
        text,
    )

    # Remove Markdown bold and italic markers.
    text = text.replace("**", "")
    text = text.replace("__", "")
    text = text.replace("*", "")

    return text.strip()


def extract_narration(analysis):
    """
    Extract complete original narration.

    NEVER use scene-based narration as a fallback.
    """

    narration_fields = [
        "narration_script",
        "original_story",
        "full_narration",
        "hindi_narration",
        "voiceover_script",
        "script",
    ]

    narration = None

    for field in narration_fields:

        value = analysis.get(field)

        if isinstance(value, str) and value.strip():
            narration = value
            break

    if not narration:
        raise ValueError(
            "Complete narration is missing. "
            "Expected narration_script or original_story. "
            "Scene summaries will NOT be used as voiceover. "
            "Run the updated story_analyzer.py first."
        )

    narration = clean_narration(narration)

    if len(narration) < MIN_NARRATION_CHARACTERS:
        raise ValueError(
            "Narration is unexpectedly short: "
            f"{len(narration)} characters. "
            "Check input/story.txt and story_analysis.json."
        )

    expected_length = analysis.get(
        "original_story_characters"
    )

    if isinstance(expected_length, int):
        if len(narration) < expected_length * 0.90:
            raise ValueError(
                "Narration is much shorter than the original story. "
                f"Expected approximately {expected_length} characters, "
                f"found {len(narration)}."
            )

    LOGGER.info(
        "Complete original narration selected: %s characters",
        len(narration),
    )

    return narration


def save_narration_text(text):
    """Save complete narration for subtitles."""

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    with open(
        NARRATION_FILE,
        "w",
        encoding="utf-8",
    ) as file:
        file.write(text)

    LOGGER.info(
        "Complete narration saved: %s",
        NARRATION_FILE,
    )


def split_long_text(text, max_chars):
    """Split narration without dropping sentences."""

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

        sentences = re.split(
            r"(?<=[.!?।])\s+",
            paragraph,
        )

        for sentence in sentences:

            sentence = sentence.strip()

            if not sentence:
                continue

            # Break an unusually long sentence at spaces,
            # without deleting any words.
            if len(sentence) > max_chars:

                words = sentence.split()
                segment = ""

                for word in words:

                    candidate = (
                        segment + " " + word
                        if segment else word
                    )

                    if len(candidate) <= max_chars:
                        segment = candidate
                    else:
                        if segment:
                            if current:
                                chunks.append(current)
                                current = ""

                            chunks.append(segment)

                        segment = word

                if segment:
                    if current:
                        candidate = current + " " + segment

                        if len(candidate) <= max_chars:
                            current = candidate
                        else:
                            chunks.append(current)
                            current = segment
                    else:
                        current = segment

                continue

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

    if not chunks:
        raise ValueError(
            "Narration could not be split into audio chunks."
        )

    # Verify that no chunk is empty.
    if any(not chunk.strip() for chunk in chunks):
        raise ValueError(
            "Empty audio chunk detected."
        )

    return chunks


def create_client():
    """Initialize Google Gen AI client."""

    api_key = get_api_key()

    if not api_key:
        raise ValueError(
            "GEMINI_API_KEY is missing."
        )

    return genai.Client(
        api_key=api_key,
        http_options={
            "api_version": "v1beta"
        },
    )


def generate_audio_chunk(
    client,
    text,
    output_path,
    model,
    voice,
    style,
    max_retries,
    retry_delay,
):
    """Generate one WAV audio chunk."""

    last_error = None

    for attempt in range(1, max_retries + 1):

        try:
            LOGGER.info(
                "Generating chunk %s | attempt %s/%s",
                output_path.name,
                attempt,
                max_retries,
            )

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

            audio_bytes = base64.b64decode(
                audio_data
            )

            if not audio_bytes.startswith(b"RIFF"):
                raise RuntimeError(
                    "Expected WAV audio output."
                )

            output_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            with open(
                output_path,
                "wb",
            ) as file:
                file.write(audio_bytes)

            with wave.open(
                str(output_path),
                "rb",
            ) as audio:

                if audio.getnframes() <= 0:
                    raise RuntimeError(
                        "Generated WAV contains no audio."
                    )

                duration = (
                    audio.getnframes()
                    / audio.getframerate()
                )

            LOGGER.info(
                "Chunk saved: %s | %.2f seconds",
                output_path.name,
                duration,
            )

            return {
                "path": str(output_path),
                "duration_seconds": round(duration, 2),
                "characters": len(text),
                "chunk_number": int(
                    output_path.stem.split("_")[-1]
                ),
            }

        except Exception as error:
            last_error = error

            LOGGER.warning(
                "Audio generation failed for %s: %s",
                output_path.name,
                error,
            )

            if attempt < max_retries:
                time.sleep(retry_delay)

    raise RuntimeError(
        f"Failed to generate {output_path.name} "
        f"after {max_retries} attempts: {last_error}"
    )


def combine_audio_chunks(chunk_paths, output_path):
    """Combine WAV chunks with matching audio parameters."""

    if not chunk_paths:
        raise ValueError(
            "No audio chunks available to combine."
        )

    reference_params = None
    total_frames = 0

    for chunk_path in chunk_paths:

        with wave.open(
            str(chunk_path),
            "rb",
        ) as audio:

            params = audio.getparams()

            if reference_params is None:
                reference_params = params

            elif (
                params.nchannels != reference_params.nchannels
                or params.sampwidth != reference_params.sampwidth
                or params.framerate != reference_params.framerate
                or params.comptype != reference_params.comptype
            ):
                raise ValueError(
                    "Audio chunks have incompatible WAV formats."
                )

            total_frames += audio.getnframes()

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with wave.open(
        str(output_path),
        "wb",
    ) as output:

        output.setnchannels(
            reference_params.nchannels
        )

        output.setsampwidth(
            reference_params.sampwidth
        )

        output.setframerate(
            reference_params.framerate
        )

        output.setcomptype(
            reference_params.comptype,
            reference_params.compname,
        )

        for chunk_path in chunk_paths:

            with wave.open(
                str(chunk_path),
                "rb",
            ) as audio:

                output.writeframes(
                    audio.readframes(
                        audio.getnframes()
                    )
                )

    duration = (
        total_frames / reference_params.framerate
    )

    LOGGER.info(
        "Combined audio saved: %s",
        output_path,
    )

    return round(duration, 2)


def save_report(report):
    """Save voice generation report."""

    with open(
        REPORT_FILE,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            report,
            file,
            ensure_ascii=False,
            indent=4,
        )


def main():
    """Run the complete Hindi voice generation pipeline."""

    setup_logging()

    LOGGER.info(
        "Starting complete Hindi voice generation."
    )

    create_project_directories()

    AUDIO_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    CHUNKS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    config = load_config()
    analysis = load_story_analysis()

    voice_config = get_voice_config(config)

    narration = extract_narration(analysis)

    save_narration_text(narration)

    chunks = split_long_text(
        narration,
        voice_config["chunk_size"],
    )

    LOGGER.info(
        "Original narration characters: %s",
        len(narration),
    )

    LOGGER.info(
        "Total audio chunks: %s",
        len(chunks),
    )

    # Remove old chunks to prevent mixing previous runs.
    for old_chunk in CHUNKS_DIR.glob("narration_*.wav"):
        old_chunk.unlink()

    client = create_client()

    generated_chunks = []
    chunk_paths = []

    for index, chunk in enumerate(chunks, start=1):

        output_path = (
            CHUNKS_DIR
            / f"narration_{index:03d}.wav"
        )

        result = generate_audio_chunk(
            client=client,
            text=chunk,
            output_path=output_path,
            model=voice_config["model"],
            voice=voice_config["voice"],
            style=voice_config["style"],
            max_retries=voice_config["max_retries"],
            retry_delay=voice_config["retry_delay"],
        )

        generated_chunks.append(result)
        chunk_paths.append(output_path)

    total_duration = combine_audio_chunks(
        chunk_paths,
        FINAL_AUDIO,
    )

    report = {
        "story_title": analysis.get(
            "title",
            "Untitled",
        ),
        "model": voice_config["model"],
        "voice": voice_config["voice"],
        "language": "Hindi",
        "style": voice_config["style"],
        "narration_source": analysis.get(
            "narration_source",
            "story_analysis.json",
        ),
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
    LOGGER.info(
        "Narration characters: %s",
        len(narration),
    )
    LOGGER.info(
        "Audio chunks: %s",
        len(generated_chunks),
    )
    LOGGER.info(
        "Final duration: %.2f seconds",
        total_duration,
    )
    LOGGER.info("-----------------------------------")


if __name__ == "__main__":
    try:
        main()

    except Exception:
        logging.exception(
            "Voice generation failed."
        )
        raise
