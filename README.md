# AI-Enhanced-F1-Simulator
Integrating IBM Granite models into an Open Source racing simulator - TORCS.

## Table of Contents
- [1. Prerequisites & Environment Setup](#1-prerequisites--environment-setup)
  - [1.1 System Requirements](#11-system-requirements)
  - [1.2 Repository & Dependency Installation](#12-repository--dependency-installation)
  - [1.3 Local AI Model Setup (Ollama & Granite 2B)](#13-local-ai-model-setup-ollama--granite-2b)
- [2. TORCS & Telemetry Configuration](#2-torcs--telemetry-configuration)
  - [2.1 TORCS Installation & Setup](#21-torcs-installation--setup)
  - [2.2 Configuring TORCS Race & Telemetry Server](#22-configuring-torcs-race--telemetry-server)
  - [2.3 Telemetry Integration Architecture](#23-telemetry-integration-architecture)
- [3. IBM Granite / Local LLM Integration](#3-ibm-granite--local-llm-integration)
- [4. Pipeline Architecture & Core Middleware](#4-pipeline-architecture--core-middleware)
  - [4.1 Hybrid Architecture & Data Flow](#41-hybrid-architecture--data-flow)
  - [4.2 Core Python Modules & Data Architecture](#42-core-python-modules--data-architecture)
- [5. Step-by-Step Execution Guide](#5-step-by-step-execution-guide)
  - [5.1 Execution Guide](#51-execution-guide)
  - [5.2 TORCS Game Controls & Display Shortcuts](#52-torcs-game-controls--display-shortcuts)
- [6. Expected Results & Verification](#6-expected-results--verification)
  - [6.1 Initialization & RAG Knowledge Base Loading](#61-initialization--rag-knowledge-base-loading)
  - [6.2 Handshake & Multiprocessing Input Startup](#62-handshake--multiprocessing-input-startup)
  - [6.3 Real-Time Coaching](#63-real-time-coaching)
  - [6.4 Post-Race Summary & Graceful System Shutdown](#64-post-race-summary--graceful-system-shutdown)
- [7. Troubleshooting & FAQs](#7-troubleshooting--faqs)

## 1. Prerequisites & Environment Setup
Before running the AI-Enhanced F1 Simulator, ensure your system satisfies the hardware/OS requirements, runtime environments, and local AI model dependencies detailed below.

### 1.1 System Requirements
* **Operating System:** 
  * **Linux:** Ubuntu 22.04 LTS (native recommended).
  * **Windows:** Windows 10/11.
  * **macOS:** macOS 12+ (Intel / Apple Silicon). *Note: Running TORCS on macOS requires [Wine](https://www.winehq.org/) to emulate the x86/Windows environment.*
* **RAM:** Minimum 8 GB (16 GB recommended to handle simultaneous simulation and LLM inference).
* **Python Environment:** Python 3.13+ (ensure `pip` and `venv` are configured).
---
### 1.2 Repository & Dependency Installation

Clone the repository and install the necessary Python packages in a virtual environment:

```bash
# Clone the repository
git clone https://github.com/F1-Group/AI-Enhanced-F1-Simulator.git

cd AI-Enhanced-F1-Simulator/

# Create virtual environment
# macOS / Linux
python3 -m venv venv  
# Windows
python -m venv venv

# Activate virtual environment
# macOS / Linux
source venv/bin/activate  
# Windows
venv\Scripts\activate

# One-line dependency installation
pip install -r requirements.txt
```
---
### 1.3 Local AI Model Setup (Ollama & Granite 2B)
The coaching middleware uses a localized, offline **Ollama** server running **IBM Granite 2B** weights to eliminate cloud API latency and avoid token limits.
#### Step 1: Install Ollama
Open a new terminal window.
* **Linux:**
  ```bash
  curl -fsSL https://ollama.com/install.sh | sh
  ```
* **macOS:** Download and install the application directly from [Ollama.com](https://ollama.com/download/mac) or install via Homebrew:
  ```bash
  # Install Homebrew if not already installed
  brew install ollama
  ```

* **Windows:** Download and run the installer directly from [Ollama.com](https://ollama.com/download/windows)

#### Step 2: Pull the Granite 2B Model
Pull the specific 2B dense model weights to your local machine:
```bash
ollama pull granite3-dense:2b
```

#### Step 3: Verify Ollama Service
Ensure the Ollama service is active and accessible locally on http://localhost:11434:
```bash
ollama list
```
*(You should see `granite3-dense:2b` listed in the installed models output.)*

Test if the model loads and responds properly in your terminal:
```bash
ollama run granite3-dense:2b "Hello"
```
*(Confirm that the model generates a response without memory or loading errors, then exit the prompt using /bypass or Ctrl+D.)*

Close the terminal window.

## 2. TORCS & Telemetry Configuration
This section guides you through installing the **TORCS (The Open Racing Car Simulator)** simulator and configuring its telemetry server to stream vehicle data (`scr_server`) over UDP sockets to the Python coaching middleware.
### 2.1 TORCS Installation & Setup
TORCS is supported natively on Windows and Linux, while macOS requires **Wine** emulation.

#### A. Windows Installation
1. Download and unzip `torcs.zip` from the [Link](https://drive.google.com/file/d/1edIgHxBrDELr5LQM50B-2MpmcIX0DVQt/view?usp=sharing).
2. Verify `torcs\wtorcs.exe` is executable.

#### B. Linux (Ubuntu / Debian) Installation
1. Install TORCS via the system package manager:
   ```bash
   sudo apt-get update
   sudo apt-get install torcs
   ```
2. Verify installation by running `torcs` in your terminal.

#### C. macOS Installation (via Wine)
Running TORCS on macOS requires running the Windows binary under **Wine**.
1. Download and unzip `torcs.zip` from the [Link](https://drive.google.com/file/d/1edIgHxBrDELr5LQM50B-2MpmcIX0DVQt/view?usp=sharing) to your preferred directory (e.g., `$HOME/torcs` or `/Applications/torcs`)..
2. Install Wine via Homebrew or [Wine Download](https://www.winehq.org/):
    ```bash
    # Install Homebrew if not already installed
    brew install --cask wine-stable
    ```
3. Open **System Settings > Privacy & Security** on your Mac and grant execution permissions to Wine.
4. Launch TORCS using one of the following methods:
  - **Via Wine Terminal:**
    Open Wine application.
    ```bash
    wine /path/to/your/torcs/torcs/wtorcs.exe
    ```
    *(Replace /path/to/your/torcs with your actual installation directory path.)*
  - **Via Finder (Right-Click):** Open Finder, navigate to your TORCS directory, right-click wtorcs.exe, select Open With, and choose Wine (or Wine Stable).

> **Warning for macOS Users:**
Running TORCS inside Wine on macOS can occasionally crash or freeze upon launching a track or loading graphical textures. If TORCS crashes unexpectedly, close the Wine process completely, re-launch, and try again. You may need to attempt launching the race 2–3 times before it runs stably.

---
### 2.2 Configuring TORCS Race & Telemetry Server
To allow the Python middleware to capture telemetry data and send control commands, TORCS must launch with `scr_server` enabled.

1. Launch **TORCS** (wtorcs.exe)
2. Navigate to: **Race** $\rightarrow$ **Quick Race** $\rightarrow$ **Configure Race**
<p align="center">
  <img src="./assets/images/Race.png" alt="TORCS Race" width="400"/>
  <img src="./assets/images/Quick_Race.png" alt="Quick Race" width="400"/>
  <img src="./assets/images/Configure_Race.png" alt="Configure Race" width="400"/>
</p>

3. **Select Track:** **Olethros Road 1**
<p align="center">
  <img src="./assets/images/Select_Track.png" alt="Select Track" width="400"/>
</p>

4. **Select Drivers:** Make sure `scr_server 1` is selected and added to the driver list. This enables the UDP socket server for AI driver integration.
<p align="center">
  <img src="./assets/images/Select_Drivers.png" alt="Select Drivers" width="400"/>
</p>

5. Click **New Race**
<p align="center">
  <img src="./assets/images/Laps.png" alt="Laps" width="400"/>
  <img src="./assets/images/New_Race.png" alt="New Race" width="400"/>
</p>

6. The simulator will pause and display: `Initializing Driver scr_server 1`
<p align="center">
  <img src="./assets/images/Initializing_Driver.png" alt="Initializing Driver" width="400"/>
</p>

*It is now waiting for the Python middleware client to connect over UDP.*

---
### 2.3 Telemetry Integration Architecture
The interaction between the simulator and the client application operates as a low-latency, bidirectional socket pipeline:
<p align="center">
  <img src="./assets/images/Telemetry_Integration_Architecture.png" alt="Telemetry Integration Architecture" width="400"/>
</p>

* **Raw Telemetry Data (UDP Stream):** `TORCS (scr_server 1)` streams live vehicle status and track sensor metrics to `Python (client.py)` over a UDP socket.
* **Driving Commands:** Based on incoming telemetry and control logic, `Python (client.py)` computes and sends actionable control inputs back to the TORCS server in real time.

## 3. IBM Granite / Local LLM Integration


## 4. Pipeline Architecture & Core Middleware

This section outlines the custom Python middleware architecture designed to resolve data isolation, handle high-frequency vehicle telemetry streaming, and process high-latency LLM inference.

### 4.1 Hybrid Architecture & Data Flow

To manage the high frequency of incoming UDP data alongside the higher latency of LLM inference, the middleware applies a **Lambda-inspired hybrid architecture** containing parallel **Fast** and **Slow** processing layers.

<p align="center">
  <img src="./assets/images/System_Architecture.png" alt="System Architecture" width="600"/>
</p>

1. **Telemetry Ingestion & Human Input Intercept:** The `Client` class connects to TORCS (`scr_server`) via UDP on port `3001` to receive continuous raw telemetry packets. Simultaneously, the dedicated `Input` process intercepts player keyboard commands asynchronously, which `Controller` converts into continuous control values to drive the vehicle.
2. **Fast Layer (Rule-Based Alerts):** Reads latest telemetry directly from the thread-safe cache. For critical driving events (e.g., immediate collision risks or severe off-track errors), the system bypasses LLM latency completely to trigger real-time audio and dashboard alerts instantly.
3. **Slow Layer (LLM Race Engineering):** Stores telemetry logs to disk and conducts lap comparisons against expert driving benchmarks. Dynamic error labels, background domain knowledge (F1 rules, track layout heuristics), and telemetry metrics are combined into structured prompts fed into the **IBM Granite 2B** model via Ollama. Validated AI responses are delivered as high-level race coaching advice.

---

### 4.2 Core Python Modules & Data Architecture

To prevent high-frequency UDP socket traffic from being blocked by disk I/O operations or heavy computation tasks, the middleware follows the **Single Responsibility Principle** using Python's `threading` and `multiprocessing` libraries.

| Module / Class | Role & Architectural Responsibility |
| :--- | :--- |
| `main.py` | **Main Entry Point:** Initializes global resources, launches background processes, and configures worker threads running in parallel without blocking system execution. |
| `cache.py` | **Thread-Safe Global Cache:** Acts as the shared state container tracking live telemetry and game state. Uses `threading.Lock()` to manage safe concurrent read/write operations across modules. |
| `Client` (`client.py`) | **UDP Network & Lifecycle Manager:** Handles UDP socket communication on port `3001` with TORCS (`scr_server 1`). Manages game lifecycles via a Finite State Machine (FSM) triggered by UDP timeouts. |
| `Input` (`input.py`) | **Asynchronous Keyboard Process:** Operates in a dedicated child process (`multiprocessing.Process`) to capture raw human keyboard input in real time, bypassing Python's GIL to ensure zero latency. |
| `Controller` (`controller.py`) | **Input Translator:** Converts raw binary keyboard events captured from `Input` into precise continuous floating-point signals for vehicle steering, braking, and throttle application. |


## 5. Step-by-Step Execution Guide

### 5.1 Execution Guide

Follow this sequence to launch TORCS and AI Race Telemetry & Coaching System in the correct order.

#### Step 1: Launch Local AI Engine (Ollama & Granite 2B)

Ensure your local Ollama background daemon is active and serving requests.

1. Start the Ollama server in your terminal (if not running as a background service):
    ```bash
    ollama serve
    ```
2. Verify that the Granite 2B model is pulled and ready:
    ```bash
    ollama list
    ```
*(Ensure granite3-dense:2b is present in the output list.)*

#### Step 2: Launch Middleware Application
Before running the middleware, set up your Python environment and install the required dependencies (for detailed installation instructions, please refer to [Section 1.2: Repository & Dependency Installation](#12-repository--dependency-installation)).

1. Open a terminal and navigate to your project directory:
    ```bash
    cd /path/to/your/repository
    ```
2. Activate your Python virtual environment (if applicable):
    ```bash
    # macOS / Linux
    source venv/bin/activate
    # Windows
    .\venv\Scripts\activate
    ```
3. Navigate to the source code directory where `main.py` is located:
    ```bash
    cd AI-Enhanced\ F1\ Simulator/src
    ```
4. Execute `main.py`:
    ```bash
    python main.py
    ```
5. Upon successful execution, the terminal will display:
    ```text
    [Main] Launching Dashboard GUI directly...
    ```
6. In the Dashboard GUI window that appears, click the **Start System** button.
<p align="center">
  <img src="./assets/images/Dashboard.png" alt="Dashboard" width="400"/>
</p>

7. The interface will begin initializing core modules.
<p align="center">
  <img src="./assets/images/Initializing_Core_Modules.png" alt="Initializing Core Modules" width="400"/>
</p>


8. Once initialization completes, the status will update to `SYSTEM READY`.
<p align="center">
  <img src="./assets/images/System_Ready.png" alt="System Ready" width="400"/>
</p>

> **Note on Troubleshooting**: If the GUI fails to display `SYSTEM READY` or throws an error during initialization, check the underlying terminal output for specific error logs (e.g., missing dependencies, unstarted Ollama service, or socket port conflicts).

#### Step 3: Launch TORCS Simulator
Once the Dashboard displays **`SYSTEM READY`**, launch TORCS simulator (for detailed step-by-step screenshots, refer to [Section 2.2: Configuring TORCS Race & Telemetry Server](#22-configuring-torcs-race--telemetry-server)).

1. Launch **TORCS** (wtorcs.exe)
2. Navigate to: **Race** $\rightarrow$ **Quick Race** $\rightarrow$ **Configure Race**
3. **Select Track:** **Olethros Road 1**
4. **Select Drivers:** Make sure `scr_server 1` is selected and added to the driver list. This enables the UDP socket server for AI driver integration.
5. Click **New Race**
6. The simulator will pause and display: `Initializing Driver scr_server 1`
7. When TORCS displays the screen showing `Initializing Driver scr_server 1...`, switch back to the **Dashboard GUI** and click the **New Race** button.
<p align="center">
  <img src="./assets/images/Initializing_Driver.png" alt="Initializing Driver" width="400"/>
  <img src="./assets/images/System_Ready.png" alt="System Ready" width="400"/>
</p>


8. Upon a successful UDP handshake, the application will transition to the **Live Telemetry Dashboard**, indicating real-time data streaming is active and the session is ready to play.
<p align="center">
  <img src="./assets/images/Live_Telemetry_Dashboard.png" alt="Live Telemetry Dashboard" width="400"/>
</p>


> **Connection Retry Warning:** On occasion, the UDP handshake between TORCS and the middleware may disconnect immediately after connecting. If the session drops, simply return to TORCS, re-select **New Race**, and click **New Race** on the Dashboard again to retry the handshake.
<p align="center">
  <img src="./assets/images/Connection_Error.png" alt="Connection Error" width="400"/>
</p>

---

### 5.2 TORCS Game Controls & Display Shortcuts

#### Primary Driving Controls
*(Note: To reverse the vehicle, use `Z` to shift down past Neutral into **Reverse (R)** gear.)*

| Key / Button | Vehicle Action |
| :--- | :--- |
| $\uparrow$ | **Throttle** (Accelerate) |
| $\downarrow$ | **Brake** |
| $\leftarrow$ | **Steer Right** |
| $\rightarrow$ | **Steer Left** |
| `A` / `a` | **Shift Up** |
| `Z` / `z` | **Shift Down** (Keep pressing to reach **R** gear for reverse) |

#### In-Game Camera & Display Shortcuts
Pressing **`F1`** during a race opens the in-game **Keys Definition** menu.

<p align="center">
  <img src="./assets/images/Keys_Definition.png" alt="Keys Definition" width="600"/>
</p>

## 6. Expected Results & Verification
You can verify that the system is operating correctly by cross-referencing your terminal output with the expected runtime execution logs below:

### 6.1 Initialization & RAG Knowledge Base Loading
When you click **Start System** on the Dashboard GUI, the terminal will log the local HuggingFace / Pygame imports, load the Ollama model, and index the RAG knowledge chunks:
```text
[Main] Launching Dashboard GUI directly...
[Main] User clicked Start. Beginning AI Core & RAG Initialization...
pygame 2.6.1 (SDL 2.28.4, Python 3.13.1)
[Ollama] Initializing local Granite engine (granite3-dense:2b)...
[Ollama SUCCESS] Model 'granite3-dense:2b' loaded successfully into memory.
Knowledge base loaded: 133 chunks indexed
```
---
### 6.2 Handshake & Multiprocessing Input Startup
When clicking **New Race**, the `Client` executes UDP handshake polling on port `3001`. The middleware includes built-in safeguards for connection timeouts and incomplete session cleanups:

**Handshake Polling & Handshake Recovery:**
```text
[Main] New Race clicked! Creating TORCS Client & Data Pipeline...
[Client] Connecting to TORCS on 127.0.0.1:3001...
[AI Thread] Async Consumer active. Listening to shared_event_queue...
[Client Warning] No response from TORCS, retrying handshake...
[Client] Handshake successful!
[InputHandler] Multiprocessing pynput listener started successfully.
```

**Invalid Session Cleanup:** If a session drops immediately after connecting, tiny invalid logs are automatically purged to keep the storage clean:
```text
[Client Error] Connection Lost: TORCS stopped sending data for >5 seconds.
Too few data, file deleted
```

---
### 6.3 Real-Time Coaching
During an active race, the terminal logs real-time interaction between the **Fast Layer** (low-latency safety alerts) and the **Slow Layer** (LLM-based sector race engineering):
```text
# Slow Layer: Complex LLM Sector Analysis
[Slow Layer Feedback]: You're losing significant time in Sector 1, especially between 0m to 2094m. Let's focus on improving your entry into the first chicane.
Speaking audio: AI Coaching Speech

# Fast Layer: Instant Event Alerts
[Fast Layer] Brake NOW!
Speaking audio: brake_now
[Fast Layer] You are off track
Speaking audio: off_track

# Audio Queue Management (Skipping Stale / Outdated Audio Alerts)
[Timeout Dropped] AI Coaching Speech is too old (2.84s old), skipping.
```

<p align="center">
  <img src="./assets/images/Live_Telemetry_Dashboard_Coaching.png" alt="Live Telemetry Dashboard Coaching" width="400"/>
</p>

---
### 6.4 Post-Race Summary & Graceful System Shutdown
When TORCS signals the end of a race session, the system transitions to `GameStatus.FINISHED`, compiles a post-race summary, and safely releases system resources:

```text
[Client] Race ended by TORCS signal.
Data saved successfully and safely! Absolute file path: .../telemetry_20260729_233538.csv
[Dashboard] GameStatus.FINISHED detected. Switching to Summary Page.

# AI Summary Generation
[AI] Compiling Macro Lap Summary Review
[AI SUCCESS] Summary successfully generated and saved to data/lap_summary.json.

# Graceful Resource Cleanup
[Audio] Initiating emergency forced shutdown...
[Audio] Terminated active background 'say' instances.
[Main] System halted cleanly.
```

<p align="center">
  <img src="./assets/images/Post-Race_Summary.png" alt="Post-Race Summary" width="400"/>
</p>

> **macOS Accessibility Notice:** Since the middleware uses global keyboard listeners (`pynput`) for input monitoring, macOS will prompt you for permission or display a warning: `This process is not trusted! Input event monitoring will not be possible...`
**Fix:** Go to **System Settings** $\rightarrow$ **Privacy & Security** $\rightarrow$ **Accessibility** (and **Input Monitoring**), toggle **ON** the switch for your terminal app (e.g., Terminal, iTerm2, or VS Code), then restart the terminal and re-run `python main.py`.


## 7. Troubleshooting & FAQs
