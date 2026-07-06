import os
import time
from dotenv import load_dotenv
from ibm_watsonx_ai import Credentials
from ibm_watsonx_ai.foundation_models import ModelInference

load_dotenv()

credentials = Credentials(
    url="https://us-south.ml.cloud.ibm.com",
    api_key=os.getenv("GRANITE_API_KEY")
)

model = ModelInference(
    model_id="ibm/granite-4-h-small",
    credentials=credentials,
    project_id=os.getenv("GRANITE_PROJECT_ID")
)


def ask_race_engineer(system_prompt, user_prompt, max_retries=3, wait_seconds=20):
    """
    Sends a request to Granite with automatic retry on rate limit (429) errors.
    Waits `wait_seconds` between attempts, up to `max_retries` times.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    for attempt in range(1, max_retries + 1):
        try:
            response = model.chat(messages=messages)
            return response['choices'][0]['message']['content']

        except Exception as e:
            error_text = str(e)

            if "429" in error_text or "consumption_limit_reached" in error_text:
                print(f"[Rate limited] Attempt {attempt}/{max_retries}. Waiting {wait_seconds}s before retrying...")
                if attempt < max_retries:
                    time.sleep(wait_seconds)
                else:
                    return "I'm currently experiencing high demand. Please try again in a few minutes."
            else:
                # Any other error (network, auth, etc.) - don't retry, just report it
                print(f"[Error] {error_text}")
                return f"Error contacting race engineer: {error_text}"

    return "Error: Max retries reached."