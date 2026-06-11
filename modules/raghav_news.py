"""Raghav — News sentiment signal (Massive news endpoint).

Pulls recent news for a ticker and reads Massive's per-article `insights`
(sentiment + reasoning the model extracted for that specific ticker). The
signal score is the mean sentiment across recent articles, mapped to [-1, 1]
(positive = +1, neutral = 0, negative = -1). It also exposes get_news() so the
dashboard can show a window of the actual headlines + insights.

Follows the standard contract, so no adapter is needed:
    analyze(ticker, period="2y") -> {ticker, signal, score in [-1,1], rating, ...}
"""
from __future__ import annotations

import sys

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("Missing dependency: run  pip install requests")

from core.env import get_key
from core.rating import score_to_rating

SIGNAL_NAME = "news"
SIGNAL_OWNER = "raghav"
SIGNAL_CATEGORY = "Sentiment"

BASE_URL = "https://api.massive.com"
NEWS_ENDPOINT = "/v2/reference/news"

_SENTIMENT = {"positive": 1.0, "neutral": 0.0, "negative": -1.0}


def _fetch_news(ticker: str, limit: int = 20) -> list[dict]:
    key = get_key("MASSIVE_API_KEY")
    resp = requests.get(
        BASE_URL + NEWS_ENDPOINT,
        params={"ticker": ticker.upper(), "limit": limit,
                "order": "desc", "sort": "published_utc", "apiKey": key},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json().get("results") or []


def _insight_for(article: dict, ticker: str) -> tuple[str, str]:
    """Return (sentiment, reasoning) for `ticker` from an article's insights."""
    for ins in article.get("insights") or []:
        if (ins.get("ticker") or "").upper() == ticker.upper():
            return (ins.get("sentiment") or "neutral"), (ins.get("sentiment_reasoning") or "")
    return "neutral", ""


def get_news(ticker: str, limit: int = 15) -> list[dict]:
    """Recent news for a ticker with its per-article sentiment + reasoning."""
    out = []
    for a in _fetch_news(ticker, limit):
        sentiment, reasoning = _insight_for(a, ticker)
        out.append({
            "title": a.get("title", ""),
            "publisher": (a.get("publisher") or {}).get("name", ""),
            "author": a.get("author", ""),
            "published": (a.get("published_utc") or "")[:10],
            "description": a.get("description", ""),
            "sentiment": sentiment,
            "reasoning": reasoning,
            "url": a.get("article_url", ""),
        })
    return out


def analyze(ticker: str, period: str = "2y", **_) -> dict:
    ticker = ticker.upper()
    articles = _fetch_news(ticker, limit=25)

    if not articles:
        return {
            "ticker": ticker, "signal": SIGNAL_NAME,
            "score": None, "rating": "N/A", "native_rating": "No recent news",
            "breakdown": ["No recent news found for this ticker."],
            "details": {"n_articles": 0},
        }

    counts = {"positive": 0, "neutral": 0, "negative": 0}
    sentiments, lines = [], []
    for a in articles:
        sentiment, reasoning = _insight_for(a, ticker)
        counts[sentiment] = counts.get(sentiment, 0) + 1
        sentiments.append(_SENTIMENT.get(sentiment, 0.0))
        tag = sentiment[:3].upper()
        pub = (a.get("publisher") or {}).get("name", "")
        lines.append(f"[{tag}] {a.get('title', '')[:78]} — {pub}")

    score = round(sum(sentiments) / len(sentiments), 3)
    if score > 0.2:
        label = "Bullish news"
    elif score < -0.2:
        label = "Bearish news"
    else:
        label = "Mixed / neutral news"

    summary = (f"{counts['positive']} positive / {counts['neutral']} neutral / "
               f"{counts['negative']} negative across {len(articles)} recent articles "
               f"→ mean sentiment {score:+.2f}.")
    return {
        "ticker": ticker, "signal": SIGNAL_NAME,
        "score": score, "rating": score_to_rating(score),
        "native_rating": label,
        "breakdown": [summary, *lines[:10]],
        "details": {"n_articles": len(articles), **counts, "score": score},
    }


if __name__ == "__main__":
    from core.env import load_local_keys
    load_local_keys()
    tk = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    res = analyze(tk)
    print(f"\n{tk} news → {res.get('native_rating')}  (score {res.get('score')})")
    for line in res.get("breakdown", []):
        print("  " + line)
