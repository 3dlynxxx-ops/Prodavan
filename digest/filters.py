"""Попередній фільтр: свіжість, ключові слова, дублікати."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .models import Article


def _compile(term: str) -> re.Pattern:
    # Короткі абревіатури ВЕЛИКИМИ (AI, VFX, UI, 3D) шукаємо з урахуванням регістру,
    # щоб "AI" не ловило "ai" всередині слів чи імен.
    case_sensitive = len(term) <= 4 and term.upper() == term
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])", flags)


class KeywordMatcher:
    def __init__(self, keywords: dict):
        self.ai = [_compile(t) for t in keywords.get("ai", [])]
        self.creative = {cat: [_compile(t) for t in terms]
                         for cat, terms in (keywords.get("creative") or {}).items()}

    @staticmethod
    def _count(patterns: list[re.Pattern], text: str) -> int:
        return sum(1 for p in patterns if p.search(text))

    def ai_hits(self, text: str) -> int:
        return self._count(self.ai, text)

    def creative_hits(self, text: str) -> dict[str, int]:
        return {cat: self._count(pats, text) for cat, pats in self.creative.items()}


def normalize_title(title: str) -> str:
    return re.sub(r"[^\w]+", " ", title.lower()).strip()


def is_fresh(article: Article, max_age_hours: float, now: datetime | None = None) -> bool:
    if article.published is None:
        return False  # без дати не знаємо, чи стаття свіжа; пропускаємо
    now = now or datetime.now(timezone.utc)
    return now - article.published <= timedelta(hours=max_age_hours)


def prefilter(articles: list[Article], cfg: dict, seen: set[str],
              now: datetime | None = None) -> tuple[list[Article], dict]:
    """Повертає кандидатів, відсортованих за попередньою оцінкою, і статистику відсіву."""
    settings = cfg["settings"]
    categories = cfg["categories"]
    matcher = KeywordMatcher(cfg["keywords"])
    stats = {"total": len(articles), "stale": 0, "seen": 0, "keywords": 0, "duplicates": 0}

    candidates: list[Article] = []
    seen_titles: set[str] = set()
    seen_urls: set[str] = set()

    # Прямі джерела раніше за Google News: при дублі лишається пряме посилання
    ordered = sorted(articles, key=lambda a: a.extra.get("google_news", False))
    for a in ordered:
        if not is_fresh(a, settings["max_age_hours"], now):
            stats["stale"] += 1
            continue
        title_key = normalize_title(a.title)
        if a.url in seen or title_key in seen:
            stats["seen"] += 1
            continue
        if a.url in seen_urls or title_key in seen_titles:
            stats["duplicates"] += 1
            continue

        text = f"{a.title}\n{a.summary}"
        ai = matcher.ai_hits(text)
        creative = matcher.creative_hits(text)
        creative_total = sum(creative.values())
        require = a.extra.get("require", ["ai"])
        if ("ai" in require and ai == 0) or ("creative" in require and creative_total == 0):
            stats["keywords"] += 1
            continue

        # Якщо джерело загальне, категорію визначаємо за ключовими словами
        if a.category == "other" and creative_total:
            a.category = max(creative, key=creative.get)

        priority = categories.get(a.category, {}).get("priority", 1.0)
        a.prefilter_score = round((min(ai, 3) + min(creative_total, 3)) * priority, 2)
        seen_urls.add(a.url)
        seen_titles.add(title_key)
        candidates.append(a)

    candidates.sort(key=lambda a: (a.prefilter_score, a.published), reverse=True)
    return candidates[: settings["max_candidates"]], stats
