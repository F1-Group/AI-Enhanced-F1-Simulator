import os
from dotenv import load_dotenv # Load .env file 
from ibm_watsonx_ai import Credentials # IBM Icloud sign in
from ibm_watsonx_ai.foundation_models import ModelInference # LLM Granite

load_dotenv()

credentials = Credentials(
    url="https://us-south.ml.cloud.ibm.com",
    api_key=os.getenv("GRANITE_API_KEY") # Sign in 
)

model = ModelInference( 
    model_id="ibm/granite-4-h-small", # The model we use
    credentials=credentials,
    project_id=os.getenv("GRANITE_PROJECT_ID")
)

def ask_race_engineer(system_prompt, user_prompt):
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    response = model.chat(messages=messages)
    return response['choices'][0]['message']['content']