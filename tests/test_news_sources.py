import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ainstagram.content import news_sources


class FakeEntry(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class FakeParsed:
    def __init__(self, entries):
        self.entries = entries


def test_fetch_recent_headlines(monkeypatch):
    def fake_parse(url):
        return FakeParsed([FakeEntry(title="A", summary="sa", link="la")])

    monkeypatch.setattr(news_sources.feedparser, "parse", fake_parse)

    result = news_sources.fetch_recent_headlines(["https://example.com/feed"], limit_per_feed=5)
    assert result == [{"title": "A", "summary": "sa", "link": "la"}]


def test_build_news_context_with_headlines(monkeypatch):
    def fake_parse(url):
        return FakeParsed([FakeEntry(title="OpenAI ships new model", summary="", link="")])

    monkeypatch.setattr(news_sources.feedparser, "parse", fake_parse)

    context = news_sources.build_news_context(["https://example.com/feed"])
    assert "OpenAI ships new model" in context


def test_build_news_context_no_headlines(monkeypatch):
    monkeypatch.setattr(news_sources.feedparser, "parse", lambda url: FakeParsed([]))
    context = news_sources.build_news_context(["https://example.com/feed"])
    assert "Could not fetch" in context
