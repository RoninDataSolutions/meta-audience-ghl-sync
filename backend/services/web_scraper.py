"""
web_scraper.py — Scrape a business website for landing page context.

Extracts: title, meta description, headings, pricing signals, CTAs, and
a short content summary. Used to give the AI audit real landing page context.
"""

import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; RDS-AuditBot/1.0; +https://ronindatasolutions.com)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

PRICE_PATTERN = re.compile(
    r'\$\s*\d[\d,]*(?:\.\d{1,2})?|\d[\d,]*(?:\.\d{1,2})?\s*(?:USD|usd)',
)

CTA_KEYWORDS = {
    "buy", "shop", "order", "purchase", "subscribe", "sign up", "signup",
    "get started", "start free", "book", "schedule", "reserve", "enroll",
    "join", "register", "download", "try", "demo", "contact", "request",
    "apply", "claim", "access", "unlock",
}


def _extract_prices(text: str) -> list[str]:
    found = PRICE_PATTERN.findall(text)
    # deduplicate, cap at 10
    seen = set()
    out = []
    for p in found:
        p = p.strip()
        if p not in seen:
            seen.add(p)
            out.append(p)
        if len(out) >= 10:
            break
    return out


def _extract_ctas(soup: BeautifulSoup) -> list[str]:
    ctas = set()
    for tag in soup.find_all(["a", "button"]):
        text = tag.get_text(strip=True).lower()
        if any(kw in text for kw in CTA_KEYWORDS) and len(text) < 60:
            ctas.add(tag.get_text(strip=True))
        if len(ctas) >= 8:
            break
    return list(ctas)


def _clean_text(text: str, max_chars: int = 500) -> str:
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:max_chars] + ("…" if len(text) > max_chars else "")


async def scrape_website(url: str, timeout: int = 15) -> dict:
    """
    Scrape a business website and return structured context for the AI.

    Returns a dict with keys:
      url, title, meta_description, h1s, h2s, prices_found,
      ctas, body_summary, error (only on failure)
    """
    if not url:
        return {}

    # Ensure scheme
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    try:
        async with httpx.AsyncClient(
            headers=HEADERS,
            timeout=timeout,
            follow_redirects=True,
            verify=False,  # some SMB sites have expired/self-signed certs
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except httpx.TimeoutException:
        logger.warning(f"Website scrape timed out: {url}")
        return {"url": url, "error": "timeout"}
    except Exception as e:
        logger.warning(f"Website scrape failed for {url}: {e}")
        return {"url": url, "error": str(e)[:200]}

    try:
        soup = BeautifulSoup(html, "html.parser")

        # Remove script/style noise
        for tag in soup(["script", "style", "noscript", "svg", "meta", "link"]):
            tag.decompose()

        title = soup.title.get_text(strip=True) if soup.title else ""

        meta_desc = ""
        for m in soup.find_all("meta"):
            if m.get("name", "").lower() in ("description", "og:description") or \
               m.get("property", "").lower() in ("og:description",):
                meta_desc = m.get("content", "")
                if meta_desc:
                    break

        h1s = [h.get_text(strip=True) for h in soup.find_all("h1")][:5]
        h2s = [h.get_text(strip=True) for h in soup.find_all("h2")][:8]

        body_text = soup.get_text(separator=" ")
        prices = _extract_prices(body_text)
        ctas = _extract_ctas(soup)
        body_summary = _clean_text(body_text, 800)

        # Try to detect platform/tech hints
        platform_hints = []
        html_lower = html.lower()
        for hint, keyword in [
            ("Shopify", "cdn.shopify.com"),
            ("WordPress", "wp-content"),
            ("Kajabi", "kajabi"),
            ("Teachable", "teachable.com"),
            ("Squarespace", "squarespace"),
            ("Wix", "wix.com"),
            ("Webflow", "webflow.io"),
            ("ClickFunnels", "clickfunnels"),
            ("Calendly", "calendly"),
            ("Mindbody", "mindbodyonline"),
        ]:
            if keyword in html_lower:
                platform_hints.append(hint)

        return {
            "url": url,
            "title": title[:200],
            "meta_description": meta_desc[:300],
            "h1s": h1s,
            "h2s": h2s,
            "prices_found": prices,
            "ctas": ctas,
            "platform_hints": platform_hints,
            "body_summary": body_summary,
        }

    except Exception as e:
        logger.warning(f"Website parse failed for {url}: {e}")
        return {"url": url, "error": f"parse error: {str(e)[:200]}"}
