# agent.py - LLM client, agent loop and document summarizer. No GUI code in here.
import glob
import json
import os
import re
import time
import urllib.error
import urllib.request

import config
import tools


class Cancelled(Exception):
    """Raised when the user presses Stop."""


class LLMError(Exception):
    """Server unreachable / HTTP error."""


class EmptyReply(LLMError):
    """The model answered with nothing usable."""


class Hooks:
    """Callbacks the GUI implements. All methods are called from the worker thread."""

    def assistant_start(self): pass
    def stream(self, text): pass
    def assistant_end(self, content, meta): pass
    def tool(self, output): pass
    def status(self, text): pass
    def confirm(self, code, reasons): return True
    def abort(self): pass


THINK_RE = re.compile(r"<think>[\s\S]*?(?:</think>|\Z)", re.I)
CODE_RE = re.compile(r"```(?:python|py)[ \t]*\r?\n([\s\S]*?)(?:```|\Z)", re.I)

DONE_HINT = "If the task is complete, reply with a short plain-text answer and no code block."
FIX_HINT = "Fix the error and reply with one corrected ```python block."

RISKS = [
    (r"\bos\.(remove|unlink|rmdir|removedirs)\b|\.unlink\(|\bshutil\.rmtree\b|\bsend2trash\b", "deletes files or folders"),
    (r"\bos\.(rename|replace)\b|\bshutil\.(move|copytree)\b|\bapply_renames\b", "renames or moves files"),
    (r"\bos\.(system|startfile|popen)\b|\bsubprocess\b", "runs other programs or shell commands"),
    (r"\bwinreg\b|\bctypes\b", "touches the Windows registry or system libraries"),
    (r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]|\.\.[\\/]", "uses absolute paths or '..' (may leave the workspace)"),
]


# --------------------------------------------------------------------------- helpers
def clean_reply(text: str) -> str:
    return THINK_RE.sub("", text).strip()


def extract_code(reply: str) -> str:
    blocks = [b.strip() for b in CODE_RE.findall(reply)]
    return "\n\n".join(b for b in blocks if b)


def find_risks(code: str) -> list:
    return [why for pattern, why in RISKS if re.search(pattern, code)]


def _est(text: str) -> int:
    return len(text) // 3 + 4          # deliberately pessimistic token estimate


def build_messages(messages: list) -> list:
    """Turn the stored conversation into a clean, alternating, budget-limited payload."""
    system = {"role": "system", "content": messages[0]["content"]}
    merged = []
    for m in messages[1:][-config.HISTORY_MESSAGES:]:
        role, text = m["role"], m["content"]
        if role == "tool":
            role = "user"
            hint = FIX_HINT if text.startswith("[ERROR]") else DONE_HINT
            text = f"[Script output]\n{text}\n\n{hint}"
        if role not in ("user", "assistant"):
            continue                    # skips 'status' rows etc.
        if merged and merged[-1]["role"] == role:
            merged[-1]["content"] += "\n\n" + text
        else:
            merged.append({"role": role, "content": text})

    budget = config.CONTEXT_TOKENS - config.MAX_TOKENS - 100 - _est(system["content"])
    while len(merged) > 1 and sum(_est(m["content"]) for m in merged) > budget:
        merged.pop(0)
    if len(merged) > 1 and merged[0]["role"] == "assistant":
        merged.pop(0)
    if merged and _est(merged[-1]["content"]) > budget:
        keep = max(budget, 200) * 3 - 40      # 40 = length of the marker below
        txt = merged[-1]["content"]
        merged[-1]["content"] = txt[: keep // 2] + "\n...[trimmed to fit context]...\n" + txt[-(keep // 2):]
    return [system] + merged


# --------------------------------------------------------------------------- LLM client
def call_llm(messages, max_tokens=None, temperature=0.2, on_text=None, cancel=None):
    """Streaming chat completion. Returns (text, meta_string)."""
    payload = {
        "model": "local-model",
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens or config.MAX_TOKENS,
        "stream": True,
    }
    if config.DISABLE_THINKING:
        payload["chat_template_kwargs"] = {"enable_thinking": False}

    req = urllib.request.Request(
        config.API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    parts, n_chunks, start = [], 0, time.time()
    try:
        with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as resp:
            for raw in resp:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                line = raw.decode("utf-8", errors="ignore").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                piece = (choices[0].get("delta") or {}).get("content") or ""
                if piece:
                    parts.append(piece)
                    n_chunks += 1

                    accumulated = "".join(parts)

                    if has_repetition_loop(accumulated):
                        return (
                            "[Generation stopped: the model entered a repetition loop. "
                            "Please clarify the requested filename, file format, and content.]",
                            "stopped by repetition guard",
                        )

                    if (
                        len(accumulated) > config.MAX_PROSE_CHARS
                        and not extract_code(accumulated)
                    ):
                        return (
                            "[Generation stopped: the response became excessively long without "
                            "producing an executable script or concise answer. Please clarify "
                            "the requested action.]",
                            "stopped by length guard",
                        )

                    if on_text:
                        on_text(piece)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:400]
        raise LLMError(f"HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        raise LLMError(f"Cannot reach llama-server at {config.SERVER_HOST}:{config.SERVER_PORT} ({e.reason})")
    except OSError as e:
        raise LLMError(f"Connection problem: {e}")

    elapsed = max(time.time() - start, 0.001)
    meta = f"~{n_chunks} tokens · {elapsed:.1f}s · {n_chunks / elapsed:.1f} tok/s"
    return "".join(parts), meta


# --------------------------------------------------------------------------- agent loop
def run_agent(conv, hooks, cancel, confirm_all=False):
    """Model -> script -> output -> model ... until the model answers without code."""
    last_code = None
    for step in range(1, config.MAX_STEPS + 1):
        hooks.assistant_start()
        reply, meta = call_llm(build_messages(conv["messages"]), on_text=hooks.stream, cancel=cancel)
        reply = clean_reply(reply)
        conv["messages"].append({"role": "assistant", "content": reply or "(empty reply)", "meta": meta})
        hooks.assistant_end(reply or "(empty reply)", meta)

        code = extract_code(reply)
        if not code:
            return

        if code == last_code:
            hooks.status("The model sent the same script again - stopping to avoid a loop.")
            return
        last_code = code

        risks = find_risks(code)
        if confirm_all or risks:
            if not hooks.confirm(code, risks):
                conv["messages"].append({"role": "tool", "content": "[The user declined to run this script.]"})
                hooks.tool("[Skipped - you declined to run the script]")
                return

        hooks.status(f"Running script (step {step}/{config.MAX_STEPS}) ...")
        ok, output = tools.execute_python_snippet(code)
        conv["messages"].append({"role": "tool", "content": output})
        hooks.tool(output)
        if cancel.is_set():
            raise Cancelled()

    hooks.status("Reached the step limit. Tell me to continue if the task is not finished.")

def has_repetition_loop(text: str) -> bool:
    """Return True when generated prose shows an obvious repetition loop."""
    normalized = " ".join(clean_reply(text).lower().split())

    if len(normalized) < config.REPETITION_MIN_CHARS:
        return False

    # Detect repeated sentences.
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", normalized)
        if len(sentence.strip()) >= 35
    ]

    counts = {}
    repeated_sentences = 0

    for sentence in sentences:
        key = sentence[:240]
        counts[key] = counts.get(key, 0) + 1

        if counts[key] == 2:
            repeated_sentences += 1

    if repeated_sentences >= config.REPETITION_SENTENCE_HITS:
        return True

    # Detect repeated word sequences even where wording has minor variations.
    words = normalized.split()
    width = config.REPETITION_WINDOW_WORDS

    if len(words) < width * 2:
        return False

    windows = {}

    for index in range(len(words) - width + 1):
        key = " ".join(words[index:index + width])
        windows[key] = windows.get(key, 0) + 1

        if windows[key] >= config.REPETITION_WINDOW_HITS:
            return True

    return False

# --------------------------------------------------------------------------- summarizer
SUMMARY_SYSTEM = "You write accurate, concise summaries. Use only the text you are given. Reply in plain text with no preamble."

FINAL_PROMPT = (
    "Write the final summary of the document '{name}' from the {kind} below.\n"
    "Format:\nOverview: 2-3 sentences.\nKey points:\n- 5 to 10 bullet points.\n"
    "Use only facts that appear in the {kind}. No preamble.\n\n{kind_up}:\n{material}"
)


def _ask(prompt, max_tokens, cancel):
    msgs = [{"role": "system", "content": SUMMARY_SYSTEM}, {"role": "user", "content": prompt}]
    text, _ = call_llm(msgs, max_tokens=max_tokens, cancel=cancel)
    text = clean_reply(text)
    if not text:
        raise EmptyReply("the model returned an empty answer (thinking mode may be using the whole token budget)")
    return text


def _condense(parts, name, hooks, cancel):
    """Shrink a list of notes until they fit in one chunk."""
    for _ in range(4):
        joined = "\n\n".join(parts)
        if len(joined) <= config.CHUNK_CHARS:
            return parts
        groups = tools.chunk_text(joined, config.CHUNK_CHARS)
        if len(groups) >= len(parts):
            break
        hooks.status(f"   condensing notes ({len(groups)} groups) ...")
        parts = [
            _ask(f"Condense these notes about '{name}' into at most 6 bullet points. "
                 f"Keep key facts, names, numbers.\n\n{g}", 300, cancel)
            for g in groups
        ]
    return ["\n\n".join(parts)[: config.CHUNK_CHARS]]


def summarize_text(text, name, hooks, cancel):
    """Map-reduce summary. Returns (summary, n_chunks, truncated)."""
    chunks = tools.chunk_text(text, config.CHUNK_CHARS)
    truncated = len(chunks) > config.MAX_CHUNKS
    chunks = chunks[: config.MAX_CHUNKS]
    n = len(chunks)

    if n == 1:
        kind, material = "text", chunks[0]
    else:
        notes = []
        for i, ch in enumerate(chunks, 1):
            hooks.status(f"   reading section {i}/{n} ...")
            notes.append(_ask(
                f"This is part {i} of {n} of the document '{name}'. Write 3-5 short bullet points "
                f"with the key facts (keep names, numbers, dates). Use only this text.\n\n{ch}", 300, cancel))
        kind, material = "notes", "\n\n".join(_condense(notes, name, hooks, cancel))

    hooks.status("   writing final summary ...")
    final = _ask(FINAL_PROMPT.format(name=name, kind=kind, kind_up=kind.upper(), material=material), 600, cancel)
    return final, n, truncated


def summary_path_for(src, workspace):
    """Beside the source if it lives in the workspace, otherwise inside the workspace."""
    src, ws = os.path.abspath(src), os.path.abspath(workspace)
    try:
        inside = os.path.commonpath([src, ws]) == ws
    except ValueError:                      # different drives
        inside = False
    folder = os.path.dirname(src) if inside else ws
    return os.path.join(folder, os.path.basename(src) + config.SUMMARY_SUFFIX)


def summarize_file(src, workspace, hooks, cancel, force=False):
    out = summary_path_for(src, workspace)
    name = os.path.basename(src)
    if os.path.exists(out) and not force:
        return {"file": src, "status": "skipped", "out": out}

    text = tools.extract_attachment_text(src)
    if not text or text.startswith("[Error"):
        return {"file": src, "status": "failed", "reason": text or "no text found"}

    summary, n, truncated = summarize_text(text, name, hooks, cancel)
    header = (
        f"# Summary: {name}\n\n"
        f"- Source: {os.path.abspath(src)}\n"
        f"- Generated: {time.strftime('%Y-%m-%d %H:%M')}\n"
        f"- Sections processed: {n}" + (" (file was longer - only the first part was summarized)" if truncated else "")
        + "\n\n---\n\n"
    )
    with open(out, "w", encoding="utf-8") as f:
        f.write(header + summary + "\n")
    return {"file": src, "status": "saved", "out": out, "summary": summary}


def resolve_targets(arg, attach, workspace):
    if attach:
        return [attach]
    arg = arg.strip().strip("\"'")
    if not arg:
        return tools.list_summarizable(workspace)
    if os.path.isfile(arg):
        return [os.path.abspath(arg)]
    hits = glob.glob(os.path.join(glob.escape(workspace), arg))
    return [p for p in sorted(hits) if os.path.isfile(p) and not p.lower().endswith(config.SUMMARY_SUFFIX)]


def infer_summarize_args(prompt, workspace):
    """For plain-English requests such as 'summarize report.pdf' or 'summarize all files here'.
    Returns an argument string for /summarize, or None if the prompt is not a summarize request."""
    if not re.match(r"^\s*(please\s+|can you\s+|could you\s+)?summari[sz]e\b", prompt, re.I):
        return None
    low = prompt.lower()
    try:
        names = [f for f in os.listdir(workspace) if os.path.isfile(os.path.join(workspace, f)) and f.lower() in low]
    except OSError:
        names = []
    if names:                                   # handles file names that contain spaces
        return max(names, key=len)
    for tok in prompt.split():
        tok = tok.strip(" \t\"'()[],;:!?.")
        if not tok or tok.lower().startswith("summari"):
            continue
        if os.path.isfile(os.path.join(workspace, tok)):
            return tok
        if "*" in tok and glob.glob(os.path.join(glob.escape(workspace), tok)):
            return tok
    if re.search(r"\b(all|every|everything|folder|directory|files|documents|docs|workspace)\b", prompt, re.I):
        return ""
    return None


def _show(path, workspace):
    try:
        rel = os.path.relpath(path, workspace)
        return path if rel.startswith("..") else rel
    except ValueError:
        return path


def run_summarize(conv, arg, attach, hooks, cancel):
    ws = tools.get_workspace()
    force = bool(re.search(r"--force\b", arg))
    arg = re.sub(r"--force\b", "", arg).strip()
    targets = resolve_targets(arg, attach, ws)
    start = time.time()

    if not targets:
        text = (f"No files to summarize in {ws}" + (f" matching '{arg}'." if arg else ".")
                + "\nSupported types: " + ", ".join(config.SUMMARIZABLE_EXTS))
        return _say(conv, text, "", hooks)

    hooks.status(f"Summarizing {len(targets)} file(s) with the local model. Press Stop to cancel.")
    results = []
    for i, src in enumerate(targets, 1):
        if cancel.is_set():
            raise Cancelled()
        hooks.status(f"[{i}/{len(targets)}] {os.path.basename(src)}")
        try:
            results.append(summarize_file(src, ws, hooks, cancel, force))
        except EmptyReply as e:
            results.append({"file": src, "status": "failed", "reason": str(e)})

    saved = [r for r in results if r["status"] == "saved"]
    skipped = [r for r in results if r["status"] == "skipped"]
    failed = [r for r in results if r["status"] == "failed"]
    lines = []
    if saved:
        lines.append("Summaries saved:")
        lines += [f"- {os.path.basename(r['file'])}  ->  {_show(r['out'], ws)}" for r in saved]
    if skipped:
        lines.append("Skipped (summary already exists - add --force to redo):")
        lines += [f"- {os.path.basename(r['file'])}" for r in skipped]
    if failed:
        lines.append("Could not summarize:")
        lines += [f"- {os.path.basename(r['file'])}: {r['reason']}" for r in failed]
    text = "\n".join(lines)
    if len(saved) == 1:
        text += "\n\n" + saved[0]["summary"]
    return _say(conv, text, f"done in {time.time() - start:.0f}s", hooks)


def _say(conv, text, meta, hooks):
    conv["messages"].append({"role": "assistant", "content": text, "meta": meta})
    hooks.assistant_start()
    hooks.assistant_end(text, meta)
