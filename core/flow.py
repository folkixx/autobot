from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import List, Optional
import json
from pathlib import Path
from datetime import datetime


@dataclass
class Step:
    type: str                        # navigate | click | fill | scroll | wait | screenshot | key_press
    url: Optional[str] = None        # navigate
    selector: Optional[str] = None   # click / fill
    x: Optional[int] = None          # click fallback coord
    y: Optional[int] = None
    text: Optional[str] = None       # fill / description
    scroll_x: Optional[int] = None   # scroll
    scroll_y: Optional[int] = None
    duration: Optional[int] = None   # wait (ms)
    filename: Optional[str] = None   # screenshot
    key: Optional[str] = None        # key_press

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, d: dict) -> Step:
        known = {k for k in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def __str__(self) -> str:
        if self.type == 'navigate':
            return f"Navigate → {self.url}"
        if self.type == 'click':
            target = self.selector or f"({self.x}, {self.y})"
            hint = f'  "{self.text}"' if self.text else ''
            return f"Click {target}{hint}"
        if self.type == 'fill':
            return f"Type \"{self.text}\" in {self.selector}"
        if self.type == 'scroll':
            return f"Scroll to ({self.scroll_x}, {self.scroll_y})"
        if self.type == 'wait':
            return f"Wait {self.duration} ms"
        if self.type == 'screenshot':
            return f"Screenshot → {self.filename}"
        if self.type == 'key_press':
            return f"Press [{self.key}]"
        return self.type


@dataclass
class Flow:
    name: str
    steps: List[Step] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    modified_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'steps': [s.to_dict() for s in self.steps],
            'created_at': self.created_at,
            'modified_at': self.modified_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Flow:
        return cls(
            name=d['name'],
            steps=[Step.from_dict(s) for s in d.get('steps', [])],
            created_at=d.get('created_at', ''),
            modified_at=d.get('modified_at', ''),
        )

    def save(self, path: Path) -> None:
        self.modified_at = datetime.now().isoformat()
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding='utf-8',
        )

    @classmethod
    def load(cls, path: Path) -> Flow:
        return cls.from_dict(json.loads(path.read_text(encoding='utf-8')))
