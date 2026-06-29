"""Human-like mouse movement and typing helpers."""
import math
import random
import time
from typing import List, Tuple


def _bezier(p0, p1, p2, p3, t: float) -> Tuple[float, float]:
    """Cubic Bezier point at parameter t."""
    mt = 1 - t
    return (
        mt**3 * p0[0] + 3*mt**2*t * p1[0] + 3*mt*t**2 * p2[0] + t**3 * p3[0],
        mt**3 * p0[1] + 3*mt**2*t * p1[1] + 3*mt*t**2 * p2[1] + t**3 * p3[1],
    )


def mouse_path(x0: float, y0: float, x1: float, y1: float) -> List[Tuple[int, int]]:
    """Curved mouse path with randomised control points (human-like arc)."""
    dist = math.hypot(x1 - x0, y1 - y0)
    if dist < 2:
        return [(int(x1), int(y1))]

    jitter = min(dist * 0.35, 180)
    cp1 = (
        x0 + (x1 - x0) * 0.25 + random.uniform(-jitter, jitter),
        y0 + (y1 - y0) * 0.25 + random.uniform(-jitter, jitter),
    )
    cp2 = (
        x0 + (x1 - x0) * 0.75 + random.uniform(-jitter, jitter),
        y0 + (y1 - y0) * 0.75 + random.uniform(-jitter, jitter),
    )

    # Office-worker profile: brisk, fairly direct motion (still curved/eased,
    # but fewer points = snappier travel).
    steps = max(8, min(22, int(dist / 20)))
    path: List[Tuple[int, int]] = []
    for i in range(steps + 1):
        t = i / steps
        # ease-in-out
        t_e = t * t * (3 - 2 * t)
        px, py = _bezier((x0, y0), cp1, cp2, (x1, y1), t_e)
        path.append((int(px), int(py)))
    return path


# ── Behaviour profile ─────────────────────────────────────────────────────────
# "Office worker, ~30, on a computer all day, types constantly."
# Proficient and quick: fast touch typing, direct confident cursor, short
# reaction times — but still human (micro-variance, the odd brief pause).

# Runtime speed multipliers (the AI can change these live via set_speed).
# Lower = faster. 1.0 = the base "office worker" tempo above.
_TYPE_MULT = 1.0     # typing speed
_MOVE_MULT = 1.0     # cursor travel speed
_REACT_MULT = 1.0    # pause-before-next-action

_NAMED = {
    "instant": 0.1, "very_fast": 0.3, "fast": 0.55, "normal": 1.0,
    "slow": 1.8, "very_slow": 2.8,
}


def _factor(v) -> float:
    """Accept a number (multiplier) or a name ('fast'/'slow'/…)."""
    try:
        return max(0.05, float(v))
    except (TypeError, ValueError):
        return _NAMED.get(str(v).lower().strip(), 1.0)


def set_speed(typing=None, cursor=None, both=None) -> str:
    """Adjust tempo at runtime. Each arg is a name or a numeric multiplier
    (lower = faster). `both` sets typing+cursor+reaction together."""
    global _TYPE_MULT, _MOVE_MULT, _REACT_MULT
    if both is not None:
        f = _factor(both)
        _TYPE_MULT = _MOVE_MULT = _REACT_MULT = f
    if typing is not None:
        _TYPE_MULT = _factor(typing)
    if cursor is not None:
        _MOVE_MULT = _factor(cursor)
        _REACT_MULT = _factor(cursor)
    return f"typing×{_TYPE_MULT:.2f} cursor×{_MOVE_MULT:.2f}"


def move_step_delay() -> float:
    """Seconds between mouse path points — quick, confident travel."""
    return random.uniform(0.002, 0.006) * _MOVE_MULT


def keystroke_delay() -> float:
    """Seconds between keystrokes — a fast touch typist (~12-28 cps)."""
    r = random.random()
    if r < 0.04:
        base = random.uniform(0.15, 0.30)   # rare brief pause
    else:
        base = random.uniform(0.035, 0.08)
    return base * _TYPE_MULT


def reaction_delay() -> float:
    """Seconds before the next action — short, this user works fast."""
    r = random.random()
    base = random.uniform(1.2, 2.2) if r < 0.10 else random.uniform(0.3, 0.9)
    return base * _REACT_MULT


def action_delay(min_ms: int = 200, max_ms: int = 1200) -> None:
    """Pause between bot actions to mimic human reaction time."""
    time.sleep(random.uniform(min_ms, max_ms) / 1000)
