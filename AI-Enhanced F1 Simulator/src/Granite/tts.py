"""
tts.py (STABLE VERSION)
Convert Granite text output into pygame-compatible PCM WAV.
"""

import pyttsx3
import os
import subprocess
from pathlib import Path

AUDIO_OUTPUT_DIR = "audio"
OUTPUT_FILENAME = "granite_coaching_output.wav"


def _convert_to_pcm_wav(input_path: str) -> str:
    input_path = Path(input_path)

    output_path = input_path.with_name(
        input_path.stem + "_pcm.wav"
    )

    result = subprocess.run([
        "ffmpeg",
        "-y",
        "-i", str(input_path),
        "-acodec", "pcm_s16le",
        "-ar", "44100",
        "-ac", "1",
        str(output_path)
    ], capture_output=True, text=True)

    # 🔥 DEBUG MUST SEE THIS
    print("FFMPEG RETURN CODE:", result.returncode)
    print("FFMPEG STDERR:\n", result.stderr)

    if result.returncode != 0:
        raise RuntimeError("FFmpeg failed! See error above.")

    return str(output_path)

def generate_wav(text: str, filename: str = OUTPUT_FILENAME) -> str:
    """
    Convert text to speech and return pygame-safe WAV path.
    """

    os.makedirs(AUDIO_OUTPUT_DIR, exist_ok=True)

    raw_path = os.path.join(AUDIO_OUTPUT_DIR, filename)

    # Generate audio (pyttsx3 → may output AIFF-C on macOS)
    engine = pyttsx3.init()
    engine.setProperty('rate', 220)
    engine.setProperty('volume', 1.0)

    engine.save_to_file(text, raw_path)
    engine.runAndWait()

    print(f"TTS generated (raw): {raw_path}")

    # Convert to PCM WAV for pygame
    safe_path = _convert_to_pcm_wav(raw_path)

    print(f"TTS converted (PCM safe): {safe_path}")

    return safe_path


if __name__ == "__main__":
    test_text = "You braked too late at Turn 1. Fix braking point."
    path = generate_wav(test_text)
    print(f"Saved: {path}")
    