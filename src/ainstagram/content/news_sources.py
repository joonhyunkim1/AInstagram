"""무료 RSS 피드에서 최신 AI 관련 헤드라인을 모은다."""
from __future__ import annotations

import feedparser


def fetch_recent_headlines(feed_urls: list[str], limit_per_feed: int = 5) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for url in feed_urls:
        parsed = feedparser.parse(url)
        for entry in parsed.entries[:limit_per_feed]:
            items.append(
                {
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", ""),
                    "link": entry.get("link", ""),
                }
            )
    return items


def build_news_context(feed_urls: list[str], limit_per_feed: int = 5) -> str:
    headlines = fetch_recent_headlines(feed_urls, limit_per_feed)
    if not headlines:
        return "Could not fetch recent headlines - write based on general recent AI trends."
    lines = [f"- {h['title']}" for h in headlines if h["title"]]
    return "Recent global AI headlines:\n" + "\n".join(lines)
