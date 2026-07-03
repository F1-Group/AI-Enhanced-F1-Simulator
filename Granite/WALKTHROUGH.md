# AI / LLM Coaching Team — Walkthrough

**Team Members:** Marty, Samuel  
**Module:** IBM Granite AI Race Engineer 

---

## Overview

The AI team is responsible for the AI Reasoning Layer of the F1 AI Simulator. Our module takes structured telemetry data and detected driving errors as input, queries IBM Granite via watsonx.ai, and outputs structured coaching feedback in JSON format for the UI team and Audio Manager.

---

## System Architecture (Our Layer)

Analysis Team (error\_detection.py)

        ↓ error objects (JSON)

RAG Knowledge Base (rag.py)

        ↓ relevant knowledge chunks

Prompt Optimizer & Context Binder (prompts.py)

        ↓ structured prompt with context

IBM Granite (granite\_client.py)

        ↓ raw text response

Output Parser & Response Guardrail (guardrail.py)

        ↓ validated JSON output

UI Team / Audio Manager

---

## Files

| File | Responsibility |
| :---- | :---- |
| `main.py` | Entry point. Orchestrates the full pipeline. |
| `prompts.py` | Builds structured prompts for Granite. Contains Track Dictionary. |
| `granite_client.py` | Connects to IBM Granite via watsonx.ai. Handles rate limit retries. |
| `guardrail.py` | Validates input questions and output responses. Returns JSON. |
| `rag.py` | Loads knowledge base and retrieves relevant chunks via semantic search. |
| `coaching_style.py` | Defines Aggressive, Supportive, and Technical coaching personas. |
| `knowledge_base/` | F1 domain knowledge organised by category. |

---

## Setup

### 1\. Install dependencies

pip install ibm-watsonx-ai python-dotenv chromadb sentence-transformers

### 2\. Configure credentials

Create a `.env` file in the `Granite/` folder:

GRANITE\_API\_KEY=your\_ibm\_cloud\_api\_key

GRANITE\_PROJECT\_ID=your\_watsonx\_project\_id

### 3\. Run

cd Granite

python3.11 main.py

---

## Input Format

### Telemetry (aligned with team schema)

telemetry \= {

    "timestamp": 45.3,        \# seconds into lap

    "lap\_distance": 1820.5,   \# metres from start

    "speed\_kmh": 212.4,       \# forward speed

    "track\_pos": 0.15,        \# lateral offset (-1 to 1\)

    "angle": 0.03,            \# car angle vs track

    "wheel\_spin": 0.12,       \# tyre slip indicator

    "lap\_time": 88.3,         \# current lap time (s)

    "best\_lap": 86.1,         \# reference lap time (s)

    "throttle": 0.68,         \# throttle input (0.0-1.0)

    "brake": 0.45,            \# brake input (0.0-1.0)

    "steer": \-0.12,           \# steering angle (-1.0-1.0)

    "gear": 5,                \# current gear (-1 to 6\)

    "rpm": 11200,             \# engine RPM

    "sector\_1": 28.3,         \# sector 1 time (s)

    "sector\_2": 35.1,         \# sector 2 time (s)

    "sector\_3": 24.9,         \# sector 3 time (s)

    "laps\_remaining": 18,     \# laps remaining

    "gap\_ahead": 2.1,         \# gap to car ahead (s)

    "gap\_behind": 4.2         \# gap to car behind (s)

}

### Error Objects (from Analysis Team)

errors \= \[

    {

        "tag": "T1\_late\_braking",

        "corner": "T1",

        "type": "late\_braking",

        "severity": "high",        \# high / medium / low

        "confidence": 0.85,

        "coaching\_hint": "Brake 25m earlier before T1.",

        "evidence": {

            "expert\_brake\_point\_m": 275.0,

            "player\_brake\_point\_m": 300.0,

            "braked\_late\_by\_m": 25.0,

            "entry\_overspeed\_kmh": 18.3

        }

    }

\]

---

## Output Format (JSON for UI Team)

{

    "is\_valid": true,

    "feedback": "Brake 25m earlier into T1 to stabilise corner entry.",

    "error\_type": "late\_braking",

    "severity": "high",

    "corner": "T1",

    "question": "Why am I losing time in Sector 2?"

}

If the question is invalid (not racing-related):

{

    "is\_valid": false,

    "feedback": "I can only answer questions related to racing.",

    "error\_type": null,

    "severity": null,

    "corner": null,

    "question": "What's the weather like today?"

}

---

## How to Integrate (for UI Team)

import sys

sys.path.append("Granite")

from main import get\_coaching\_feedback

\# Call this function with telemetry \+ question

result \= get\_coaching\_feedback(

    telemetry=telemetry\_dict,

    question="Why am I losing time in Sector 2?",

    track="monza",

    errors=errors\_from\_analysis\_team

)

\# Use the JSON output

print(result\["feedback"\])    \# display in UI

print(result\["severity"\])    \# colour code the alert

print(result\["corner"\])      \# highlight corner on map

---

## Coaching Styles

Three coaching personas are available via `coaching_style.py`:

| Style | Description | Example |
| :---- | :---- | :---- |
| `aggressive` | Blunt and direct | "You braked 25m too late. Fix it." |
| `supportive` | Encouraging and constructive | "Good pace in S1. Try braking earlier into T1." |
| `technical` | Pure data, no emotion | "T1 brake delta: \+25m. Adjust to reference." |

Default style: `technical`

from coaching\_style import get\_system\_prompt

system\_prompt \= get\_system\_prompt("supportive")

---

## RAG Knowledge Base

Located in `knowledge_base/`, organised into:

knowledge\_base/

├── driving\_technique/

│   ├── braking.txt

│   ├── cornering.txt

│   ├── gear\_rpm.txt

│   ├── steering\_angle.txt

│   ├── throttle.txt

│   └── weight\_transfer.txt

├── race\_strategy/

│   ├── pit\_strategy.txt

│   └── tyre\_management.txt

├── telemetry/

│   ├── lap\_distance\_sector.txt

│   └── track\_position.txt

└── tracks/

    ├── monza.txt

    ├── silverstone.txt

    └── spa.txt

The RAG system uses `sentence-transformers` and `ChromaDB` to semantically retrieve the most relevant knowledge chunks for each question before sending to Granite.

---

## Guardrail Rules

### Input (blocks non-racing questions)

Blocked topics: weather, politics, food, music, movies, sports (non-F1), relationships, etc.

### Output (validates Granite responses)

- Truncates responses over 40 words  
- Replaces invalid responses ("I don't know", "As an AI") with fallback coaching hints  
- Returns structured JSON regardless of Granite response quality

---

## Key Design Decisions

1. **RAG over fine-tuning** — Injecting knowledge via prompt is faster to iterate and does not require retraining Granite.  
2. **Error-driven prompting** — Analysis team errors are included in the prompt so Granite references real measured data, not guesses.  
3. **JSON output** — Structured output allows UI team and Audio Manager to consume feedback without parsing free text.  
4. **Guardrail as safety layer** — Rule-based checks reduce hallucination and keep responses on-topic without relying on Granite to self-regulate.  
5. **Coaching styles** — Separate system prompts for different personas allow the same pipeline to serve different user preferences.

