"""Форматування дайджесту і відправка в Telegram-канал."""
from __future__ import annotations

import html
import logging
from collections import defaultdict
from datetime import datetime

import requests

from .models import Article

log = logging.getLogger(__name__)

TG_LIMIT = 4096
SAFE_LIMIT = 3900  # запас під службові символи

MONTHS_UK = ["січня", "лютого", "березня", "квітня", "травня", "червня", "липня",
             "серпня", "вересня", "жовтня", "листопада", "грудня"]


def _esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def format_item(a: Article) -> str:
    title = a.title_uk or a.title
    meta = f"<i>{_esc(a.source)}</i>"
    if a.extra.get("scored"):  # оцінка має сенс лише якщо її поставив ШІ
        meta += f" · ⭐ {round(a.score)}/10"
    lines = [f'<b><a href="{html.escape(a.url, quote=True)}">{_esc(title)}</a></b>', meta]
    if a.summary_uk:
        lines.append(_esc(a.summary_uk))
    if a.post_idea_uk:
        lines.append(f"💡 <i>Ідея для поста:</i> {_esc(a.post_idea_uk)}")
    return "\n".join(lines)


def format_digest(articles: list[Article], categories: dict, today: datetime) -> list[str]:
    """Повертає список повідомлень (кожне ≤ ліміту Telegram), розбитих по межах статей."""
    header = f"🗞 <b>ШІ × Креатив: дайджест на {today.day} {MONTHS_UK[today.month - 1]}</b>"
    if not articles:
        return [header + "\n\nСьогодні нічого вартого уваги не знайшлося. Завтра спробуємо ще 🙂"]

    by_cat: dict[str, list[Article]] = defaultdict(list)
    for a in articles:
        by_cat[a.category if a.category in categories else "other"].append(a)

    blocks: list[str] = []
    for cat, meta in categories.items():
        items = by_cat.get(cat)
        if not items:
            continue
        blocks.append(f"{meta.get('emoji', '•')} <b>{_esc(meta.get('title', cat))}</b>")
        blocks.extend(format_item(a) for a in items)

    messages, current = [], header
    for block in blocks:
        candidate = f"{current}\n\n{block}"
        if len(candidate) > SAFE_LIMIT and current:
            messages.append(current)
            current = block
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages


def send_messages(token: str, chat_id: str, messages: list[str]) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for text in messages:
        resp = requests.post(url, timeout=30, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        })
        if not resp.ok:
            # Не друкуємо URL (там токен), лише відповідь Telegram
            raise RuntimeError(f"Telegram API {resp.status_code}: {resp.text[:500]}")
