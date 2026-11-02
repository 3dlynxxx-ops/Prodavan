"""Пам'ять про вже оброблені статті (data/seen.json), щоб не повторюватись."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import ROOT
from .filters import normalize_title
from .models import Article

DEFAULT_STATE = ROOT / "data" / "seen.json"


def load_seen(path: Path = DEFAULT_STATE) -> dict[str, str]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def mark_seen(seen: dict[str, str], articles: list[Article], now: datetime | None = None) -> None:
    stamp = (now or datetime.now(timezone.utc)).date().isoformat()
    for a in articles:
        seen[a.url] = stamp
        seen[normalize_title(a.title)] = stamp


def save_seen(seen: dict[str, str], retention_days: int, path: Path = DEFAULT_STATE,
              now: datetime | None = None) -> None:
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=retention_days)).date().isoformat()
    pruned = {k: v for k, v in seen.items() if v >= cutoff}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(pruned.items())), f, ensure_ascii=False, indent=0)
        f.write("\n")
