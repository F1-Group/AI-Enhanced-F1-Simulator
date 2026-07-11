import os
import time
from dotenv import load_dotenv
from ibm_watsonx_ai import Credentials
from ibm_watsonx_ai.foundation_models import ModelInference
from pathlib import Path

os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)

load_dotenv()

credentials = Credentials(
    url="https://us-south.ml.cloud.ibm.com",
    api_key=os.getenv("GRANITE_API_KEY")
)
model = None

def init_granite_model():
    global model
    if model is None:
        model = ModelInference(
            model_id="ibm/granite-4-h-small",
            credentials=credentials,
            project_id=os.getenv("GRANITE_PROJECT_ID")
        )
    return model

def get_ai_link_status():
    # Health check interface for the UI team to verify the IBM Watsonx API connection.
    try:
        # Send a minimal 1-token prompt to check connection and API quota
        test_messages = [{"role": "user", "content": "ping"}]
        
        # Replace with your actual Watsonx model invocation syntax
        model.chat(messages=test_messages)
        
        return {
            "llm_connected": True,
            "message": "AI Link operational."
        }
    except Exception as e:
        error_msg = str(e)
        # Catching the token quota exhaustion (403) or network failure
        if "403" in error_msg or "quota" in error_msg.lower():
            return {
                "llm_connected": False,
                "message": "AI quota exhausted (403 Quota Reached)."
            }
        return {
            "llm_connected": False,
            "message": f"AI connection failed: {error_msg}"
        }

FALLBACK_SCRIPTS = {
    "poor_corner_exit": "Corner exit speed too low. Apply throttle earlier and more progressively.",
    "poor_track_position": "You are off the ideal racing line. Follow the baseline more closely.",
    "unstable_throttle": "Throttle is unstable. Use one smooth application instead of pumping.",
    "sector_time_loss": "Significant time lost in this sector. Focus on corner exits.",
}

FALLBACK_DEFAULT = "Focus on smooth inputs and following the racing line."
AUDIO_DIR = Path(__file__).parent / "audio"


def get_fallback_text(error_type: str) -> str:
    return FALLBACK_SCRIPTS.get(error_type, FALLBACK_DEFAULT)


def get_fallback_wav(error_type: str):
    wav_path = AUDIO_DIR / f"fallback_{error_type}_pcm.wav"
    if wav_path.exists():
        return str(wav_path)
    any_fallback = list(AUDIO_DIR.glob("fallback_*_pcm.wav"))
    if any_fallback:
        return str(any_fallback[0])
    return None


def ask_race_engineer(system_prompt, user_prompt, max_retries=2, wait_seconds=5, error_type=None):
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_prompt}
    ]

    for attempt in range(1, max_retries + 1):
        try:
            response = model.chat(messages=messages)
            text = response['choices'][0]['message']['content']
            return text, False
        except Exception as e:
            error_text = str(e)
            if "429" in error_text or "consumption_limit_reached" in error_text:
                print(f"[Rate limited] Attempt {attempt}/{max_retries}. Waiting {wait_seconds}s...")
                if attempt < max_retries:
                    time.sleep(wait_seconds)
                else:
                    print("[Rate limited] Max retries reached. Using fallback.")
                    break
            else:
                print(f"[Granite error] {error_text}")
                break

    fallback_text = get_fallback_text(error_type or "")
    print(f"[Fallback] Using rule-based text: {fallback_text}")
    return fallback_text, True
