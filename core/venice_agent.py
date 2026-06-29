"""Venice AI agentic loop — the brain of AutoBot.

The agent controls everything: proxy, browser session, and page interactions.
It receives a natural language instruction and decides each step autonomously.
"""
import json
import re
import base64
import random
import time
import urllib.request
from typing import Callable, Optional
from core.human_emulator import reaction_delay
from config import VENICE_API_KEY, VENICE_BASE_URL, VENICE_AGENT_MODEL, MAX_STEPS, USE_SCREENSHOT

# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the AI brain of an RPA automation bot.
You control everything: proxy IP, browser sessions, and browser interactions.
At each step you decide the next action based on the task instruction and current state.
Return ONLY a single JSON action — no markdown, no explanation.

## Infrastructure actions (proxy & browser)

{"action": "set_proxy", "state": "tx"}
  — Change StarZone proxy to a US state (2-letter code). Use before opening browser.
    Tax-free states: or, mt, nh, de. Major markets: ny, ca, tx, fl, ny.

{"action": "rotate_ip"}
  — Rotate to a fresh IP within the current proxy state.

{"action": "open_browser"}
  — Create and start a Linken Sphere Hybrid 2.0 session with current proxy.
    Do this before any page interaction. Wait for it to be ready.

{"action": "close_browser"}
  — Stop and delete the current browser session. Use when done or before opening a new one.

## Browser actions (require open browser)

{"action": "navigate", "url": "https://..."}
{"action": "click", "index": 3}
{"action": "fill", "index": 1, "text": "value"}
{"action": "select_option", "index": 5, "value": "Limited Liability Company"}
  — For DROPDOWNS (<select>). The element list shows a select's choices after
    "options:". Pick one by its visible text. Do NOT click a <select> and hope
    options appear — they won't be in the element list; use select_option.
{"action": "click_coords", "x": 100, "y": 200}
{"action": "press", "key": "Enter"}
{"action": "scroll", "y": 500}
{"action": "wait", "seconds": 2}

## Batching for speed — IMPORTANT

When you are confident about several actions on the CURRENT page (e.g. filling
a login form: username, password, then submit), return them as a JSON ARRAY and
they run in one fast sequence without re-checking the page between each:

[{"action":"fill","index":0,"text":"folki"},
 {"action":"fill","index":1,"text":"secret"},
 {"action":"click","index":2}]

Rules for batching:
- Only batch actions that target elements ALREADY in the current element list.
- A navigate (or open_browser) ends the batch — put nothing useful after it,
  you'll get a fresh page to look at next.
- When unsure what happens next, return a SINGLE action and observe the result.

## How to target elements — IMPORTANT

After each page you receive a screenshot AND a list of interactive elements
with their screen coordinates, like:
  [0] <input text> "Login" @ (350,210)
  [1] <input password> @ (350,260)
  [2] <button> "Sign in" @ (350,320)

Look at the screenshot to understand the page, then act on an element by its
INDEX: {"action":"fill","index":1,"text":"folki"} or {"action":"click","index":2}.
We click the element's exact coordinates and type character-by-character with a
humanized mouse path — you do NOT need selectors or coordinates yourself.
If the element you need is not in the list, scroll or wait, then read the
refreshed list. As a last resort you may use click_coords with x,y you read
from the screenshot.

## Control actions

{"action": "notify_admin", "message": "What human help is needed"}
  — Sends Telegram alert with screenshot to admin. Use for one-way alerts
    (FYI, progress) where you do NOT need an answer back.

{"action": "ask_human", "question": "What should I do here?"}
  — Sends the question (with a screenshot) to the operator on Telegram and
    WAITS for their reply. Use this whenever you are unsure what to do, hit a
    decision point, an unfamiliar page, a CAPTCHA, or anything the task did not
    spell out. The operator's reply comes back as your next observation — then
    act on it. This is your main way to get guidance in manual-control mode.

{"action": "remember", "instruction": "On site X, the login button is the purple one at the bottom"}
  — Permanently save a piece of guidance you just learned (usually from an
    ask_human reply) so you follow it automatically in future runs. After the
    operator teaches you how to handle something, remember it.

{"action": "save_data", "data": {"name": "John Doe", "address": "...", "result": "registered"}}
  — Save COLLECTED DATA (results, extracted fields) to the run's data file.
    Use whenever you gather a piece of information worth keeping.

{"action": "mark_done", "index": 0}
  — Mark WORK QUEUE item [N] as completed so it is never processed again.
    Call this the moment you finish a queue item. (Items tagged (repeat) stay.)

{"action": "done", "result": "Summary of what was accomplished"}
  — Use when the entire task is complete.

{"action": "error", "message": "Why the task cannot be completed"}
  — Use when the task is impossible to complete.

## Tabs
If a click opens a NEW TAB, the system automatically switches you to it — the
next element list and screenshot are from the new tab. So after a click that you
expect opens a new page, just look at the refreshed elements and continue; do
NOT assume you are stuck on the old page.

## Manual-control behaviour
When the task is vague or you are not confident about the next step, prefer
ask_human over guessing. After the operator answers, do what they say AND, if
it is a reusable rule, remember it. Over time your LEARNED INSTRUCTIONS grow and
you ask less. Never loop blindly repeating a failing action — ask_human instead.

## Rules
1. Return ONLY valid JSON. One action per response.
2. Always set_proxy and open_browser before navigating — unless the browser is already open.
3. Use close_browser + open_browser to get a fresh session with a new IP when needed.
4. Target elements by their INDEX from the element list. Never invent selectors.
5. If the same action fails twice, the index is wrong — re-read the element list
   and pick a different index, or wait/scroll for the page to update.
6. Be patient — wait for pages to load before acting.
7. The full conversation history is your memory — refer back to previous steps.
"""


# ── Venice API call ───────────────────────────────────────────────────────────

def _call_venice(messages: list) -> str:
    payload = json.dumps({
        "model": VENICE_AGENT_MODEL,
        "messages": messages,
        "max_tokens": 600,
        "temperature": 0.2,
        "venice_parameters": {
            "include_venice_system_prompt": False,
            # qwen3 is a reasoning model: its <think> output expands to fill the
            # entire token budget, hits finish_reason=length, and never emits
            # `content` (that's the empty "Bad JSON"). disable_thinking turns
            # reasoning OFF — clean JSON, ~25 tokens instead of ~800.
            "disable_thinking": True,
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
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())

    msg = data["choices"][0]["message"]

    # content = actual response, reasoning_content = thinking process (separate)
    content = (msg.get("content") or "").strip()

    if not content:
        # Fallback: sometimes reasoning models emit only thinking tokens
        # Try to extract JSON from reasoning_content directly
        reasoning = (msg.get("reasoning_content") or msg.get("reasoning") or "").strip()
        if reasoning:
            # Pull last JSON block from reasoning as the intended action
            m = re.search(r'(\{[^{}]*"action"[^{}]*\})', reasoning[::-1])
            if m:
                content = m.group(1)[::-1]

    return content


def _strip_old_images(messages: list) -> None:
    """Replace screenshots in ALL messages except the most recent image-bearing
    one with a text placeholder. Old images dominate token cost and latency."""
    # find index of the last message that contains an image
    last_img = -1
    for i, m in enumerate(messages):
        c = m.get("content")
        if isinstance(c, list) and any(p.get("type") == "image_url" for p in c):
            last_img = i
    for i, m in enumerate(messages):
        if i == last_img:
            continue
        c = m.get("content")
        if isinstance(c, list):
            new = []
            for p in c:
                if p.get("type") == "image_url":
                    new.append({"type": "text", "text": "[screenshot omitted]"})
                else:
                    new.append(p)
            m["content"] = new


def _trim_history(messages: list, keep: int = 14) -> None:
    """Cap conversation length: keep system + task + the last `keep` messages."""
    if len(messages) <= keep + 2:
        return
    head = messages[:2]            # system prompt + original task
    tail = messages[-keep:]
    messages[:] = head + tail


def _parse_actions(raw: str) -> list:
    """Parse the model output into a LIST of action dicts. Accepts a single
    object, a top-level JSON array, or an {"actions":[...]} wrapper."""
    obj = _parse_action(raw)
    if obj is None:
        # Try a bare JSON array
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        if m:
            try:
                arr = json.loads(m.group())
                if isinstance(arr, list):
                    return [x for x in arr if isinstance(x, dict) and x.get("action")]
            except json.JSONDecodeError:
                pass
        return []
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict) and x.get("action")]
    if isinstance(obj, dict):
        if isinstance(obj.get("actions"), list):
            return [x for x in obj["actions"] if isinstance(x, dict) and x.get("action")]
        if obj.get("action"):
            return [obj]
    return []


def _parse_action(raw: str) -> Optional[dict]:
    """Extract JSON action from Venice response, even if wrapped in markdown."""
    raw = raw.strip()
    # Direct JSON
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # JSON inside ```...```
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # First { ... } block
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None


# ── Page state builder ────────────────────────────────────────────────────────

def _format_elements(items: list) -> str:
    """Human-readable element list WITH coordinates for the agent."""
    if not items:
        return "(no interactive elements found)"
    lines = []
    for it in items:
        desc = f"[{it['i']}] <{it['tag']}" + (f" {it['type']}" if it.get("type") else "") + ">"
        if it.get("label"):
            desc += f' "{it["label"]}"'
        if it.get("value"):
            desc += f' value="{it["value"]}"'
        if it.get("options"):
            desc += f' options: {it["options"]}'
        desc += f" @ ({it['x']},{it['y']})"
        lines.append(desc)
    return "\n".join(lines)


def _build_page_state(browser, use_screenshot: bool, on_log=None, save_png: str = "") -> dict:
    # If a click opened a new tab, follow it (CDP otherwise stays on the old tab)
    try:
        if browser.ensure_active_tab() and on_log:
            on_log("   ↪ switched to new tab")
    except Exception:
        pass
    # Make sure the visible bot-cursor is present (re-injects after navigation)
    try:
        browser.install_cursor()
    except Exception:
        pass

    url = browser.get_url()
    title = browser.get_title()

    # Scan elements WITH their pixel coordinates, captured at the same instant
    # as the screenshot. We stash the coord map on the browser so the executor
    # can click stored coordinates directly — no DOM lookup at action time.
    items = browser.scan_elements()
    browser._ai_elements = {it["i"]: (it["x"], it["y"]) for it in items}
    elements = _format_elements(items)

    # Capture any visible error/alert/toast text (login failures, validation…)
    try:
        notices = browser.notifications()
    except Exception:
        notices = ""

    if on_log:
        on_log(f"   page: {url[:60]} | {len(items)} elements")
        on_log(f"   elements:\n{elements[:700]}")
        if notices:
            on_log(f"   ⚠ page message: {notices[:200]}")

    notice_block = f"\nPAGE MESSAGES (errors/alerts): {notices}\n" if notices else ""

    instructions = (
        "Interactive elements below — each has an index [N] and its screen "
        "coordinates @ (x,y). To act on one, reference it by index N "
        '(e.g. {"action":"fill","index":0,"text":"..."} or '
        '{"action":"click","index":2}). You may also use click_coords with '
        "explicit x,y if you see a target in the screenshot that is not listed."
    )

    if use_screenshot:
        img_bytes = browser.screenshot()
        if save_png:
            try:
                with open(save_png, "wb") as f:
                    f.write(img_bytes)
            except Exception:
                pass
        img_b64 = base64.b64encode(img_bytes).decode()
        return {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                },
                {
                    "type": "text",
                    "text": (f"URL: {url}\nTitle: {title}\n{notice_block}\n{instructions}\n\n"
                             f"{elements}\n\nWhat is the next action?"),
                },
            ],
        }
    else:
        if save_png:
            try:
                with open(save_png, "wb") as f:
                    f.write(browser.screenshot())
            except Exception:
                pass
        return {
            "role": "user",
            "content": (f"URL: {url}\nTitle: {title}\n{notice_block}\n{instructions}\n\n"
                        f"{elements}\n\nNext action?"),
        }


# ── Operator pause/resume conversation ────────────────────────────────────────

_PAUSE_WORDS  = {"стоп", "стой", "пауза", "подожди", "stop", "pause", "wait", "hold"}
_RESUME_WORDS = {"продолжай", "продолжить", "дальше", "давай", "поехали",
                 "continue", "go", "resume", "proceed", "ok"}


def _operator_cmd(text: str) -> Optional[str]:
    """Classify an operator message: 'pause', 'resume', or None (normal msg)."""
    t = (text or "").strip().lower()
    first = t.split()[0] if t.split() else ""
    if first in _PAUSE_WORDS or t in _PAUSE_WORDS:
        return "pause"
    if first in _RESUME_WORDS or t in _RESUME_WORDS:
        return "resume"
    return None


def _answer_operator(messages: list) -> str:
    """Ask Venice for a plain-language reply to the operator (not a JSON action)."""
    tmp = messages + [{
        "role": "user",
        "content": ("The task is PAUSED and the operator is chatting with you. "
                    "Reply in plain natural language (NOT JSON, no action) — answer "
                    "their question or discuss. Be concise and in their language."),
    }]
    try:
        return _call_venice(tmp).strip() or "(пусто)"
    except Exception as e:
        return f"(ошибка ответа: {e})"


def _pause_dialogue(messages, on_log, stop_flag, on_tg_send, on_tg_wait, on_remember):
    """Operator wrote 'стоп': freeze the task and chat in Telegram. The agent
    answers questions in plain text and only returns (resumes) when the operator
    writes 'продолжай'. Everything discussed stays in `messages` so the agent
    continues informed."""
    if not (on_tg_send and on_tg_wait):
        on_log("Pause requested but Telegram chat not available.")
        return
    on_log("⏸ Paused by operator — dialogue mode. Send «продолжай» to resume.")
    on_tg_send("⏸ Остановился. Спрашивай и уточняй — отвечу. "
               "Напиши «продолжай», когда можно работать дальше.")
    while not stop_flag():
        msg = on_tg_wait()          # blocks for the next operator message
        if msg is None:
            continue
        cmd = _operator_cmd(msg)
        if cmd == "resume":
            messages.append({"role": "user", "content":
                "Operator reviewed and said CONTINUE. Resume the task now, applying "
                "everything we just discussed."})
            on_log("▶️ Resumed by operator.")
            on_tg_send("▶️ Продолжаю.")
            return
        if cmd == "pause":
            on_tg_send("Уже на паузе. Задавай вопросы или напиши «продолжай».")
            continue
        # A normal message — discuss it
        on_log(f"💬 Operator: {msg[:120]}")
        messages.append({"role": "user", "content": f"Operator (paused) says: {msg}"})
        answer = _answer_operator(messages)
        messages.append({"role": "assistant", "content": answer})
        on_log(f"🤖 Reply: {answer[:120]}")
        on_tg_send(answer)
        if on_remember:
            on_remember(f"Discussed while paused: {msg} -> {answer[:200]}")


# ── Main agentic loop ─────────────────────────────────────────────────────────

def run_agent(
    instruction: str,
    on_log: Callable[[str], None],
    on_notify: Callable[[str, bytes | None], None],
    stop_flag: Callable[[], bool],
    # Infrastructure callbacks — agent calls these when needed
    on_set_proxy: Callable[[str], bool],
    on_rotate_ip: Callable[[], bool],
    on_open_browser: Callable[[], dict],   # returns {"browser": CDPBrowser, "uuid": str} or {"error": ...}
    on_close_browser: Callable[[], None],
    get_browser: Callable[[], Optional[object]],  # returns current CDPBrowser or None
    # Manual-control callbacks (Telegram human-in-the-loop)
    on_ask_human: Callable[[str], Optional[str]] = None,   # ask via TG, returns reply
    on_remember: Callable[[str], None] = None,             # persist a learned instruction
    learned: str = "",                                     # preloaded learned instructions
    knowledge: str = "",                                   # operator notes always-on
    worklist: str = "",                                    # numbered queue of pending work items
    on_mark_done: Callable[[int], None] = None,            # mark a work item done
    # Recording
    on_save_data: Callable[[dict], None] = None,           # persist collected data
    record_dir: str = "",                                  # folder to save per-step screenshots
    # Live operator interrupts: returns a list of unsolicited Telegram messages
    on_operator_msgs: Callable[[], list] = None,
    on_tg_send: Callable[[str], None] = None,   # send a message to the operator
    on_tg_wait: Callable[[], Optional[str]] = None,  # block for next operator message
) -> str:
    import os as _os
    system = SYSTEM_PROMPT
    if knowledge.strip():
        system += ("\n\n## KNOWLEDGE BASE (operator notes, ALWAYS in memory — "
                   "use these facts/credentials/rules whenever relevant):\n"
                   + knowledge.strip())
    if worklist.strip():
        system += ("\n\n## WORK QUEUE — process these items one by one. Each has an "
                   "index [N]. When you FINISH an item, call "
                   '{"action":"mark_done","index":N} so it is never repeated. '
                   "Items tagged (repeat) must be done every run and stay in the "
                   "queue. Only these PENDING items remain (done ones are already "
                   "removed):\n" + worklist.strip())
    if learned.strip():
        system += ("\n\n## LEARNED INSTRUCTIONS (from the operator — these are your "
                   "standing rules, ALWAYS follow them, they override defaults):\n"
                   + learned.strip())

    messages = [
        {"role": "system", "content": system},
        {"role": "user",   "content": f"TASK:\n{instruction}"},
    ]

    on_log("Venice agent started.")
    on_log(f"Task: {instruction[:120]}...")
    if knowledge.strip():
        on_log(f"📚 Knowledge base loaded ({len(knowledge)} chars).")
    if worklist.strip():
        on_log(f"📋 Work queue: {worklist.count(chr(10)) + 1} pending item(s).")
    if learned.strip():
        # Recall remembered instructions out loud at every launch
        on_log(f"🧠 Recalled {learned.count(chr(10)) + 1} saved instruction(s):")
        for ln in [l for l in learned.splitlines() if l.strip()][:20]:
            on_log(f"     • {ln.strip()[:120]}")

    step = 0
    while True:
        step += 1
        if MAX_STEPS and step > MAX_STEPS:   # MAX_STEPS == 0 → unlimited
            break
        if stop_flag():
            on_log("Stopped by user.")
            return "Stopped"

        on_log(f"── Step {step}{('/' + str(MAX_STEPS)) if MAX_STEPS else ''} ──")

        # Live operator messages from Telegram since the last step.
        if on_operator_msgs:
            try:
                for msg in (on_operator_msgs() or []):
                    cmd = _operator_cmd(msg)
                    if cmd == "pause":
                        _pause_dialogue(messages, on_log, stop_flag,
                                        on_tg_send, on_tg_wait, on_remember)
                        if stop_flag():
                            on_log("Stopped by user.")
                            return "Stopped"
                    elif cmd == "resume":
                        pass  # nothing to resume when not paused
                    else:
                        on_log(f"📩 Operator interrupt: {msg[:120]}")
                        messages.append({
                            "role": "user",
                            "content": (f"OPERATOR INTERRUPT (follow this immediately, "
                                        f"it overrides your current plan): {msg}")
                        })
                        if on_remember:
                            on_remember(f"Operator standing instruction: {msg}")
            except Exception:
                pass

        browser = get_browser()

        # Build page state only if browser is open
        if browser is not None:
            try:
                save_png = (_os.path.join(record_dir, f"step_{step:03d}.png")
                            if record_dir else "")
                state_msg = _build_page_state(browser, USE_SCREENSHOT, on_log, save_png)
                messages.append(state_msg)
            except Exception as e:
                on_log(f"Page state error: {e}")
                messages.append({"role": "user", "content": f"Browser error: {e}. What next?"})
        else:
            messages.append({"role": "user", "content": "Browser is not open. What is the next action?"})

        # Speed/cost: keep only the LATEST screenshot in history (old images
        # cost the most tokens and slow every call); replace older ones with a
        # short text placeholder. Also cap history length.
        _strip_old_images(messages)
        _trim_history(messages, keep=14)

        # Ask Venice
        try:
            raw = _call_venice(messages)
            on_log(f"Venice → {raw[:200]}")
        except Exception as e:
            on_log(f"Venice API error: {e}")
            time.sleep(3)
            continue

        # Parse action(s) — the model may return ONE action or a LIST of them
        actions = _parse_actions(raw)
        if not actions:
            on_log(f"Bad JSON: {raw[:100]}")
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": "Invalid JSON. Return a valid JSON action."})
            continue

        messages.append({"role": "assistant", "content": json.dumps(actions)})

        results = []
        terminal = None  # set to a return value if done/error encountered

        for action in actions:
            if stop_flag():
                on_log("Stopped by user.")
                return "Stopped"

            a = action.get("action", "")
            browser = get_browser()  # refresh — a prior action may have opened it
            result = ""

            try:
                # ── Infrastructure ──────────────────────────────
                if a == "set_proxy":
                    state = action.get("state", "").lower()
                    ok = on_set_proxy(state)
                    result = f"Proxy set to {state}" if ok else f"set_proxy failed for {state}"

                elif a == "rotate_ip":
                    ok = on_rotate_ip()
                    result = "IP rotated" if ok else "IP rotation failed"

                elif a == "open_browser":
                    res = on_open_browser()
                    result = (f"open_browser failed: {res['error']}"
                              if "error" in res else "Browser opened and ready")

                elif a == "close_browser":
                    on_close_browser()
                    result = "Browser closed"

                # ── notify_admin works with or without browser ──
                elif a == "notify_admin":
                    msg = action.get("message", "Bot needs help")
                    img = None
                    if browser is not None:
                        try:
                            img = browser.screenshot()
                        except Exception:
                            pass
                    on_notify(msg, img)
                    result = f"Admin notified: {msg}"

                # ── ask_human: send question to TG, BLOCK for reply ──
                elif a == "ask_human":
                    q = action.get("question", action.get("message", "What should I do?"))
                    if on_ask_human is None:
                        result = "ask_human not available."
                    else:
                        on_log(f"⏳ Asking operator: {q[:120]}")
                        reply = on_ask_human(q)
                        if reply is None:
                            result = "No reply from operator (timed out or stopped)."
                        else:
                            on_log(f"Operator replied: {reply[:120]}")
                            result = f"Operator says: {reply}"

                # ── remember: persist a learned instruction ──────
                elif a == "remember":
                    note = action.get("instruction", action.get("note", "")).strip()
                    if note and on_remember:
                        on_remember(note)
                        result = f"Remembered: {note[:80]}"
                    else:
                        result = "Nothing to remember."

                # ── mark_done: mark a work-queue item complete ───
                elif a == "mark_done":
                    idx = action.get("index", action.get("item"))
                    if idx is not None and on_mark_done:
                        try:
                            on_mark_done(int(re.search(r'\d+', str(idx)).group()))
                            result = f"Marked work item {idx} done"
                        except Exception as e:
                            result = f"mark_done failed: {e}"
                    else:
                        result = "No index for mark_done."

                # ── save_data: persist collected data to file ────
                elif a == "save_data":
                    data = action.get("data", action.get("record"))
                    if data and on_save_data:
                        on_save_data(data if isinstance(data, dict) else {"value": data})
                        result = f"Data saved: {str(data)[:100]}"
                    else:
                        result = "Nothing to save."

                # ── Browser actions ─────────────────────────────
                elif a in ("navigate", "click", "click_coords", "fill",
                           "select_option", "select", "press", "scroll",
                           "wait", "screenshot"):
                    if browser is None:
                        result = "No browser open. Use open_browser first."
                    else:
                        result = _execute_browser_action(browser, action, on_notify)

                # ── Terminal ────────────────────────────────────
                elif a == "done":
                    terminal = action.get("result", "Done")
                    result = "Task completed."
                elif a == "error":
                    terminal = f"Error: {action.get('message', 'Unknown')}"
                    result = "Task failed."
                else:
                    result = f"Unknown action: {a}"

            except Exception as e:
                result = f"Execution error: {e}"

            on_log(f"→ {result}")
            results.append(f"{a}: {result}")

            # Stop the batch early if the page navigated or we hit a terminal —
            # remaining queued actions were planned for the OLD page state.
            if terminal is not None:
                break
            if a in ("navigate", "open_browser") and len(actions) > 1:
                results.append("(stopped batch — page changed, re-reading)")
                break

        if terminal is not None:
            on_log(terminal if terminal.startswith("Error") else "Task completed.")
            return terminal

        messages.append({"role": "user", "content": "Results: " + " | ".join(results)})

        # Casual user pauses to read the page before the next move
        time.sleep(reaction_delay())

    on_log("Max steps reached.")
    return "Max steps reached"


def _resolve_index(action: dict):
    """Extract the element index the model referenced (index/element/selector/
    target; '[2]', '2', 2, "[data-ai='2']")."""
    raw = action.get("index")
    if raw is None:
        raw = (action.get("element") or action.get("selector")
               or action.get("target"))
    if raw is None:
        return None
    m = re.search(r'\d+', str(raw))
    return int(m.group()) if m else None


def _norm_text(action: dict) -> str:
    for k in ("text", "value", "content", "input"):
        if k in action and action[k] is not None:
            return str(action[k])
    return ""


def _execute_browser_action(browser, action: dict, on_notify) -> str:
    a = action.get("action", "")

    if a == "navigate":
        browser.navigate(action["url"])
        browser.wait_for_load()
        return f"Navigated to {action['url']}"

    elif a == "click":
        idx = _resolve_index(action)
        if idx is None:
            return f"No element index in action: {action}"
        ok = browser.click_index(idx)
        time.sleep(reaction_delay())
        return f"Clicked element [{idx}]" if ok else f"Element [{idx}] not found"

    elif a == "click_coords":
        browser.click(action["x"], action["y"])
        time.sleep(reaction_delay())
        return f"Clicked ({action['x']}, {action['y']})"

    elif a == "fill":
        idx = _resolve_index(action)
        if idx is None:
            return f"No element index in action: {action}"
        ok = browser.fill_index(idx, _norm_text(action))
        time.sleep(random.uniform(0.3, 0.8))
        return f"Filled element [{idx}]" if ok else f"Element [{idx}] not found"

    elif a in ("select_option", "select"):
        idx = _resolve_index(action)
        val = _norm_text(action) or action.get("option", "")
        if idx is None:
            return f"No element index in action: {action}"
        ok = browser.select_option(idx, val)
        time.sleep(random.uniform(0.2, 0.5))
        return f"Selected '{val}' in [{idx}]" if ok else f"Option '{val}' not found in [{idx}]"

    elif a == "press":
        browser.press_key(action["key"])
        time.sleep(random.uniform(0.1, 0.25))
        return f"Pressed {action['key']}"

    elif a == "scroll":
        browser.scroll_to(y=int(action.get("y", 0)))
        return f"Scrolled to y={action.get('y')}"

    elif a == "wait":
        secs = min(float(action.get("seconds", 1)), 30)
        time.sleep(secs)
        return f"Waited {secs}s"

    elif a == "screenshot":
        return "Screenshot taken"

    elif a == "notify_admin":
        msg = action.get("message", "Bot needs help")
        try:
            img = browser.screenshot()
        except Exception:
            img = None
        on_notify(msg, img)
        return f"Admin notified: {msg}"

    return f"Unknown browser action: {a}"
