from pathlib import Path
from typing import List
from .flow import Flow

FLOWS_DIR = Path(__file__).parent.parent / 'flows'


def _ensure() -> None:
    FLOWS_DIR.mkdir(parents=True, exist_ok=True)


def list_flows() -> List[Flow]:
    _ensure()
    flows = []
    for p in sorted(FLOWS_DIR.glob('*.json'), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            flows.append(Flow.load(p))
        except Exception:
            pass
    return flows


def save_flow(flow: Flow) -> None:
    _ensure()
    safe_name = ''.join(c if c.isalnum() or c in '-_ ' else '_' for c in flow.name)
    safe_name = safe_name.strip().replace(' ', '_') or 'flow'
    flow.save(FLOWS_DIR / f'{safe_name}.json')


def delete_flow(name: str) -> None:
    _ensure()
    for p in FLOWS_DIR.glob('*.json'):
        try:
            if Flow.load(p).name == name:
                p.unlink()
                return
        except Exception:
            pass


def get_flow(name: str) -> Flow:
    _ensure()
    for p in FLOWS_DIR.glob('*.json'):
        try:
            fl = Flow.load(p)
            if fl.name == name:
                return fl
        except Exception:
            pass
    raise FileNotFoundError(f"Flow not found: {name!r}")
