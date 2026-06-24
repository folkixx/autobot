"""AutoBot RPA — main tkinter window."""
import queue
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Optional

import core.storage as storage
from core.flow import Flow, Step
from core.recorder import Recorder
from core.player import Player

# ── Colour palette (Catppuccin Mocha) ──────────────────────────
BG      = '#1e1e2e'
BG2     = '#24243c'
BG3     = '#313244'
FG      = '#cdd6f4'
ACCENT  = '#89b4fa'
GREEN   = '#a6e3a1'
RED     = '#f38ba8'
YELLOW  = '#f9e2af'
GRAY    = '#6c7086'
MAUVE   = '#cba6f7'

FONT_UI   = ('Segoe UI', 10)
FONT_BOLD = ('Segoe UI', 10, 'bold')
FONT_MONO = ('Consolas', 10)
FONT_SM   = ('Segoe UI', 9)
FONT_H1   = ('Segoe UI', 14, 'bold')


class AutoBotApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AutoBot RPA")
        self.geometry("980x660")
        self.minsize(720, 480)
        self.configure(bg=BG)

        self._recorder: Optional[Recorder] = None
        self._player: Optional[Player] = None
        self._current_flow: Optional[Flow] = None
        self._recording = False
        self._running = False

        self._log_q: queue.Queue = queue.Queue()
        self._step_q: queue.Queue = queue.Queue()

        self._setup_styles()
        self._build_ui()
        self._refresh_flow_list()
        self._poll()

    # ── Styles ──────────────────────────────────────────────────

    def _setup_styles(self):
        s = ttk.Style(self)
        s.theme_use('clam')

        s.configure('TFrame',            background=BG)
        s.configure('Panel.TFrame',      background=BG2)
        s.configure('TLabel',            background=BG,  foreground=FG,    font=FONT_UI)
        s.configure('Panel.TLabel',      background=BG2, foreground=FG,    font=FONT_UI)
        s.configure('Gray.TLabel',       background=BG2, foreground=GRAY,  font=FONT_SM)
        s.configure('Accent.TLabel',     background=BG2, foreground=ACCENT,font=FONT_BOLD)
        s.configure('Head.TLabel',       background=BG,  foreground=GRAY,  font=('Segoe UI', 8, 'bold'))

        # Buttons
        s.configure('TButton', background=BG3, foreground=FG, font=FONT_UI,
                    borderwidth=0, relief='flat', padding=(8, 4))
        s.map('TButton',
              background=[('active', ACCENT), ('pressed', ACCENT)],
              foreground=[('active', BG),     ('pressed', BG)])

        s.configure('Rec.TButton',  background='#45475a', foreground=RED,   font=FONT_BOLD, padding=(10,5))
        s.map('Rec.TButton',  background=[('active', RED)],   foreground=[('active', BG)])

        s.configure('RecOn.TButton', background=RED, foreground=BG, font=FONT_BOLD, padding=(10,5))
        s.map('RecOn.TButton', background=[('active', '#eba0ac')], foreground=[('active', BG)])

        s.configure('Run.TButton',  background='#45475a', foreground=GREEN, font=FONT_BOLD, padding=(10,5))
        s.map('Run.TButton',  background=[('active', GREEN)], foreground=[('active', BG)])

        s.configure('Stop.TButton', background=RED, foreground=BG, font=FONT_BOLD, padding=(10,5))
        s.map('Stop.TButton', background=[('active', '#eba0ac')], foreground=[('active', BG)])

        s.configure('Sm.TButton', background=BG3, foreground=FG, font=FONT_SM,
                    borderwidth=0, relief='flat', padding=(6, 3))
        s.map('Sm.TButton',
              background=[('active', ACCENT)], foreground=[('active', BG)])

        s.configure('Del.TButton', background=BG3, foreground=RED, font=FONT_SM,
                    borderwidth=0, relief='flat', padding=(6, 3))
        s.map('Del.TButton',
              background=[('active', RED)], foreground=[('active', BG)])

        # Treeview
        s.configure('Treeview', background=BG2, foreground=FG, fieldbackground=BG2,
                    font=FONT_UI, rowheight=26, borderwidth=0)
        s.configure('Treeview.Heading', background=BG3, foreground=ACCENT,
                    font=('Segoe UI', 9, 'bold'), borderwidth=0, relief='flat')
        s.map('Treeview',
              background=[('selected', BG3)], foreground=[('selected', ACCENT)])

        # Notebook
        s.configure('TNotebook',        background=BG,  borderwidth=0)
        s.configure('TNotebook.Tab',    background=BG2, foreground=GRAY,
                    font=FONT_UI, padding=(12, 5))
        s.map('TNotebook.Tab',
              background=[('selected', BG3)], foreground=[('selected', FG)])

        # Sash
        s.configure('Sash', sashthickness=4)

    # ── Layout ──────────────────────────────────────────────────

    def _build_ui(self):
        self._build_header()
        self._build_statusbar()
        self._build_main_pane()

    def _build_header(self):
        hdr = tk.Frame(self, bg=BG2, height=56)
        hdr.pack(fill='x', side='top')
        hdr.pack_propagate(False)

        tk.Label(hdr, text="AutoBot", font=FONT_H1, bg=BG2, fg=ACCENT).pack(side='left', padx=16, pady=12)
        tk.Label(hdr, text="RPA Dashboard", font=FONT_UI, bg=BG2, fg=GRAY).pack(side='left', pady=12)

        btn_f = tk.Frame(hdr, bg=BG2)
        btn_f.pack(side='right', padx=14, pady=10)

        self._stop_btn = ttk.Button(btn_f, text="⏹  Stop",   style='Stop.TButton',
                                    command=self._stop_all, state='disabled')
        self._stop_btn.pack(side='right', padx=4)

        self._run_btn = ttk.Button(btn_f, text="▶  Run Flow", style='Run.TButton',
                                   command=self._run_flow, state='disabled')
        self._run_btn.pack(side='right', padx=4)

        self._rec_btn = ttk.Button(btn_f, text="●  Record", style='Rec.TButton',
                                   command=self._toggle_record)
        self._rec_btn.pack(side='right', padx=4)

    def _build_statusbar(self):
        sb = tk.Frame(self, bg=BG3, height=22)
        sb.pack(fill='x', side='bottom')
        sb.pack_propagate(False)
        self._status_var = tk.StringVar(value="Ready")
        tk.Label(sb, textvariable=self._status_var, font=FONT_SM,
                 bg=BG3, fg=GRAY, anchor='w').pack(side='left', padx=10)

    def _build_main_pane(self):
        pane = tk.PanedWindow(self, orient='horizontal', bg=BG,
                              sashwidth=5, sashrelief='flat', sashpad=0)
        pane.pack(fill='both', expand=True)

        self._build_left_panel(pane)
        self._build_right_panel(pane)

    # ── Left panel: flow list ────────────────────────────────────

    def _build_left_panel(self, pane):
        left = tk.Frame(pane, bg=BG2, width=230)
        pane.add(left, minsize=170)

        tk.Label(left, text="FLOWS", font=('Segoe UI', 8, 'bold'),
                 bg=BG2, fg=GRAY, anchor='w').pack(fill='x', padx=10, pady=(10, 4))

        lf = tk.Frame(left, bg=BG2)
        lf.pack(fill='both', expand=True, padx=6)

        vsb = tk.Scrollbar(lf, orient='vertical', bg=BG2, troughcolor=BG3, width=8)
        vsb.pack(side='right', fill='y')

        self._flow_lb = tk.Listbox(
            lf, bg=BG2, fg=FG, selectbackground=BG3, selectforeground=ACCENT,
            font=FONT_UI, borderwidth=0, highlightthickness=0, activestyle='none',
            yscrollcommand=vsb.set,
        )
        self._flow_lb.pack(fill='both', expand=True)
        vsb.config(command=self._flow_lb.yview)
        self._flow_lb.bind('<<ListboxSelect>>', self._on_flow_select)
        self._flow_lb.bind('<Double-Button-1>', lambda _: self._run_flow())

        bf = tk.Frame(left, bg=BG2)
        bf.pack(fill='x', padx=6, pady=6)
        ttk.Button(bf, text="+ New",    style='Sm.TButton', command=self._new_flow   ).pack(side='left', padx=2)
        ttk.Button(bf, text="✎ Rename", style='Sm.TButton', command=self._rename_flow).pack(side='left', padx=2)
        ttk.Button(bf, text="✕",        style='Del.TButton', command=self._delete_flow).pack(side='left', padx=2)

    # ── Right panel: notebook ────────────────────────────────────

    def _build_right_panel(self, pane):
        right = tk.Frame(pane, bg=BG)
        pane.add(right, minsize=450)

        nb = ttk.Notebook(right)
        nb.pack(fill='both', expand=True, padx=6, pady=6)

        self._build_steps_tab(nb)
        self._build_log_tab(nb)

    def _build_steps_tab(self, nb):
        tab = tk.Frame(nb, bg=BG2)
        nb.add(tab, text="  Steps  ")

        # Header row
        hdr = tk.Frame(tab, bg=BG2)
        hdr.pack(fill='x', padx=8, pady=(8, 4))

        self._flow_name_lbl = tk.Label(hdr, text="No flow selected",
                                        font=FONT_BOLD, bg=BG2, fg=ACCENT)
        self._flow_name_lbl.pack(side='left')

        bf = tk.Frame(hdr, bg=BG2)
        bf.pack(side='right')
        ttk.Button(bf, text="Add Wait",       style='Sm.TButton', command=self._add_wait      ).pack(side='left', padx=2)
        ttk.Button(bf, text="Add Screenshot", style='Sm.TButton', command=self._add_screenshot ).pack(side='left', padx=2)
        ttk.Button(bf, text="Add Navigate",   style='Sm.TButton', command=self._add_navigate  ).pack(side='left', padx=2)
        ttk.Button(bf, text="↑",              style='Sm.TButton', command=self._move_step_up  ).pack(side='left', padx=2)
        ttk.Button(bf, text="↓",              style='Sm.TButton', command=self._move_step_down).pack(side='left', padx=2)
        ttk.Button(bf, text="Delete",         style='Del.TButton', command=self._delete_step  ).pack(side='left', padx=2)

        # Treeview
        tf = tk.Frame(tab, bg=BG2)
        tf.pack(fill='both', expand=True, padx=8, pady=(0, 8))

        vsb = tk.Scrollbar(tf, orient='vertical', bg=BG2, troughcolor=BG3, width=8)
        vsb.pack(side='right', fill='y')

        cols = ('idx', 'type', 'detail')
        self._steps_tv = ttk.Treeview(tf, columns=cols, show='headings',
                                       yscrollcommand=vsb.set, selectmode='browse')
        self._steps_tv.pack(fill='both', expand=True)
        vsb.config(command=self._steps_tv.yview)

        self._steps_tv.heading('idx',    text='#')
        self._steps_tv.heading('type',   text='Type')
        self._steps_tv.heading('detail', text='Description')
        self._steps_tv.column('idx',    width=38,  anchor='center', stretch=False)
        self._steps_tv.column('type',   width=110, anchor='w',      stretch=False)
        self._steps_tv.column('detail', width=600, anchor='w')

    def _build_log_tab(self, nb):
        tab = tk.Frame(nb, bg=BG2)
        nb.add(tab, text="  Run Log  ")

        hdr = tk.Frame(tab, bg=BG2)
        hdr.pack(fill='x', padx=8, pady=(8, 4))
        tk.Label(hdr, text="Execution Log", font=FONT_BOLD, bg=BG2, fg=ACCENT).pack(side='left')
        ttk.Button(hdr, text="Clear", style='Sm.TButton', command=self._clear_log).pack(side='right')

        lf = tk.Frame(tab, bg='#11111b')
        lf.pack(fill='both', expand=True, padx=8, pady=(0, 8))

        vsb = tk.Scrollbar(lf, orient='vertical', bg='#11111b', troughcolor=BG3, width=8)
        vsb.pack(side='right', fill='y')

        self._log_txt = tk.Text(
            lf, bg='#11111b', fg=FG, font=FONT_MONO,
            borderwidth=0, highlightthickness=0, state='disabled',
            wrap='word', yscrollcommand=vsb.set,
        )
        self._log_txt.pack(fill='both', expand=True)
        vsb.config(command=self._log_txt.yview)

        self._log_txt.tag_config('info',    foreground=FG)
        self._log_txt.tag_config('step',    foreground=ACCENT)
        self._log_txt.tag_config('success', foreground=GREEN)
        self._log_txt.tag_config('error',   foreground=RED)
        self._log_txt.tag_config('warn',    foreground=YELLOW)
        self._log_txt.tag_config('rec',     foreground=MAUVE)

    # ── Flow CRUD ────────────────────────────────────────────────

    def _refresh_flow_list(self):
        self._flow_lb.delete(0, 'end')
        for fl in storage.list_flows():
            self._flow_lb.insert('end', fl.name)

    def _on_flow_select(self, _event=None):
        sel = self._flow_lb.curselection()
        if not sel:
            return
        name = self._flow_lb.get(sel[0])
        try:
            self._current_flow = storage.get_flow(name)
        except Exception as e:
            self._log(f"Load error: {e}", 'error')
            return
        self._flow_name_lbl.config(text=self._current_flow.name)
        self._refresh_steps()
        if not self._running:
            self._run_btn.config(state='normal')

    def _new_flow(self):
        name = simpledialog.askstring("New Flow", "Flow name:", parent=self)
        if not name or not name.strip():
            return
        fl = Flow(name=name.strip())
        storage.save_flow(fl)
        self._refresh_flow_list()
        self._select_flow_by_name(fl.name)

    def _rename_flow(self):
        if not self._current_flow:
            return
        old = self._current_flow.name
        new = simpledialog.askstring("Rename", "New name:", initialvalue=old, parent=self)
        if not new or not new.strip() or new.strip() == old:
            return
        storage.delete_flow(old)
        self._current_flow.name = new.strip()
        storage.save_flow(self._current_flow)
        self._refresh_flow_list()
        self._flow_name_lbl.config(text=self._current_flow.name)

    def _delete_flow(self):
        if not self._current_flow:
            return
        if not messagebox.askyesno("Delete", f"Delete flow \"{self._current_flow.name}\"?", parent=self):
            return
        storage.delete_flow(self._current_flow.name)
        self._current_flow = None
        self._flow_name_lbl.config(text="No flow selected")
        self._run_btn.config(state='disabled')
        self._refresh_steps()
        self._refresh_flow_list()

    def _select_flow_by_name(self, name: str):
        for i in range(self._flow_lb.size()):
            if self._flow_lb.get(i) == name:
                self._flow_lb.selection_clear(0, 'end')
                self._flow_lb.selection_set(i)
                self._on_flow_select()
                return

    # ── Steps ────────────────────────────────────────────────────

    def _refresh_steps(self):
        for row in self._steps_tv.get_children():
            self._steps_tv.delete(row)
        if not self._current_flow:
            return
        for i, step in enumerate(self._current_flow.steps):
            self._steps_tv.insert('', 'end', values=(i + 1, step.type, str(step)))

    def _selected_step_idx(self) -> int:
        sel = self._steps_tv.selection()
        if not sel:
            return -1
        return int(self._steps_tv.item(sel[0])['values'][0]) - 1

    def _add_navigate(self):
        if not self._current_flow:
            messagebox.showinfo("Info", "Select or create a flow first.", parent=self)
            return
        url = simpledialog.askstring("Navigate", "URL:", initialvalue="https://", parent=self)
        if not url or not url.strip():
            return
        self._current_flow.steps.append(Step(type='navigate', url=url.strip()))
        storage.save_flow(self._current_flow)
        self._refresh_steps()

    def _add_wait(self):
        if not self._current_flow:
            messagebox.showinfo("Info", "Select or create a flow first.", parent=self)
            return
        ms = simpledialog.askinteger("Wait", "Duration (ms):", initialvalue=1000,
                                     minvalue=100, maxvalue=60_000, parent=self)
        if ms is None:
            return
        self._current_flow.steps.append(Step(type='wait', duration=ms))
        storage.save_flow(self._current_flow)
        self._refresh_steps()

    def _add_screenshot(self):
        if not self._current_flow:
            messagebox.showinfo("Info", "Select or create a flow first.", parent=self)
            return
        fname = simpledialog.askstring("Screenshot", "Filename (.png):",
                                       initialvalue="screenshot.png", parent=self)
        if not fname:
            return
        self._current_flow.steps.append(Step(type='screenshot', filename=fname.strip()))
        storage.save_flow(self._current_flow)
        self._refresh_steps()

    def _delete_step(self):
        if not self._current_flow:
            return
        idx = self._selected_step_idx()
        if idx < 0:
            return
        self._current_flow.steps.pop(idx)
        storage.save_flow(self._current_flow)
        self._refresh_steps()

    def _move_step_up(self):
        if not self._current_flow:
            return
        idx = self._selected_step_idx()
        if idx <= 0:
            return
        steps = self._current_flow.steps
        steps[idx - 1], steps[idx] = steps[idx], steps[idx - 1]
        storage.save_flow(self._current_flow)
        self._refresh_steps()
        # Restore selection
        rows = self._steps_tv.get_children()
        if idx - 1 < len(rows):
            self._steps_tv.selection_set(rows[idx - 1])

    def _move_step_down(self):
        if not self._current_flow:
            return
        idx = self._selected_step_idx()
        steps = self._current_flow.steps
        if idx < 0 or idx >= len(steps) - 1:
            return
        steps[idx], steps[idx + 1] = steps[idx + 1], steps[idx]
        storage.save_flow(self._current_flow)
        self._refresh_steps()
        rows = self._steps_tv.get_children()
        if idx + 1 < len(rows):
            self._steps_tv.selection_set(rows[idx + 1])

    # ── Recording ────────────────────────────────────────────────

    def _toggle_record(self):
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        if not self._current_flow:
            name = simpledialog.askstring("New Flow", "Name for this recording:", parent=self)
            if not name or not name.strip():
                return
            self._current_flow = Flow(name=name.strip())
            storage.save_flow(self._current_flow)
            self._refresh_flow_list()
            self._flow_name_lbl.config(text=self._current_flow.name)

        self._recording = True
        self._rec_btn.config(text="⏹  Stop Rec", style='RecOn.TButton')
        self._run_btn.config(state='disabled')
        self._stop_btn.config(state='normal')
        self._status_var.set(f"● Recording: {self._current_flow.name}")
        self._log(f"Recording started → {self._current_flow.name}", 'warn')
        self._log("Perform actions in the browser. Password fields are NOT captured.", 'info')

        self._recorder = Recorder(
            on_step=lambda d: self._step_q.put(('step', d)),
            on_navigate=lambda d: self._step_q.put(('navigate', d)),
            on_stop=lambda: self._step_q.put(('stopped', None)),
        )
        self._recorder.start()

    def _stop_recording(self):
        if self._recorder:
            self._recorder.stop()

    def _process_step_q(self):
        while not self._step_q.empty():
            kind, data = self._step_q.get_nowait()

            if kind == 'stopped':
                self._recording = False
                self._rec_btn.config(text="●  Record", style='Rec.TButton')
                self._stop_btn.config(state='disabled')
                self._run_btn.config(state='normal' if self._current_flow else 'disabled')
                self._status_var.set("Ready")
                self._log("Recording stopped.", 'success')
                continue

            if not self._current_flow:
                continue

            step = self._build_step(kind, data)
            if step:
                self._current_flow.steps.append(step)
                storage.save_flow(self._current_flow)
                self._refresh_steps()
                self._log(f"  • {step}", 'rec')

    def _build_step(self, kind: str, data: dict) -> Optional[Step]:
        steps = self._current_flow.steps

        if kind == 'navigate':
            url = data.get('url', '')
            if steps and steps[-1].type == 'navigate' and steps[-1].url == url:
                return None
            return Step(type='navigate', url=url)

        t = data.get('type', '')

        if t == 'click':
            sel = data.get('selector', '')
            x, y = data.get('x'), data.get('y')
            # Deduplicate: skip if identical click just recorded
            if (steps and steps[-1].type == 'click' and steps[-1].selector == sel
                    and abs((steps[-1].x or 0) - (x or 0)) < 4):
                return None
            return Step(type='click', selector=sel, x=x, y=y, text=data.get('text', ''))

        if t == 'fill':
            sel = data.get('selector', '')
            val = data.get('value', '')
            # Update existing fill for same selector instead of adding duplicate
            for i in range(len(steps) - 1, max(len(steps) - 8, -1), -1):
                if steps[i].type == 'fill' and steps[i].selector == sel:
                    steps[i] = Step(type='fill', selector=sel, text=val)
                    storage.save_flow(self._current_flow)
                    self._refresh_steps()
                    return None  # already updated in-place
            return Step(type='fill', selector=sel, text=val)

        if t == 'key_press':
            return Step(type='key_press', key=data.get('key', ''))

        if t == 'scroll':
            sx, sy = data.get('scroll_x', 0), data.get('scroll_y', 0)
            if steps and steps[-1].type == 'scroll':
                steps[-1] = Step(type='scroll', scroll_x=sx, scroll_y=sy)
                storage.save_flow(self._current_flow)
                self._refresh_steps()
                return None
            return Step(type='scroll', scroll_x=sx, scroll_y=sy)

        return None

    # ── Playback ─────────────────────────────────────────────────

    def _run_flow(self):
        if not self._current_flow:
            return
        if not self._current_flow.steps:
            messagebox.showinfo("Empty Flow", "Add steps first (record or manual).", parent=self)
            return

        self._running = True
        self._run_btn.config(state='disabled')
        self._rec_btn.config(state='disabled')
        self._stop_btn.config(state='normal')
        self._status_var.set(f"▶ Running: {self._current_flow.name}")
        self._log(f"──── Run: {self._current_flow.name} ────", 'success')

        self._player = Player(
            on_log=lambda msg: self._log_q.put(('log', msg)),
            on_done=lambda: self._log_q.put(('done', None)),
        )
        self._player.play(self._current_flow)

    def _stop_all(self):
        if self._recording and self._recorder:
            self._stop_recording()
        if self._running and self._player:
            self._player.stop()
        self._stop_btn.config(state='disabled')

    def _on_player_done(self):
        self._running = False
        self._run_btn.config(state='normal' if self._current_flow else 'disabled')
        self._rec_btn.config(state='normal')
        self._stop_btn.config(state='disabled')
        self._status_var.set("Ready")

    # ── Logging ──────────────────────────────────────────────────

    def _log(self, msg: str, tag: str = 'info'):
        self._log_txt.config(state='normal')
        self._log_txt.insert('end', msg + '\n', tag)
        self._log_txt.see('end')
        self._log_txt.config(state='disabled')

    def _clear_log(self):
        self._log_txt.config(state='normal')
        self._log_txt.delete('1.0', 'end')
        self._log_txt.config(state='disabled')

    # ── Poll loop ────────────────────────────────────────────────

    def _poll(self):
        # Drain log queue (from player thread)
        while not self._log_q.empty():
            kind, msg = self._log_q.get_nowait()
            if kind == 'done':
                self._on_player_done()
                self._log("──── Run complete ────", 'success')
            else:
                tag = 'info'
                if msg.startswith('['):
                    tag = 'step'
                if 'Error' in msg or 'error' in msg:
                    tag = 'error'
                elif 'Warning' in msg or 'failed' in msg:
                    tag = 'warn'
                elif 'completed' in msg or 'Saved' in msg:
                    tag = 'success'
                self._log(msg, tag)

        # Drain step queue (from recorder thread)
        if self._recording:
            self._process_step_q()

        self.after(100, self._poll)
