from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yaml"


def load_config(path: str | Path | None = None) -> dict:
    path = Path(path) if path else DEFAULT_CONFIG
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    cfg.setdefault("settings", {})
    cfg.setdefault("llm", {})
    cfg.setdefault("categories", {})
    cfg.setdefault("keywords", {"ai": [], "creative": {}})
    cfg.setdefault("google_news", {"enabled": False, "queries": []})
    cfg.setdefault("sources", [])

    s = cfg["settings"]
    s.setdefault("max_age_hours", 48)
    s.setdefault("top_n", 8)
    s.setdefault("min_score", 6)
    s.setdefault("max_candidates", 40)
    s.setdefault("send_empty", True)
    s.setdefault("seen_retention_days", 30)
    s.setdefault("timezone", "Europe/Kyiv")

    cfg["llm"].setdefault("model", "claude-haiku-5-5")
    cfg["llm"].setdefault("audience", "")

    if "other" not in cfg["categories"]:
        cfg["categories"]["other"] = {"title": "Інше", "emoji": "✨", "priority": 0.8}

    for src in cfg["sources"]:
        src.setdefault("enabled", True)
        src.setdefault("require", ["ai"])
        src.setdefault("category", "other")
    return cfg
