"""Точка входу: python -m digest [--dry-run] [--no-llm] [--check-sources]"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .config import load_config
from .extract import extract_all
from .fetch import build_source_list, fetch_all
from .filters import KeywordMatcher, is_fresh, prefilter
from .llm import Scorer, fallback_score
from .state import load_seen, mark_seen, save_seen
from .telegram import format_digest, send_messages

log = logging.getLogger("digest")


def run_digest(cfg: dict, dry_run: bool, use_llm: bool, save_state: bool) -> int:
    settings = cfg["settings"]
    now = datetime.now(timezone.utc)
    seen = load_seen()

    articles, errors = fetch_all(build_source_list(cfg))
    candidates, stats = prefilter(articles, cfg, set(seen), now)
    log.info("Зібрано %(total)d записів; відсіяно: старі %(stale)d, вже були %(seen)d, "
             "дублі %(duplicates)d, без ключових слів %(keywords)d", stats)
    log.info("Кандидатів на оцінку: %d (джерел з помилками: %d)", len(candidates), len(errors))

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if use_llm and api_key and candidates:
        extract_all(candidates)
        Scorer(cfg["llm"]["model"], cfg["llm"]["audience"]).score(candidates)
        processed = [a for a in candidates if a.extra.get("scored")]
        if len(processed) < len(candidates):
            log.warning("ШІ не оцінив %d статей; вони спробують потрапити в наступний запуск",
                        len(candidates) - len(processed))
    else:
        if use_llm and not api_key:
            log.warning("ANTHROPIC_API_KEY не задано: працюю без ШІ (оригінальні заголовки, без ідей постів)")
        fallback_score(candidates)
        processed = candidates

    selected = sorted((a for a in processed if a.score >= settings["min_score"]),
                      key=lambda a: a.score, reverse=True)[: settings["top_n"]]
    log.info("У дайджест потрапило: %d", len(selected))

    today = now.astimezone(ZoneInfo(settings["timezone"]))
    messages = format_digest(selected, cfg["categories"], today)
    if not selected and not settings["send_empty"]:
        messages = []

    if dry_run:
        for m in messages:
            print(m)
            print("\n" + "─" * 60 + "\n")
    elif messages:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            log.error("Не задано TELEGRAM_BOT_TOKEN або TELEGRAM_CHAT_ID")
            return 2
        send_messages(token, chat_id, messages)
        log.info("Надіслано повідомлень у Telegram: %d", len(messages))

    if save_state and not dry_run:
        mark_seen(seen, processed, now)
        save_seen(seen, settings["seen_retention_days"], now=now)
    return 0


def run_check_sources(cfg: dict) -> int:
    """Перевіряє кожне джерело: чи відповідає, скільки записів, наскільки свіжі, скільки про ШІ."""
    now = datetime.now(timezone.utc)
    matcher = KeywordMatcher(cfg["keywords"])
    sources = build_source_list(cfg)
    articles, errors = fetch_all(sources)
    failed = {s["name"]: err for s, err in errors}

    by_feed: dict[str, list] = {}
    for a in articles:
        by_feed.setdefault(a.extra["feed"], []).append(a)

    print(f"{'Статус':<7} {'Записів':>7} {'За 7 дн':>7} {'Про ШІ':>6}  {'Найновіший':<16} Джерело")
    week = 24 * 7
    for s in sources:
        name = s["name"]
        if name in failed:
            print(f"{'❌':<7} {'-':>7} {'-':>7} {'-':>6}  {'-':<16} {name}\n        └ {failed[name][:150]}")
            continue
        items = by_feed.get(name, [])
        fresh = [a for a in items if is_fresh(a, week, now)]
        ai = [a for a in fresh if matcher.ai_hits(f"{a.title}\n{a.summary}")]
        dates = [a.published for a in items if a.published]
        newest = max(dates).strftime("%Y-%m-%d %H:%M") if dates else "без дат"
        status = "✅" if fresh else ("⚠️" if items else "❓")
        print(f"{status:<7} {len(items):>7} {len(fresh):>7} {len(ai):>6}  {newest:<16} {name}")

    print(f"\nПрацює: {len(sources) - len(failed)}/{len(sources)}. "
          "✅ є свіжі записи · ⚠️ стрічка жива, але давно не оновлювалась · ❓ порожня · ❌ помилка")
    cats = Counter(a.category for a in articles if is_fresh(a, week, now))
    print("Свіжих записів за категоріями (7 днів):", dict(cats))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="digest", description="Щоденний дайджест ШІ × Креатив")
    parser.add_argument("--config", help="шлях до config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="надрукувати дайджест замість відправки")
    parser.add_argument("--no-llm", action="store_true", help="не використовувати ШІ")
    parser.add_argument("--no-save", action="store_true", help="не оновлювати data/seen.json")
    parser.add_argument("--check-sources", action="store_true", help="перевірити всі джерела і вийти")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s", stream=sys.stderr)
    # httpx/anthropic логують кожен запит на INFO; приглушуємо
    for noisy in ("httpx", "anthropic", "trafilatura", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    cfg = load_config(args.config)
    if args.check_sources:
        return run_check_sources(cfg)
    return run_digest(cfg, dry_run=args.dry_run, use_llm=not args.no_llm, save_state=not args.no_save)


if __name__ == "__main__":
    sys.exit(main())
