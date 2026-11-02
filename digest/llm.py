"""Оцінка релевантності, переклад і ідеї постів через Claude."""
from __future__ import annotations

import json
import logging
from typing import Literal

import anthropic
from pydantic import BaseModel

from .models import Article

log = logging.getLogger(__name__)

BATCH_SIZE = 10

Category = Literal["archviz", "video", "animation", "webdesign", "other"]


class Verdict(BaseModel):
    id: int
    relevance: int
    category: Category
    title_uk: str
    summary_uk: str
    post_idea_uk: str


class BatchVerdict(BaseModel):
    items: list[Verdict]


SYSTEM_PROMPT = """Ти редактор, який готує щоденний дайджест новин про ШІ в креативних індустріях.

Для кого дайджест:
{audience}

Для кожної статті:
- relevance: ціле число 0–10, наскільки стаття корисна цій аудиторії:
  9–10: конкретне застосування ШІ в архвізі чи креативному продакшні, реальні кейси, \
важливий реліз інструменту, яким можна користуватися вже зараз;
  6–8: помітна новина про ШІ-інструменти чи тренди в креативних індустріях;
  3–5: загальна новина про ШІ, з креативом пов'язана опосередковано;
  0–2: не про ШІ, реклама, вакансії, дубль, поверхневий контент.
- category: archviz, video, animation, webdesign або other.
- title_uk: короткий заголовок українською (до 90 символів), без клікбейту.
- summary_uk: 2–3 речення українською: що сталося і чому це важливо для креативників.
- post_idea_uk: одне речення: кут подачі для Instagram-поста (карусель, рілс, порівняння до/після тощо).

Пиши природною українською. Назви інструментів і компаній лишай в оригіналі.
Поверни результат для кожної статті з тим самим id."""


def _article_payload(idx: int, a: Article) -> dict:
    return {
        "id": idx,
        "title": a.title,
        "source": a.source,
        "category_hint": a.category,
        "published": a.published.isoformat() if a.published else None,
        "text": a.text or a.summary or "(текст недоступний, оцінюй за заголовком)",
    }


class Scorer:
    def __init__(self, model: str, audience: str):
        self.client = anthropic.Anthropic()
        self.model = model
        self.system = SYSTEM_PROMPT.format(audience=audience.strip())

    def _score_batch(self, batch: list[Article]) -> None:
        payload = [_article_payload(i, a) for i, a in enumerate(batch)]
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=self.system,
            output_config={"effort": "low"},
            messages=[{
                "role": "user",
                "content": "Оціни ці статті:\n\n" + json.dumps(payload, ensure_ascii=False, indent=1),
            }],
            output_format=BatchVerdict,
        )
        if response.stop_reason == "refusal":
            log.warning("Модель відмовилась обробляти пакет: %s", response.stop_details)
            return
        result = response.parsed_output
        if result is None:
            log.warning("Порожня відповідь моделі (stop_reason=%s)", response.stop_reason)
            return
        for v in result.items:
            if 0 <= v.id < len(batch):
                a = batch[v.id]
                a.score = max(0, min(10, v.relevance))
                a.category = v.category
                a.title_uk = v.title_uk.strip()
                a.summary_uk = v.summary_uk.strip()
                a.post_idea_uk = v.post_idea_uk.strip()
                a.extra["scored"] = True

    def score(self, articles: list[Article]) -> None:
        for start in range(0, len(articles), BATCH_SIZE):
            batch = articles[start:start + BATCH_SIZE]
            try:
                self._score_batch(batch)
            except anthropic.AuthenticationError:
                raise  # неправильний ключ: немає сенсу продовжувати
            except anthropic.APIStatusError as exc:
                log.error("Помилка API (%s) для пакета %d: %s", exc.status_code, start, exc.message)
            except anthropic.APIConnectionError as exc:
                log.error("Немає з'єднання з API для пакета %d: %s", start, exc)


def fallback_score(articles: list[Article]) -> None:
    """Режим без ШІ: оцінка = попередній бал за ключовими словами, текст в оригіналі."""
    for a in articles:
        a.score = min(10.0, a.prefilter_score + 4)
        a.title_uk = a.title
        a.summary_uk = (a.summary[:280] + "…") if len(a.summary) > 280 else a.summary
        a.post_idea_uk = ""
