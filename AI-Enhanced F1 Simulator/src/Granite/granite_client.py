import os
import time
import subprocess
from pathlib import Path
import ollama

MODEL_NAME = "granite3-dense:2b"

def init_granite_model():
    """自動檢查並啟動 Ollama 服務與 Granite 2B 模型"""
    print(f"[Ollama] Initializing local Granite engine ({MODEL_NAME})...")
    
    # 1. 檢查 Ollama 背景服務是否已在運行，若沒運行則嘗試自動啟動
    try:
        ollama.list()
    except Exception:
        print("[Ollama] Service not running. Attempting to start Ollama background process...")
        try:
            # 在背景啟動 Ollama 服務 (Mac / Linux)
            subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(2)  # 等待服務啟動
        except Exception as e:
            print(f"[Ollama Error] Could not auto-start Ollama process: {e}")

    # 2. 預熱/預載入模型 (Warm-up)，讓第一句語音推論更快速
    try:
        ollama.chat(model=MODEL_NAME, messages=[{"role": "user", "content": "hi"}])
        print(f"[Ollama SUCCESS] Model '{MODEL_NAME}' loaded successfully into memory.")
    except Exception as e:
        print(f"[Ollama Warning] Model load failed. Did you download it with 'ollama pull {MODEL_NAME}'? Error: {e}")

    return None

def get_ai_link_status():
    """Health check interface to verify local Ollama API connection."""
    try:
        test_messages = [{"role": "user", "content": "ping"}]
        ollama.chat(model=MODEL_NAME, messages=test_messages)
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


def ask_race_engineer(system_prompt, user_prompt, max_retries=2, wait_seconds=2, error_type=None):
    """
    Call local Ollama Granite and return coaching text as a string.
    Falls back to a rule-based text if Ollama is unavailable.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_prompt}
    ]

    for attempt in range(1, max_retries + 1):
        try:
            # 使用 local ollama chat 替代 model.chat
            response = ollama.chat(
                model=MODEL_NAME,
                messages=messages
            )
            return response['message']['content']
        except Exception as e:
            error_text = str(e)
            print(f"[Local Granite Error] Attempt {attempt}/{max_retries}: {error_text}")
            if attempt < max_retries:
                time.sleep(wait_seconds)
            else:
                break

    fallback_text = get_fallback_text(error_type or "")
    print(f"[Fallback] Using rule-based text: {fallback_text}")
    return fallback_text