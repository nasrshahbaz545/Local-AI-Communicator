# config.py
import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# ---- llama-server connection -------------------------------------------------
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 1010
API_URL = f"http://{SERVER_HOST}:{SERVER_PORT}/v1/chat/completions"
MODELS_URL = f"http://{SERVER_HOST}:{SERVER_PORT}/v1/models"
HTTP_TIMEOUT = 600            # seconds to wait for the next byte (CPU prompt-eval can be slow)

# ---- context budget ----------------------------------------------------------
# MUST match the -c value you start llama-server with (start_server.bat uses 4096).
CONTEXT_TOKENS = 8192
MAX_TOKENS = 4096              # max length of one agent reply
DISABLE_THINKING = True       # asks Qwen-style models not to emit <think> blocks

# ---- agent loop --------------------------------------------------------------
MAX_STEPS = 4                 # script -> output -> fix cycles per request
HISTORY_MESSAGES = 6          # rolling window sent to the model (plus system prompt)
EXEC_TIMEOUT = 120            # seconds a generated script may run
MAX_OUTPUT_CHARS = 2000       # script output sent back to the model is clipped to this

# ---- repetition protection --------------------------------------------------
REPETITION_MIN_CHARS = 240
REPETITION_WINDOW_WORDS = 14
REPETITION_WINDOW_HITS = 3
REPETITION_SENTENCE_HITS = 2

# Stop an excessively long prose response that contains neither code nor
# a concise final answer.
MAX_PROSE_CHARS = 12000

# ---- documents / summarizer --------------------------------------------------
CHUNK_CHARS = CONTEXT_TOKENS          # ~1000-1300 tokens per chunk: safe for the context above
MAX_ATTACH_CHARS = CONTEXT_TOKENS     # attached file text pasted into a normal chat turn
MAX_CHUNKS = 40                       # safety cap per file (40 chunks is roughly 160k characters)
SUMMARY_SUFFIX = ".summary.md"        # report.pdf -> report.pdf.summary.md
SUMMARIZABLE_EXTS = (".pdf", ".docx", ".txt", ".md", ".csv", ".json", ".log", ".xlsx", ".py")

# ---- sessions ----------------------------------------------------------------
SESSIONS_FILE = os.path.join(APP_DIR, "sessions.json")
MAX_SESSIONS = 30

BASE_PROMPT = (
    "You are a local Windows automation agent. You do tasks by writing short Python scripts "
    "that run automatically in the workspace folder, and then you see their output.\n\n"
    "RULES:\n"
    "1. Put code in ONE ```python block. Keep it short. Prefer the standard library "
    "(os, pathlib, csv, json, re, shutil).\n"
    "2. Print plain ASCII only. No emojis.\n"
    "3. Never delete files. Never overwrite originals: write a new output file instead.\n"
    "4. If an [ACTIVE SKILL] is shown, follow it exactly and use its helper functions.\n"
    "5. After you see the script output: if the task is done, reply with a short plain-text "
    "answer and NO code block. If it failed, reply with one corrected ```python block.\n"
    "6. Document summaries are built in: tell the user to type /summarize (do not write code for it)."
)

THEMES = {
    "light": {
        "bg_main": "#ffffff",
        "bg_sidebar": "#f4f4f5",
        "bg_card": "#f8f8fa",
        "bg_input": "#f4f4f5",
        "bg_hover": "#e4e4e7",
        "border": "#e4e4e7",
        "text_primary": "#09090b",
        "text_muted": "#71717a",
        "accent": "#18181b",
        "accent_fg": "#ffffff",
        "code_bg": "#18181b",
        "code_fg": "#f4f4f5",
        "status_ok": "#16a34a",
        "status_err": "#dc2626",
    },
    "dark": {
        "bg_main": "#0d0d0d",
        "bg_sidebar": "#171717",
        "bg_card": "#1c1c1e",
        "bg_input": "#1e1e20",
        "bg_hover": "#27272a",
        "border": "#27272a",
        "text_primary": "#fafafa",
        "text_muted": "#a1a1aa",
        "accent": "#fafafa",
        "accent_fg": "#09090b",
        "code_bg": "#000000",
        "code_fg": "#e4e4e7",
        "status_ok": "#22c55e",
        "status_err": "#ef4444",
    },
}
