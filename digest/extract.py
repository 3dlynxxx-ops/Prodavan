"""Витягування повного тексту статті (для якісніших підсумків ШІ)."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import requests
import trafilatura

from .fetch import TIMEOUT, USER_AGENT
from .models import Article

log = logging.getLogger(__name__)

MAX_CHARS = 12000  # стелю ставимо лише проти аномально довгих сторінок


def _extract(article: Article) -> None:
    if article.extra.get("google_news") or not article.url.startswith("http"):
        return  # посилання Google News: це редирект, текст звідти не витягнеш
    try:
        resp = requests.get(article.url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        resp.raise_for_status()
        text = trafilatura.extract(resp.text, include_comments=False, include_tables=False)
        if text:
            article.text = text[:MAX_CHARS]
    except Exception as exc:
        log.info("Не вдалося витягнути текст %s: %s", article.url, exc)


def extract_all(articles: list[Article], workers: int = 8) -> None:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(_extract, articles))
