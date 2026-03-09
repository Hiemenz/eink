"""
Web search tool using DuckDuckGo (no API key needed, lightweight).
Falls back to a simple HTTP request with basic scraping.
"""

from __future__ import annotations

import requests
from typing import Any


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """
    Search the web using DuckDuckGo Instant Answer API.
    Returns list of {title, url, snippet} dicts.
    """
    results: list[dict] = []

    # DuckDuckGo Lite search (no JS required)
    try:
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (AI Brain Bot/1.0)"},
            timeout=10,
        )
        resp.raise_for_status()

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "lxml")

        for a in soup.select(".result__a")[:max_results]:
            title = a.get_text(strip=True)
            href = a.get("href", "")
            snippet_tag = a.find_parent().find(class_="result__snippet") if a.find_parent() else None
            snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""
            if href:
                results.append({"title": title, "url": href, "snippet": snippet})

    except Exception as e:
        results.append({"title": "Search error", "url": "", "snippet": str(e)})

    return results[:max_results]


def fetch_page(url: str, max_chars: int = 4000) -> str:
    """Fetch and return plain text content of a web page."""
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (AI Brain Bot/1.0)"},
            timeout=15,
        )
        resp.raise_for_status()
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return text[:max_chars]
    except Exception as e:
        return f"Error fetching {url}: {e}"
