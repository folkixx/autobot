"""AutoBot RPA — AI Mode UI (Venice brain + Linken Sphere + StarZone)."""
import json
import queue
import random
import threading
import tkinter as tk
import urllib.request
from tkinter import messagebox, scrolledtext, ttk
from typing import Optional

import os

import core.starzone as starzone
import core.linken_sphere as ls
from core.cdp_browser import CDPBrowser
from core.venice_agent import run_agent
from core import telegram_notify
from config import VENICE_API_KEY, VENICE_BASE_URL, VENICE_MODEL

_PROJECT_DIR = os.path.dirname(os.path.dirname(__file__))
# Learned instructions the AI accumulates from operator replies (manual control)
LEARNED_PATH = os.path.join(_PROJECT_DIR, "learned_instructions.txt")
# Operator-curated knowledge that is ALWAYS in the bot's memory
KNOWLEDGE_PATH = os.path.join(_PROJECT_DIR, "knowledge.txt")
# Per-run recordings (screenshots, log, collected data)
RUNS_DIR = os.path.join(_PROJECT_DIR, "runs")

# ── Palette (deep "glass" dark) ───────────────────────────────
BG      = '#0d0d16'   # near-black base (window shows desktop faintly via alpha)
BG2     = '#16162a'   # frosted panel
BG3     = '#23233f'   # raised element / input
FG      = '#e6e6f5'
ACCENT  = '#8aa0ff'   # indigo glass accent
GREEN   = '#a6e3a1'
RED     = '#f38ba8'
YELLOW  = '#f9e2af'
GRAY    = '#7a7a96'
MAUVE   = '#cba6f7'
TEAL    = '#94e2d5'

FONT_UI   = ('Segoe UI', 10)
FONT_BOLD = ('Segoe UI', 10, 'bold')
FONT_MONO = ('Consolas', 10)
FONT_H1   = ('Segoe UI', 14, 'bold')
FONT_SM   = ('Segoe UI', 9)
FONT_CHAT = ('Segoe UI', 10)

# ── Chat system prompt ────────────────────────────────────────
CHAT_SYSTEM = """You are an expert AI assistant helping configure and improve AutoBot — an RPA automation system.

## What AutoBot is
AutoBot is a Python-based RPA bot that controls a real browser autonomously using AI decision-making. The user describes a task in natural language, and the AI executes it step by step.

## Technical Architecture

### Browser & Fingerprint
- Anti-detect browser: **Linken Sphere** with **Hybrid 2.0 (mimic)** fingerprint
- Each session has a unique browser fingerprint (canvas, WebGL, fonts, timezone, etc.)
- Sessions are created/started/stopped via Linken Sphere local REST API on port 35000
- No Playwright, no Selenium — pure **CDP (Chrome DevTools Protocol)** via WebSocket

### Proxy
- **StarZone** residential SOCKS5 proxy: `proxy.starzone.io:51313`
- API: `https://api.starhome.io/v1/` with email + auth_token auth
- Country: USA only. Before each task, Venice AI picks the optimal US state
- Commands: update_ip_configuration (change state), ip_update_now (rotate IP)

### AI Brain — Venice AI
- Model: `qwen-3-6-plus` — uncensored, vision, reasoning, 1M context
- API: OpenAI-compatible Venice API
- The AI sees a **screenshot** of the current browser page on every step
- Returns a single JSON action to execute
- Full conversation history is kept (up to 1M tokens)

### Human Emulation
- Mouse moves along **Bezier curves** (randomized control points)
- Delays between actions: **200–1200ms** random
- Typing speed: **40–160ms** per keystroke with occasional pauses
- The bot handles this automatically — the AI just decides WHAT to do

### Available Actions (what the AI can command)
```
navigate      → go to URL, wait for page load
click         → click element by CSS selector (preferred)
click_coords  → click by x,y coordinates (fallback)
fill          → clear field + type text with human keystrokes
press         → keyboard key: Enter, Tab, Escape, ArrowDown, etc.
scroll        → scroll page to Y coordinate
wait          → pause N seconds
screenshot    → capture for analysis (already happens each step)
notify_admin  → send Telegram alert + screenshot to human admin
done          → task complete, return result summary
error         → task failed, explain why
```

### Telegram Notifications
- Bot: @AutoRegerBot_bot → sends to admin chat
- When to notify: defined by the USER in their task instruction
- Each notification includes a screenshot of the current page
- Typical cases: CAPTCHA, SMS verification, unexpected blocks, decisions needing human input

### Session Lifecycle (each task)
1. Venice AI picks best US state for the task
2. StarZone sets proxy to that state + rotates IP
3. Linken Sphere creates session (Hybrid 2.0) with SOCKS5 proxy
4. Session starts → bot gets CDP debug port
5. CDPBrowser connects via WebSocket
6. Agentic loop: screenshot → Venice decides → bot executes → repeat
7. Task ends with `done` or `error`
8. Session is stopped and deleted

### File Structure
```
autobot/
├── config.py              ← all API keys and settings
├── main.py                ← entry point
├── core/
│   ├── venice_agent.py    ← agentic loop, Venice API calls
│   ├── starzone.py        ← StarZone proxy management
│   ├── linken_sphere.py   ← Linken Sphere session management
│   ├── cdp_browser.py     ← CDP browser control (click, type, scroll, screenshot)
│   ├── telegram_notify.py ← Telegram alerts
│   └── human_emulator.py  ← Bezier mouse, typing delays
└── ui/
    └── ai_app.py          ← this file (tkinter UI)
```

## Your Role
Help the user:
1. **Write system prompts** for the Venice AI agent (the SYSTEM_PROMPT in venice_agent.py)
2. **Write task instructions** for specific automations (what user types in the Task tab)
3. **Debug issues** and suggest fixes
4. **Plan features** or improvements
5. Answer any questions about how the bot works

When asked to write a system prompt or instruction, make it practical, specific, and ready to use.
Always respond in the same language the user writes in (Russian or English).
Be direct and concise."""


class AIBotApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AutoBot RPA — AI Mode")
        self.geometry("1100x720")
        self.minsize(800, 540)
        self.configure(bg=BG)
        # Subtle window translucency for a frosted-glass feel
        try:
            self.attributes('-alpha', 0.965)
        except Exception:
            pass

        self._log_q: queue.Queue = queue.Queue()
        self._chat_q: queue.Queue = queue.Queue()
        self._stop_flag = False
        self._running = False
        self._browser: Optional[CDPBrowser] = None
        self._session_uuid: Optional[str] = None
        self._notif_count = 0
        self._waiting_mark = '1.0'

        # Chat history for Venice (keeps context between messages)
        self._chat_history: list[dict] = [
            {"role": "system", "content": CHAT_SYSTEM}
        ]

        self._setup_styles()
        self._build_ui()
        self._poll()

    # ── Styles ────────────────────────────────────────────────

    def _setup_styles(self):
        s = ttk.Style(self)
        s.theme_use('clam')
        s.configure('TFrame',       background=BG)
        s.configure('Panel.TFrame', background=BG2)
        s.configure('TLabel',       background=BG,  foreground=FG, font=FONT_UI)
        s.configure('Panel.TLabel', background=BG2, foreground=FG, font=FONT_UI)

        for name, bg_c, fg_c, bold in [
            ('Run',  '#45475a', GREEN, True),
            ('Stop', RED,       BG,    True),
            ('Sm',   BG3,       FG,    False),
            ('Del',  BG3,       RED,   False),
            ('Send', ACCENT,    BG,    True),
        ]:
            s.configure(f'{name}.TButton',
                        background=bg_c, foreground=fg_c,
                        font=FONT_BOLD if bold else FONT_SM,
                        borderwidth=0, relief='flat',
                        padding=(10, 5) if bold else (6, 3))
            s.map(f'{name}.TButton',
                  background=[('active', ACCENT)],
                  foreground=[('active', BG)])

        s.configure('TNotebook',     background=BG,  borderwidth=0)
        s.configure('TNotebook.Tab', background=BG2, foreground=GRAY,
                    font=FONT_BOLD, padding=(14, 6))
        s.map('TNotebook.Tab',
              background=[('selected', BG3)], foreground=[('selected', FG)])

    # ── Layout ────────────────────────────────────────────────

    def _build_ui(self):
        self._build_header()
        self._build_statusbar()
        self._build_body()

    def _build_header(self):
        hdr = tk.Frame(self, bg=BG2, height=56)
        hdr.pack(fill='x')
        hdr.pack_propagate(False)

        tk.Label(hdr, text="AutoBot", font=FONT_H1,
                 bg=BG2, fg=ACCENT).pack(side='left', padx=16, pady=12)
        tk.Label(hdr, text="AI Mode  •  qwen-3-6-plus  •  Linken Sphere  •  StarZone",
                 font=FONT_SM, bg=BG2, fg=GRAY).pack(side='left', pady=12)

        bf = tk.Frame(hdr, bg=BG2)
        bf.pack(side='right', padx=14, pady=10)

        self._stop_btn = ttk.Button(bf, text="⏹  Stop", style='Stop.TButton',
                                    command=self._stop, state='disabled')
        self._stop_btn.pack(side='right', padx=4)

        self._run_btn = ttk.Button(bf, text="▶  Start Task", style='Run.TButton',
                                   command=self._start)
        self._run_btn.pack(side='right', padx=4)

    def _build_statusbar(self):
        sb = tk.Frame(self, bg=BG3, height=22)
        sb.pack(fill='x', side='bottom')
        sb.pack_propagate(False)
        self._status_var = tk.StringVar(value="Ready")
        tk.Label(sb, textvariable=self._status_var, font=FONT_SM,
                 bg=BG3, fg=GRAY, anchor='w').pack(side='left', padx=10)
        self._stats_var = tk.StringVar(value="")
        tk.Label(sb, textvariable=self._stats_var, font=FONT_SM,
                 bg=BG3, fg=GRAY, anchor='e').pack(side='right', padx=10)

    def _build_body(self):
        body = tk.Frame(self, bg=BG)
        body.pack(fill='both', expand=True, padx=8, pady=6)

        # Left sidebar
        left = tk.Frame(body, bg=BG2, width=260)
        left.pack(side='left', fill='y', padx=(0, 6))
        left.pack_propagate(False)
        self._build_session_panel(left)
        self._build_proxy_panel(left)
        self._build_telegram_panel(left)

        # Right: notebook with Task + Chat tabs
        right = tk.Frame(body, bg=BG)
        right.pack(side='left', fill='both', expand=True)

        self._nb = ttk.Notebook(right)
        self._nb.pack(fill='both', expand=True)

        task_tab = tk.Frame(self._nb, bg=BG)
        self._nb.add(task_tab, text="  ⚡ Task  ")
        self._build_task_tab(task_tab)

        know_tab = tk.Frame(self._nb, bg=BG)
        self._nb.add(know_tab, text="  🧠 Knowledge  ")
        self._build_knowledge_tab(know_tab)

        chat_tab = tk.Frame(self._nb, bg=BG)
        self._nb.add(chat_tab, text="  💬 Chat with AI  ")
        self._build_chat_tab(chat_tab)

    # ── Knowledge tab (always-in-memory operator knowledge) ────

    def _build_knowledge_tab(self, parent):
        hdr = tk.Frame(parent, bg=BG2)
        hdr.pack(fill='x')
        tk.Label(hdr, text="ALWAYS-IN-MEMORY KNOWLEDGE", font=('Segoe UI', 8, 'bold'),
                 bg=BG2, fg=MAUVE).pack(side='left', padx=10, pady=6)
        tk.Label(hdr, text="Facts/rules the bot keeps in memory on every run",
                 font=FONT_SM, bg=BG2, fg=GRAY).pack(side='left', pady=6)
        self._know_status = tk.Label(hdr, text="", font=FONT_SM, bg=BG2, fg=GREEN)
        self._know_status.pack(side='right', padx=10)
        ttk.Button(hdr, text="💾 Save", style='Run.TButton',
                   command=self._save_knowledge).pack(side='right', padx=6, pady=4)

        self._knowledge = tk.Text(
            parent, bg='#11111b', fg=FG, font=FONT_MONO,
            borderwidth=0, highlightthickness=1,
            highlightcolor=MAUVE, highlightbackground=BG3,
            insertbackground=FG, wrap='word', undo=True,
        )
        self._knowledge.pack(fill='both', expand=True, pady=(0, 4))
        self._bind_clipboard(self._knowledge)
        self._build_context_menu(self._knowledge)
        # Save on Ctrl+S too
        self._knowledge.bind('<Control-KeyPress>',
                             lambda e: (self._save_knowledge() or 'break') if e.keycode == 83 else None,
                             add='+')

        # Load existing knowledge
        try:
            if os.path.exists(KNOWLEDGE_PATH):
                with open(KNOWLEDGE_PATH, encoding='utf-8') as f:
                    self._knowledge.insert('1.0', f.read())
            else:
                self._knowledge.insert('1.0',
                    "# Сюда впиши всё, что бот должен ВСЕГДА помнить.\n"
                    "# Например — данные для входа, правила, особенности сайтов:\n"
                    "#   sabotage.ink: логин folki, пароль zxcASD\n"
                    "#   На форме логина сначала кликни поле, потом вводи\n")
        except Exception:
            pass

    def _save_knowledge(self):
        try:
            txt = self._knowledge.get('1.0', 'end').strip()
            with open(KNOWLEDGE_PATH, 'w', encoding='utf-8') as f:
                f.write(txt)
            import datetime as _dt
            self._know_status.config(text=f"✓ saved {_dt.datetime.now():%H:%M:%S}")
        except Exception as e:
            self._know_status.config(text=f"save failed: {e}", fg=RED)

    # ── Left panels ───────────────────────────────────────────

    def _section(self, parent, title):
        tk.Label(parent, text=title, font=('Segoe UI', 8, 'bold'),
                 bg=BG2, fg=GRAY, anchor='w').pack(fill='x', padx=10, pady=(10, 4))

    def _build_session_panel(self, parent):
        self._section(parent, "LINKEN SPHERE")
        f = tk.Frame(parent, bg=BG2)
        f.pack(fill='x', padx=10, pady=(0, 8))
        self._lbl(f, "Browser:", "Hybrid 2.0 (mimic)", 0)
        tk.Label(f, text="Session:", font=FONT_SM, bg=BG2, fg=GRAY).grid(
            row=1, column=0, sticky='w', pady=2)
        self._session_lbl = tk.Label(f, text="Not created", font=FONT_SM,
                                      bg=BG2, fg=YELLOW)
        self._session_lbl.grid(row=1, column=1, sticky='w', padx=6)

    def _build_proxy_panel(self, parent):
        self._section(parent, "STARZONE PROXY")
        f = tk.Frame(parent, bg=BG2)
        f.pack(fill='x', padx=10, pady=(0, 8))
        self._lbl(f, "Country:", "USA 🇺🇸", 0)
        tk.Label(f, text="State:", font=FONT_SM, bg=BG2, fg=GRAY).grid(
            row=1, column=0, sticky='w', pady=2)
        self._state_lbl = tk.Label(f, text="Auto (Venice picks)",
                                    font=FONT_SM, bg=BG2, fg=ACCENT)
        self._state_lbl.grid(row=1, column=1, sticky='w', padx=6)
        tk.Label(f, text="IP:", font=FONT_SM, bg=BG2, fg=GRAY).grid(
            row=2, column=0, sticky='w', pady=2)
        self._ip_lbl = tk.Label(f, text="—", font=FONT_SM, bg=BG2, fg=FG)
        self._ip_lbl.grid(row=2, column=1, sticky='w', padx=6)

    def _build_telegram_panel(self, parent):
        self._section(parent, "TELEGRAM")
        f = tk.Frame(parent, bg=BG2)
        f.pack(fill='x', padx=10, pady=(0, 8))
        self._lbl(f, "Bot:", "@AutoRegerBot_bot", 0)
        tk.Label(f, text="Alerts:", font=FONT_SM, bg=BG2, fg=GRAY).grid(
            row=1, column=0, sticky='w', pady=2)
        self._notif_lbl = tk.Label(f, text="0 sent", font=FONT_SM, bg=BG2, fg=FG)
        self._notif_lbl.grid(row=1, column=1, sticky='w', padx=6)
        ttk.Button(f, text="Send test", style='Sm.TButton',
                   command=self._test_telegram).grid(
            row=2, column=0, columnspan=2, sticky='w', pady=(6, 0))

    def _lbl(self, parent, key, val, row):
        tk.Label(parent, text=key, font=FONT_SM, bg=BG2, fg=GRAY).grid(
            row=row, column=0, sticky='w', pady=2)
        tk.Label(parent, text=val, font=FONT_SM, bg=BG2, fg=FG).grid(
            row=row, column=1, sticky='w', padx=6)

    # ── Task tab ──────────────────────────────────────────────

    def _build_task_tab(self, parent):
        # Instruction area
        hdr = tk.Frame(parent, bg=BG2)
        hdr.pack(fill='x')
        tk.Label(hdr, text="TASK INSTRUCTION", font=('Segoe UI', 8, 'bold'),
                 bg=BG2, fg=GRAY).pack(side='left', padx=10, pady=6)
        tk.Label(hdr, text="Describe task + when to notify admin",
                 font=FONT_SM, bg=BG2, fg=GRAY).pack(side='left', pady=6)

        self._instruction = tk.Text(
            parent, height=7, bg='#11111b', fg=FG, font=FONT_MONO,
            borderwidth=0, highlightthickness=1,
            highlightcolor=ACCENT, highlightbackground=BG3,
            insertbackground=FG, wrap='word', undo=True,
        )
        self._instruction.pack(fill='x', pady=(0, 4))
        self._bind_clipboard(self._instruction)
        self._build_context_menu(self._instruction)
        self._instruction.insert('1.0',
            "Введи инструкцию для задачи здесь.\n"
            "Например: Зайди на https://... найди цену товара SKU-123, запиши в лог.\n"
            "Если встретишь капчу — напиши админу в Telegram.")

        # Log area
        log_hdr = tk.Frame(parent, bg=BG2)
        log_hdr.pack(fill='x')
        tk.Label(log_hdr, text="EXECUTION LOG", font=('Segoe UI', 8, 'bold'),
                 bg=BG2, fg=GRAY).pack(side='left', padx=10, pady=6)
        ttk.Button(log_hdr, text="Clear", style='Sm.TButton',
                   command=self._clear_log).pack(side='right', padx=6, pady=4)

        lf = tk.Frame(parent, bg='#11111b')
        lf.pack(fill='both', expand=True)
        vsb = tk.Scrollbar(lf, orient='vertical', bg='#11111b',
                           troughcolor=BG3, width=8)
        vsb.pack(side='right', fill='y')
        self._log = tk.Text(lf, bg='#11111b', fg=FG, font=FONT_MONO,
                             borderwidth=0, highlightthickness=0,
                             state='disabled', wrap='word',
                             yscrollcommand=vsb.set)
        self._log.pack(fill='both', expand=True)
        vsb.config(command=self._log.yview)

        self._log.tag_config('info',    foreground=FG)
        self._log.tag_config('step',    foreground=ACCENT)
        self._log.tag_config('success', foreground=GREEN)
        self._log.tag_config('error',   foreground=RED)
        self._log.tag_config('warn',    foreground=YELLOW)
        self._log.tag_config('notify',  foreground=MAUVE)
        self._log.tag_config('venice',  foreground=TEAL)

    # ── Chat tab ──────────────────────────────────────────────

    def _build_chat_tab(self, parent):
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)

        # Row 0 — hint bar
        hint = tk.Frame(parent, bg=BG3, height=30)
        hint.grid(row=0, column=0, sticky='ew')
        hint.grid_propagate(False)
        tk.Label(hint, text="Попроси AI составить system prompt, инструкцию или задай вопрос",
                 font=FONT_SM, bg=BG3, fg=GRAY).pack(side='left', padx=12, pady=6)
        ttk.Button(hint, text="Очистить чат", style='Sm.TButton',
                   command=self._clear_chat).pack(side='right', padx=8, pady=4)

        # Row 1 — ScrolledText (most reliable cross-platform widget)
        self._chat_display = scrolledtext.ScrolledText(
            parent,
            bg='#1e1e2e', fg='white',
            font=('Consolas', 10),
            borderwidth=0, highlightthickness=0,
            wrap='word', relief='flat',
            spacing1=2, spacing3=4,
            cursor='arrow',
        )
        self._chat_display.grid(row=1, column=0, sticky='nsew', padx=6, pady=(2, 0))

        # Prevent typing, allow Ctrl+C / Ctrl+A
        self._chat_display.bind('<Key>', self._chat_display_key)
        self._build_context_menu(self._chat_display, read_only=True)

        # Tags — color only, NO font override
        self._chat_display.tag_config('ai',   foreground='#a6e3a1')
        self._chat_display.tag_config('you',  foreground='#89b4fa')
        self._chat_display.tag_config('err',  foreground='#f38ba8')
        self._chat_display.tag_config('dim',  foreground='#45475a')
        self._chat_display.tag_config('wait', foreground='#6c7086')

        # Row 2 — input area
        inp = tk.Frame(parent, bg=BG2)
        inp.grid(row=2, column=0, sticky='ew')

        # Quick buttons
        qf = tk.Frame(inp, bg=BG2)
        qf.pack(fill='x', padx=8, pady=(6, 2))
        tk.Label(qf, text="Быстро:", font=FONT_SM, bg=BG2, fg=GRAY).pack(side='left')
        for lbl, msg in [
            ("System prompt",     "Составь оптимальный system prompt для Venice AI агента нашего бота. Учти всю архитектуру."),
            ("Пример инструкции", "Напиши пример инструкции для задачи мониторинга цен на сайте по списку артикулов."),
            ("Как работает?",     "Объясни кратко как работает agentic loop в нашем боте — от инструкции до результата."),
        ]:
            ttk.Button(qf, text=lbl, style='Sm.TButton',
                       command=lambda m=msg: self._send_chat(m)).pack(side='left', padx=3)

        row = tk.Frame(inp, bg=BG2)
        row.pack(fill='x', padx=8, pady=(2, 8))

        self._chat_input = tk.Text(
            row, height=3,
            bg=BG3, fg='white', font=('Consolas', 10),
            borderwidth=0, highlightthickness=1,
            highlightcolor=ACCENT, highlightbackground='#45475a',
            insertbackground='white', wrap='word', undo=True,
        )
        self._chat_input.pack(side='left', fill='x', expand=True, padx=(0, 8))

        self._chat_input.bind('<Return>',    self._on_chat_enter)
        self._chat_input.bind('<KP_Enter>',  self._on_chat_enter)
        self._bind_clipboard(self._chat_input)
        self._build_context_menu(self._chat_input)

        self._send_btn = ttk.Button(row, text="Send ↵", style='Send.TButton',
                                     command=lambda: self._send_chat())
        self._send_btn.pack(side='left', ipady=8)

        # Welcome
        self._cw('ai',  'AutoBot AI\n')
        self._cw(None,
            'Привет! Я знаю всю архитектуру этого бота.\n\n'
            'Могу помочь:\n'
            '  * Составить system prompt для Venice AI агента\n'
            '  * Написать инструкцию для конкретной задачи\n'
            '  * Объяснить как работает любой компонент\n\n'
            'Enter = отправить,  Shift+Enter = новая строка\n'
        )

    def _chat_display_key(self, event):
        if event.state & 0x4 and event.keysym in ('c', 'C'):
            return None  # allow Ctrl+C
        if event.state & 0x4 and event.keysym in ('a', 'A'):
            self._chat_display.tag_add('sel', '1.0', 'end')
            return 'break'
        return 'break'

    def _cw(self, tag, text: str):
        """Write to chat display. tag=None means plain white text."""
        if tag:
            self._chat_display.insert('end', text, tag)
        else:
            self._chat_display.insert('end', text)
        self._chat_display.see('end')
        self._chat_display.update_idletasks()

    # ── Clipboard helpers ─────────────────────────────────────

    def _paste(self, widget: tk.Text):
        try:
            widget.insert('insert', self.clipboard_get())
        except Exception:
            pass
        return 'break'

    def _select_all(self, widget: tk.Text):
        widget.tag_add('sel', '1.0', 'end')
        widget.mark_set('insert', '1.0')
        return 'break'

    def _bind_clipboard(self, widget: tk.Text, read_only: bool = False):
        """Layout-independent Ctrl+C/V/X/A/Z. The default <Control-v> binding
        fails on a Russian keyboard layout (keysym becomes Cyrillic), so we
        dispatch on keycode (Windows virtual-key codes, same on any layout)."""
        def on_key(e):
            kc = e.keycode
            if kc == 86 and not read_only:        # V — paste
                return self._paste(widget)
            if kc == 67:                          # C — copy
                widget.event_generate('<<Copy>>'); return 'break'
            if kc == 88 and not read_only:        # X — cut
                widget.event_generate('<<Cut>>'); return 'break'
            if kc == 65:                          # A — select all
                return self._select_all(widget)
            if kc == 90 and not read_only:        # Z — undo
                try: widget.edit_undo()
                except Exception: pass
                return 'break'
            return None
        widget.bind('<Control-KeyPress>', on_key)

    def _build_context_menu(self, widget: tk.Text, read_only: bool = False):
        menu = tk.Menu(widget, tearoff=0, bg=BG3, fg=FG,
                       activebackground=ACCENT, activeforeground=BG)
        if not read_only:
            menu.add_command(label="Вырезать",    command=lambda: widget.event_generate('<<Cut>>'))
        menu.add_command(label="Копировать",      command=lambda: widget.event_generate('<<Copy>>'))
        if not read_only:
            menu.add_command(label="Вставить",    command=lambda: self._paste(widget))
            menu.add_separator()
            menu.add_command(label="Выбрать всё", command=lambda: self._select_all(widget))
        widget.bind('<Button-3>', lambda e: menu.tk_popup(e.x_root, e.y_root))

    # ── Chat logic ────────────────────────────────────────────

    def _clear_chat(self):
        self._chat_display.delete('1.0', 'end')
        self._chat_history = [{"role": "system", "content": CHAT_SYSTEM}]
        self._cw('ai', 'AutoBot AI\n')
        self._cw(None, 'Чат очищен. Чем могу помочь?\n')

    def _on_chat_enter(self, event):
        if not (event.state & 0x1):
            self._send_chat()
            return 'break'

    def _send_chat(self, preset_text: str = ""):
        if preset_text:
            text = preset_text
        else:
            text = self._chat_input.get('1.0', 'end').strip()
            self._chat_input.delete('1.0', 'end')
        if not text:
            return

        self._cw('dim', '─' * 52 + '\n')
        self._cw('you', 'Вы\n')
        self._cw(None,  text + '\n')

        self._send_btn.config(state='disabled')
        self._waiting_mark = self._chat_display.index('end-1c')
        self._cw('wait', '\n[ждем ответа...]\n')

        # Add to history
        self._chat_history.append({"role": "user", "content": text})

        # Call Venice in background
        threading.Thread(
            target=self._call_venice_chat,
            args=(list(self._chat_history),),
            daemon=True,
        ).start()

    def _call_venice_chat(self, messages: list):
        import datetime, os
        log_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'chat_log.txt')

        def log(label: str, text: str):
            ts = datetime.datetime.now().strftime('%H:%M:%S')
            line = f"[{ts}] {label}:\n{text}\n{'─'*60}\n"
            try:
                with open(log_path, 'a', encoding='utf-8') as f:
                    f.write(line)
            except Exception:
                pass

        try:
            payload_dict = {
                "model": VENICE_MODEL,
                "messages": messages,
                "max_tokens": 8000,
                "temperature": 0.7,
                "venice_parameters": {
                    "include_venice_system_prompt": False,
                    # Reasoning ate the whole budget → answer got cut off empty.
                    # Off = the full answer lands in content, no truncation.
                    "disable_thinking": True,
                },
            }
            log("REQUEST", f"model={VENICE_MODEL} messages={len(messages)} last_role={messages[-1]['role']} last_len={len(str(messages[-1]['content']))}")

            payload = json.dumps(payload_dict).encode()
            req = urllib.request.Request(
                f"{VENICE_BASE_URL}/chat/completions",
                data=payload,
                headers={
                    "Authorization": f"Bearer {VENICE_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                raw = resp.read()

            log("RAW_RESPONSE", raw.decode('utf-8', errors='replace')[:4000])

            data = json.loads(raw)
            choice = data["choices"][0]
            message = choice["message"]
            reply = message.get("content") or ""

            log("PARSED_REPLY", f"finish_reason={choice.get('finish_reason')} content_len={len(reply)}\n{reply[:2000]}")

            if not reply:
                # Try alternate fields (reasoning models sometimes use different keys)
                for key in ("reasoning_content", "reasoning", "text"):
                    alt = message.get(key, "")
                    if alt:
                        log("ALT_FIELD", f"found content in '{key}': {str(alt)[:200]}")
                        reply = f"[{key}]: {alt}"
                        break

            self._chat_history.append({"role": "assistant", "content": reply or "(empty)"})
            self._chat_q.put(('reply', reply if reply else "(Venice вернул пустой ответ — см. chat_log.txt)"))

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            log("ERROR", tb)
            self._chat_q.put(('error', str(e)))

    # ── Helpers ───────────────────────────────────────────────

    def _log_msg(self, msg: str, tag: str = 'info'):
        self._log.config(state='normal')
        self._log.insert('end', msg + '\n', tag)
        self._log.see('end')
        self._log.config(state='disabled')

    def _clear_log(self):
        self._log.config(state='normal')
        self._log.delete('1.0', 'end')
        self._log.config(state='disabled')

    def _test_telegram(self):
        ok = telegram_notify.send_message(
            "🤖 <b>AutoBot RPA</b>\n\nТест уведомления ✅\nBot: @AutoRegerBot_bot"
        )
        tag = 'success' if ok else 'error'
        msg = "Telegram ✓ сообщение отправлено" if ok else \
              "Telegram ✗ — нажми /start у @AutoRegerBot_bot"
        self._log_msg(msg, tag)
        if not ok:
            self._nb.select(0)  # switch to Task tab to see log

    # ── Start / Stop ──────────────────────────────────────────

    def _start(self):
        instruction = self._instruction.get('1.0', 'end').strip()
        if not instruction or instruction.startswith('Введи инструкцию'):
            messagebox.showinfo("Пусто", "Напиши инструкцию для задачи.", parent=self)
            return
        # Persist knowledge edits now (main thread — Tk widget access)
        try:
            self._save_knowledge()
        except Exception:
            pass
        self._nb.select(0)  # switch to Task tab
        self._running = True
        self._stop_flag = False
        self._run_btn.config(state='disabled')
        self._stop_btn.config(state='normal')
        self._status_var.set("Running...")
        self._log_msg("═" * 55, 'info')
        self._log_msg("Task started", 'success')
        threading.Thread(target=self._run_task, args=(instruction,), daemon=True).start()

    def _stop(self):
        self._stop_flag = True
        self._log_msg("Stop requested...", 'warn')
        self._stop_btn.config(state='disabled')

    def _on_done(self):
        self._running = False
        self._run_btn.config(state='normal')
        self._stop_btn.config(state='disabled')
        self._status_var.set("Ready")
        for obj, method in [(self._browser, 'close'),]:
            if obj:
                try: getattr(obj, method)()
                except Exception: pass
        self._browser = None
        if self._session_uuid:
            try:
                ls.stop_session(self._session_uuid)
                ls.remove_session(self._session_uuid)
            except Exception:
                pass
            self._session_uuid = None
        self._session_lbl.config(text="Not created", fg=YELLOW)

    # ── Task runner ───────────────────────────────────────────

    def _run_task(self, instruction: str):
        import time as _time
        import datetime as _dt

        # Per-run output folder: runs/<timestamp>/ with log, screenshots, data
        run_dir = os.path.join(RUNS_DIR, _dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
        try:
            os.makedirs(run_dir, exist_ok=True)
        except Exception:
            run_dir = ""
        run_log_path = os.path.join(run_dir, "log.txt") if run_dir else ""

        def log(msg: str):
            tag = 'venice'  if msg.startswith('Venice')        else \
                  'step'    if msg.startswith('──')            else \
                  'notify'  if 'notif' in msg.lower()          else \
                  'error'   if 'error' in msg.lower()          else \
                  'success' if ('done' in msg.lower() or
                                'complete' in msg.lower())     else 'info'
            self._log_q.put(('log', msg, tag))
            if run_log_path:
                try:
                    with open(run_log_path, "a", encoding="utf-8") as f:
                        f.write(msg + "\n")
                except Exception:
                    pass

        def do_save_data(data: dict):
            if not run_dir:
                return
            try:
                with open(os.path.join(run_dir, "data.jsonl"), "a", encoding="utf-8") as f:
                    f.write(json.dumps(data, ensure_ascii=False) + "\n")
                log(f"💾 Saved data: {str(data)[:80]}")
            except Exception as e:
                log(f"save_data failed: {e}")

        def notify(msg, screenshot=None):
            self._notif_count += 1
            self._log_q.put(('notify', msg))
            telegram_notify.notify(msg, screenshot)

        # ── Infrastructure callbacks ──────────────────────────

        def do_set_proxy(state: str) -> bool:
            log(f"StarZone → {state}")
            ok = starzone.set_state(state)
            _time.sleep(1)
            self._log_q.put(('state', state.upper()))
            return ok

        def do_rotate_ip() -> bool:
            log("Rotating IP...")
            return starzone.rotate_ip()

        def do_open_browser() -> dict:
            # Clean up any leftover session from a previous failed attempt so
            # sessions don't pile up and choke Linken Sphere.
            if self._session_uuid:
                try:
                    ls.stop_session(self._session_uuid)
                    ls.remove_session(self._session_uuid)
                except Exception:
                    pass
                self._session_uuid = None

            log("Opening Linken Sphere session (Hybrid 2.0)...")
            # Proxy ONLY the browser session (not the whole host) so the bot's
            # Venice API calls stay on the fast direct link. StarZone SOCKS5 is
            # IP-whitelist authed (no user/pass) — the machine's public IP must
            # be in StarZone 'Authorized IPs'.
            creds = starzone.get_proxy_credentials(on_log=log)
            result = ls.launch_session(
                proxy_host=creds["host"],
                proxy_port=creds["port"],
                proxy_type=creds["type"],
                proxy_login=creds["username"],
                proxy_password=creds["password"],
                name="autobot-ai",
                on_log=log,
            )
            if "error" in result:
                log(f"LS error: {result['error']}")
                return result
            self._session_uuid = result["uuid"]
            self._log_q.put(('session', result["uuid"][:8]))
            log(f"Session {result['uuid'][:8]}... port={result.get('debug_port')}")
            log("Connecting CDP (waiting for debug port)...")
            try:
                self._browser = CDPBrowser(result["debug_port"])
            except Exception as e:
                # Tear down the half-open session so it doesn't accumulate
                log(f"CDP connect failed: {e}")
                try:
                    ls.stop_session(self._session_uuid)
                    ls.remove_session(self._session_uuid)
                except Exception:
                    pass
                self._session_uuid = None
                return {"error": f"CDP connect failed: {e}"}
            log("Browser ready ✓")
            return result

        def do_close_browser():
            if self._browser:
                try: self._browser.close()
                except Exception: pass
                self._browser = None
            if self._session_uuid:
                try:
                    ls.stop_session(self._session_uuid)
                    ls.remove_session(self._session_uuid)
                except Exception: pass
                self._session_uuid = None
            self._log_q.put(('session_closed',))
            log("Browser closed")

        def get_browser():
            return self._browser

        # ── Manual-control callbacks (Telegram human-in-the-loop) ──
        def do_ask_human(question: str):
            self._log_q.put(('notify', f"❓ {question}"))
            img = None
            url = ""
            if self._browser is not None:
                try: img = self._browser.screenshot()
                except Exception: pass
                try: url = self._browser.get_url()
                except Exception: pass
            reply = telegram_notify.ask(
                question, screenshot=img,
                stop_flag=lambda: self._stop_flag, timeout=1800.0, on_log=log)
            if reply:
                self._log_q.put(('log', f"💬 You: {reply}", 'notify'))
                # AUTO-REMEMBER: persist every operator instruction so it
                # survives restarts (the model rarely calls remember itself).
                page = f" (on {url})" if url else ""
                do_remember(f"When asked \"{question[:120]}\"{page} — do: {reply}")
            return reply

        def do_remember(note: str):
            try:
                with open(LEARNED_PATH, "a", encoding="utf-8") as f:
                    f.write(note.strip() + "\n")
                log(f"🧠 Remembered: {note.strip()[:90]}")
            except Exception as e:
                log(f"remember failed: {e}")

        # Live operator interrupts: start the Telegram listener and drain any
        # messages the operator typed proactively (auto-remember + apply).
        telegram_notify.start_listener(stop_flag=lambda: self._stop_flag)

        def get_operator_msgs():
            out = []
            while True:
                m = telegram_notify.next_message()
                if not m:
                    break
                self._log_q.put(('log', f"💬 You (live): {m}", 'notify'))
                do_remember(f"Operator standing instruction: {m}")
                out.append(m)
            return out

        learned = ""
        try:
            if os.path.exists(LEARNED_PATH):
                with open(LEARNED_PATH, encoding="utf-8") as f:
                    learned = f.read()
        except Exception:
            pass

        # Always-in-memory knowledge (saved on the main thread at _start)
        knowledge = ""
        try:
            if os.path.exists(KNOWLEDGE_PATH):
                with open(KNOWLEDGE_PATH, encoding="utf-8") as f:
                    knowledge = f.read()
        except Exception:
            pass

        # ── Run agent ─────────────────────────────────────────
        try:
            final = run_agent(
                instruction=instruction,
                on_log=log,
                on_notify=notify,
                stop_flag=lambda: self._stop_flag,
                on_set_proxy=do_set_proxy,
                on_rotate_ip=do_rotate_ip,
                on_open_browser=do_open_browser,
                on_close_browser=do_close_browser,
                get_browser=get_browser,
                on_ask_human=do_ask_human,
                on_remember=do_remember,
                learned=learned,
                knowledge=knowledge,
                on_save_data=do_save_data,
                record_dir=run_dir,
                on_operator_msgs=get_operator_msgs,
            )
            if run_dir:
                log(f"📁 Run saved to: {run_dir}")
            log(f"Result: {final}")
            self._log_q.put(('done', final))
        except Exception as e:
            log(f"Task error: {e}")
            self._log_q.put(('done', f"Error: {e}"))

    def _pick_state(self, instruction: str, log) -> str:
        import re
        states = starzone.get_us_states()
        if not states:
            states = ["ny", "ca", "tx", "fl", "or", "mt", "nh", "de"]

        payload = json.dumps({
            "model": VENICE_MODEL,
            "messages": [
                {"role": "system",
                 "content": (
                     "You pick the best US state code for a residential proxy.\n"
                     "Tax-free states for shopping: or, mt, nh, de.\n"
                     "Major markets: ny, ca, tx, fl.\n"
                     "Reply with exactly one JSON object, nothing else: "
                     '{\"state\":\"xx\",\"reason\":\"brief reason\"}'
                 )},
                {"role": "user",
                 "content": f"Task: {instruction[:400]}\nAvailable states: {', '.join(states[:30])}"},
            ],
            "max_tokens": 120,
            "temperature": 0.2,
            "venice_parameters": {
                "include_venice_system_prompt": False,
                "disable_thinking": True,   # need a quick JSON pick, no reasoning
            },
        }).encode()

        req = urllib.request.Request(
            f"{VENICE_BASE_URL}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {VENICE_API_KEY}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
            text = data["choices"][0]["message"]["content"] or ""
            # Extract JSON even if model wraps it in markdown
            m = re.search(r'\{[^}]+\}', text)
            if m:
                result = json.loads(m.group())
                state = result.get("state", "").lower().strip()
                if state in states:
                    log(f"Venice → {state}: {result.get('reason','')}")
                    return state
            log(f"State parse failed, raw: {text[:80]}")
        except Exception as e:
            log(f"State pick failed: {e}")
        chosen = random.choice(states)
        log(f"Using random state: {chosen}")
        return chosen

    # ── Poll loop ─────────────────────────────────────────────

    def _poll(self):
        # Task log queue
        while not self._log_q.empty():
            item = self._log_q.get_nowait()
            if item[0] == 'log':
                self._log_msg(item[1], item[2])
            elif item[0] == 'done':
                self._log_msg(f"═ {item[1]} ═", 'success')
                self._on_done()
            elif item[0] == 'state':
                self._state_lbl.config(text=item[1].upper())
            elif item[0] == 'session':
                self._session_lbl.config(text=f"Active: {item[1]}...", fg=GREEN)
            elif item[0] == 'session_closed':
                self._session_lbl.config(text="Not created", fg=YELLOW)
            elif item[0] == 'notify':
                self._log_msg(f"📨 Telegram: {item[1]}", 'notify')
                self._notif_lbl.config(text=f"{self._notif_count} sent")

        # Chat queue
        while not self._chat_q.empty():
            kind, data = self._chat_q.get_nowait()

            # Delete the "[ждем ответа...]" placeholder using saved mark
            try:
                self._chat_display.delete(self._waiting_mark, 'end')
            except Exception:
                pass

            if kind == 'reply':
                self._cw('ai',  '\nAutoBot AI\n')
                self._cw(None, str(data) + '\n')
            else:
                self._cw('err', '\nОшибка\n')
                self._cw(None, str(data) + '\n')

            self._send_btn.config(state='normal')

        self.after(100, self._poll)
