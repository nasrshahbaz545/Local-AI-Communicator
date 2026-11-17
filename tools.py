# tools.py
import glob
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

import config

SKILLS_DIR = os.path.join(config.APP_DIR, "skills")
LOADED_SKILLS = {}
_WORKSPACE = os.getcwd()


# --------------------------------------------------------------------------- workspace
def set_workspace(path: str) -> bool:
    global _WORKSPACE
    if path and os.path.isdir(path):
        _WORKSPACE = os.path.abspath(path)
        return True
    return False


def get_workspace() -> str:
    """Always call this instead of importing a constant (a constant would go stale)."""
    return _WORKSPACE


# --------------------------------------------------------------------------- skills
def init_skills() -> str:
    """Scan skills/*.md and return a short index to append to the system prompt."""
    os.makedirs(SKILLS_DIR, exist_ok=True)
    LOADED_SKILLS.clear()
    index_lines = []

    for filepath in sorted(glob.glob(os.path.join(SKILLS_DIR, "*.md"))):
        try:
            with open(filepath, "r", encoding="utf-8-sig") as f:
                raw = f.read()

            name = os.path.splitext(os.path.basename(filepath))[0]
            triggers, desc, body = [], "General task skill.", raw

            m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", raw, re.S)
            if m:
                header, body = m.group(1), m.group(2).strip()
                for line in header.splitlines():
                    if line.startswith("name:"):
                        name = line.split(":", 1)[1].strip()
                    elif line.startswith("triggers:"):
                        triggers = [t.strip().lower() for t in line.split(":", 1)[1].split(",") if t.strip()]
                    elif line.startswith("description:"):
                        desc = line.split(":", 1)[1].strip()

            LOADED_SKILLS[name] = {"triggers": triggers, "description": desc, "body": body}
            index_lines.append(f"- {name}: {desc}")
        except Exception as e:
            print(f"[Skill Load Warning] {filepath}: {e}")

    if not index_lines:
        return ""
    return "\n\nSKILLS (loaded automatically when relevant):\n" + "\n".join(index_lines)


def match_relevant_skills(user_prompt: str, max_skills: int = 2) -> str:
    """Return the full text of the best-matching skills for this prompt."""
    p = user_prompt.lower()
    scored = []
    for name, data in LOADED_SKILLS.items():
        hits = sum(1 for t in data["triggers"] if t in p)
        if hits:
            scored.append((hits, name))
    scored.sort(reverse=True)
    blocks = [f"[ACTIVE SKILL: {n}]\n{LOADED_SKILLS[n]['body'][:2500]}" for _, n in scored[:max_skills]]
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------- reading files
def extract_attachment_text(file_path: str) -> str:
    """Return the text of a file. Failures are returned as a string starting with '[Error'."""
    if not file_path or not os.path.isfile(file_path):
        return "[Error: file not found]"

    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".pdf":
            import pypdf

            with open(file_path, "rb") as f:
                if not f.read(5).startswith(b"%PDF-"):
                    return "[Error: not a valid PDF (it may be an HTML page or a corrupted download)]"
            reader = pypdf.PdfReader(file_path)
            if reader.is_encrypted and not reader.decrypt(""):
                return "[Error: PDF is password protected]"
            pages = [(p.extract_text() or "") for p in reader.pages]
            content = "\n".join(pages)
            if not content.strip():
                return "[Error: no extractable text in this PDF (scanned/image-only?)]"

        elif ext == ".docx":
            import docx

            d = docx.Document(file_path)
            lines = [p.text for p in d.paragraphs]
            for table in d.tables:
                for row in table.rows:
                    lines.append(" | ".join(c.text.strip() for c in row.cells))
            content = "\n".join(lines)

        elif ext == ".doc":
            return "[Error: old .doc format is not supported - save it as .docx first]"

        elif ext in (".xlsx", ".xlsm"):
            import openpyxl

            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            lines = []
            for ws in wb.worksheets:
                lines.append(f"## Sheet: {ws.title}")
                for i, row in enumerate(ws.iter_rows(values_only=True)):
                    if i >= 500:
                        lines.append("[...more rows not shown...]")
                        break
                    lines.append("\t".join("" if c is None else str(c) for c in row))
            content = "\n".join(lines)

        else:
            with open(file_path, "rb") as f:
                if b"\x00" in f.read(2048):
                    return "[Error: binary file - cannot read as text]"
            with open(file_path, "r", encoding="utf-8-sig", errors="replace") as f:
                content = f.read()

    except ImportError as e:
        return f"[Error: missing Python package '{e.name}'. Run: pip install pypdf python-docx openpyxl]"
    except Exception as e:
        return f"[Error reading file: {e}]"

    return content.strip()


def list_summarizable(folder: str, pattern: str = "*") -> list:
    """Files in `folder` (not recursive) that /summarize can handle, excluding our own summaries."""
    out = []
    for p in sorted(glob.glob(os.path.join(glob.escape(folder), pattern))):
        low = p.lower()
        if os.path.isfile(p) and low.endswith(config.SUMMARIZABLE_EXTS) and not low.endswith(config.SUMMARY_SUFFIX):
            out.append(p)
    return out


def chunk_text(text: str, size: int) -> list:
    """Split text into pieces of at most `size` characters, preferring paragraph/line breaks."""
    chunks, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        while len(para) > size:
            cut = max(para.rfind(" ", 0, size), para.rfind("\n", 0, size))
            if cut < size // 2:
                cut = size
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(para[:cut].strip())
            para = para[cut:].lstrip()
        if not para:
            continue
        if cur and len(cur) + len(para) + 2 <= size:
            cur = f"{cur}\n\n{para}"
        elif not cur:
            cur = para
        else:
            chunks.append(cur)
            cur = para
    if cur:
        chunks.append(cur)
    return chunks


# --------------------------------------------------------------------------- running scripts
def _clip(text: str) -> str:
    limit = config.MAX_OUTPUT_CHARS
    if len(text) <= limit:
        return text
    head, tail = int(limit * 0.4), int(limit * 0.6)
    return text[:head] + "\n...[output truncated]...\n" + text[-tail:]


def execute_python_snippet(code: str, timeout: int = None):
    """Run code in the workspace. Returns (ok: bool, output: str)."""
    timeout = timeout or config.EXEC_TIMEOUT
    fd, tmp_path = tempfile.mkstemp(suffix=".py", prefix="agent_")
    with os.fdopen(fd, "w", encoding="utf-8") as tmp:
        tmp.write(code)

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"      # avoid cp1252 print crashes on Windows
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = config.APP_DIR + os.pathsep + env.get("PYTHONPATH", "")   # lets scripts `import helpers`

    try:
        proc = subprocess.run(
            [sys.executable, tmp_path],
            cwd=get_workspace(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
            stdin=subprocess.DEVNULL,      # input() fails fast instead of hanging
        )
        out, err = proc.stdout.strip(), proc.stderr.strip()
        if proc.returncode != 0:
            msg = f"[ERROR] Script failed (exit code {proc.returncode})"
            if out:
                msg += f"\n{out}"
            if err:
                msg += f"\n[STDERR]\n{err}"
            return False, _clip(msg)
        text = out or "(Script ran with no output)"
        if err:
            text += f"\n[WARNINGS]\n{err}"
        return True, _clip(text)
    except subprocess.TimeoutExpired:
        return False, f"[ERROR] Script timed out after {timeout} seconds."
    except Exception as exc:
        return False, f"[ERROR] {exc}"
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


# --------------------------------------------------------------------------- web
def local_web_search(query: str) -> str:
    """Very small DuckDuckGo HTML lookup (best effort)."""
    try:
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            html_data = resp.read().decode("utf-8", errors="ignore")
        from html.parser import HTMLParser

        class _Text(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text = []

            def handle_data(self, data):
                self.text.append(data)

        parser = _Text()
        parser.feed(html_data)
        results = [t.strip() for t in parser.text if len(t.strip()) > 30]
        return "\n".join(results[:5]) if results else "No detailed snippets found."
    except Exception as e:
        return f"[Search Error: {e}]"
