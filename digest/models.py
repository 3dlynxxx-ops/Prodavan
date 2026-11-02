from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Article:
    url: str
    title: str
    source: str
    published: datetime | None
    summary: str = ""            # опис із RSS (очищений від HTML)
    category: str = "other"      # категорія-підказка від джерела або ключових слів
    text: str = ""               # повний текст статті (якщо вдалося витягнути)
    prefilter_score: float = 0.0

    # Заповнюється після оцінки ШІ (або фолбеком без ШІ)
    score: float = 0.0
    title_uk: str = ""
    summary_uk: str = ""
    post_idea_uk: str = ""
    extra: dict = field(default_factory=dict)
