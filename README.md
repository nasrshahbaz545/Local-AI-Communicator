# local AI communicator (v1.0)

**local AI communicator** is a lightweight, resource-efficient Windows desktop application designed to replace heavy web-based UIs. Built completely natively using Python and Tkinter, it connects directly to a local `llama-cpp` server to act as both an autonomous automation agent and a seamless local chat companion.

## Features

* **Lightweight & Resource-Efficient**: Built with native Python and Tkinter, avoiding heavy browser dependencies like Electron or webviews to operate within strict system resource constraints.
* **Direct llama-cpp Integration**: Communicates seamlessly with your local `llama-server` backend.
* **Intelligent Task Router**: Automatically distinguishes between casual chat/long-form writing tasks and technical automation requests.
* **Multi-Section Prose & Essay Writer**: Bypasses short-token model limitations to generate detailed, multi-thousand-word long-form texts and automatically saves outputs to `.txt` files.
* **Autonomous Code & Script Execution**: Writes and executes Python snippets, CMD commands, and PowerShell scripts safely within your active workspace folder[cite: 4, 6].
* **Document Summarization**: Built-in map-reduce text summarization supporting `.pdf`, `.docx`, `.xlsx`, `.csv`, `.md`, and `.txt` files[cite: 4, 7, 10].
* **Data & File Utilities**: Includes helper tools for cleaning messy spreadsheets (trimming spaces, dropping duplicates) and safe file renaming with preview and undo support[cite: 5, 6].
* **Polished Desktop UI**: Features a clean split layout with right-aligned user prompts, left-aligned AI responses, markdown text rendering, instant clipboard copy support, and a Light/Dark theme toggle.

## Tech Stack

* **Language**: Python 3.14+
* **UI Framework**: Tkinter / ScrolledText
* **Backend**: Local `llama-cpp` (`llama-server`) via OpenAI-compatible API
* **Document Parsing**: `pypdf`, `python-docx`, `openpyxl`[cite: 3]

## Quick Setup

1. Clone or download this repository into your local workspace.
2. Install the required Python dependencies:
   ```bash
   pip install -r requirements.txt
