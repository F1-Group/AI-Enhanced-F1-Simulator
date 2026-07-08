"""
tts.py
Converts Granite's text output into a .wav file
and passes it to Team 2's Audio Manager for playback.
"""

import pyttsx3
import os

# Output directory for generated audio files
AUDIO_OUTPUT_DIR = "audio"
OUTPUT_FILENAME = "granite_coaching_output.wav"


def generate_wav(text: str, filename: str = OUTPUT_FILENAME) -> str:
    """
    Convert text to speech and save as .wav file.
    Returns the path to the generated file.
    """
    # Make sure audio/ folder exists
    os.makedirs(AUDIO_OUTPUT_DIR, exist_ok=True)
    
    output_path = os.path.join(AUDIO_OUTPUT_DIR, filename)
    
    engine = pyttsx3.init()
    
    # Optional: adjust voice settings
    engine.setProperty('rate', 160)    # Speed (default ~200, slower = clearer)
    engine.setProperty('volume', 1.0)  # Volume 0.0 to 1.0
    
    engine.save_to_file(text, output_path)
    engine.runAndWait()
    
    print(f"TTS generated: {output_path}")
    return output_path


if __name__ == "__main__":
    # Quick test
    test_text = "You braked 80 metres too late at Turn 1. Fix your braking point."
    path = generate_wav(test_text)
    print(f"Saved to: {path}")