"""Browser recorder: injects JS event listeners and collects user actions."""
import threading
from typing import Callable

# Injected into every page via add_init_script (survives navigation).
# Password fields are deliberately excluded.
_RECORD_JS = r"""
(function () {
  if (window.__autobot_v1) return;
  window.__autobot_v1 = true;

  /* ---- selector builder ---- */
  function cssEscape(str) {
    if (typeof CSS !== 'undefined' && CSS.escape) return CSS.escape(str);
    return str.replace(/([^a-zA-Z0-9_-])/g, '\\$1');
  }

  function getSelector(el) {
    if (!el || el.nodeType !== 1) return 'body';

    // Fast path: unique id
    if (el.id) return '#' + cssEscape(el.id);

    // Attribute-based unique selector
    var attrs = ['data-testid', 'data-cy', 'aria-label', 'name', 'placeholder', 'role'];
    for (var a = 0; a < attrs.length; a++) {
      var v = el.getAttribute(attrs[a]);
      if (v) {
        var s = el.tagName.toLowerCase() + '[' + attrs[a] + '="' + v.replace(/"/g, '\\"') + '"]';
        try { if (document.querySelectorAll(s).length === 1) return s; } catch (e) {}
      }
    }

    // Walk up the DOM (max 6 levels)
    var parts = [];
    var cur = el;
    while (cur && cur !== document.documentElement && parts.length < 6) {
      if (cur.id) { parts.unshift('#' + cssEscape(cur.id)); break; }

      var tag = cur.tagName.toLowerCase();
      var cls = '';
      if (cur.className && typeof cur.className === 'string') {
        var stable = cur.className.trim().split(/\s+/)
          .filter(function (c) {
            return c && !/^(active|hover|focus|selected|open|show|disabled|visible|hidden|is-)/.test(c);
          }).slice(0, 2);
        if (stable.length) cls = '.' + stable.join('.');
      }
      var nth = 1;
      var sib = cur.previousElementSibling;
      while (sib) { if (sib.tagName === cur.tagName) nth++; sib = sib.previousElementSibling; }
      parts.unshift(tag + cls + (nth > 1 ? ':nth-of-type(' + nth + ')' : ''));
      cur = cur.parentElement;
    }

    var sel = parts.join(' > ');
    try { if (sel && document.querySelectorAll(sel).length === 1) return sel; } catch (e) {}
    return sel || el.tagName.toLowerCase();
  }

  /* ---- click ---- */
  document.addEventListener('click', function (e) {
    try {
      var t = e.target;
      var label = (t.innerText || t.value || t.getAttribute('aria-label') || '').trim().slice(0, 60);
      window.__autobotRecord({
        type: 'click',
        selector: getSelector(t),
        x: Math.round(e.clientX),
        y: Math.round(e.clientY),
        tag: t.tagName.toLowerCase(),
        text: label
      });
    } catch (ex) {}
  }, true);

  /* ---- fill (debounced, ignores password) ---- */
  var _fillTimers = {};
  document.addEventListener('input', function (e) {
    try {
      var t = e.target;
      if ((t.type || '').toLowerCase() === 'password') return;  // never record passwords
      var sel = getSelector(t);
      clearTimeout(_fillTimers[sel]);
      var val = t.value;
      _fillTimers[sel] = setTimeout(function () {
        window.__autobotRecord({ type: 'fill', selector: sel, value: val });
      }, 600);
    } catch (ex) {}
  }, true);

  /* ---- special keys ---- */
  var SPECIAL = ['Enter','Tab','Escape','F5',
                 'ArrowUp','ArrowDown','ArrowLeft','ArrowRight',
                 'Backspace','Delete','Home','End','PageUp','PageDown'];
  document.addEventListener('keydown', function (e) {
    try {
      if (SPECIAL.indexOf(e.key) !== -1)
        window.__autobotRecord({ type: 'key_press', key: e.key });
    } catch (ex) {}
  }, true);

  /* ---- scroll (debounced) ---- */
  var _scrollTimer = null;
  window.addEventListener('scroll', function () {
    clearTimeout(_scrollTimer);
    _scrollTimer = setTimeout(function () {
      window.__autobotRecord({
        type: 'scroll',
        scroll_x: Math.round(window.pageXOffset),
        scroll_y: Math.round(window.pageYOffset)
      });
    }, 400);
  }, { capture: true, passive: true });

})();
"""


class Recorder:
    """Launches a visible Chromium window, injects event listeners,
    and forwards captured actions via callbacks."""

    def __init__(
        self,
        on_step: Callable[[dict], None],
        on_navigate: Callable[[dict], None],
        on_stop: Callable[[], None],
    ):
        self.on_step = on_step
        self.on_navigate = on_navigate
        self.on_stop = on_stop
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_base_url = ''

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name='autobot-recorder')
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright  # local import keeps startup fast
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=False,
                    args=['--start-maximized', '--disable-blink-features=AutomationControlled'],
                )
                ctx = browser.new_context(no_viewport=True)
                page = ctx.new_page()

                # expose_function survives navigations automatically
                page.expose_function('__autobotRecord', self._handle_action)
                page.add_init_script(_RECORD_JS)

                page.on('framenavigated', self._make_nav_handler(page))
                page.goto('about:blank')

                while not self._stop_event.is_set():
                    try:
                        if page.is_closed() or not browser.is_connected():
                            break
                        page.wait_for_timeout(150)
                    except Exception:
                        break

                try:
                    browser.close()
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            self.on_stop()

    def _handle_action(self, data: dict) -> None:
        self.on_step(data)

    def _make_nav_handler(self, page):
        def _on_nav(frame):
            try:
                if frame != page.main_frame:
                    return
                url = page.url
                if not url or url.startswith('about:'):
                    return
                base = url.split('#')[0]
                if base == self._last_base_url:
                    return
                self._last_base_url = base
                self.on_navigate({'type': 'navigate', 'url': url})
            except Exception:
                pass
        return _on_nav
