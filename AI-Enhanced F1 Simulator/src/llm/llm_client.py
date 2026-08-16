import os
import time
import platform
import shutil
import subprocess
from pathlib import Path
import ollama

MODEL_NAME = "granite3-dense:2b"

# We added a timeout for Ollama calls to prevent them from hanging indefinitely and allow the retry and fallback logic to run.
REQUEST_TIMEOUT_SECONDS = 10
_client = ollama.Client(timeout=REQUEST_TIMEOUT_SECONDS)

def init_granite_model():
    """Automatically check and start the Ollama service and Granite 2B model (supports Win / Mac / Linux)."""
    print(f"[Ollama] Initializing local Granite engine ({MODEL_NAME})...")
    
    os_type = platform.system()
    
    # Check if the Ollama service is running
    try:
        _client.list()
    except Exception:
        print("[Ollama] Service not running. Attempting to start Ollama background process...")
        try:
            if os_type == "Windows":
                # Find the Ollama executable and launch it in the background
                ollama_bin = shutil.which("ollama.exe") or shutil.which("ollama")
                if not ollama_bin:
                    # Common default installation path on Windows
                    local_appdata = os.environ.get("LOCALAPPDATA", "")
                    candidate = os.path.join(local_appdata, "Programs", "Ollama", "ollama.exe")
                    if os.path.exists(candidate):
                        ollama_bin = candidate

                if ollama_bin:
                    # To prevents the black CMD window from popping up when running the command.
                    subprocess.Popen(
                        [ollama_bin, "serve"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=0x08000000
                    )
                else:
                    raise FileNotFoundError("Ollama executable not found in PATH or standard directory.")
            else:
                # macOS or Linux logic
                subprocess.Popen(
                    ["ollama", "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )

            # Give Windows a slightly longer wait time to start the service
            wait_time = 3.0 if os_type == "Windows" else 2.0
            time.sleep(wait_time)
            
        except Exception as e:
            print(f"[Ollama Error] Could not auto-start Ollama process: {e}")

    # Pre-load the model into memory
    try:
        _client.chat(model=MODEL_NAME, messages=[{"role": "user", "content": "hi"}])
        print(f"[Ollama SUCCESS] Model '{MODEL_NAME}' loaded successfully into memory.")
    except Exception as e:
        print(f"[Ollama Warning] Model load failed. Did you download it with 'ollama pull {MODEL_NAME}'? Error: {e}")

    return None

def get_ai_link_status():
    """Health check interface to verify local Ollama API connection."""
    try:
        test_messages = [{"role": "user", "content": "ping"}]
        _client.chat(model=MODEL_NAME, messages=test_messages)
        return {"llm_connected": True, "message": f"Local Ollama Granite ({MODEL_NAME}) operational."}
    except Exception as e:
        error_msg = str(e)
        status_msg = (
            f"Local Ollama connection failed! Reason: {error_msg}. "
            f"Please ensure 'ollama run granite3-dense:2b' is running in Terminal. "
            f"System switched to fallback mode."
        )
        return {"llm_connected": False, "message": status_msg}

FALLBACK_SCRIPTS = {
    "late_braking": "Brake earlier before the corner and release more smoothly for better entry stability.",
    "poor_corner_exit": "Corner exit speed too low. Apply throttle earlier and more progressively.",
    "poor_track_position": "You are off the ideal racing line. Follow the baseline more closely.",
    "unstable_throttle": "Throttle is unstable. Use one smooth application instead of pumping.",
    "sector_time_loss": "Significant time lost in this sector. Focus on corner exits.",
}

FALLBACK_DEFAULT = "Focus on smooth inputs and following the racing line."
AUDIO_DIR = Path(__file__).parent / "audio"


def get_fallback_text(error_type: str) -> str:
    return FALLBACK_SCRIPTS.get(error_type, FALLBACK_DEFAULT)


def ask_race_engineer(system_prompt, user_prompt, max_retries=1, wait_seconds=2):
    """
    Call local Ollama Granite and return coaching text as a string.
    Raises the last error once retries are exhausted, so the caller decides
    how to handle unavailability (fallback text, tripping a circuit breaker, etc).
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_prompt}
    ]

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            response = _client.chat(
                model=MODEL_NAME,
                messages=messages
            )
            return response['message']['content']
        except Exception as e:
            last_error = e
            print(f"[Local Granite Error] Attempt {attempt}/{max_retries}: {e}")
            if attempt < max_retries:
                time.sleep(wait_seconds)

    raise last_error