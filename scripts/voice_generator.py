
"""
Hindi Horror Story Automation
Reliable Gemini TTS Generator

The original input/story.txt is the ONLY narration source.
Scene summaries are never used for voiceover.
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
    read_story,
    create_project_directories,
)


BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_ROOT = Path(OUTPUT_DIR)

AUDIO_DIR = OUTPUT_ROOT / "audio"
CHUNKS_DIR = AUDIO_DIR / "chunks"

ANALYSIS_FILE = OUTPUT_ROOT / "story_analysis.json"
NARRATION_FILE = AUDIO_DIR / "narration.txt"
FINAL_AUDIO = AUDIO_DIR / "narration.wav"
REPORT_FILE = OUTPUT_ROOT / "voice_generation_report.json"

DEFAULT_MODEL = "gemini-3.8-flash-tts"
DEFAULT_VOICE = "Charon"

DEFAULT_STYLE = (
    "Natural Hindi storytelling. Deep, calm, cinematic horror "
    "narration with clear pronunciation, controlled pacing, "
    "natural pauses and subtle suspense. Read the supplied "
    "transcript verbatim without adding or omitting words."
)

MIN_NARRATION_CHARACTERS = 300
MIN_EXPECTED_AUDIO_SECONDS_PER_CHARACTER = 1 / 35


def setup_logging():
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


LOGGER = logging.getLogger("horror_voice_generator")


def clean_narration(text):
    """Remove Markdown formatting without changing story wording."""

    if not isinstance(text, str):
        raise ValueError("Story narration must be text.")

    text = text.strip()

    text = re.sub(
        r"(?m)^\s*#{1,6}\s*",
        "",
        text,
    )

    text = text.replace("**", "")
    text = text.replace("__", "")
    text = text.replace("*", "")

    # Use one consistent whitespace format throughout the pipeline.
    # This prevents newline and multiple-space count mismatches.
    text = re.sub(r"\s+", " ", text).strip()

    return text


def normalize_for_comparison(text):
    return clean_narration(text)


def load_story_analysis():
    if not ANALYSIS_FILE.exists():
        LOGGER.warning(
            "Story analysis file not found. "
            "Narration will still use input/story.txt."
        )
        return {}

    try:
        with open(
            ANALYSIS_FILE,
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

        if isinstance(data, dict):
            return data

    except Exception as error:
        LOGGER.warning(
            "Could not read story analysis: %s",
            error,
        )

    return {}


def get_voice_config(config):
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
        voice_settings.get("chunk_size", 1200)
    )

    if not 300 <= chunk_size <= 4000:
        raise ValueError(
            "TTS chunk_size must be between 300 and 4000."
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


def extract_original_narration(analysis):
    """
    Original input/story.txt is the only narration source.
    Analysis JSON is used only for comparison and metadata.
    """

    original_story = read_story()
    narration = clean_narration(original_story)

    if len(narration) < MIN_NARRATION_CHARACTERS:
        raise ValueError(
            "Original story is too short for production: "
            f"{len(narration)} characters."
        )

    analyzed_story = analysis.get("original_story")

    if isinstance(analyzed_story, str) and analyzed_story.strip():

        analyzed_normalized = normalize_for_comparison(
            analyzed_story
        )

        if analyzed_normalized != narration:
            LOGGER.warning(
                "Analysis narration differs from input/story.txt. "
                "The original input story will be used."
            )

    LOGGER.info(
        "Original input story selected: %s characters",
        len(narration),
    )

    return narration


def save_narration_text(text):
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    NARRATION_FILE.write_text(
        text,
        encoding="utf-8",
    )

    LOGGER.info(
        "Complete narration saved: %s",
        NARRATION_FILE,
    )


def split_long_text(text, max_chars):
    """
    Split narration at word boundaries.

    Every chunk is reconstructed using exactly one space
    between chunks. The reconstructed text must exactly
    match the canonical narration.
    """

    if not isinstance(text, str) or not text.strip():
        raise ValueError("Narration is empty.")

    if re.sub(r"\s+", " ", text).strip() != text:
        raise ValueError(
            "Narration must be whitespace-normalized before splitting."
        )

    if max_chars < 1:
        raise ValueError("Invalid maximum chunk size.")

    words = text.split(" ")

    chunks = []
    current_words = []
    current_length = 0

    for word in words:

        if not word:
            continue

        additional_length = (
            len(word)
            if not current_words
            else len(word) + 1
        )

        if (
            current_words
            and current_length + additional_length > max_chars
        ):
            chunks.append(" ".join(current_words))
            current_words = [word]
            current_length = len(word)

        else:
            current_words.append(word)
            current_length += additional_length

    if current_words:
        chunks.append(" ".join(current_words))

    if not chunks:
        raise ValueError("No narration chunks were created.")

    reconstructed = " ".join(chunks)

    if reconstructed != text:
        raise ValueError(
            "Chunk reconstruction failed. "
            "Original narration was not preserved."
        )

    LOGGER.info(
        "Chunk integrity passed: %s characters reconstructed.",
        len(reconstructed),
    )

    return chunks


def create_client():
    api_key = get_api_key()

    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing.")

    return genai.Client(
        api_key=api_key,
        http_options={
            "api_version": "v1beta"
        },
    )


def validate_wav(path, text_length):
    """Validate WAV structure, duration and audio format."""

    try:
        with wave.open(str(path), "rb") as audio:

            frames = audio.getnframes()
            sample_rate = audio.getframerate()
            channels = audio.getnchannels()
            sample_width = audio.getsampwidth()

            if frames <= 0 or sample_rate <= 0:
                raise ValueError("Generated WAV is empty.")

            if channels < 1 or sample_width < 1:
                raise ValueError("Invalid WAV audio format.")

            duration = frames / sample_rate

    except (wave.Error, EOFError) as error:
        raise ValueError(
            f"Invalid or corrupted WAV file: {error}"
        ) from error

    minimum_duration = (
        text_length * MIN_EXPECTED_AUDIO_SECONDS_PER_CHARACTER
    )

    if duration < minimum_duration:
        raise ValueError(
            "Generated audio is suspiciously short. "
            f"Text: {text_length} characters; "
            f"audio: {duration:.2f}s; "
            f"minimum expected: {minimum_duration:.2f}s."
        )

    return round(duration, 2)


def generate_audio_chunk(
    client,
    text,
    output_path,
    settings,
    chunk_number,
):
    """Generate one complete WAV chunk using Gemini Interactions API."""

    last_error = None

    for attempt in range(
        1,
        settings["max_retries"] + 1,
    ):

        try:
            LOGGER.info(
                "Generating chunk %s | %s characters | attempt %s/%s",
                chunk_number,
                len(text),
                attempt,
                settings["max_retries"],
            )

            interaction = client.interactions.create(
                model=settings["model"],
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
                                        "style": settings["style"],
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
                            "voice": settings["voice"]
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

            if isinstance(audio_data, str):
                audio_bytes = base64.b64decode(
                    audio_data,
                    validate=True,
                )
            else:
                audio_bytes = bytes(audio_data)

            if not audio_bytes.startswith(b"RIFF"):
                raise RuntimeError(
                    "Gemini output is not a valid WAV file."
                )

            output_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            output_path.write_bytes(audio_bytes)

            duration = validate_wav(
                output_path,
                len(text),
            )

            LOGGER.info(
                "Chunk %s completed: %.2f seconds",
                chunk_number,
                duration,
            )

            return {
                "chunk_number": chunk_number,
                "characters": len(text),
                "duration_seconds": duration,
                "path": str(output_path),
                "status": "success",
            }

        except Exception as error:

            last_error = error

            LOGGER.warning(
                "Chunk %s failed: %s",
                chunk_number,
                error,
            )

            if output_path.exists():
                output_path.unlink()

            if attempt < settings["max_retries"]:
                time.sleep(settings["retry_delay"])

    raise RuntimeError(
        f"Chunk {chunk_number} failed after "
        f"{settings['max_retries']} attempts: {last_error}"
    )


def combine_audio_chunks(chunk_paths, output_path):
    """Combine WAV files without changing their sample format."""

    if not chunk_paths:
        raise ValueError("No audio chunks to combine.")

    reference_params = None
    total_frames = 0

    for path in chunk_paths:

        with wave.open(str(path), "rb") as audio:

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

    with wave.open(str(output_path), "wb") as output:

        output.setnchannels(reference_params.nchannels)
        output.setsampwidth(reference_params.sampwidth)
        output.setframerate(reference_params.framerate)
        output.setcomptype(
            reference_params.comptype,
            reference_params.compname,
        )

        for path in chunk_paths:

            with wave.open(str(path), "rb") as audio:
                output.writeframes(
                    audio.readframes(audio.getnframes())
                )

    duration = total_frames / reference_params.framerate

    LOGGER.info(
        "Combined narration audio: %.2f seconds",
        duration,
    )

    return round(duration, 2)


def save_report(report):
    REPORT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    REPORT_FILE.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=4,
        ),
        encoding="utf-8",
    )


def main():
    setup_logging()

    LOGGER.info("Starting Hindi horror voice generation.")

    create_project_directories()

    config = load_config()
    analysis = load_story_analysis()
    settings = get_voice_config(config)

    # Read and normalize the original story exactly once.
    narration = extract_original_narration(analysis)

    # This exact text is used for both saving and TTS generation.
    save_narration_text(narration)

    chunks = split_long_text(
        narration,
        settings["chunk_size"],
    )

    LOGGER.info(
        "Narration length: %s characters",
        len(narration),
    )

    LOGGER.info(
        "Total chunks to generate: %s",
        len(chunks),
    )

    # Remove old chunks and old final output before a fresh run.
    CHUNKS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    for old_file in CHUNKS_DIR.glob("narration_*.wav"):
        old_file.unlink()

    for old_file in [
        FINAL_AUDIO,
        REPORT_FILE,
    ]:
        if old_file.exists():
            old_file.unlink()

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
            settings=settings,
            chunk_number=index,
        )

        generated_chunks.append(result)
        chunk_paths.append(output_path)

    if len(generated_chunks) != len(chunks):
        raise RuntimeError(
            "Not all narration chunks were generated."
        )

    # Verify every generated chunk against its source chunk.
    for index, (result, source_chunk) in enumerate(
        zip(generated_chunks, chunks),
        start=1,
    ):

        if result["characters"] != len(source_chunk):
            raise RuntimeError(
                f"Chunk {index} character count mismatch."
            )

        if result["status"] != "success":
            raise RuntimeError(
                f"Chunk {index} was not generated successfully."
            )

        if not Path(result["path"]).exists():
            raise RuntimeError(
                f"Chunk {index} audio file is missing."
            )

    # Include spaces between chunks in the total character count.
    reconstructed_narration = " ".join(chunks)

    generated_characters = len(reconstructed_narration)

    if reconstructed_narration != narration:
        raise RuntimeError(
            "Generated chunk text does not match original narration."
        )

    if generated_characters != len(narration):
        raise RuntimeError(
            "Generated narration character count does not match "
            "the original narration."
        )

    # Combine all successfully generated audio chunks.
    total_duration = combine_audio_chunks(
        chunk_paths,
        FINAL_AUDIO,
    )

    final_duration = validate_wav(
        FINAL_AUDIO,
        len(narration),
    )

    if abs(total_duration - final_duration) > 0.1:
        raise RuntimeError(
            "Final audio duration verification failed."
        )

    chunk_character_sum = sum(
        item["characters"]
        for item in generated_chunks
    )

    report = {
        "story_title": analysis.get(
            "title",
            "Hindi Horror Story",
        ),
        "model": settings["model"],
        "voice": settings["voice"],
        "language": "Hindi",
        "narration_source": "input/story.txt",
        "narration_characters": len(narration),
        "generated_characters": generated_characters,
        "chunk_character_sum_without_separators": (
            chunk_character_sum
        ),
        "inter_chunk_spaces": max(0, len(chunks) - 1),
        "total_chunks": len(generated_chunks),
        "total_duration_seconds": final_duration,
        "average_characters_per_second": round(
            len(narration) / final_duration,
            2,
        ),
        "narration_file": str(NARRATION_FILE),
        "final_audio": str(FINAL_AUDIO),
        "chunks": generated_chunks,
        "status": "success",
    }

    save_report(report)

    LOGGER.info("------------------------------------")
    LOGGER.info("VOICE GENERATION COMPLETED")
    LOGGER.info("Original characters: %s", len(narration))
    LOGGER.info("Generated characters: %s", generated_characters)
    LOGGER.info("Total chunks: %s", len(generated_chunks))
    LOGGER.info("Final audio: %.2f seconds", final_duration)
    LOGGER.info("Final audio file: %s", FINAL_AUDIO)
    LOGGER.info("------------------------------------")


if __name__ == "__main__":

    try:
        main()

    except Exception:
        logging.exception(
            "VOICE GENERATION FAILED"
        )
        raise
