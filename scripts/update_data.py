#!/usr/bin/env python3
"""
Game Market Intelligence — 3-Agent Automated System
Uses Google Gemini API (free tier) for AI-powered game market intelligence.

Agent 1: Product Discovery — finds new games from TapTap, 九遊, 17173, GameRes
Agent 2: Product Status Update — checks existing products, auto-updates status
Agent 3: Daily Brief + Industry News — generates daily brief from RSS + search

Runs daily via GitHub Actions at 08:30 TST.
Env: GEMINI_API_KEY (required)
"""
import json, os, sys, hashlib, re, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None
try:
    import feedparser
except ImportError:
    feedparser = None

DATA_DIR = Path(__file__).parent.parent / "data"
PRODUCTS_FILE = DATA_DIR / "products.json"
DAILY_FILE = DATA_DIR / "daily-brief.json"
QUARTERLY_FILE = DATA_DIR / "quarterly.json"
AUDIT_FILE = DATA_DIR / "audit-report.json"
MAX_DAILY_ITEMS = 20
GEMINI_MODEL = "gemini-2.0-flash"

# ══════════════════════════════════════════
# Gemini API Helper
# ══════════════════════════════════════════
def gemini_call(prompt, use_search=False, max_tokens=4000):
    """Call Gemini API with optional Google Search grounding."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or not requests:
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.2},
    }

    if use_search:
        body["tools"] = [{"google_search": {}}]

    try:
        r = requests.post(url, json=body, timeout=60)
        if r.status_code == 200:
            data = r.json()
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                text = " ".join(p.get("text", "") for p in parts if "text" in p)
                return text.strip()
            return None
        else:
            print(f"    Gemini API error {r.status_code}: {r.text[:200]}")
            return None
    except Exception as e:
        print(f"    Gemini call failed: {e}")
        return None


def parse_json_from_text(text):
    """Extract JSON array or object from AI response text."""
    if not text:
        return None
    # Try to find JSON in the response
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    # Find first [ or {
    for i, c in enumerate(text):
        if c in "[{":
            # Find matching end
            depth = 0
            for j in range(i, len(text)):
                if text[j] in "[{":
                    depth += 1
                elif text[j] in "]}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[i:j+1])
                        except json.JSONDecodeError:
                            break
            break
    return None


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default if default is not None else {}


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  ✓ Saved {path.name}")


# ══════════════════════════════════════════
# AGENT 1: Product Discovery
# ══════════════════════════════════════════
def agent1_discover_products():
    """Search for new game products and add them to the database."""
    print("\n🔍 Agent 1: Product Discovery")
    print("  Searching for new game announcements...")

    products = load_json(PRODUCTS_FILE, [])
    existing_names = {g["name"] for g in products}
    if products:
        max_id = max(g["id"] for g in products)
    else:
        max_id = 0

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Search multiple sources for new games
    sources = [
        f"2026年6月 手遊新作 開測 上線 預約 封測 TapTap 九遊",
        f"2026 新手遊 公測 二次元 RPG 開放世界 最新",
        f"mobile game new release beta 2026 June July global launch",
    ]

    all_discoveries = []
    for query_text in sources:
        prompt = f"""You are a game industry analyst. Search for the latest mobile and console game releases, betas, and announcements.

Search query context: {query_text}

Based on your search results, list NEW games that are:
1. Announced or entering beta/launch within the last 3 months or next 6 months
2. Significant enough to be tracked (major studio, high pre-registration, or innovative gameplay)

For each game, output a JSON array where each item has:
- "name": Chinese name if available, otherwise English (繁體中文優先)
- "nameEn": English name
- "developer": developer studio name
- "publisher": publisher name
- "genre": game genre in Chinese
- "platform": array like ["Mobile"] or ["PC","Mobile","Console"]
- "region": like "CN/Global" or "JP/Global"
- "model": "F2P" or "Buy-to-Play"
- "stage": one of "announced","pre-reg","cbt","soft-launch","live-ops"
- "threat": "critical","high","medium","low" based on market impact
- "launchEst": estimated launch like "2026-07" or "2026-Q3"
- "desc": 1-2 sentence description in 繁體中文
- "tags": array of relevant tags in Chinese
- "threatAnalysis": why this game is a threat, in 繁體中文
- "testType": test type like "不刪檔公測","刪檔不計費","刪檔計費" etc
- "testDateStart": test start date if known
- "testDateEnd": test end date if known

IMPORTANT: Only include games you are confident actually exist. Do NOT invent or hallucinate games.
Output ONLY a valid JSON array. No other text."""

        result = gemini_call(prompt, use_search=True, max_tokens=4000)
        parsed = parse_json_from_text(result)
        if parsed and isinstance(parsed, list):
            all_discoveries.extend(parsed)
            print(f"    Found {len(parsed)} potential products from search")
        time.sleep(2)  # Rate limiting

    # Deduplicate and filter
    new_products = []
    seen_names = set()
    for disc in all_discoveries:
        name = disc.get("name", "")
        if not name or name in existing_names or name in seen_names:
            continue
        seen_names.add(name)

        # Build product entry
        max_id += 1
        product = {
            "id": max_id,
            "name": name,
            "nameEn": disc.get("nameEn", ""),
            "developer": disc.get("developer", "未知"),
            "studio": disc.get("developer", "未知"),
            "publisher": disc.get("publisher", "未知"),
            "genre": disc.get("genre", "未知"),
            "platform": disc.get("platform", ["Mobile"]),
            "region": disc.get("region", "未知"),
            "model": disc.get("model", "F2P"),
            "stage": disc.get("stage", "announced"),
            "threat": disc.get("threat", "medium"),
            "prereg": None,
            "sentiment": 70,
            "launchEst": disc.get("launchEst", "待定"),
            "desc": disc.get("desc", ""),
            "tags": disc.get("tags", []),
            "threatAnalysis": disc.get("threatAnalysis", ""),
            "verified": f"AI Agent 自動發現 ({today})，待人工確認",
            "category": "active",
            "testType": disc.get("testType", "未知"),
            "testDateStart": disc.get("testDateStart", "待確認"),
            "testDateEnd": disc.get("testDateEnd", "待確認"),
            "launchRegions": [],
            "history": [{"date": datetime.now(timezone.utc).strftime("%Y-%m"), "s": disc.get("stage", "announced")}],
            "updatedAt": today,
            "autoDiscovered": True,
        }
        new_products.append(product)

    if new_products:
        products.extend(new_products)
        save_json(PRODUCTS_FILE, products)
        print(f"  ✅ Added {len(new_products)} new products:")
        for p in new_products:
            print(f"    + {p['name']} ({p['developer']}) [{p['stage']}]")
    else:
        print("  No new products discovered")

    return len(new_products)


# ══════════════════════════════════════════
# AGENT 2: Product Status Update
# ══════════════════════════════════════════
def agent2_update_status():
    """Check each product for status changes and auto-update."""
    print("\n🔄 Agent 2: Product Status Update")

    products = load_json(PRODUCTS_FILE, [])
    if not products:
        print("  No products to check")
        return 0

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    updates_made = 0
    audit_findings = []

    # Process in batches of 5
    active_products = [g for g in products if g.get("category") != "tracking" or True]

    for i in range(0, len(active_products), 5):
        batch = active_products[i:i+5]
        batch_info = "\n".join([
            f"- {g['name']} | 目前狀態: {g['stage']} | 開發商: {g.get('developer','未知')} | 預計上線: {g.get('launchEst','未定')} | 品類: {g['genre']}"
            for g in batch
        ])

        prompt = f"""You are a game industry analyst. Today is {today}. Check the current status of these games using web search.

Games to check:
{batch_info}

For each game, search for the latest news and determine:
1. Has the game's status changed? (e.g., was in beta, now launched)
2. Has the launch date been updated?
3. Are there any major recent events? (anniversary, new version, shutdown)
4. Is the developer/publisher info still correct?

Output a JSON array where each item has:
- "name": the game name (exactly as provided)
- "statusChanged": true/false
- "newStage": new stage if changed (announced/pre-reg/cbt/soft-launch/live-ops), or null
- "newLaunchEst": updated launch date if changed, or null
- "recentEvent": description of major recent event if any, or null
- "shouldBeTracking": true if this is a long-running game (1+ year) that should be in tracking category
- "shouldRemove": true if game is dead/shutdown/irrelevant
- "removeReason": reason if shouldRemove is true
- "notes": any other relevant update in 繁體中文

IMPORTANT: Only report changes you are confident about from search results. If unsure, set statusChanged to false.
Output ONLY valid JSON array."""

        result = gemini_call(prompt, use_search=True, max_tokens=3000)
        parsed = parse_json_from_text(result)

        if parsed and isinstance(parsed, list):
            for update in parsed:
                name = update.get("name", "")
                product = next((g for g in products if g["name"] == name), None)
                if not product:
                    continue

                # Apply status change
                if update.get("statusChanged") and update.get("newStage"):
                    old_stage = product["stage"]
                    product["stage"] = update["newStage"]
                    product["updatedAt"] = today
                    if product.get("history"):
                        product["history"].append({"date": datetime.now(timezone.utc).strftime("%Y-%m"), "s": update["newStage"]})
                    updates_made += 1
                    finding = f"✏️ {name}: {old_stage} → {update['newStage']}"
                    if update.get("notes"):
                        finding += f" ({update['notes']})"
                    audit_findings.append({"name": name, "status": "warning", "issue": finding, "suggestion": f"自動更新為 {update['newStage']}", "confidence": "high", "autoFixed": True})
                    print(f"    ✏️ {name}: {old_stage} → {update['newStage']}")

                # Apply launch date change
                if update.get("newLaunchEst"):
                    old_est = product.get("launchEst", "")
                    if old_est != update["newLaunchEst"]:
                        product["launchEst"] = update["newLaunchEst"]
                        product["updatedAt"] = today
                        updates_made += 1
                        print(f"    📅 {name}: launch {old_est} → {update['newLaunchEst']}")

                # Apply tracking category
                if update.get("shouldBeTracking") and product.get("category") != "tracking":
                    product["category"] = "tracking"
                    if update.get("recentEvent"):
                        product["currentEvent"] = update["recentEvent"]
                    product["updatedAt"] = today
                    updates_made += 1
                    print(f"    📡 {name}: moved to tracking")

                # Mark for removal
                if update.get("shouldRemove"):
                    audit_findings.append({"name": name, "status": "error",
                        "issue": f"建議移除: {update.get('removeReason', '未知原因')}",
                        "suggestion": "需人工確認後移除", "confidence": "medium", "autoFixed": False})
                    print(f"    ⚠️ {name}: flagged for removal - {update.get('removeReason')}")

                # Update recent event for tracking products
                if update.get("recentEvent") and product.get("category") == "tracking":
                    product["currentEvent"] = update["recentEvent"]
                    has_alert = any(kw in update["recentEvent"] for kw in ["週年", "慶典", "新版本", "聯動", "大型更新"])
                    product["eventNote"] = ("⚡ " if has_alert else "") + update["recentEvent"]
                    product["updatedAt"] = today

        time.sleep(3)  # Rate limiting between batches

    if updates_made > 0:
        save_json(PRODUCTS_FILE, products)
    print(f"  ✅ {updates_made} updates applied")

    # Save audit report
    now = datetime.now(timezone.utc)
    report = {
        "lastAudit": now.isoformat(),
        "totalProducts": len(products),
        "issuesFound": len(audit_findings),
        "updatesApplied": updates_made,
        "findings": audit_findings,
        "nextAudit": (now + timedelta(days=1)).strftime("%Y-%m-%d"),
    }
    save_json(AUDIT_FILE, report)

    return updates_made


# ══════════════════════════════════════════
# AGENT 3: Daily Brief + Industry News
# ══════════════════════════════════════════
def agent3_daily_brief():
    """Generate daily brief from RSS feeds and AI-powered news search."""
    print("\n📋 Agent 3: Daily Brief & Industry News")

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    existing = load_json(DAILY_FILE, {"items": [], "fetchCount": 0})
    existing_ids = {i["id"] for i in existing.get("items", [])}

    # Step 1: Fetch RSS
    rss_entries = []
    RSS_FEEDS = [
        {"url": "https://www.ign.com/articles.rss", "name": "IGN"},
        {"url": "https://www.gamespot.com/feeds/news/", "name": "GameSpot"},
        {"url": "https://www.gematsu.com/feed", "name": "Gematsu"},
        {"url": "https://automaton-media.com/en/feed/", "name": "Automaton"},
    ]
    if feedparser:
        for feed in RSS_FEEDS:
            try:
                f = feedparser.parse(feed["url"])
                for e in f.entries[:10]:
                    title = e.get("title", "").strip()
                    summary = re.sub(r"<[^>]+>", "", e.get("summary", ""))[:300]
                    GAME_KW = ["game","launch","beta","trailer","announce","mobile","console","rpg","mmo"]
                    if any(kw in f"{title} {summary}".lower() for kw in GAME_KW):
                        rss_entries.append({"title": title, "summary": summary, "source": feed["name"]})
            except:
                pass
    print(f"  RSS: {len(rss_entries)} game articles found")

    # Step 2: AI search for Chinese gaming news
    prompt = f"""Today is {today}. Search for the latest Chinese and global gaming industry news from this week.

Focus on:
1. New game announcements or launches (手遊/主機遊戲 新作/上線/開測)
2. Major game updates (大版本更新/週年慶)
3. Industry business news (財報/收購/合作)
4. Market trends (市場趨勢/出海/版號)

Output a JSON array of the top 10 most important news items. Each item:
- "title": news title in 繁體中文
- "detail": 1-2 sentence summary in 繁體中文
- "action": recommended action for a game PM in 繁體中文
- "priority": "urgent" / "watch" / "info"
- "platform": "mobile" / "console"
- "source": source name

Output ONLY valid JSON array."""

    ai_items = []
    result = gemini_call(prompt, use_search=True, max_tokens=3000)
    parsed = parse_json_from_text(result)
    if parsed and isinstance(parsed, list):
        ai_items = parsed
        print(f"  AI search: {len(ai_items)} news items")

    # Step 3: Combine and deduplicate
    new_items = []
    icons = {"urgent": "🚨", "watch": "👁", "info": "ℹ️"}

    for item in ai_items:
        iid = hashlib.md5(f"{item.get('title','')}{today}".encode()).hexdigest()[:8]
        iid = f"brief-{today.replace('-','')}-{iid}"
        if iid not in existing_ids:
            new_items.append({
                "id": iid,
                "priority": item.get("priority", "info"),
                "icon": icons.get(item.get("priority", "info"), "📰"),
                "title": item.get("title", ""),
                "detail": item.get("detail", ""),
                "action": item.get("action", "持續觀察"),
                "platform": item.get("platform", "mobile"),
                "source": item.get("source", "AI 搜索"),
                "fetchedAt": now.isoformat(),
            })

    # For RSS entries without AI, add as info items
    for entry in rss_entries[:5]:
        iid = hashlib.md5(f"{entry['title']}{today}".encode()).hexdigest()[:8]
        iid = f"brief-{today.replace('-','')}-{iid}"
        if iid not in existing_ids and iid not in {i["id"] for i in new_items}:
            new_items.append({
                "id": iid,
                "priority": "info",
                "icon": "📰",
                "title": entry["title"][:80],
                "detail": entry["summary"][:200],
                "action": "待審閱",
                "platform": "console",
                "source": entry["source"],
                "fetchedAt": now.isoformat(),
            })

    # Merge: new first, then existing
    all_items = new_items + existing.get("items", [])
    all_items = all_items[:MAX_DAILY_ITEMS]

    # Sort by priority then date
    po = {"urgent": 0, "watch": 1, "info": 2}
    all_items.sort(key=lambda x: (po.get(x.get("priority", "info"), 3), x.get("fetchedAt", "")),
                   reverse=False)
    # Actually sort urgent first, then by date desc within same priority
    all_items.sort(key=lambda x: x.get("fetchedAt", ""), reverse=True)
    all_items.sort(key=lambda x: po.get(x.get("priority", "info"), 3))

    save_json(DAILY_FILE, {
        "lastUpdated": now.isoformat(),
        "fetchCount": existing.get("fetchCount", 0) + 1,
        "items": all_items,
    })
    print(f"  ✅ +{len(new_items)} new items, total {len(all_items)}")

    # Step 4: Update quarterly
    quarterly = load_json(QUARTERLY_FILE, {})
    cur_year = now.year
    if quarterly.get("year") != cur_year:
        print(f"  🔄 New year! Resetting quarterly for {cur_year}")
        quarterly = {"year": cur_year, "resetAt": f"{cur_year}-01-01T00:00:00Z",
            "quarters": {f"Q{i}": {"label": f"{cur_year} Q{i}", "events": []} for i in range(1, 5)}}

    quarter = f"Q{(now.month - 1) // 3 + 1}"
    if quarter not in quarterly.get("quarters", {}):
        quarterly["quarters"][quarter] = {"label": f"{cur_year} {quarter}", "events": []}

    # Add urgent/watch items as quarterly events
    existing_sigs = set()
    for qv in quarterly.get("quarters", {}).values():
        for ev in qv.get("events", []):
            existing_sigs.add(f"{ev.get('date')}-{ev.get('product','')}")

    for item in new_items:
        if item["priority"] in ("urgent", "watch"):
            sig = f"{today}-{item['title'][:20]}"
            if sig not in existing_sigs:
                quarterly["quarters"][quarter]["events"].append({
                    "date": today,
                    "product": item["title"][:30],
                    "type": "media",
                    "detail": item["detail"][:100],
                    "platform": item["platform"],
                })

    quarterly["lastUpdated"] = now.isoformat()
    save_json(QUARTERLY_FILE, quarterly)

    return len(new_items)


# ══════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════
def main():
    print("=" * 55)
    print("🎮 Game Market Intelligence — 3-Agent System")
    now = datetime.now(timezone.utc)
    print(f"   {now.strftime('%Y-%m-%d %H:%M UTC')} ({now.strftime('%A')})")
    print("=" * 55)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("\n⚠️ GEMINI_API_KEY not set!")
        print("  Set it in GitHub repo Settings → Secrets → Actions")
        print("  Get a free key at: https://aistudio.google.com/apikey")
        print("\n  Running in limited mode (RSS only)...")

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Always run Agent 3 (daily brief)
    agent3_daily_brief()

    if api_key:
        # Agent 2: Status update (every day)
        agent2_update_status()

        # Agent 1: Product discovery (every 3 days to save API quota)
        day_of_year = now.timetuple().tm_yday
        if day_of_year % 3 == 0 or "--discover" in sys.argv:
            agent1_discover_products()
        else:
            print(f"\n⏭ Agent 1 skipped (runs every 3 days, next: {3 - day_of_year % 3} days)")

        # Force all agents
        if "--all" in sys.argv:
            agent1_discover_products()
    else:
        print("\n⏭ Agent 1 & 2 skipped (no API key)")

    print("\n" + "=" * 55)
    print("✅ All agents complete!")
    print("=" * 55)


if __name__ == "__main__":
    main()
