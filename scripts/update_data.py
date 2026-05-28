#!/usr/bin/env python3
"""
Game Market Intelligence — Daily Data Updater
Runs via GitHub Actions every day at 00:30 UTC (08:30 TST).
Fetches gaming news from RSS feeds and public APIs,
updates daily-brief.json and quarterly.json.

Usage:
  python scripts/update_data.py

Environment variables:
  ANTHROPIC_API_KEY  — (optional) for AI-powered summarization
"""

import json
import os
import sys
import hashlib
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Safe imports (GitHub Actions will pip install these) ──
try:
    import feedparser
except ImportError:
    feedparser = None

try:
    import requests
except ImportError:
    requests = None

# ── Config ────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent.parent / "data"
DAILY_FILE = DATA_DIR / "daily-brief.json"
QUARTERLY_FILE = DATA_DIR / "quarterly.json"
PRODUCTS_FILE = DATA_DIR / "products.json"

MAX_DAILY_ITEMS = 20
CURRENT_YEAR = datetime.now(timezone.utc).year

# RSS feeds for gaming news (public, no auth)
RSS_FEEDS = [
    {"url": "https://www.ign.com/articles.rss",           "name": "IGN",           "lang": "en"},
    {"url": "https://www.gamespot.com/feeds/news/",        "name": "GameSpot",      "lang": "en"},
    {"url": "https://www.pcgamer.com/rss/",                "name": "PC Gamer",      "lang": "en"},
    {"url": "https://www.eurogamer.net/feed",              "name": "Eurogamer",     "lang": "en"},
    {"url": "https://www.gematsu.com/feed",                "name": "Gematsu",       "lang": "en"},
    {"url": "https://automaton-media.com/en/feed/",        "name": "Automaton",     "lang": "en"},
]

# Keywords to filter for relevant gaming industry news
GAME_KEYWORDS = [
    "game", "gaming", "launch", "release", "beta", "closed beta",
    "open beta", "early access", "trailer", "announce", "reveal",
    "pre-register", "soft launch", "mobile", "console", "pc",
    "playstation", "xbox", "nintendo", "switch", "steam",
    "mmorpg", "rpg", "moba", "fps", "battle royale",
    "free-to-play", "f2p", "gacha", "mihoyo", "hoyoverse",
    "tencent", "netease", "supercell", "riot", "capcom",
    "square enix", "fromsoft", "rockstar", "activision",
    "ubisoft", "ea", "epic games", "valve", "nintendo",
]

# Platform classification keywords
MOBILE_KEYWORDS = ["mobile", "ios", "android", "iphone", "ipad", "smartphone", "tablet", "手機", "行動"]
CONSOLE_KEYWORDS = ["ps5", "playstation", "xbox", "switch", "console", "pc", "steam", "主機"]

# ── Helper functions ─────────────────────────────────────

def load_json(path, default=None):
    """Load JSON file, return default if not found."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def save_json(path, data):
    """Save data to JSON file with pretty formatting."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  ✓ Saved {path}")


def make_id(text, date_str):
    """Generate a unique ID from text + date."""
    h = hashlib.md5(f"{text}{date_str}".encode()).hexdigest()[:8]
    return f"brief-{date_str.replace('-','')}-{h}"


def classify_platform(text):
    """Classify news as mobile or console based on content."""
    text_lower = text.lower()
    has_mobile = any(kw in text_lower for kw in MOBILE_KEYWORDS)
    has_console = any(kw in text_lower for kw in CONSOLE_KEYWORDS)
    if has_mobile:
        return "mobile"
    if has_console:
        return "console"
    return "console"  # default


def classify_priority(text):
    """Classify news priority based on content."""
    text_lower = text.lower()
    urgent_keywords = ["breaking", "launch date", "release date", "confirmed",
                       "leaked", "cancelled", "shutdown", "delay", "重大", "緊急"]
    watch_keywords = ["beta", "test", "update", "patch", "expansion",
                      "new trailer", "gameplay", "preview", "封測", "更新"]

    if any(kw in text_lower for kw in urgent_keywords):
        return "urgent"
    if any(kw in text_lower for kw in watch_keywords):
        return "watch"
    return "info"


def classify_event_type(text):
    """Classify event type for quarterly tracking."""
    text_lower = text.lower()
    if any(kw in text_lower for kw in ["launch", "release", "available now", "out now"]):
        return "launch"
    if any(kw in text_lower for kw in ["beta", "test", "early access"]):
        return "cbt"
    if any(kw in text_lower for kw in ["pre-register", "pre-order", "wishlist"]):
        return "pre-reg"
    if any(kw in text_lower for kw in ["announce", "reveal", "teaser", "trailer"]):
        return "announced"
    return "media"


def get_quarter(date_obj):
    """Get quarter string (Q1-Q4) from a date."""
    month = date_obj.month
    if month <= 3:
        return "Q1"
    elif month <= 6:
        return "Q2"
    elif month <= 9:
        return "Q3"
    else:
        return "Q4"


def get_priority_icon(priority):
    """Return emoji icon for priority level."""
    return {"urgent": "🚨", "watch": "👁", "info": "ℹ️"}.get(priority, "📰")


def is_game_related(title, summary=""):
    """Check if an article is related to gaming."""
    combined = f"{title} {summary}".lower()
    return any(kw in combined for kw in GAME_KEYWORDS)


def extract_product_name(title, known_products):
    """Try to match a known product name in the title."""
    for product in known_products:
        if product["name"].lower() in title.lower():
            return product["name"]
    return None


# ── RSS Fetching ─────────────────────────────────────────

def fetch_rss_entries():
    """Fetch entries from all configured RSS feeds."""
    if feedparser is None:
        print("  ⚠ feedparser not installed, using sample data")
        return []

    all_entries = []
    for feed_info in RSS_FEEDS:
        try:
            print(f"  Fetching {feed_info['name']}...")
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries[:15]:  # max 15 per feed
                pub_date = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                    pub_date = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
                else:
                    pub_date = datetime.now(timezone.utc)

                title = entry.get("title", "").strip()
                summary = entry.get("summary", "").strip()
                # Strip HTML tags from summary
                summary = re.sub(r"<[^>]+>", "", summary)[:300]
                link = entry.get("link", "")

                if is_game_related(title, summary):
                    all_entries.append({
                        "title": title,
                        "summary": summary,
                        "source": feed_info["name"],
                        "link": link,
                        "pub_date": pub_date,
                    })
        except Exception as e:
            print(f"  ✗ Error fetching {feed_info['name']}: {e}")

    # Sort by date, newest first
    all_entries.sort(key=lambda x: x["pub_date"], reverse=True)
    print(f"  Found {len(all_entries)} game-related articles")
    return all_entries


# ── AI Summarization (optional) ──────────────────────────

def ai_summarize(entries):
    """Use Anthropic API to summarize entries into brief items (optional)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or requests is None:
        return None

    try:
        articles_text = "\n\n".join([
            f"[{e['source']}] {e['title']}\n{e['summary']}"
            for e in entries[:15]
        ])

        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 2000,
                "messages": [{"role": "user", "content": f"""You are a game industry analyst. Analyze these gaming news articles and output a JSON array of the top 10 most important items. Each item should have:
- "title": concise Traditional Chinese title (繁體中文)
- "detail": 1-2 sentence summary in Traditional Chinese
- "action": recommended action for a game PM in Traditional Chinese
- "priority": "urgent" / "watch" / "info"
- "platform": "mobile" / "console"

Only output valid JSON array, no other text.

Articles:
{articles_text}"""}],
            },
            timeout=30,
        )

        if response.status_code == 200:
            data = response.json()
            text = data["content"][0]["text"]
            # Clean potential markdown fences
            text = re.sub(r"```json\s*", "", text)
            text = re.sub(r"```\s*", "", text)
            return json.loads(text.strip())
    except Exception as e:
        print(f"  ⚠ AI summarization failed: {e}")

    return None


# ── Update Daily Brief ───────────────────────────────────

def update_daily_brief():
    """Fetch news and update daily-brief.json."""
    print("\n📋 Updating Daily Brief...")

    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")

    # Load existing
    existing = load_json(DAILY_FILE, {"items": [], "lastUpdated": None, "fetchCount": 0})
    existing_ids = {item["id"] for item in existing.get("items", [])}

    # Fetch RSS
    entries = fetch_rss_entries()

    # Try AI summarization first
    ai_items = ai_summarize(entries)

    new_items = []
    if ai_items:
        print("  Using AI-summarized items")
        for item in ai_items:
            item_id = make_id(item.get("title", ""), today_str)
            if item_id not in existing_ids:
                new_items.append({
                    "id": item_id,
                    "priority": item.get("priority", "info"),
                    "icon": get_priority_icon(item.get("priority", "info")),
                    "title": item.get("title", ""),
                    "detail": item.get("detail", ""),
                    "action": item.get("action", "持續觀察。"),
                    "platform": item.get("platform", "console"),
                    "source": "AI 彙整",
                    "fetchedAt": now.isoformat(),
                })
    else:
        # Manual processing without AI
        print("  Processing entries manually")
        products = load_json(PRODUCTS_FILE, [])
        for entry in entries[:15]:
            item_id = make_id(entry["title"], today_str)
            if item_id not in existing_ids:
                priority = classify_priority(entry["title"] + " " + entry["summary"])
                platform = classify_platform(entry["title"] + " " + entry["summary"])
                new_items.append({
                    "id": item_id,
                    "priority": priority,
                    "icon": get_priority_icon(priority),
                    "title": entry["title"][:80],
                    "detail": entry["summary"][:200] if entry["summary"] else entry["title"],
                    "action": "待人工審核確認影響程度。",
                    "platform": platform,
                    "source": entry["source"],
                    "fetchedAt": now.isoformat(),
                })

    # Merge: new items first, then existing
    all_items = new_items + existing.get("items", [])

    # Keep only top MAX_DAILY_ITEMS
    all_items = all_items[:MAX_DAILY_ITEMS]

    # Sort: urgent > watch > info, then by fetchedAt desc
    priority_order = {"urgent": 0, "watch": 1, "info": 2}
    all_items.sort(key=lambda x: (
        priority_order.get(x.get("priority", "info"), 3),
        x.get("fetchedAt", "")
    ))
    # Actually, newest first within same priority
    all_items.sort(key=lambda x: x.get("fetchedAt", ""), reverse=True)
    all_items.sort(key=lambda x: priority_order.get(x.get("priority", "info"), 3))

    result = {
        "lastUpdated": now.isoformat(),
        "fetchCount": existing.get("fetchCount", 0) + 1,
        "items": all_items,
    }

    save_json(DAILY_FILE, result)
    print(f"  Added {len(new_items)} new items, total {len(all_items)} items")


# ── Update Quarterly ─────────────────────────────────────

def update_quarterly():
    """Update quarterly.json. Reset if new year."""
    print("\n📅 Updating Quarterly Tracking...")

    now = datetime.now(timezone.utc)
    current_year = now.year

    existing = load_json(QUARTERLY_FILE, {})

    # Reset if new year
    if existing.get("year") != current_year:
        print(f"  🔄 New year detected! Resetting quarterly data for {current_year}")
        existing = {
            "year": current_year,
            "resetAt": f"{current_year}-01-01T00:00:00Z",
            "lastUpdated": now.isoformat(),
            "quarters": {
                "Q1": {"label": f"{current_year} Q1 (1月-3月)", "events": []},
                "Q2": {"label": f"{current_year} Q2 (4月-6月)", "events": []},
                "Q3": {"label": f"{current_year} Q3 (7月-9月)", "events": []},
                "Q4": {"label": f"{current_year} Q4 (10月-12月)", "events": []},
            },
        }

    # Check daily brief for product events to add to quarterly
    daily = load_json(DAILY_FILE, {"items": []})
    products = load_json(PRODUCTS_FILE, [])
    product_names = [p["name"] for p in products]

    today_str = now.strftime("%Y-%m-%d")
    quarter = get_quarter(now)

    # Get existing event signatures to avoid duplicates
    q_data = existing.get("quarters", {}).get(quarter, {"events": []})
    existing_sigs = {f"{e['date']}-{e['product']}-{e['type']}" for e in q_data.get("events", [])}

    new_events = []
    for item in daily.get("items", []):
        fetched = item.get("fetchedAt", "")[:10]
        if fetched != today_str:
            continue

        # Try to match to a known product
        matched_product = None
        for pname in product_names:
            if pname.lower() in item.get("title", "").lower() or pname.lower() in item.get("detail", "").lower():
                matched_product = pname
                break

        if matched_product:
            event_type = classify_event_type(item.get("title", "") + " " + item.get("detail", ""))
            sig = f"{today_str}-{matched_product}-{event_type}"
            if sig not in existing_sigs:
                new_events.append({
                    "date": today_str,
                    "product": matched_product,
                    "type": event_type,
                    "detail": item.get("title", ""),
                    "platform": item.get("platform", "console"),
                })
                existing_sigs.add(sig)

    if new_events:
        if quarter not in existing.get("quarters", {}):
            existing["quarters"][quarter] = {
                "label": f"{current_year} {quarter}",
                "events": []
            }
        existing["quarters"][quarter]["events"].extend(new_events)
        # Sort events by date within quarter
        existing["quarters"][quarter]["events"].sort(key=lambda x: x.get("date", ""))
        print(f"  Added {len(new_events)} events to {quarter}")

    existing["lastUpdated"] = now.isoformat()
    save_json(QUARTERLY_FILE, existing)


# ── Main ─────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("🎮 Game Market Intelligence — Daily Update")
    print(f"   {datetime.now(timezone.utc).isoformat()}")
    print("=" * 50)

    # Ensure data directory exists
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    update_daily_brief()
    update_quarterly()

    print("\n✅ All updates complete!")


if __name__ == "__main__":
    main()
