# app.py
import json
import os
import re
import threading
import time
import tkinter as tk
import traceback
import urllib.request
from tkinter import filedialog, messagebox

import agent
import config
import tools
from config import THEMES
from ui import LlamaInterface

SYSTEM_PROMPT = config.BASE_PROMPT + tools.init_skills()
SUMMARIZE_CMD = re.compile(r"^\s*/summari[sz]e\b(.*)$", re.I | re.S)


class UIHooks(agent.Hooks):
    """Bridges the worker thread to the Tk main thread (every widget call goes through .after)."""

    def __init__(self, app):
        self.app = app
        self.buf, self.partial, self.last_flush = [], [], 0.0

    def assistant_start(self):
        self.buf, self.partial = [], []
        self.app.after(0, self.app.begin_stream)

    def stream(self, text):
        self.partial.append(text)
        self.buf.append(text)
        if time.time() - self.last_flush > 0.1:
            self._flush()

    def _flush(self):
        if self.buf:
            chunk, self.buf = "".join(self.buf), []
            self.last_flush = time.time()
            self.app.after(0, lambda c=chunk: self.app.stream_text(c))

    def assistant_end(self, content, meta):
        self.buf = []
        self.app.after(0, lambda: self.app.end_stream(content, meta))

    def tool(self, output):
        self.app.after(0, lambda o=output: self.app.append_formatted_message("tool", o))

    def status(self, text):
        self.app.after(0, lambda s=text: self.app.append_formatted_message("status", s))

    def abort(self):
        partial = agent.clean_reply("".join(self.partial))
        self.buf = []
        self.app.after(0, lambda p=partial: self.app.abort_stream(p))

    def confirm(self, code, reasons):
        box, done = {}, threading.Event()

        def ask():
            preview = code if len(code) <= 1200 else code[:1200] + "\n..."
            why = "\n".join(f"- {r}" for r in reasons) or "- 'Confirm each script' is switched on."
            box["ok"] = messagebox.askyesno(
                "Run this script?",
                f"The agent wants to run a script.\n\nWhy you are being asked:\n{why}\n\n{preview}\n\nRun it?",
                icon="warning", parent=self.app)
            done.set()

        self.app.after(0, ask)
        while not done.wait(0.2):
            if self.app.cancel.is_set():
                return False
        return box.get("ok", False)


class AutonomousAgentApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("llama-ui · Autonomous Local Agent")
        self.geometry("1140x740")
        self.minsize(850, 560)

        self.busy = False
        self.streaming = False
        self.cancel = threading.Event()
        self.confirm_all = False
        self.attached_file_path = None
        self.conversations = []
        self.active_conv_idx = 0
        self.load_state()
        if not self.conversations:
            self.conversations = [self._blank_conv("New Agent Session")]

        callbacks = {
            "new_chat": self.new_chat,
            "select_directory": self.select_directory,
            "attach_file": self.attach_file,
            "summarize_workspace": self.summarize_workspace,
            "check_server": self.check_server_health,
            "on_enter": self.on_enter_pressed,
            "send_message": self.send_message,
            "on_theme": self.on_theme_changed,
        }
        self.ui = LlamaInterface(self, callbacks)
        ws = tools.get_workspace()
        self.ui.workspace_lbl.configure(text=f"Dir: {os.path.basename(ws) or ws}")
        self.render_sidebar_history()
        self.reload_chat_display()
        self.check_server_health()

        self.bind("<Control-n>", lambda e: self.new_chat())
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------ persistence
    def _blank_conv(self, title):
        return {"title": title, "messages": [{"role": "system", "content": SYSTEM_PROMPT}]}

    def load_state(self):
        try:
            with open(config.SESSIONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            tools.set_workspace(data.get("workspace", ""))
            for conv in data.get("conversations", []):
                msgs = conv.get("messages", [])
                if not msgs or msgs[0].get("role") != "system":
                    msgs.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
                msgs[0]["content"] = SYSTEM_PROMPT      # always use the current prompt + skills index
                self.conversations.append({"title": conv.get("title", "Session"), "messages": msgs})
        except (OSError, ValueError, AttributeError):
            self.conversations = []

    def save_state(self):
        try:
            data = {"workspace": tools.get_workspace(),
                    "conversations": self.conversations[: config.MAX_SESSIONS]}
            tmp = config.SESSIONS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, config.SESSIONS_FILE)
        except OSError:
            pass

    def on_close(self):
        self.cancel.set()
        self.save_state()
        self.destroy()

    # ------------------------------------------------------------------ sidebar / sessions
    def select_directory(self):
        dir_path = filedialog.askdirectory(title="Select Working Directory", initialdir=tools.get_workspace())
        if dir_path and tools.set_workspace(dir_path):
            ws = tools.get_workspace()
            self.ui.workspace_lbl.configure(text=f"Dir: {os.path.basename(ws) or ws}")
            self.save_state()
            messagebox.showinfo("Workspace Updated", f"Active working directory set to:\n{ws}")

    def attach_file(self):
        file_path = filedialog.askopenfilename(
            title="Select Attachment", initialdir=tools.get_workspace(),
            filetypes=[("Documents & Text", "*.pdf *.docx *.txt *.md *.py *.csv *.json *.xlsx"), ("All Files", "*.*")])
        if file_path:
            self.attached_file_path = file_path
            self.ui.attachment_lbl.configure(text=f"📎 Attached: {os.path.basename(file_path)}")
            self.ui.attachment_bar.pack(fill=tk.X, before=self.ui.form_container)

    def _block_if_busy(self):
        if self.busy:
            messagebox.showinfo("Busy", "A task is running. Wait for it to finish or press Stop.")
        return self.busy

    def new_chat(self):
        if self._block_if_busy():
            return
        self.conversations.insert(0, self._blank_conv(f"Session #{len(self.conversations) + 1}"))
        self.active_conv_idx = 0
        self.attached_file_path = None
        self.ui.attachment_bar.pack_forget()
        self.render_sidebar_history()
        self.reload_chat_display()
        self.save_state()

    def switch_chat(self, idx):
        if self._block_if_busy():
            return
        self.active_conv_idx = idx
        self.render_sidebar_history()
        self.reload_chat_display()

    def on_theme_changed(self):
        self.render_sidebar_history()

    def render_sidebar_history(self):
        t = THEMES[self.ui.theme_name]
        for child in self.ui.history_list_frame.winfo_children():
            child.destroy()
        for idx, conv in enumerate(self.conversations):
            is_active = idx == self.active_conv_idx
            tk.Button(
                self.ui.history_list_frame, text=conv["title"], anchor="w",
                font=("Segoe UI", 9, "bold" if is_active else "normal"),
                bg=t["bg_hover"] if is_active else t["bg_sidebar"], fg=t["text_primary"],
                activebackground=t["bg_hover"], bd=0, padx=10, pady=6, cursor="hand2",
                command=lambda i=idx: self.switch_chat(i),
            ).pack(fill=tk.X, pady=1)

    # ------------------------------------------------------------------ chat rendering (main thread only)
    def reload_chat_display(self):
        self.ui.chat_display.configure(state=tk.NORMAL)
        self.ui.chat_display.delete("1.0", tk.END)
        self.ui.chat_display.configure(state=tk.DISABLED)
        for msg in self.conversations[self.active_conv_idx]["messages"]:
            if msg["role"] == "system":
                continue
            self.append_formatted_message(msg["role"], msg.get("display", msg["content"]), msg.get("meta"))

    def _insert_formatted_markdown(self, cd, text, tag_name):
        """Simple parser to insert text with **bold** and *italic* support into Tkinter Text widget."""
        parts = re.split(r"(\*\*.*?\*\*|\*.*?\*)", text)
        for part in parts:
            if part.startswith("**") and part.endswith("**"):
                cd.insert(tk.END, part[2:-2], ("bold", tag_name))
            elif part.startswith("*") and part.endswith("*"):
                cd.insert(tk.END, part[1:-1], ("italic", tag_name))
            else:
                cd.insert(tk.END, part, tag_name)

    def _render_assistant_body(self, content, meta):
        cd = self.ui.chat_display
        for part in re.split(r"(```(?:python|py|cmd|powershell|ps1)[ \t]*\r?\n[\s\S]*?(?:```|\Z))", content, flags=re.I):
            if re.match(r"```(?:python|py|cmd|powershell|ps1)", part, re.I):
                code = re.sub(r"^```(?:python|py|cmd|powershell|ps1)[ \t]*\r?\n", "", part, flags=re.I)
                code = re.sub(r"```\s*$", "", code).strip()
                cd.insert(tk.END, f"\n{code}\n\n", "code_block")
            elif part.strip():
                self._insert_formatted_markdown(cd, part.strip() + "\n", "msg_ai")
        cd.insert(tk.END, f"⚡ {meta}\n\n" if meta else "\n", "meta_stats")

    def append_formatted_message(self, role, content, meta=None):
        cd = self.ui.chat_display
        cd.configure(state=tk.NORMAL)
        if role == "user":
            cd.insert(tk.END, "You\n", "role_user")
            cd.insert(tk.END, f"{content}\n\n", "msg_user")
        elif role == "assistant":
            cd.insert(tk.END, "llama-ui Agent\n", "role_ai")
            self._render_assistant_body(content, meta)
        elif role == "tool":
            cd.insert(tk.END, f"▶ Script output:\n{content}\n\n", "exec_output")
        elif role == "status":
            cd.insert(tk.END, f"{content}\n", "status_line")
        cd.see(tk.END)
        cd.configure(state=tk.DISABLED)

    def begin_stream(self):
        cd = self.ui.chat_display
        cd.configure(state=tk.NORMAL)
        cd.insert(tk.END, "llama-ui Agent\n", "role_ai")
        cd.mark_set("stream_start", "end-1c")
        cd.mark_gravity("stream_start", tk.LEFT)
        self.streaming = True
        cd.see(tk.END)
        cd.configure(state=tk.DISABLED)

    def stream_text(self, text):
        if not self.streaming:
            return
        cd = self.ui.chat_display
        cd.configure(state=tk.NORMAL)
        cd.insert(tk.END, text, "msg_ai")
        cd.see(tk.END)
        cd.configure(state=tk.DISABLED)

    def end_stream(self, content, meta):
        cd = self.ui.chat_display
        cd.configure(state=tk.NORMAL)
        if self.streaming:
            cd.delete("stream_start", "end-1c")
            self.streaming = False
        self._render_assistant_body(content, meta)
        cd.see(tk.END)
        cd.configure(state=tk.DISABLED)

    def abort_stream(self, partial):
        if self.streaming:
            self.end_stream((partial + "\n" if partial else "") + "[stopped]", None)

    def check_server_health(self):
        def worker():
            t = THEMES[self.ui.theme_name]
            try:
                with urllib.request.urlopen(urllib.request.Request(config.MODELS_URL), timeout=2) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        model_id = (data.get("data") or [{}])[0].get("id", "local-model")
                        badge = f"{model_id} · {config.SERVER_HOST}:{config.SERVER_PORT}"
                        self.after(0, lambda: self.ui.model_badge.configure(text=badge))
                        self.after(0, lambda: self.ui.status_lbl.configure(text="● Connected", fg=t["status_ok"]))
                        return
            except Exception:
                pass
            self.after(0, lambda: self.ui.status_lbl.configure(
                text=f"● Offline (Port {config.SERVER_PORT})", fg=t["status_err"]))

        threading.Thread(target=worker, daemon=True).start()

    def on_enter_pressed(self, event):
        if event.state & 0x0001:
            return None
        if not self.busy:
            self.send_message()
        return "break"

    def summarize_workspace(self):
        if not self._block_if_busy():
            self.submit("/summarize")

    def send_message(self):
        if self.busy:
            self.cancel.set()
            self.ui.send_btn.configure(text="Stopping...", state=tk.DISABLED)
            return
        prompt = self.ui.prompt_box.get("1.0", tk.END).strip()
        attach = self.attached_file_path
        if not prompt and not attach:
            return
        self.ui.prompt_box.delete("1.0", tk.END)
        self.attached_file_path = None
        self.ui.attachment_bar.pack_forget()
        self.submit(prompt, attach)

    def submit(self, prompt, attach=None):
        conv = self.conversations[self.active_conv_idx]
        shown = prompt or f"(attached {os.path.basename(attach)})"
        if attach and prompt:
            shown += f"\n[attached: {os.path.basename(attach)}]"
        if len(conv["messages"]) == 1:
            conv["title"] = (prompt[:32] + "...") if len(prompt) > 32 else (prompt or "File task")
            self.render_sidebar_history()
        self.append_formatted_message("user", shown)

        self.busy = True
        self.cancel = threading.Event()
        self.confirm_all = self.ui.confirm_all.get()
        self.ui.send_btn.configure(text="Stop ■", state=tk.NORMAL)
        threading.Thread(target=self._worker, args=(conv, prompt, attach, shown), daemon=True).start()

    # ------------------------------------------------------------------ worker thread
    def _worker(self, conv, prompt, attach, shown):
        hooks = UIHooks(self)
        try:
            cmd = SUMMARIZE_CMD.match(prompt)
            if cmd:
                arg = cmd.group(1)
            elif attach and re.search(r"summari[sz]", prompt, re.I):
                arg = ""
            else:
                arg = agent.infer_summarize_args(prompt, tools.get_workspace())

            if arg is not None:
                conv["messages"].append({"role": "user", "content": prompt, "display": shown})
                agent.run_summarize(conv, arg, attach, hooks, self.cancel)
            elif self._is_prose_request(prompt, attach):
                self._run_prose_writer(conv, prompt, shown, hooks)
            else:
                self._run_agent(conv, prompt, attach, shown, hooks)
        except agent.Cancelled:
            hooks.abort()
            hooks.status("Stopped.")
        except agent.LLMError as e:
            hooks.status(f"[LLM error] {e}")
        except Exception:
            hooks.status("[Internal error]\n" + traceback.format_exc(limit=4))
        finally:
            self.after(0, self._finish)

    def _is_prose_request(self, prompt, attach):
        if attach:
            return False
        if re.search(r"\b(csv|xlsx|excel|pdf|docx|rename|folder|directory|script|python|code|cmd|powershell|ps1|scrape|calculate)\b", prompt, re.I):
            return False
        return bool(re.search(r"\b(essay|article|blog|story|explain|define|what is|how does|discuss|elaborate|write about|write a)\b", prompt, re.I))

    def _run_prose_writer(self, conv, prompt, shown, hooks):
        conv["messages"].append({"role": "user", "content": prompt, "display": shown})

        word_match = re.search(r"\b(\d{3,5})\s*words?\b", prompt, re.I)
        target_words = int(word_match.group(1)) if word_match else 0
        save_to_file = bool(re.search(r"\b(save|text file|\.txt|file)\b", prompt, re.I))

        writer_system = (
            "You are a clear, thorough writer. Answer directly in well-structured plain prose. "
            "Do NOT write Python code, scripts, or markdown code blocks. Do NOT repeat earlier topics."
        )

        if target_words > 600:
            sections = max(3, min(10, target_words // 350))
            hooks.status(f"Generating long-form piece in {sections} detailed sections (~{target_words} words)...")
            full_sections = []
            start_time = time.time()

            for i in range(1, sections + 1):
                if self.cancel.is_set():
                    raise agent.Cancelled()
                hooks.status(f"Writing section {i}/{sections}...")
                prev_tail = full_sections[-1][-400:] if full_sections else "None (this is the introduction)."
                sec_prompt = (
                    f"Task: {prompt}\n\n"
                    f"Write Section {i} of {sections}. Write at least 350-450 detailed words for this section alone. "
                    f"Do not summarize or conclude early unless this is Section {sections}. "
                    f"No code blocks.\n\n"
                    f"Ending of previous section for continuity:\n{prev_tail}"
                )
                msgs = [
                    {"role": "system", "content": writer_system},
                    {"role": "user", "content": sec_prompt},
                ]
                part, _ = agent.call_llm(msgs, max_tokens=1500, temperature=0.4, cancel=self.cancel)
                part = agent.clean_reply(part)
                if part:
                    full_sections.append(part)

            combined = "\n\n".join(full_sections)
            word_count = len(combined.split())
            elapsed = max(time.time() - start_time, 0.1)
            meta = f"{word_count} words · {sections} sections · {elapsed:.1f}s"

            if save_to_file:
                out_path = os.path.join(tools.get_workspace(), f"essay_{int(time.time())}.txt")
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(combined + "\n")
                hooks.status(f"Saved {word_count} words to: {out_path}")

            conv["messages"].append({"role": "assistant", "content": combined, "meta": meta})
            hooks.assistant_start()
            hooks.assistant_end(combined, meta)
        else:
            msgs = [
                {"role": "system", "content": writer_system},
                {"role": "user", "content": prompt},
            ]
            hooks.assistant_start()
            reply, meta = agent.call_llm(msgs, max_tokens=8192, temperature=0.3, on_text=hooks.stream, cancel=self.cancel)
            reply = agent.clean_reply(reply)
            if save_to_file and reply:
                out_path = os.path.join(tools.get_workspace(), f"output_{int(time.time())}.txt")
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(reply + "\n")
                hooks.status(f"Saved to: {out_path}")
            conv["messages"].append({"role": "assistant", "content": reply or "(empty reply)", "meta": meta})
            hooks.assistant_end(reply or "(empty reply)", meta)

    def _run_agent(self, conv, prompt, attach, shown, hooks):
        parts = [f"[Workspace: {tools.get_workspace()}]"]
        skills = tools.match_relevant_skills(prompt)
        if skills:
            parts.append(skills)
        if attach:
            text = tools.extract_attachment_text(attach)
            if text.startswith("[Error"):
                hooks.status(text)
                return
            if len(text) > config.MAX_ATTACH_CHARS:
                text = text[: config.MAX_ATTACH_CHARS] + "\n[...file truncated - use /summarize for the whole file...]"
            parts.append(f"[Attached file: {os.path.basename(attach)}]\n{text}")
        parts.append(f"User request: {prompt or '(see the attached file)'}")
        conv["messages"].append({"role": "user", "content": "\n\n".join(parts), "display": shown})
        agent.run_agent(conv, hooks, self.cancel, self.confirm_all)

    def _finish(self):
        self.busy = False
        self.streaming = False
        self.ui.send_btn.configure(state=tk.NORMAL, text="Send ↑")
        self.render_sidebar_history()
        self.save_state()


if __name__ == "__main__":
    app = AutonomousAgentApp()
    app.mainloop()