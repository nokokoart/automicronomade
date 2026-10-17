"""Estado persistente (qué se usó y cuándo) para no repetir contenido."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

HISTORY_LIMIT = 400


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = {"releases": {}, "tracks": {}, "history": [], "counters": {}}
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                # Un estado corrupto no debe frenar todo: arrancamos limpio pero dejamos copia.
                self.path.rename(self.path.with_suffix(".corrupt.json"))

    # -- consultas -----------------------------------------------------------
    def last_used(self, kind: str, key: str) -> str:
        """kind: 'releases' | 'tracks'. Devuelve ISO o '' si nunca se usó."""
        return self.data[kind].get(key, {}).get("last", "")

    def counter(self, name: str) -> int:
        return int(self.data["counters"].get(name, 0))

    # -- escritura -----------------------------------------------------------
    def mark(self, kind: str, key: str, label: str) -> None:
        entry = self.data[kind].setdefault(key, {"count": 0})
        entry["count"] += 1
        entry["last"] = now_iso()
        entry["label"] = label

    def bump(self, name: str) -> int:
        self.data["counters"][name] = self.counter(name) + 1
        return self.data["counters"][name]

    def log(self, entry: dict) -> None:
        entry = {"at": now_iso(), **entry}
        self.data["history"].append(entry)
        self.data["history"] = self.data["history"][-HISTORY_LIMIT:]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
