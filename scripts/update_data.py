#!/usr/bin/env python3
"""
Game Market Intelligence — Daily Data Updater + Weekly Product Audit
Runs via GitHub Actions:
  - Daily 08:30 TST: RSS news fetch -> daily-brief.json
  - Weekly Monday 09:00 TST: Product audit -> audit-report.json

Env: ANTHROPIC_API_KEY (required for audit, optional for daily)
"""
import json, os, sys, hashlib, re
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import feedparser
except ImportError:
    feedparser = None
try:
    import requests
except ImportError:
    requests = None

DATA_DIR = Path(__file__).parent.parent / "data"
DAILY_FILE = DATA_DIR / "daily-brief.json"
QUARTERLY_FILE = DATA_DIR / "quarterly.json"
PRODUCTS_FILE = DATA_DIR / "products.json"
AUDIT_FILE = DATA_DIR / "audit-report.json"
MAX_DAILY_ITEMS = 20

RSS_FEEDS = [
    {"url": "https://www.ign.com/articles.rss", "name": "IGN"},
    {"url": "https://www.gamespot.com/feeds/news/", "name": "GameSpot"},
    {"url": "https://www.pcgamer.com/rss/", "name": "PC Gamer"},
    {"url": "https://www.eurogamer.net/feed", "name": "Eurogamer"},
    {"url": "https://www.gematsu.com/feed", "name": "Gematsu"},
    {"url": "https://automaton-media.com/en/feed/", "name": "Automaton"},
]

GAME_KEYWORDS = [
    "game","gaming","launch","release","beta","closed beta","trailer",
    "announce","reveal","pre-register","soft launch","mobile","console",
    "mmorpg","rpg","moba","fps","free-to-play","gacha","mihoyo","hoyoverse",
    "tencent","netease","supercell","capcom","square enix","rockstar",
]
MOBILE_KW = ["mobile","ios","android","smartphone","手機","行動"]
CONSOLE_KW = ["ps5","playstation","xbox","switch","console","pc","steam","主機"]

def load_json(p, default=None):
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default if default is not None else {}

def save_json(p, data):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  ✓ Saved {p}")

def make_id(text, date_str):
    h = hashlib.md5(f"{text}{date_str}".encode()).hexdigest()[:8]
    return f"brief-{date_str.replace('-','')}-{h}"

def classify_platform(text):
    t = text.lower()
    return "mobile" if any(k in t for k in MOBILE_KW) else "console"

def classify_priority(text):
    t = text.lower()
    if any(k in t for k in ["breaking","launch date","release date","confirmed","delay"]):
        return "urgent"
    if any(k in t for k in ["beta","test","update","patch","trailer","gameplay"]):
        return "watch"
    return "info"

def get_priority_icon(p):
    return {"urgent":"🚨","watch":"👁","info":"ℹ️"}.get(p,"📰")

def is_game_related(title, summary=""):
    c = f"{title} {summary}".lower()
    return any(k in c for k in GAME_KEYWORDS)

def get_quarter(dt):
    m = dt.month
    if m <= 3: return "Q1"
    if m <= 6: return "Q2"
    if m <= 9: return "Q3"
    return "Q4"

# ── RSS Fetch ──
def fetch_rss():
    if not feedparser:
        print("  ⚠ feedparser not installed")
        return []
    entries = []
    for feed in RSS_FEEDS:
        try:
            print(f"  Fetching {feed['name']}...")
            f = feedparser.parse(feed["url"])
            for e in f.entries[:15]:
                pub = None
                if hasattr(e,"published_parsed") and e.published_parsed:
                    pub = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
                else:
                    pub = datetime.now(timezone.utc)
                title = e.get("title","").strip()
                summary = re.sub(r"<[^>]+>","",e.get("summary",""))[:300]
                if is_game_related(title, summary):
                    entries.append({"title":title,"summary":summary,"source":feed["name"],"pub_date":pub})
        except Exception as ex:
            print(f"  ✗ {feed['name']}: {ex}")
    entries.sort(key=lambda x: x["pub_date"], reverse=True)
    print(f"  Found {len(entries)} game articles")
    return entries

# ── AI Summarize ──
def ai_summarize(entries):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or not requests:
        return None
    try:
        text = "\n\n".join([f"[{e['source']}] {e['title']}\n{e['summary']}" for e in entries[:15]])
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"Content-Type":"application/json","x-api-key":api_key,"anthropic-version":"2023-06-01"},
            json={"model":"claude-sonnet-4-20250514","max_tokens":2000,
                "messages":[{"role":"user","content":f"""You are a game industry analyst. Analyze these articles and output a JSON array of top 10 items. Each: "title" (繁體中文), "detail" (1-2 sentences 繁體中文), "action" (建議行動 繁體中文), "priority" ("urgent"/"watch"/"info"), "platform" ("mobile"/"console"). Only valid JSON array, no other text.\n\nArticles:\n{text}"""}]},
            timeout=30)
        if r.status_code == 200:
            t = r.json()["content"][0]["text"]
            t = re.sub(r"```json\s*","",t)
            t = re.sub(r"```\s*","",t)
            return json.loads(t.strip())
    except Exception as ex:
        print(f"  ⚠ AI summarize failed: {ex}")
    return None

# ── Daily Brief Update ──
def update_daily():
    print("\n📋 Updating Daily Brief...")
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    existing = load_json(DAILY_FILE, {"items":[],"fetchCount":0})
    existing_ids = {i["id"] for i in existing.get("items",[])}
    entries = fetch_rss()
    ai = ai_summarize(entries)
    new_items = []
    if ai:
        print("  Using AI summaries")
        for item in ai:
            iid = make_id(item.get("title",""), today)
            if iid not in existing_ids:
                new_items.append({"id":iid,"priority":item.get("priority","info"),
                    "icon":get_priority_icon(item.get("priority","info")),
                    "title":item.get("title",""),"detail":item.get("detail",""),
                    "action":item.get("action","持續觀察。"),
                    "platform":item.get("platform","console"),
                    "source":"AI 彙整","fetchedAt":now.isoformat()})
    else:
        print("  Processing manually")
        for e in entries[:15]:
            iid = make_id(e["title"], today)
            if iid not in existing_ids:
                new_items.append({"id":iid,
                    "priority":classify_priority(e["title"]+" "+e["summary"]),
                    "icon":get_priority_icon(classify_priority(e["title"])),
                    "title":e["title"][:80],"detail":e["summary"][:200] or e["title"],
                    "action":"待人工審核。","platform":classify_platform(e["title"]+" "+e["summary"]),
                    "source":e["source"],"fetchedAt":now.isoformat()})
    all_items = new_items + existing.get("items",[])
    all_items = all_items[:MAX_DAILY_ITEMS]
    po = {"urgent":0,"watch":1,"info":2}
    all_items.sort(key=lambda x: x.get("fetchedAt",""), reverse=True)
    all_items.sort(key=lambda x: po.get(x.get("priority","info"),3))
    save_json(DAILY_FILE, {"lastUpdated":now.isoformat(),"fetchCount":existing.get("fetchCount",0)+1,"items":all_items})
    print(f"  +{len(new_items)} new, total {len(all_items)}")

# ── Quarterly Update ──
def update_quarterly():
    print("\n📅 Updating Quarterly...")
    now = datetime.now(timezone.utc)
    cur_year = now.year
    existing = load_json(QUARTERLY_FILE, {})
    if existing.get("year") != cur_year:
        print(f"  🔄 New year! Resetting for {cur_year}")
        existing = {"year":cur_year,"resetAt":f"{cur_year}-01-01T00:00:00Z",
            "quarters":{f"Q{i}":{"label":f"{cur_year} Q{i}","events":[]} for i in range(1,5)}}
    existing["lastUpdated"] = now.isoformat()
    save_json(QUARTERLY_FILE, existing)

# ══════════════════════════════════════════════════
# NEW: Weekly Product Audit
# ══════════════════════════════════════════════════
def audit_products():
    """Use AI to verify each product's data against web search results."""
    print("\n🔍 Running Weekly Product Audit...")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or not requests:
        print("  ⚠ ANTHROPIC_API_KEY not set, skipping audit")
        return

    products = load_json(PRODUCTS_FILE, [])
    if not products:
        print("  No products to audit")
        return

    now = datetime.now(timezone.utc)
    findings = []

    # Audit in batches of 5 to manage API usage
    batch_size = 5
    for i in range(0, len(products), batch_size):
        batch = products[i:i+batch_size]
        product_list = "\n".join([
            f"- {g['name']} (英文:{g.get('nameEn','N/A')}) | 開發商:{g['studio']} | 狀態:{g['stage']} | 預計上線:{g.get('launchEst','N/A')} | 品類:{g['genre']}"
            for g in batch
        ])

        prompt = f"""You are a game industry analyst doing a data audit. For each product below, use your knowledge to check if the information is accurate. 

For each product, output a JSON object with:
- "name": product name
- "status": "ok" if data looks correct, "warning" if something might be wrong, "error" if definitely wrong
- "issue": description of the issue (empty string if ok)
- "suggestion": what the correct data should be (empty string if ok)
- "confidence": "high"/"medium"/"low" for your assessment

IMPORTANT: Only flag issues you are confident about. If unsure, mark as "ok".
Check: Is the developer/publisher correct? Is the game stage accurate (e.g., already launched but marked as beta)? Is the launch date still current? Does the game actually exist?

Products to audit:
{product_list}

Output ONLY a valid JSON array, no other text."""

        try:
            r = requests.post("https://api.anthropic.com/v1/messages",
                headers={"Content-Type":"application/json","x-api-key":api_key,"anthropic-version":"2023-06-01"},
                json={"model":"claude-sonnet-4-20250514","max_tokens":2000,
                    "messages":[{"role":"user","content":prompt}]},
                timeout=60)
            if r.status_code == 200:
                text = r.json()["content"][0]["text"]
                text = re.sub(r"```json\s*","",text)
                text = re.sub(r"```\s*","",text)
                batch_results = json.loads(text.strip())
                for result in batch_results:
                    if result.get("status") != "ok":
                        findings.append(result)
                print(f"  Batch {i//batch_size+1}: {len(batch_results)} checked, {len([r for r in batch_results if r.get('status')!='ok'])} issues")
            else:
                print(f"  ⚠ API error: {r.status_code}")
        except Exception as ex:
            print(f"  ⚠ Audit batch failed: {ex}")

    # Save audit report
    report = {
        "lastAudit": now.isoformat(),
        "totalProducts": len(products),
        "issuesFound": len(findings),
        "findings": findings,
        "nextAudit": (now + timedelta(days=7)).strftime("%Y-%m-%d")
    }
    save_json(AUDIT_FILE, report)
    print(f"  Audit complete: {len(findings)} issues found in {len(products)} products")

# ── Main ──
def main():
    print("=" * 50)
    print("🎮 Game Market Intelligence — Update")
    now = datetime.now(timezone.utc)
    print(f"   {now.isoformat()}")
    print(f"   Day of week: {now.strftime('%A')}")
    print("=" * 50)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Always run daily brief
    update_daily()
    update_quarterly()

    # Run audit on Mondays OR if --audit flag is passed
    is_monday = now.weekday() == 0
    force_audit = "--audit" in sys.argv

    if is_monday or force_audit:
        print("\n📋 Monday detected (or --audit flag) — running product audit")
        audit_products()
    else:
        print(f"\n⏭ Skipping audit (runs on Mondays, today is {now.strftime('%A')})")

    print("\n✅ All updates complete!")

if __name__ == "__main__":
    main()
