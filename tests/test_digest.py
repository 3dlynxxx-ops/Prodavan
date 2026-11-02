from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from digest.config import load_config
from digest.fetch import normalize_url, parse_feed
from digest.filters import KeywordMatcher, prefilter
from digest.llm import fallback_score
from digest.models import Article
from digest.telegram import TG_LIMIT, format_digest

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


def rss(items):
    body = "".join(
        f"<item><title>{t}</title><link>{link}</link><description>{d}</description>"
        f"<pubDate>{format_datetime(NOW - timedelta(hours=age))}</pubDate></item>"
        for t, link, d, age in items
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>{body}</channel></rss>'.encode()


def test_normalize_url_strips_tracking():
    assert normalize_url("https://Site.com/a/?utm_source=x&id=5#top") == "https://site.com/a?id=5"


def test_keyword_matcher_case_rules():
    m = KeywordMatcher({"ai": ["AI", "generative"], "creative": {"archviz": ["architecture"]}})
    assert m.ai_hits("New AI renderer") == 1
    assert m.ai_hits("Ai Weiwei exhibition") == 0          # абревіатура чутлива до регістру
    assert m.ai_hits("said the chairman") == 0             # не всередині слова
    assert m.ai_hits("Generative tools") == 1              # довгі слова без регістру
    assert m.creative_hits("Architecture news")["archviz"] == 1


def test_prefilter_freshness_keywords_dedup():
    cfg = load_config()
    source = {"name": "Test", "category": "archviz", "require": ["ai"]}
    articles = parse_feed(rss([
        ("AI rendering for architecture", "https://x.com/1?utm_source=rss", "Diffusion in archviz", 5),
        ("AI rendering for architecture", "https://y.com/dup", "same story elsewhere", 6),
        ("New chair design", "https://x.com/2", "No machine stuff", 5),
        ("Old AI news", "https://x.com/3", "AI", 24 * 10),
        ("Seen AI story", "https://x.com/4", "AI", 3),
    ]), source)
    candidates, stats = prefilter(articles, cfg, seen={"https://x.com/4"}, now=NOW)
    assert [a.url for a in candidates] == ["https://x.com/1"]
    assert stats == {"total": 5, "stale": 1, "seen": 1, "keywords": 1, "duplicates": 1}


def test_google_news_title_and_source():
    feed = (b'<?xml version="1.0"?><rss version="2.0"><channel><item>'
            b"<title>AI tool for architects - ArchDaily</title>"
            b"<link>https://news.google.com/rss/articles/abc</link>"
            b'<source url="https://archdaily.com">ArchDaily</source>'
            b"<pubDate>Mon, 02 Nov 2026 08:00:00 GMT</pubDate></item></channel></rss>")
    [a] = parse_feed(feed, {"name": "GN", "category": "archviz", "kind": "google_news"})
    assert a.title == "AI tool for architects"
    assert a.source == "ArchDaily"


def test_format_digest_splits_long_messages():
    cfg = load_config()
    arts = []
    for i in range(30):
        a = Article(url=f"https://x.com/{i}", title=f"Title {i} <b>", source="Src",
                    published=NOW, summary="lorem ipsum " * 40, category="archviz",
                    prefilter_score=4)
        arts.append(a)
    fallback_score(arts)
    msgs = format_digest(arts, cfg["categories"], NOW)
    assert len(msgs) > 1
    assert all(len(m) <= TG_LIMIT for m in msgs)
    assert "&lt;b&gt;" in msgs[0]  # HTML екранується


def test_empty_digest_message():
    msgs = format_digest([], load_config()["categories"], NOW)
    assert len(msgs) == 1 and "нічого" in msgs[0]


def test_scorer_maps_llm_verdicts(monkeypatch):
    from types import SimpleNamespace

    from digest import llm

    captured = {}

    def fake_parse(**kwargs):
        captured.update(kwargs)
        items = [llm.Verdict(id=0, relevance=12, category="archviz", title_uk="Заголовок",
                             summary_uk="Підсумок.", post_idea_uk="Карусель.")]
        return SimpleNamespace(stop_reason="end_turn", stop_details=None,
                               parsed_output=llm.BatchVerdict(items=items))

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    scorer = llm.Scorer("claude-haiku-5-5", "аудиторія")
    monkeypatch.setattr(scorer.client.messages, "parse", fake_parse)
    a = Article(url="https://x.com/1", title="T", source="S", published=NOW, category="other")
    b = Article(url="https://x.com/2", title="T2", source="S", published=NOW)
    scorer.score([a, b])

    assert captured["model"] == "claude-haiku-5-5"
    assert captured["output_format"] is llm.BatchVerdict
    assert "аудиторія" in captured["system"]
    assert (a.score, a.category, a.title_uk) == (10, "archviz", "Заголовок")  # оцінку обмежено 10
    assert a.extra.get("scored") and not b.extra.get("scored")
    assert "⭐ 10/10" in format_digest([a], load_config()["categories"], NOW)[0]
