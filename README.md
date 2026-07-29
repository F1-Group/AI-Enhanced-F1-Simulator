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
- [5. Step-by-Step Execution Guide](#5-step-by-step-execution-guide)
- [6. Expected Results & Verification](#6-expected-results--verification)
- [7. Troubleshooting & FAQs](#7-troubleshooting--faqs)

## 1. Prerequisites & Environment Setup
Before running the AI-Enhanced F1 Simulator, ensure your system satisfies the hardware/OS requirements, runtime environments, and local AI model dependencies detailed below.

### 1.1 System Requirements
* **Operating System:** 
  * **Linux:** Ubuntu 22.04 LTS (native recommended).
  * **Windows:** Windows 10/11 with **WSL2** (Ubuntu distribution).
  * **macOS:** macOS 12+ (Intel / Apple Silicon). *Note: Running TORCS on macOS requires [Wine](https://www.winehq.org/) to emulate the x86/Windows environment.*
* **RAM:** Minimum 8 GB (16 GB recommended to handle simultaneous simulation and LLM inference).
* **Python Environment:** Python 3.10+ (ensure `pip` and `venv` are configured).

### 1.2 Repository & Dependency Installation

Clone the repository and install the necessary Python packages in a virtual environment:

```bash
# Clone the repository
git clone https://github.com/F1-Group/AI-Enhanced-F1-Simulator.git

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
### 1.3 Local AI Model Setup (Ollama & Granite 2B)
The coaching middleware uses a localized, offline **Ollama** server running **IBM Granite 2B** weights to eliminate cloud API latency and avoid token limits.
#### Step 1: Install Ollama
* **Linux / WSL2:**
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


## 2. TORCS & Telemetry Configuration
This section guides you through installing the **TORCS (The Open Racing Car Simulator)** simulator and configuring its telemetry server to stream vehicle data (`scr_server`) over UDP sockets to the Python coaching middleware.
### 2.1 TORCS Installation & Setup
TORCS is supported natively on Windows and Linux, while macOS requires **Wine** emulation.

#### A. Windows / Linux Installation
1. Download and unzip `torcs.zip` from the [Link](https://drive.google.com/file/d/1edIgHxBrDELr5LQM50B-2MpmcIX0DVQt/view?usp=sharing).
2. Verify `torcs\wtorcs.exe` is executable.

#### B. macOS Installation (via Wine)
Running TORCS on macOS requires running the Windows binary under **Wine**.
1. Download and unzip `torcs.zip` from the [Link](https://drive.google.com/file/d/1edIgHxBrDELr5LQM50B-2MpmcIX0DVQt/view?usp=sharing) to your preferred directory (e.g., `$HOME/torcs` or `/Applications/torcs`)..
2. Install Wine via Homebrew:
    ```bash
    # Install Homebrew if not already installed
    brew install --cask wine-stable
    ```
3. Open **System Settings > Privacy & Security** on your Mac and grant execution permissions to Wine.
4. Launch TORCS using one of the following methods:
  - **Via Wine Terminal:**
    ```bash
    wine /path/to/your/torcs/torcs/wtorcs.exe
    ```
    *(Replace /path/to/your/torcs with your actual installation directory path.)*
  - **Via Finder (Right-Click):** Open Finder, navigate to your TORCS directory, right-click wtorcs.exe, select Open With, and choose Wine (or Wine Stable).

**Warning for macOS Users:**
Running TORCS inside Wine on macOS can occasionally crash or freeze upon launching a track or loading graphical textures. If TORCS crashes unexpectedly, close the Wine process completely, re-launch, and try again. You may need to attempt launching the race 2–3 times before it runs stably.

### 2.2 Configuring TORCS Race & Telemetry Server
To allow the Python middleware to capture telemetry data and send control commands, TORCS must launch with `scr_server` enabled.

1. Launch **TORCS** (wtorcs.exe)
2. Navigate to: **Race** → **Quick Race** → **Configure Race**.
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

5. Click **New Race**.
<p align="center">
  <img src="./assets/images/Laps.png" alt="Laps" width="400"/>
  <img src="./assets/images/New_Race.png" alt="New Race" width="400"/>
</p>

6. The simulator will pause and display: `Initializing Driver scr_server 1`
<p align="center">
  <img src="./assets/images/Initializing_Driver.png" alt="Initializing Driver" width="400"/>
</p>

*It is now waiting for the Python middleware client to connect over UDP.*

### 2.3 Telemetry Integration Architecture
The interaction between the simulator and the client application operates as a low-latency, bidirectional socket pipeline:
<p align="center">
  <img src="./assets/images/Telemetry_Integration_Architecture.png" alt="Telemetry Integration Architecture" width="400"/>
</p>

* **Raw Telemetry Data (UDP Stream):** `TORCS (scr_server 1)` streams live vehicle status and track sensor metrics to `Python (client.py)` over a UDP socket.
* **Driving Commands:** Based on incoming telemetry and control logic, `Python (client.py)` computes and sends actionable control inputs back to the TORCS server in real time.

## 3. IBM Granite / Local LLM Integration


## 4. Pipeline Architecture & Core Middleware


## 5. Step-by-Step Execution Guide


## 6. Expected Results & Verification


## 7. Troubleshooting & FAQs