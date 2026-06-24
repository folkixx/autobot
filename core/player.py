"""Flow player: executes recorded steps with human-like timing and mouse movement."""
import random
import threading
import time
from pathlib import Path
from typing import Callable

from .flow import Flow, Step
from .human_emulator import action_delay, keystroke_delay, mouse_path


class Player:
    def __init__(
        self,
        on_log: Callable[[str], None],
        on_done: Callable[[], None],
    ):
        self._on_log = on_log
        self._on_done = on_done
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def play(self, flow: Flow) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(flow,), daemon=True, name='autobot-player'
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ #

    def _log(self, msg: str) -> None:
        self._on_log(msg)

    def _run(self, flow: Flow) -> None:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=False,
                    args=['--start-maximized', '--disable-blink-features=AutomationControlled'],
                )
                ctx = browser.new_context(no_viewport=True)
                page = ctx.new_page()

                total = len(flow.steps)
                for idx, step in enumerate(flow.steps):
                    if self._stop.is_set():
                        self._log("Stopped by user.")
                        break
                    self._log(f"[{idx+1}/{total}] {step}")
                    try:
                        self._execute(page, step)
                    except Exception as e:
                        self._log(f"  Error: {e}")

                    if not self._stop.is_set():
                        action_delay(200, 1200)

                if not self._stop.is_set():
                    self._log("Flow completed successfully.")

                try:
                    browser.close()
                except Exception:
                    pass
        except Exception as e:
            self._log(f"Fatal error: {e}")
        finally:
            self._on_done()

    # ------------------------------------------------------------------ #

    def _execute(self, page, step: Step) -> None:
        if step.type == 'navigate':
            page.goto(step.url, wait_until='domcontentloaded', timeout=30_000)

        elif step.type == 'click':
            coords = self._resolve_coords(page, step)
            if coords:
                self._human_click(page, *coords)
            else:
                raise RuntimeError(f"Cannot find click target: {step.selector}")

        elif step.type == 'fill':
            loc = page.locator(step.selector).first
            loc.wait_for(state='visible', timeout=8_000)
            loc.click()
            time.sleep(random.uniform(0.08, 0.25))
            page.keyboard.press('Control+a')
            time.sleep(0.05)
            self._human_type(page, step.text or '')

        elif step.type == 'key_press':
            time.sleep(random.uniform(0.04, 0.12))
            page.keyboard.press(step.key)

        elif step.type == 'scroll':
            # Smooth scroll in increments
            target_y = step.scroll_y or 0
            current_y = page.evaluate("window.pageYOffset")
            steps_n = max(4, abs(target_y - current_y) // 80)
            for i in range(1, steps_n + 1):
                y = current_y + (target_y - current_y) * i / steps_n
                page.evaluate(f"window.scrollTo({step.scroll_x or 0}, {int(y)})")
                time.sleep(random.uniform(0.02, 0.06))

        elif step.type == 'wait':
            duration = (step.duration or 1000) / 1000
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline and not self._stop.is_set():
                time.sleep(0.1)

        elif step.type == 'screenshot':
            fname = step.filename or f"screenshot_{int(time.time())}.png"
            out = Path('screenshots') / fname
            out.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out), full_page=False)
            self._log(f"  Saved: {out}")

    def _resolve_coords(self, page, step: Step):
        """Return (cx, cy) for a click step, trying selector first."""
        if step.selector:
            try:
                loc = page.locator(step.selector).first
                loc.wait_for(state='visible', timeout=6_000)
                box = loc.bounding_box()
                if box:
                    return (box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
            except Exception:
                pass

        if step.x is not None and step.y is not None:
            return (step.x, step.y)
        return None

    def _human_click(self, page, cx: float, cy: float) -> None:
        """Move mouse along a Bezier curve then click."""
        sx = random.uniform(50, page.viewport_size['width'] - 50) if page.viewport_size else random.uniform(50, 750)
        sy = random.uniform(50, page.viewport_size['height'] - 50) if page.viewport_size else random.uniform(50, 450)
        path = mouse_path(sx, sy, cx, cy)

        for px, py in path[:-1]:
            page.mouse.move(px, py)
            time.sleep(random.uniform(0.002, 0.009))

        page.mouse.move(cx, cy)
        time.sleep(random.uniform(0.06, 0.18))
        # Occasionally move slightly and click
        if random.random() < 0.3:
            page.mouse.move(cx + random.uniform(-2, 2), cy + random.uniform(-2, 2))
            time.sleep(random.uniform(0.03, 0.08))
        page.mouse.click(cx, cy)

    def _human_type(self, page, text: str) -> None:
        """Type text character-by-character with variable inter-key delays."""
        for ch in text:
            page.keyboard.type(ch)
            time.sleep(keystroke_delay())
