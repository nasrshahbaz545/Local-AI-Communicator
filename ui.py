# ui.py
import tkinter as tk
from tkinter import scrolledtext

import config
from config import THEMES


class LlamaInterface:
    def __init__(self, root, callbacks):
        self.root = root
        self.cb = callbacks
        self.theme_name = "light"
        self.sidebar_expanded = True
        self.confirm_all = tk.BooleanVar(master=root, value=False)

        self._build_layout()
        self.apply_theme()

    def _build_layout(self):
        self.root_frame = tk.Frame(self.root, padx=8, pady=8)
        self.root_frame.pack(fill=tk.BOTH, expand=True)

        # --- SIDEBAR ---
        self.sidebar = tk.Frame(self.root_frame, width=260, bd=1, relief=tk.FLAT)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))
        self.sidebar.pack_propagate(False)

        sb_top = tk.Frame(self.sidebar, pady=8, padx=10)
        sb_top.pack(fill=tk.X)

        self.logo_btn = tk.Label(sb_top, text="▲ llama-ui", font=("Segoe UI", 11, "bold"), cursor="hand2", padx=8, pady=4)
        self.logo_btn.pack(side=tk.LEFT)
        self.logo_btn.bind("<Button-1>", lambda e: self.cb["new_chat"]())

        self.collapse_btn = tk.Button(sb_top, text="◫", font=("Segoe UI", 10), bd=0, cursor="hand2", command=self.toggle_sidebar, padx=6, pady=2)
        self.collapse_btn.pack(side=tk.RIGHT)

        self.nav_frame = tk.Frame(self.sidebar, padx=8, pady=4)
        self.nav_frame.pack(fill=tk.X)

        self.nav_buttons = []
        nav_items = [
            ("✎  New chat", "Ctrl+N", self.cb["new_chat"]),
            ("📁 Set Workspace", "", self.cb["select_directory"]),
            ("📎 Attach File", "", self.cb["attach_file"]),
            ("📝 Summarize Workspace", "", self.cb["summarize_workspace"]),
            (f"⬡  Server: {config.SERVER_HOST}:{config.SERVER_PORT}", "", self.cb["check_server"]),
            ("◐  Toggle Light / Dark", "", self.toggle_theme),
        ]
        for label_text, shortcut, cmd in nav_items:
            btn = tk.Button(self.nav_frame, text=f"{label_text}   {shortcut}".strip(), anchor="w", font=("Segoe UI", 10), bd=0, padx=10, pady=6, cursor="hand2", command=cmd)
            btn.pack(fill=tk.X, pady=1)
            self.nav_buttons.append(btn)

        self.workspace_lbl = tk.Label(self.sidebar, text="Dir: Default Workspace", anchor="w", font=("Segoe UI", 8), padx=14, pady=2)
        self.workspace_lbl.pack(fill=tk.X)

        self.recent_lbl = tk.Label(self.sidebar, text="Recent conversations", anchor="w", font=("Segoe UI", 9, "bold"), padx=14, pady=4)
        self.recent_lbl.pack(fill=tk.X)

        self.history_list_frame = tk.Frame(self.sidebar, padx=8)
        self.history_list_frame.pack(fill=tk.BOTH, expand=True)

        # --- MAIN WORKSPACE ---
        self.main_area = tk.Frame(self.root_frame)
        self.main_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.top_bar = tk.Frame(self.main_area, height=40, padx=14)
        self.top_bar.pack(fill=tk.X)

        self.expand_btn = tk.Button(self.top_bar, text="☰", font=("Segoe UI", 10), bd=0, cursor="hand2", command=self.toggle_sidebar)

        self.model_badge = tk.Label(self.top_bar, text=f"local-model · {config.SERVER_HOST}:{config.SERVER_PORT}", font=("Segoe UI", 10, "bold"))
        self.model_badge.pack(side=tk.LEFT, pady=8)

        self.status_lbl = tk.Label(self.top_bar, text="● Checking server...", font=("Segoe UI", 9))
        self.status_lbl.pack(side=tk.RIGHT, pady=8)

        # Chat display with full text selection support
        self.chat_display = scrolledtext.ScrolledText(self.main_area, wrap=tk.WORD, font=("Segoe UI", 11), bd=0, padx=20, pady=14, state=tk.DISABLED, cursor="xterm")
        self.chat_display.pack(fill=tk.BOTH, expand=True)

        # --- Right-Click Context Menu & Copy Bindings ---
        self.context_menu = tk.Menu(self.chat_display, tearoff=0)
        self.context_menu.add_command(label="Copy Selected Text", command=self.copy_selection)
        self.context_menu.add_command(label="Copy All Conversation", command=self.copy_all_text)

        self.chat_display.bind("<Button-3>", self.show_context_menu)
        self.chat_display.bind("<Control-c>", lambda e: self.copy_selection())

        self.attachment_bar = tk.Frame(self.main_area, padx=24, pady=2)
        self.attachment_lbl = tk.Label(self.attachment_bar, text="", font=("Segoe UI", 9), fg="#16a34a")
        self.attachment_lbl.pack(side=tk.LEFT)

        self.form_container = tk.Frame(self.main_area, padx=24, pady=10)
        self.form_container.pack(fill=tk.X)

        self.input_card = tk.Frame(self.form_container, bd=1, relief=tk.SOLID, padx=12, pady=8)
        self.input_card.pack(fill=tk.X)

        self.prompt_box = tk.Text(self.input_card, height=3, wrap=tk.WORD, font=("Segoe UI", 11), bd=0)
        self.prompt_box.pack(fill=tk.X)
        self.prompt_box.bind("<Return>", self.cb["on_enter"])

        self.form_actions = tk.Frame(self.input_card, pady=4)
        self.form_actions.pack(fill=tk.X)

        self.hint_lbl = tk.Label(self.form_actions, text="Enter to send · Shift+Enter new line · /summarize [file]", font=("Segoe UI", 9))
        self.hint_lbl.pack(side=tk.LEFT)

        self.send_btn = tk.Button(self.form_actions, text="Send ↑", font=("Segoe UI", 10, "bold"), bd=0, padx=14, pady=4, cursor="hand2", command=self.cb["send_message"])
        self.send_btn.pack(side=tk.RIGHT)

        self.confirm_chk = tk.Checkbutton(self.form_actions, text="Confirm each script", variable=self.confirm_all, font=("Segoe UI", 9), bd=0, cursor="hand2")
        self.confirm_chk.pack(side=tk.RIGHT, padx=10)

    def show_context_menu(self, event):
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    def copy_selection(self):
        try:
            selected_text = self.chat_display.get(tk.SEL_FIRST, tk.SEL_LAST)
            if selected_text:
                self.root.clipboard_clear()
                self.root.clipboard_append(selected_text)
                self.root.update()
        except tk.TclError:
            pass

    def copy_all_text(self):
        all_text = self.chat_display.get("1.0", tk.END)
        if all_text:
            self.root.clipboard_clear()
            self.root.clipboard_append(all_text)
            self.root.update()

    def apply_theme(self):
        t = THEMES[self.theme_name]
        self.root.configure(bg=t["bg_main"])
        self.root_frame.configure(bg=t["bg_main"])
        self.sidebar.configure(bg=t["bg_sidebar"], highlightbackground=t["border"], highlightthickness=1)

        for w in self.sidebar.winfo_children():
            if isinstance(w, tk.Frame):
                w.configure(bg=t["bg_sidebar"])

        self.logo_btn.configure(bg=t["bg_sidebar"], fg=t["text_primary"])
        self.collapse_btn.configure(bg=t["bg_sidebar"], fg=t["text_muted"], activebackground=t["bg_hover"])
        self.recent_lbl.configure(bg=t["bg_sidebar"], fg=t["text_muted"])
        self.workspace_lbl.configure(bg=t["bg_sidebar"], fg=t["text_muted"])

        for btn in self.nav_buttons:
            btn.configure(bg=t["bg_sidebar"], fg=t["text_primary"], activebackground=t["bg_hover"], activeforeground=t["text_primary"])

        self.main_area.configure(bg=t["bg_main"])
        self.top_bar.configure(bg=t["bg_main"])
        self.expand_btn.configure(bg=t["bg_main"], fg=t["text_primary"], activebackground=t["bg_hover"])
        self.model_badge.configure(bg=t["bg_main"], fg=t["text_primary"])
        self.status_lbl.configure(bg=t["bg_main"])

        cd = self.chat_display
        cd.configure(bg=t["bg_main"], fg=t["text_primary"], insertbackground=t["text_primary"])
        
        # Clean chat formatting tags (Right-aligned user prompts, Left-aligned AI answers)
        cd.tag_configure("role_user", font=("Segoe UI", 9, "bold"), foreground=t["text_muted"], spacing1=14, justify="right")
        cd.tag_configure("msg_user", font=("Segoe UI", 11), lmargin1=120, lmargin2=120, rmargin=20, spacing3=8, justify="right")
        cd.tag_configure("role_ai", font=("tahoma", 9, "bold"), foreground=t["text_primary"], spacing1=18, lmargin1=20)
        cd.tag_configure("msg_ai", font=("Sans Sarif", 11), lmargin1=20, lmargin2=20, rmargin=40, spacing3=10)
        
        # Inline markdown weights for AI formatting
        cd.tag_configure("bold", font=("Segoe UI", 11, "bold"))
        cd.tag_configure("italic", font=("Segoe UI", 11, "italic"))
        
        cd.tag_configure("code_block", font=("Consolas", 10), background=t["code_bg"], foreground=t["code_fg"], lmargin1=20, lmargin2=20, rmargin=20, spacing1=8, spacing3=8)
        cd.tag_configure("exec_output", font=("Consolas", 11), background=t["bg_sidebar"], foreground=t["status_ok"], lmargin1=20, lmargin2=20, rmargin=20, spacing1=6, spacing3=10)
        cd.tag_configure("meta_stats", font=("Segoe UI", 8), foreground=t["text_muted"], lmargin1=20, spacing3=12)
        cd.tag_configure("status_line", font=("Segoe UI", 9, "italic"), foreground=t["text_muted"], lmargin1=20, lmargin2=20, spacing1=2, spacing3=2)

        self.form_container.configure(bg=t["bg_main"])
        self.input_card.configure(bg=t["bg_input"], highlightbackground=t["border"], highlightthickness=0.25)
        self.prompt_box.configure(bg=t["bg_input"], fg=t["text_primary"], insertbackground=t["text_primary"])
        self.form_actions.configure(bg=t["bg_input"])
        self.hint_lbl.configure(bg=t["bg_input"], fg=t["text_muted"])
        self.confirm_chk.configure(bg=t["bg_input"], fg=t["text_primary"], activebackground=t["bg_input"],
                                   activeforeground=t["text_primary"], selectcolor=t["bg_main"])
        self.send_btn.configure(bg=t["accent"], fg=t["accent_fg"], activebackground=t["text_muted"])
        self.attachment_bar.configure(bg=t["bg_main"])
        self.attachment_lbl.configure(bg=t["bg_main"])

    def toggle_theme(self):
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self.apply_theme()
        on_theme = self.cb.get("on_theme")
        if on_theme:
            on_theme()

    def toggle_sidebar(self):
        if self.sidebar_expanded:
            self.sidebar.pack_forget()
            self.expand_btn.pack(side=tk.LEFT, padx=(0, 8))
            self.sidebar_expanded = False
        else:
            self.expand_btn.pack_forget()
            self.main_area.pack_forget()
            self.sidebar.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))
            self.main_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            self.sidebar_expanded = True