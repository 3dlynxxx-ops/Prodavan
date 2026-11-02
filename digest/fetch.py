"""Завантаження RSS/Atom-стрічок і перетворення їх на Article."""
from __future__ import annotations

import calendar
import html
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

import feedparser
import requests

from .models import Article

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; ai-creative-digest/0.1; +https://github.com/)"
TIMEOUT = 25

_TRACKING_PARAMS = re.compile(r"^(utm_|fbclid$|gclid$|mc_cid$|mc_eid$|ref$|ref_src$|cmpid$)")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_html(text: str) -> str:
    text = _TAG_RE.sub(" ", text or "")
    return _WS_RE.sub(" ", html.unescape(text)).strip()


def normalize_url(url: str) -> str:
    """Прибирає utm-мітки, фрагменти й кінцевий слеш, щоб дублікати мали однаковий ключ."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not _TRACKING_PARAMS.match(k.lower())]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def google_news_url(query: str, gn: dict) -> str:
    q = quote_plus(f"{query} when:2d")
    return (f"https://news.google.com/rss/search?q={q}"
            f"&hl={gn.get('hl', 'en-US')}&gl={gn.get('gl', 'US')}&ceid={gn.get('ceid', 'US:en')}")


def _read(url: str) -> bytes:
    if url.startswith("file://"):
        return Path(url[len("file://"):]).read_bytes()
    if "://" not in url and Path(url).exists():
        return Path(url).read_bytes()
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.content


def _entry_date(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        t = entry.get(key)
        if t:
            return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc)
    return None


def parse_feed(raw: bytes, source: dict) -> list[Article]:
    feed = feedparser.parse(raw)
    if feed.bozo and not feed.entries:
        raise ValueError(f"не схоже на RSS/Atom: {feed.get('bozo_exception')}")

    is_gnews = source.get("kind") == "google_news"
    articles = []
    for e in feed.entries:
        link = e.get("link")
        title = clean_html(e.get("title", ""))
        if not link or not title:
            continue
        source_name = source["name"]
        if is_gnews:
            # Google News: "Заголовок - Назва видання", справжнє джерело в <source>
            src = e.get("source") or {}
            if src.get("title"):
                source_name = src["title"]
                suffix = f" - {src['title']}"
                if title.endswith(suffix):
                    title = title[: -len(suffix)]
        summary = clean_html(e.get("summary", "") or e.get("description", ""))
        if is_gnews:
            summary = ""  # у Google News опис = дубль заголовка з посиланнями
        articles.append(Article(
            url=link if is_gnews else normalize_url(link),
            title=title,
            source=source_name,
            published=_entry_date(e),
            summary=summary[:1500],
            category=source.get("category", "other"),
            extra={"feed": source["name"], "require": source.get("require", ["ai"]),
                   "google_news": is_gnews},
        ))
    return articles


def build_source_list(cfg: dict) -> list[dict]:
    sources = [s for s in cfg["sources"] if s.get("enabled", True)]
    gn = cfg.get("google_news") or {}
    if gn.get("enabled"):
        for item in gn.get("queries", []):
            sources.append({
                "name": f"Google News: {item['q']}",
                "url": google_news_url(item["q"], gn),
                "category": item.get("category", "other"),
                "require": item.get("require", ["ai"]),
                "kind": "google_news",
            })
    return sources


def fetch_source(source: dict) -> tuple[dict, list[Article], str | None]:
    try:
        return source, parse_feed(_read(source["url"]), source), None
    except Exception as exc:  # одне зламане джерело не повинно валити весь запуск
        return source, [], f"{type(exc).__name__}: {exc}"


def fetch_all(sources: list[dict], workers: int = 8) -> tuple[list[Article], list[tuple[dict, str]]]:
    articles: list[Article] = []
    errors: list[tuple[dict, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for source, items, err in pool.map(fetch_source, sources):
            if err:
                log.warning("Джерело %s: %s", source["name"], err)
                errors.append((source, err))
            else:
                log.info("Джерело %s: %d записів", source["name"], len(items))
                articles.extend(items)
    return articles, errors
