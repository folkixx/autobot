"""CDP Browser — controls Linken Sphere session via Chrome DevTools Protocol.

No Playwright, no Selenium. Pure WebSocket + HTTP CDP.
"""
import json
import time
import random
import base64
import threading
import urllib.request
import websocket   # websocket-client library
from core.human_emulator import mouse_path, keystroke_delay, action_delay


class CDPBrowser:
    """Thin CDP wrapper over a running Chromium-based browser."""

    def __init__(self, debug_port: int, connect_timeout: float = 20.0):
        self._port = debug_port
        self._ws: websocket.WebSocket | None = None
        self._msg_id = 0
        self._target_id = None
        self._lock = threading.Lock()
        self._connect_with_retry(connect_timeout)

    # ── Connection ────────────────────────────────────────────

    def _connect_with_retry(self, timeout: float):
        """The browser's debug port opens a moment AFTER the session starts —
        connecting instantly gives WinError 10061 (connection refused). Poll
        until the port is up, then connect."""
        deadline = time.monotonic() + timeout
        last_err = None
        while time.monotonic() < deadline:
            try:
                self._connect()
                return
            except Exception as e:
                last_err = e
                time.sleep(0.6)
        raise RuntimeError(f"CDP debug port {self._port} never became ready: {last_err}")

    def _connect(self):
        targets = self._get_targets()
        page = next((t for t in targets if t.get("type") == "page"), None)
        if not page:
            raise RuntimeError("No page target found in CDP")
        self._target_id = page.get("id")
        ws_url = page["webSocketDebuggerUrl"]
        # Chromium 111+ rejects CDP WebSocket connections whose Origin header
        # is not in the (empty-by-default) allowlist — that's the 403 Forbidden.
        # The check is SKIPPED entirely when no Origin header is present, so we
        # suppress it. websocket-client otherwise auto-adds Origin from the host.
        self._ws = websocket.create_connection(
            ws_url,
            timeout=15,
            suppress_origin=True,
        )

    def install_cursor(self):
        """Inject a VISIBLE cursor dot that follows the bot's mouse and flashes
        on click, so the operator can watch what the bot is doing. It's driven
        by the real mouse events the bot dispatches (display only — the actual
        interaction is genuine cursor movement)."""
        js = r"""
        (function(){
          function ensure(){
            var d = document.getElementById('__botCursor');
            if(!d){
              d = document.createElement('div');
              d.id='__botCursor';
              d.style.cssText='position:fixed;width:22px;height:22px;border-radius:50%;'+
                'background:rgba(255,60,60,0.55);border:2px solid #fff;'+
                'box-shadow:0 0 10px rgba(0,0,0,0.6);z-index:2147483647;'+
                'pointer-events:none;transform:translate(-50%,-50%);left:-50px;top:-50px;'+
                'transition:left .05s linear,top .05s linear,width .1s,height .1s,background .1s;';
              (document.body||document.documentElement).appendChild(d);
            }
            return d;
          }
          ensure();
          if(window.__botCursorListener) return;
          window.__botCursorListener=true;
          document.addEventListener('mousemove',function(e){
            var d=ensure(); d.style.left=e.clientX+'px'; d.style.top=e.clientY+'px';
          },true);
          document.addEventListener('mousedown',function(e){
            var d=ensure(); d.style.background='rgba(60,170,255,0.9)';
            d.style.width='32px'; d.style.height='32px';
            setTimeout(function(){ d.style.background='rgba(255,60,60,0.55)';
              d.style.width='22px'; d.style.height='22px'; },220);
          },true);
        })();
        """
        try:
            self._call("Runtime.evaluate", {"expression": js})
        except Exception:
            pass

    def maximize(self) -> bool:
        """Maximize the browser window to fill the screen (via CDP, not OS)."""
        try:
            win = self._call("Browser.getWindowForTarget",
                             {"targetId": self._target_id} if self._target_id else {})
            wid = win.get("windowId")
            if wid is None:
                return False
            # Setting to 'normal' first then 'maximized' makes it reliable
            self._call("Browser.setWindowBounds",
                       {"windowId": wid, "bounds": {"windowState": "maximized"}})
            return True
        except Exception:
            return False

    def _get_targets(self) -> list:
        url = f"http://127.0.0.1:{self._port}/json"
        with urllib.request.urlopen(url, timeout=8) as r:
            return json.loads(r.read())

    def close(self):
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    # ── CDP call ─────────────────────────────────────────────

    def _call(self, method: str, params: dict | None = None) -> dict:
        with self._lock:
            self._msg_id += 1
            msg = {"id": self._msg_id, "method": method, "params": params or {}}
            self._ws.send(json.dumps(msg))
            # Wait for matching response
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                raw = self._ws.recv()
                data = json.loads(raw)
                if data.get("id") == self._msg_id:
                    if "error" in data:
                        raise RuntimeError(f"CDP error: {data['error']}")
                    return data.get("result", {})
            raise TimeoutError(f"CDP timeout waiting for {method}")

    # ── Navigation ────────────────────────────────────────────

    def navigate(self, url: str):
        self._call("Page.navigate", {"url": url})
        time.sleep(random.uniform(0.4, 0.8))
        self.wait_for_load(timeout=8.0)

    def wait_for_load(self, timeout: float = 10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self._call("Runtime.evaluate",
                               {"expression": "document.readyState"})
            if state.get("result", {}).get("value") == "complete":
                return
            time.sleep(0.3)

    # ── Screenshot ────────────────────────────────────────────

    def screenshot(self) -> bytes:
        result = self._call("Page.captureScreenshot", {"format": "png", "quality": 80})
        return base64.b64decode(result["data"])

    # ── Accessibility tree (text, cheap) ──────────────────────

    def accessibility_tree(self) -> str:
        """Extract semantic page structure as compact text."""
        js = """
        (function() {
          function walk(el, depth) {
            if (!el || depth > 8) return '';
            var tag = el.tagName ? el.tagName.toLowerCase() : '';
            var skip = ['script','style','head','meta','link','noscript','svg'];
            if (skip.includes(tag)) return '';
            var role = el.getAttribute ? (el.getAttribute('role') || '') : '';
            var label = (el.getAttribute && (
              el.getAttribute('aria-label') ||
              el.getAttribute('placeholder') ||
              el.getAttribute('title') ||
              el.getAttribute('name') ||
              (el.tagName === 'A' ? el.textContent : '') ||
              (el.tagName === 'BUTTON' ? el.textContent : '') ||
              (el.tagName === 'INPUT' ? el.value : '')
            ) || '').trim().slice(0, 80);
            var id = el.id ? '#' + el.id : '';
            var line = tag ? ('[' + tag + (id||'') + (role?' role='+role:'') +
              (label?' "'+label+'"':'') + ']') : '';
            var children = '';
            if (el.children) {
              for (var i=0; i<Math.min(el.children.length,20); i++)
                children += walk(el.children[i], depth+1);
            }
            return line + children;
          }
          return walk(document.body, 0).slice(0, 8000);
        })()
        """
        result = self._call("Runtime.evaluate", {"expression": js})
        return result.get("result", {}).get("value", "")

    def scan_elements(self) -> list:
        """Scan the page (and same-origin iframes) for interactive elements.
        Returns a list of dicts: {i, tag, type, label, value, x, y} where x,y
        are EXACT viewport pixel coordinates (iframe offset included), captured
        at the same instant as the screenshot. The agent picks an element by
        index; we click its stored x,y directly — no DOM lookup at action time,
        so SPA re-renders can't break it."""
        js = r"""
        (function() {
          var sel = 'input,textarea,select,button,a[href],[role=button],[onclick]';
          var out = [];
          function scan(doc, offX, offY) {
            var nodes;
            try { nodes = doc.querySelectorAll(sel); } catch(e) { return; }
            for (var i = 0; i < nodes.length; i++) {
              var el = nodes[i];
              var r = el.getBoundingClientRect();
              if (r.width < 2 || r.height < 2) continue;
              var win = el.ownerDocument.defaultView || window;
              var st = win.getComputedStyle(el);
              if (st.display === 'none' || st.visibility === 'hidden') continue;
              var tag = el.tagName.toLowerCase();
              out.push({
                tag: tag,
                type: el.getAttribute('type') || '',
                label: (el.getAttribute('placeholder') || el.getAttribute('name') ||
                        (el.textContent||'').trim() || el.getAttribute('aria-label') || '').slice(0,50),
                value: (el.value || '').slice(0, 30),
                x: Math.round(offX + r.left + r.width/2),
                y: Math.round(offY + r.top + r.height/2)
              });
            }
            var frames = doc.querySelectorAll('iframe,frame');
            for (var j = 0; j < frames.length; j++) {
              try {
                var fdoc = frames[j].contentDocument;
                if (!fdoc) continue;
                var fr = frames[j].getBoundingClientRect();
                scan(fdoc, offX + fr.left, offY + fr.top);
              } catch(e) {}
            }
          }
          scan(document, 0, 0);
          return JSON.stringify(out);
        })()
        """
        result = self._call("Runtime.evaluate", {"expression": js})
        raw = result.get("result", {}).get("value", "") or "[]"
        try:
            items = json.loads(raw)
        except Exception:
            items = []
        for idx, it in enumerate(items):
            it["i"] = idx
        return items

    # Same element selector used by the snapshot — keep in ONE place so the
    # numbering in interactive_snapshot() and _coords_by_index() always match.
    _INTERACTIVE_SEL = "input,textarea,select,button,a[href],[role=button],[onclick]"

    def _coords_by_index(self, index: int):
        """Re-scan the page LIVE and return viewport coords of the index-th
        visible interactive element — same order as interactive_snapshot().
        This re-queries at action time, so it survives SPA re-renders that
        would wipe a previously-set data-ai attribute (the real bug)."""
        js = f"""
        (function() {{
          var sel = {json.dumps(self._INTERACTIVE_SEL)};
          var want = {int(index)};
          var n = 0, hit = null;
          function scan(doc, offX, offY) {{
            var nodes;
            try {{ nodes = doc.querySelectorAll(sel); }} catch(e) {{ return; }}
            for (var i = 0; i < nodes.length && hit === null; i++) {{
              var el = nodes[i];
              var r = el.getBoundingClientRect();
              if (r.width < 2 || r.height < 2) continue;
              var win = el.ownerDocument.defaultView || window;
              var st = win.getComputedStyle(el);
              if (st.display === 'none' || st.visibility === 'hidden') continue;
              if (n === want) {{
                hit = {{x: offX + r.left + r.width/2, y: offY + r.top + r.height/2}};
                return;
              }}
              n++;
            }}
            var frames = doc.querySelectorAll('iframe,frame');
            for (var j = 0; j < frames.length && hit === null; j++) {{
              try {{
                var fdoc = frames[j].contentDocument;
                if (!fdoc) continue;
                var fr = frames[j].getBoundingClientRect();
                scan(fdoc, offX + fr.left, offY + fr.top);
              }} catch(e) {{}}
            }}
          }}
          scan(document, 0, 0);
          return hit;
        }})()
        """
        result = self._call("Runtime.evaluate", {"expression": js})
        return result.get("result", {}).get("value")

    def _find_element_coords(self, selector: str):
        """Resolve a selector to coords. If it's a data-ai index reference
        ([data-ai='N'] or bare N), re-scan live by index (re-render proof).
        Otherwise fall back to a plain querySelector across iframes."""
        import re as _re
        m = _re.search(r"data-ai=['\"]?(\d+)", selector)
        if not m:
            m = _re.fullmatch(r"\s*(\d+)\s*", selector)
        if m:
            return self._coords_by_index(int(m.group(1)))

        # Plain CSS selector path (rarely used now)
        js = f"""
        (function() {{
          var want = {json.dumps(selector)};
          function search(doc, offX, offY) {{
            var el;
            try {{ el = doc.querySelector(want); }} catch(e) {{ el = null; }}
            if (el) {{
              var r = el.getBoundingClientRect();
              return {{x: offX + r.left + r.width/2, y: offY + r.top + r.height/2}};
            }}
            var frames = doc.querySelectorAll('iframe,frame');
            for (var j = 0; j < frames.length; j++) {{
              try {{
                var fdoc = frames[j].contentDocument;
                if (!fdoc) continue;
                var fr = frames[j].getBoundingClientRect();
                var hit = search(fdoc, offX + fr.left, offY + fr.top);
                if (hit) return hit;
              }} catch(e) {{}}
            }}
            return null;
          }}
          return search(document, 0, 0);
        }})()
        """
        result = self._call("Runtime.evaluate", {"expression": js})
        return result.get("result", {}).get("value")

    def notifications(self) -> str:
        """Capture visible alert / error / toast text so login failures and
        validation messages are seen instead of guessed at."""
        js = r"""
        (function() {
          var sel = '[role=alert],[role=status],.error,.alert,.toast,.notification,'
                  + '.message,.invalid-feedback,.form-error,.help-block,[class*=error],[class*=toast]';
          var seen = {};
          var out = [];
          document.querySelectorAll(sel).forEach(function(el){
            var r = el.getBoundingClientRect();
            if (r.width < 1 || r.height < 1) return;
            var st = window.getComputedStyle(el);
            if (st.display==='none'||st.visibility==='hidden'||st.opacity==='0') return;
            var t = (el.innerText||el.textContent||'').trim().slice(0,200);
            if (t && !seen[t]) { seen[t]=1; out.push(t); }
          });
          return out.join(' | ');
        })()
        """
        result = self._call("Runtime.evaluate", {"expression": js})
        return result.get("result", {}).get("value", "") or ""

    def get_url(self) -> str:
        result = self._call("Runtime.evaluate", {"expression": "window.location.href"})
        return result.get("result", {}).get("value", "")

    def get_title(self) -> str:
        result = self._call("Runtime.evaluate", {"expression": "document.title"})
        return result.get("result", {}).get("value", "")

    # ── Mouse ─────────────────────────────────────────────────

    def mouse_move(self, x: float, y: float):
        self._call("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": x, "y": y,
        })

    def click(self, x: float, y: float):
        """Human-like curved move (from last cursor position) then click."""
        sx, sy = getattr(self, "_last_xy", None) or (
            random.uniform(100, 900), random.uniform(100, 600))
        pts = mouse_path(sx, sy, x, y)
        for px, py in pts[:-1]:
            self.mouse_move(px, py)
            time.sleep(random.uniform(0.001, 0.003))
        self.mouse_move(x, y)
        self._last_xy = (x, y)
        time.sleep(random.uniform(0.02, 0.06))
        # `buttons: 1` (left button bitmask) is required for many frameworks to
        # treat this as a genuine click and fire onClick/submit handlers.
        self._call("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y,
            "button": "left", "buttons": 1, "clickCount": 1,
        })
        time.sleep(random.uniform(0.02, 0.06))
        self._call("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y,
            "button": "left", "buttons": 1, "clickCount": 1,
        })

    def click_selector(self, selector: str) -> bool:
        """Find element (across iframes) and click its center with a human path."""
        coords = self._find_element_coords(selector)
        if not coords:
            return False
        self.click(coords["x"], coords["y"])
        return True

    # ── Keyboard ──────────────────────────────────────────────

    # Characters whose key events carry a usable virtual-key code
    _VK = {
        **{c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz"},
        **{c: ord(c) for c in "0123456789"},
    }

    def type_text(self, text: str):
        """Type text char-by-char with human delays.

        keyDown/keyUp carry the virtual-key code but NO `text` (so they fire
        keydown/keyup for listeners & anti-fraud WITHOUT inserting a char),
        and Input.insertText commits exactly one character. This avoids both
        the old "nothing inserted" bug and any double-insertion."""
        for ch in text:
            vk = self._VK.get(ch.lower(), 0)
            self._call("Input.dispatchKeyEvent", {
                "type": "keyDown",
                "key": ch,
                "windowsVirtualKeyCode": vk,
                "nativeVirtualKeyCode": vk,
            })
            self._call("Input.insertText", {"text": ch})
            self._call("Input.dispatchKeyEvent", {
                "type": "keyUp",
                "key": ch,
                "windowsVirtualKeyCode": vk,
                "nativeVirtualKeyCode": vk,
            })
            time.sleep(keystroke_delay())

    def press_key(self, key: str):
        """Press a special key (Enter, Tab, Escape, etc.)."""
        self._call("Input.dispatchKeyEvent", {
            "type": "keyDown", "key": key,
        })
        time.sleep(0.05)
        self._call("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": key,
        })

    def fill_at(self, x: float, y: float, text: str) -> bool:
        """Click a coordinate (human path), clear the field, type text."""
        self.click(x, y)
        time.sleep(random.uniform(0.04, 0.1))
        # Select all + delete whatever is there
        self._call("Input.dispatchKeyEvent",
                   {"type": "keyDown", "key": "a", "modifiers": 8})  # Ctrl+A
        self._call("Input.dispatchKeyEvent",
                   {"type": "keyUp", "key": "a", "modifiers": 8})
        time.sleep(0.03)
        self.press_key("Backspace")
        time.sleep(0.03)
        self.type_text(text)
        return True

    def fill(self, selector: str, text: str) -> bool:
        """Legacy selector-based fill (kept for compatibility)."""
        coords = self._find_element_coords(selector)
        if not coords:
            return False
        return self.fill_at(coords["x"], coords["y"], text)

    # ── Index-based actions (coordinate-independent, reliable) ─────

    def _locate_index(self, index: int, action: str) -> dict | None:
        """Re-scan the page live (same order as scan_elements), find the Nth
        visible interactive element, scroll it into view, and perform `action`
        ('focus' or 'click') ON IT VIA JS — independent of click coordinates.
        Returns {x,y,tag} (post-scroll viewport coords for a humanized mouse
        move) or None if not found."""
        do_click = "el.click();" if action == "click" else ""
        do_focus = "try { el.focus(); } catch(e) {}" if action == "focus" else ""
        js = f"""
        (function() {{
          var sel = {json.dumps(self._INTERACTIVE_SEL)};
          var want = {int(index)}, n = 0, res = null;
          function scan(doc, ox, oy) {{
            var nodes;
            try {{ nodes = doc.querySelectorAll(sel); }} catch(e) {{ return; }}
            for (var i = 0; i < nodes.length && res === null; i++) {{
              var el = nodes[i], r = el.getBoundingClientRect();
              if (r.width < 2 || r.height < 2) continue;
              var st = (el.ownerDocument.defaultView || window).getComputedStyle(el);
              if (st.display === 'none' || st.visibility === 'hidden') continue;
              if (n === want) {{
                el.scrollIntoView({{block: 'center', inline: 'center'}});
                {do_focus}
                {do_click}
                var rr = el.getBoundingClientRect();
                res = {{x: ox + rr.left + rr.width/2, y: oy + rr.top + rr.height/2,
                        tag: el.tagName.toLowerCase()}};
                return;
              }}
              n++;
            }}
            var fr = doc.querySelectorAll('iframe,frame');
            for (var j = 0; j < fr.length && res === null; j++) {{
              try {{
                var fd = fr[j].contentDocument; if (!fd) continue;
                var b = fr[j].getBoundingClientRect();
                scan(fd, ox + b.left, oy + b.top);
              }} catch(e) {{}}
            }}
          }}
          scan(document, 0, 0);
          return res;
        }})()
        """
        return self._call("Runtime.evaluate", {"expression": js}).get("result", {}).get("value")

    def _set_value_index(self, index: int, text: str) -> bool:
        """Fallback: set the Nth element's value directly + fire input/change
        events so frameworks (React/Vue) register it. Used if typing didn't
        land in the field."""
        js = f"""
        (function() {{
          var sel = {json.dumps(self._INTERACTIVE_SEL)};
          var want = {int(index)}, n = 0, done = false;
          var val = {json.dumps(text)};
          function scan(doc) {{
            var nodes; try {{ nodes = doc.querySelectorAll(sel); }} catch(e) {{ return; }}
            for (var i = 0; i < nodes.length && !done; i++) {{
              var el = nodes[i], r = el.getBoundingClientRect();
              if (r.width < 2 || r.height < 2) continue;
              var st = (el.ownerDocument.defaultView || window).getComputedStyle(el);
              if (st.display === 'none' || st.visibility === 'hidden') continue;
              if (n === want) {{
                var proto = el.tagName === 'TEXTAREA'
                  ? window.HTMLTextAreaElement.prototype
                  : window.HTMLInputElement.prototype;
                var setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
                setter.call(el, val);
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                done = true; return;
              }}
              n++;
            }}
            var fr = doc.querySelectorAll('iframe,frame');
            for (var j = 0; j < fr.length && !done; j++) {{
              try {{ if (fr[j].contentDocument) scan(fr[j].contentDocument); }} catch(e) {{}}
            }}
          }}
          scan(document);
          return done;
        }})()
        """
        return bool(self._call("Runtime.evaluate", {"expression": js}).get("result", {}).get("value"))

    def _value_index(self, index: int) -> str:
        """Read the Nth element's current value (to verify a fill worked)."""
        js = f"""
        (function() {{
          var sel = {json.dumps(self._INTERACTIVE_SEL)};
          var want = {int(index)}, n = 0, v = null;
          function scan(doc) {{
            var nodes; try {{ nodes = doc.querySelectorAll(sel); }} catch(e) {{ return; }}
            for (var i = 0; i < nodes.length && v === null; i++) {{
              var el = nodes[i], r = el.getBoundingClientRect();
              if (r.width < 2 || r.height < 2) continue;
              var st = (el.ownerDocument.defaultView || window).getComputedStyle(el);
              if (st.display === 'none' || st.visibility === 'hidden') continue;
              if (n === want) {{ v = el.value || ''; return; }}
              n++;
            }}
            var fr = doc.querySelectorAll('iframe,frame');
            for (var j = 0; j < fr.length && v === null; j++) {{
              try {{ if (fr[j].contentDocument) scan(fr[j].contentDocument); }} catch(e) {{}}
            }}
          }}
          scan(document);
          return v;
        }})()
        """
        return self._call("Runtime.evaluate", {"expression": js}).get("result", {}).get("value") or ""

    def click_index(self, index: int) -> bool:
        """Click the Nth interactive element reliably (JS click by index) with
        a humanized mouse move to its position for realism."""
        info = self._locate_index(index, "click")
        if not info:
            return False
        # Visual/anti-fraud mouse movement to the element (JS already clicked)
        try:
            self._move_only(info["x"], info["y"])
        except Exception:
            pass
        return True

    def fill_index(self, index: int, text: str) -> bool:
        """Fill the Nth interactive element reliably: focus by index (JS),
        humanized mouse move, type via insertText, then verify and fall back to
        a direct value-set if the field is still empty."""
        info = self._locate_index(index, "focus")
        if not info:
            return False
        try:
            self._move_only(info["x"], info["y"])
        except Exception:
            pass
        # Clear any existing value
        self._call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "modifiers": 8})
        self._call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "modifiers": 8})
        self.press_key("Backspace")
        time.sleep(0.03)
        self.type_text(text)
        # Verify it landed; if not, force it via JS + events
        if self._value_index(index).strip() != text.strip():
            self._set_value_index(index, text)
        return True

    def _move_only(self, x: float, y: float):
        """Humanized mouse move WITHOUT a click (won't blur a focused field)."""
        sx, sy = getattr(self, "_last_xy", None) or (
            random.uniform(100, 900), random.uniform(100, 600))
        for px, py in mouse_path(sx, sy, x, y)[:-1]:
            self.mouse_move(px, py)
            time.sleep(random.uniform(0.001, 0.003))
        self.mouse_move(x, y)
        self._last_xy = (x, y)

    # ── Scroll ────────────────────────────────────────────────

    def scroll_to(self, x: int = 0, y: int = 0):
        steps = max(4, abs(y) // 100)
        current_y = int(self._call("Runtime.evaluate",
                                   {"expression": "window.pageYOffset"})
                        .get("result", {}).get("value", 0))
        for i in range(1, steps + 1):
            ty = current_y + (y - current_y) * i // steps
            self._call("Runtime.evaluate",
                       {"expression": f"window.scrollTo({x},{ty})"})
            time.sleep(random.uniform(0.02, 0.06))

    # ── JS eval ───────────────────────────────────────────────

    def evaluate(self, expression: str):
        result = self._call("Runtime.evaluate", {"expression": expression})
        return result.get("result", {}).get("value")
