#!/usr/bin/env python3
"""
Game Market Intelligence — 4-Agent v5 (Scrape First, AI Analyze, Priority-Aware)
Architecture: Free RSS scraping → Gemini analysis (3~4 API calls)

Step 1: Scrape RSS feeds (巴哈GNN, 4Gamers, Gematsu, IGN, GameLook, 遊資網 etc) (FREE)
Step 2: Gemini Call 1 → Process scraped data into daily brief + industry trends
Step 3: Gemini Call 2 → Update 5~8 existing products (rotating)
Step 4: Gemini Call 3 → Discover new products, prioritizing 二游/大IP回憶向/大廠新作
Step 5: Gemini Call 4 (every N days) → 版號雷達：中國遊戲版號公示新品掃描

v5 changes vs v4:
- Expanded RSS sources incl. mobile/二游-focused feeds (config-overridable)
- Broader keyword filters (復刻/重啟/手遊化/IP改編/版號/二游 etc.)
- Agent1 prompt explicitly asks for 二游/大IP回憶向/大廠新作/版號公示中的新品
  and requires the model to self-rate "threat" + "threatReason"
- Full existing-product list sent for dedup (was truncated to 40)
- New Agent4: 版號雷達 (license-registry radar), runs every N days
- High-threat new discoveries are auto-pushed into daily-brief as urgent items
"""
import json, os, sys, hashlib, re, time, socket
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests as req
except ImportError:
    req = None
try:
    import feedparser
except ImportError:
    feedparser = None

DATA = Path(__file__).parent.parent / "data"
CONFIG = Path(__file__).parent.parent / "config.json"
PRODUCTS = DATA / "products.json"
DAILY = DATA / "daily-brief.json"
QUARTERLY = DATA / "quarterly.json"
AUDIT = DATA / "audit-report.json"
INDUSTRY = DATA / "industry-trends.json"
OVERRIDES = DATA / "overrides.json"
MODEL = "gemini-2.5-flash"

# ══════════════════════════════════════════
# Shared helpers
# ══════════════════════════════════════════

def load(p, d=None):
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return d if d is not None else {}

def save(p, d):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    print(f" ✓ {p.name}")

def get_config():
    c = load(CONFIG, {})
    mp = c.get("myProduct", {})
    return c, mp

def gemini(prompt, search=False, tokens=8000):
    key = os.environ.get("GEMINI_API_KEY")
    if not key or not req:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={key}"
    attempts = []
    if search:
        attempts.append(True)
    attempts.append(False)
    for use_s in attempts:
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"maxOutputTokens": tokens, "temperature": 0.2}}
        if use_s:
            body["tools"] = [{"google_search": {}}]
        for retry in range(3):
            try:
                r = req.post(url, json=body, timeout=90)
                if r.status_code == 200:
                    d = r.json()
                    c = d.get("candidates", [])
                    if c:
                        finish = c[0].get("finishReason", "")
                        if finish == "MAX_TOKENS":
                            print(f" ⚠️ Hit maxOutputTokens ({tokens}) — response was cut off, raise the tokens= value for this call")
                        text = " ".join(p.get("text", "") for p in c[0].get("content", {}).get("parts", []) if "text" in p).strip()
                        print(f" ✅ Gemini: {len(text)} chars")
                        return text
                    return None
                elif r.status_code == 429:
                    if use_s and retry == 0:
                        print(" ⚠️ Search blocked, fallback...")
                        break
                    w = 15 * (retry + 1)
                    print(f" ⏳ 429 {w}s...")
                    time.sleep(w)
                elif r.status_code == 503:
                    w = 20 * (retry + 1)
                    print(f" ⏳ 503 {w}s...")
                    time.sleep(w)
                else:
                    print(f" ❌ {r.status_code}")
                    return None
            except req.exceptions.Timeout:
                if retry < 2:
                    print(f" ⏳ Timeout, retrying ({retry + 1}/3)...")
                    continue
                print(" ❌ Timed out after 3 attempts")
                return None
            except Exception as e:
                print(f" ❌ {e}")
                return None
    return None

def parse_json(text):
    """Parse a JSON array from a Gemini response, salvaging whatever complete
    objects exist even if the response was truncated mid-object (hit
    maxOutputTokens). The old version required the whole array to be
    perfectly balanced — one truncated object at the tail meant the ENTIRE
    batch was discarded, even when 5+ earlier candidates were fully formed
    and valid. This is what silently dropped every Agent1/Agent2 candidate
    in production (log showed 'Parse failed' despite a well-formed prefix)."""
    if not text:
        return None
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    text = text.strip()

    # Fast path: response is already valid JSON.
    try:
        return json.loads(text)
    except Exception:
        pass

    # Salvage path: walk the text from the first '[', pull out each complete
    # top-level {...} object one at a time (string-aware, so braces inside
    # quoted values don't confuse the depth counter), and stop the moment we
    # hit an object that doesn't close — that's the truncated tail, discard
    # just that one instead of everything before it.
    start = text.find("[")
    if start == -1:
        return None
    body = text[start + 1:]
    items = []
    i, n = 0, len(body)
    while i < n:
        while i < n and body[i] in " \t\n\r,":
            i += 1
        if i >= n or body[i] != "{":
            break
        depth, j, in_str, esc = 0, i, False, False
        while j < n:
            ch = body[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        break
            j += 1
        if depth != 0:
            break  # truncated mid-object — this is the cut-off tail, stop here
        try:
            items.append(json.loads(body[i:j + 1]))
        except Exception:
            break
        i = j + 1
    if items:
        print(f" 🔧 Salvaged {len(items)} complete item(s) from a truncated/malformed response")
        return items
    return None

def normalize_platform(platform_list):
    """The frontend's mobile/console tab split depends on an exact-string
    check (g.platform.includes('Mobile')). Existing data keeps granular
    platform names (PS5, Switch 2, Xbox Series, iOS, Android, Mac, Quest,
    PS VR2, Consoles, etc.) rather than collapsing them — so we must NOT
    flatten everything into a generic 'Console' token, or we'd throw away
    detail that's present throughout the real dataset and make new
    discoveries look coarser than everything else on the board. We only
    fix the one thing that actually breaks the tab split: casing/wording
    variants of "mobile" that wouldn't match the literal 'Mobile' check."""
    if not platform_list:
        return ["Mobile"]
    tokens = []
    for p in platform_list:
        s = str(p).strip()
        if s.lower() in ("mobile", "ios", "android"):
            tokens.append("Mobile")
        elif s.lower() == "pc":
            tokens.append("PC")
        else:
            tokens.append(s)  # keep as-is: PS5, Switch 2, Xbox Series, Consoles, Mac, Quest, etc.
    return tokens or ["Mobile"]

def brief_platform_tag(platform_list):
    """BriefView filters items with an exact 'mobile'/'console' string match —
    anything else silently disappears from both tabs. Always resolve to one
    of those two literal values."""
    norm = normalize_platform(platform_list)
    return "mobile" if "Mobile" in norm else "console"

def normalize_name(name):
    """Exact-string dedup let through confirmed duplicates in production data
    (e.g. '幻想水滸傳 STAR LEAP' vs '幻想水滸傳：星躍' — same game, added twice
    8 days apart because a colon/spacing difference bypassed `n in existing`).
    Strip whitespace/punctuation before comparing so near-identical names
    collide correctly."""
    if not name:
        return ""
    return re.sub(r"[\s:：\-－_·,\.，。]+", "", str(name).lower())

def is_placeholder(v):
    """Detects the '（請填入...）' style placeholder text left in a fresh config.json
    so we don't feed literal placeholder strings into a Gemini prompt as if they
    were real product data."""
    if not v or not isinstance(v, str):
        return not v
    return "請填入" in v or v.strip() in ("", "TBD", "N/A")

def build_watch_context(config, my):
    """Turn config.json's myProduct/watchGenres/watchRegions into a short prompt
    fragment so discovery threat-scoring is relative to the user's actual product,
    not a generic guess. Returns "" if myProduct is still unfilled placeholder data."""
    genres = config.get("watchGenres", [])
    regions = config.get("watchRegions", [])
    has_real_product = my and not is_placeholder(my.get("name")) and not is_placeholder(my.get("genre"))
    lines = []
    if has_real_product:
        lines.append(
            f"我方正在開發的產品：{my.get('name')}，類型：{my.get('genre')}，"
            f"平台：{','.join(my.get('platform', []))}，目標市場：{my.get('targetMarket','?')}，"
            f"預計上線：{my.get('launchWindow','?')}，核心賣點：{my.get('coreFeatures','?')}。"
            f"請優先評估新發現的產品是否會與我方產品搶佔同一群受眾/檔期，這類重疊度高的請標記為 threat=high。"
        )
    if genres:
        lines.append(f"我們重點關注的品類：{', '.join(genres)}。")
    if regions:
        lines.append(f"我們重點關注的市場：{', '.join(regions)}。")
    return "\n".join(lines)

def new_brief_id(today, title):
    return f"brief-{today.replace('-', '')}-{hashlib.md5(title.encode()).hexdigest()[:8]}"

def push_urgent_brief(items, tag="🚨新品雷達"):
    """Inject high-threat discoveries straight into the daily brief so they
    don't get buried until someone happens to scroll the products list."""
    if not items:
        return
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    existing = load(DAILY, {"items": [], "fetchCount": 0})
    eids = {i["id"] for i in existing.get("items", [])}
    added = 0
    for it in items:
        title = f"{tag}: {it.get('name', '')}"
        iid = new_brief_id(today, title)
        if iid in eids:
            continue
        existing.setdefault("items", []).insert(0, {
            "id": iid,
            "priority": "urgent",
            "icon": "🚨",
            "title": f"{it.get('name','')}（新發現・高威脅）",
            "detail": it.get("threatReason", it.get("desc", ""))[:60],
            "action": "加入追蹤並評估上線時程",
            "platform": brief_platform_tag(it.get("platform")),
            "source": it.get("sourceType", "Agent發現"),
            "fetchedAt": now.isoformat(),
        })
        added += 1
    if added:
        items_all = existing["items"][:20]
        po = {"urgent": 0, "watch": 1, "info": 2}
        items_all.sort(key=lambda x: x.get("fetchedAt", ""), reverse=True)
        items_all.sort(key=lambda x: po.get(x.get("priority", "info"), 3))
        existing["items"] = items_all
        existing["lastUpdated"] = now.isoformat()
        save(DAILY, existing)
        print(f" 🚨 +{added} urgent brief item(s) from discovery")

# ══════════════════════════════════════════
# STEP 1: Free RSS Scraping (NO API cost)
# ══════════════════════════════════════════

# Words that mark a headline as plausibly relevant to *new / changing* mobile
# game products — deliberately broader than "有新遊戲上線" so that reboots,
# IP tie-ins and license announcements aren't filtered out before Gemini
# even sees them.
GAME_KEYWORDS = [
    "遊戲", "手遊", "上線", "封測", "開測", "公測", "預約", "事前登錄", "新作",
    "復刻", "重啟", "回歸", "手遊化", "IP改編", "代理", "代理權", "二游", "二次元",
    "抽卡", "版號", "定檔", "續作", "改編", "聯動", "周年",
    "game", "launch", "beta", "trailer", "announce", "mobile", "console",
    "rpg", "mmo", "mmorpg", "update", "release", "pre-register", "gacha",
]

def scrape_rss():
    """Scrape all configured RSS feeds. Returns list of articles.
    Feeds marked "trusted": True are assumed to be 100% game-focused already
    (e.g. dedicated mobile-game trade press) and skip the keyword filter —
    that filter exists to strip noise from general tech/entertainment feeds,
    not to gatekeep sources that are already on-topic.
    """
    print("\n📡 Step 1: RSS Scraping (free)")
    config, _ = get_config()
    feeds = config.get("rssFeeds", [
        {"url": "https://gnn.gamer.com.tw/rss.xml", "name": "巴哈GNN", "trusted": False},
        {"url": "https://www.4gamers.com.tw/rss/latest-news", "name": "4Gamers", "trusted": False},
        {"url": "https://www.gematsu.com/feed", "name": "Gematsu", "trusted": False},
        {"url": "https://www.ign.com/articles.rss", "name": "IGN", "trusted": False},
        {"url": "https://automaton-media.com/en/feed/", "name": "Automaton", "trusted": False},
        # Mobile / 二游-leaning sources — check these URLs still resolve before relying on them
        {"url": "https://www.gamelook.com.cn/feed", "name": "GameLook", "trusted": True},
        {"url": "https://www.youxituoluo.com/feed", "name": "遊資網", "trusted": True},
        {"url": "https://www.gamersky.com/rss/", "name": "遊民星空", "trusted": True},
    ])
    all_articles = []
    if not feedparser:
        print(" ⚠️ feedparser not installed")
        return all_articles

    for feed in feeds:
        try:
            socket.setdefaulttimeout(15)  # feedparser has no native timeout param — guard at the socket level
            f = feedparser.parse(feed["url"])
            count = 0
            trusted = feed.get("trusted", False)
            for e in f.entries[:30]:
                title = e.get("title", "").strip()
                summary = re.sub(r"<[^>]+>", "", e.get("summary", ""))[:400]
                combined = f"{title} {summary}".lower()
                relevant = trusted or any(kw in combined for kw in GAME_KEYWORDS)
                if relevant:
                    pub = None
                    if hasattr(e, "published_parsed") and e.published_parsed:
                        pub = datetime(*e.published_parsed[:6], tzinfo=timezone.utc).isoformat()
                    all_articles.append({
                        "title": title, "summary": summary,
                        "source": feed["name"],
                        "link": e.get("link", ""),
                        "date": pub or datetime.now(timezone.utc).isoformat(),
                    })
                    count += 1
            print(f" {feed['name']}: {count} game articles" + (" (trusted, no filter)" if trusted else ""))
        except Exception as ex:
            print(f" ❌ {feed['name']}: {ex}")
    print(f" Total scraped: {len(all_articles)} articles")
    return all_articles

# ══════════════════════════════════════════
# AGENT 3: Daily Brief from scraped data (1 API call)
# ══════════════════════════════════════════

def agent3(articles):
    print("\n📋 Agent 3: Daily Brief + Industry")
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    existing = load(DAILY, {"items": [], "fetchCount": 0})
    eids = {i["id"] for i in existing.get("items", [])}
    config, my = get_config()

    article_text = "\n".join([f"[{a['source']}] {a['title']}\n{a['summary'][:150]}" for a in articles[:25]])
    prompt = f"""Today is {today}. Here are today's game news headlines:

{article_text}

Pick the 6 most important items. Output a JSON array (NOT object). Each item:
{{"title":"繁中標題(15字內)","detail":"繁中摘要(30字內)","action":"建議(15字內)","priority":"urgent/watch/info","platform":"mobile/console","source":"來源"}}
Keep VERY short. Output ONLY valid JSON array, no markdown."""
    r = gemini(prompt)
    p = parse_json(r)
    icons = {"urgent": "🚨", "watch": "👁", "info": "ℹ️"}
    new_items = []
    if p and isinstance(p, list):
        for it in p:
            iid = new_brief_id(today, it.get("title", ""))
            if iid not in eids:
                new_items.append({
                    "id": iid, "priority": it.get("priority", "info"),
                    "icon": icons.get(it.get("priority"), "📰"),
                    "title": it.get("title", ""), "detail": it.get("detail", ""),
                    "action": it.get("action", ""), "platform": it.get("platform", "mobile"),
                    "source": it.get("source", ""), "fetchedAt": now.isoformat(),
                })
    else:
        print(f" ⚠️ Parse failed: {str(r)[:200] if r else 'None'}")

    all_i = new_items + existing.get("items", [])
    all_i = all_i[:20]
    po = {"urgent": 0, "watch": 1, "info": 2}
    all_i.sort(key=lambda x: x.get("fetchedAt", ""), reverse=True)
    all_i.sort(key=lambda x: po.get(x.get("priority", "info"), 3))
    save(DAILY, {"lastUpdated": now.isoformat(), "fetchCount": existing.get("fetchCount", 0) + 1, "items": all_i})
    print(f" ✅ +{len(new_items)} items, total {len(all_i)}")

    if new_items:
        ind_kw = ["財報", "版號", "收購", "市場", "AI", "出海", "融資", "營收", "投資", "合併"]
        ind = [{"title": it["title"], "detail": it["detail"], "category": "市場",
                "pmInsight": it.get("action", ""), "source": it.get("source", ""), "date": today}
               for it in new_items if any(k in it.get("title", "") for k in ind_kw)]
        if ind:
            save(INDUSTRY, {"lastUpdated": now.isoformat(), "trends": ind})
            print(f" ✅ {len(ind)} industry trends")

    q = load(QUARTERLY, {})
    cy = now.year
    if q.get("year") != cy:
        q = {"year": cy, "resetAt": f"{cy}-01-01T00:00:00Z", "quarters": {f"Q{i}": {"label": f"{cy} Q{i}", "events": []} for i in range(1, 5)}}
    qk = f"Q{(now.month - 1) // 3 + 1}"
    if qk not in q.get("quarters", {}):
        q["quarters"][qk] = {"label": f"{cy} {qk}", "events": []}
    q["lastUpdated"] = now.isoformat()
    save(QUARTERLY, q)

# ══════════════════════════════════════════
# AGENT 2: Status Update (1 API call, rotating batch)
# ══════════════════════════════════════════

def agent2():
    print("\n🔄 Agent 2: Status Update")
    config, _ = get_config()
    batch_size = config.get("agent2BatchSize", 6)
    products = load(PRODUCTS, [])
    if not products:
        return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now = datetime.now(timezone.utc)

    # Manual overrides (set via the frontend, written by the Apps Script proxy)
    # win over anything the model decides here — once a person has locked in a
    # threat level or excluded a product, Agent2 shouldn't keep re-litigating
    # it every time it happens to rotate onto that product.
    overrides = load(OVERRIDES, {})
    locked_ids = {str(k) for k, v in overrides.items() if v.get("threat") or v.get("excluded")}

    for g in products:
        if "lastChecked" not in g:
            g["lastChecked"] = "2000-01-01"
    products.sort(key=lambda g: g["lastChecked"])
    batch = products[:batch_size]
    print(f" Checking: {', '.join(g['name'] for g in batch)}")

    info = "\n".join([f"- {g['name']} | 狀態:{g['stage']} | 開發商:{g.get('developer','?')} | 上線:{g.get('launchEst','?')} | 測試:{g.get('testType','?')} {g.get('testDateStart','?')}~{g.get('testDateEnd','?')} | 目前威脅:{g.get('threat','?')} | 目前分類:{g.get('category','?')}" for g in batch])
    prompt = f"""Today is {today}. Check these games for concrete status changes AND re-evaluate their threat level.

Games:
{info}

For EACH game, search and check:
1. Has beta/test ENDED? When exactly?
2. Has LAUNCH DATE been confirmed or changed?
3. Has the game LAUNCHED? On which platforms/regions?
4. Any delay, shutdown, or new test phase?
5. 重新評估威脅等級——不要假設舊的威脅等級一定還對，根據目前實際市場地位/營收/話題度重新判斷：
   - critical: 現象級大作，長期霸榜、話題度極高
   - high: 具體威脅，二次元/大IP/大廠自研，規模明顯
   - medium: 中等規模，仍待觀察
   - low: 小型/長尾，已無明顯威脅
6. 如果這款產品分類是「tracking」（長期營運)，評估是否有明顯衰退訊號（營收/玩家熱度/話題度持續下滑、遠不如上線初期)——只有證據明確時才回報 true，不確定就回報 false
7. 確認實際已上線/預計上線的地區——如果目前資料是「待確認」或明顯不完整，查證並填入實際地區（例如 "CN/Global"、"TW/HK/MO"、"JP"、"US" 等)

Output JSON array:
- "name": exact name
- "statusChanged": true/false
- "newStage": "announced"/"pre-reg"/"cbt"/"soft-launch"/"live-ops" or null
- "newLaunchEst": updated date or null
- "testDateUpdate": test end date or null
- "recentEvent": event description or null
- "shouldBeTracking": true if live 1+ year
- "shouldRemove": true if dead
- "removeReason": or null
- "newThreat": "critical"/"high"/"medium"/"low"（跟目前不同才填，否則填 null）
- "newThreatReason": 一句話說明為什麼調整（跟目前相同則填 null）
- "newSentiment": 0-100 的整數，根據目前真實市場表現重新估算（不是憑空給分)
- "isDeclining": true/false（只有分類是 tracking 的產品才需要判斷,其他填 false）
- "declineReason": 一句話說明衰退證據，isDeclining 為 false 則填 null
- "newRegion": 確認後的實際地區字串（跟目前相同或無法確認則填 null，不要亂猜)
- "newLaunchRegions": 逐地區上線狀態陣列（格式同 Agent1，找到具體地區資訊才填,不確定就填 null 不要覆蓋原本資料)
- "pmAnalysis": 繁體中文 PM analysis, format: "【威脅類型】描述\\n\\n▶ 建議行動：\\n(1)...\\n(2)...\\n(3)..."
- "directLinks": [{{"label":"TapTap","url":"real URL"}},{{"label":"官網","url":"URL"}}]

Output ONLY valid JSON array."""
    r = gemini(prompt, search=True, tokens=32000)
    p = parse_json(r)
    updates = 0
    findings = []
    if p and isinstance(p, list):
        print(f" Got {len(p)} results")
        for u in p:
            n = u.get("name", "")
            prod = next((g for g in products if g["name"] == n), None)
            if not prod:
                continue
            prod["lastChecked"] = today
            if u.get("statusChanged") and u.get("newStage"):
                old = prod["stage"]
                prod["stage"] = u["newStage"]
                prod["updatedAt"] = today
                if prod.get("history"):
                    prod["history"].append({"date": now.strftime("%Y-%m"), "s": u["newStage"]})
                updates += 1
                findings.append({"name": n, "status": "warning", "issue": f"狀態: {old}→{u['newStage']}", "autoFixed": True})
                print(f" ✏️ {n}: {old} → {u['newStage']}")
            if u.get("newLaunchEst") and u["newLaunchEst"] != prod.get("launchEst"):
                old = prod.get("launchEst", "")
                prod["launchEst"] = u["newLaunchEst"]
                prod["updatedAt"] = today
                updates += 1
                print(f" 📅 {n}: {old} → {u['newLaunchEst']}")
            if u.get("newRegion") and u["newRegion"] != prod.get("region"):
                old_r = prod.get("region", "?")
                prod["region"] = u["newRegion"]
                prod["updatedAt"] = today
                updates += 1
                print(f" 🌏 {n}: 地區 {old_r} → {u['newRegion']}")
            if u.get("newLaunchRegions") and isinstance(u["newLaunchRegions"], list) and len(u["newLaunchRegions"]) > 0:
                prod["launchRegions"] = u["newLaunchRegions"]
                prod["updatedAt"] = today
                updates += 1
                print(f" 🗺️ {n}: 補上逐地區狀態 ({len(u['newLaunchRegions'])} 個地區)")
            if u.get("newThreat") and u["newThreat"] != prod.get("threat"):
                if str(prod.get("id")) in locked_ids:
                    print(f" 🔒 {n}: 已手動鎖定，略過威脅重新評估（模型建議: {u['newThreat']}）")
                else:
                    old_t = prod.get("threat", "?")
                    prod["threat"] = u["newThreat"]
                    prod["threatReason"] = u.get("newThreatReason", prod.get("threatReason", ""))
                    prod["updatedAt"] = today
                    updates += 1
                    print(f" ⚠️ {n}: 威脅 {old_t} → {u['newThreat']} ({u.get('newThreatReason','')})")
            if u.get("newSentiment") is not None and str(prod.get("id")) not in locked_ids:
                try:
                    ns = int(u["newSentiment"])
                    if 0 <= ns <= 100 and ns != prod.get("sentiment"):
                        prod["sentiment"] = ns
                        prod["updatedAt"] = today
                        updates += 1
                except (ValueError, TypeError):
                    pass
            if u.get("isDeclining") and prod.get("category") == "tracking" and str(prod.get("id")) not in locked_ids:
                prod["category"] = "excluded"
                prod["excludedReason"] = u.get("declineReason", "")
                prod["excludedAt"] = today
                prod["updatedAt"] = today
                updates += 1
                print(f" 📉 {n}: 明顯衰退，移入已排除 ({u.get('declineReason','')})")
            if u.get("testDateUpdate"):
                prod["testDateEnd"] = u["testDateUpdate"]
                prod["updatedAt"] = today
                updates += 1
                print(f" 🧪 {n}: end → {u['testDateUpdate']}")
            if u.get("pmAnalysis") and len(u["pmAnalysis"]) > 30:
                prod["threatAnalysis"] = u["pmAnalysis"]
                prod["updatedAt"] = today
                updates += 1
            if u.get("directLinks") and isinstance(u["directLinks"], list) and len(u["directLinks"]) > 0:
                prod["sourceLinks"] = u["directLinks"]
                prod["updatedAt"] = today
                updates += 1
            if u.get("shouldBeTracking") and prod.get("category") != "tracking":
                prod["category"] = "tracking"
                prod["updatedAt"] = today
                updates += 1
            if u.get("recentEvent"):
                prod["currentEvent"] = u["recentEvent"]
            if u.get("recentEvent") and prod.get("category") == "tracking":
                prod["currentEvent"] = u["recentEvent"]
                prod["eventNote"] = ("⚡ " if any(k in u["recentEvent"] for k in ["週年", "慶典", "新版本", "聯動", "大型"]) else "") + u["recentEvent"]
            if u.get("shouldRemove"):
                findings.append({"name": n, "status": "error", "issue": f"移除: {u.get('removeReason','')}", "autoFixed": False})
    else:
        print(f" ⚠️ Parse failed: {str(r)[:200] if r else 'None'}")

    products.sort(key=lambda g: g.get("id", 0))
    if updates:
        save(PRODUCTS, products)
    print(f" ✅ {updates} updates")
    next_batch_names = [g["name"] for g in products if g["lastChecked"] < today][:batch_size]
    next_audit = (
        f"下次執行時將檢查：{', '.join(next_batch_names)}" if next_batch_names
        else "所有產品已於今日檢查完畢，下輪從最舊的重新開始"
    )
    save(AUDIT, {
        "lastAudit": now.isoformat(), "totalProducts": len(products), "updatesApplied": updates,
        "issuesFound": len(findings), "nextAudit": next_audit,
        "findings": findings, "checked": [g["name"] for g in batch],
        "nextBatch": next_batch_names,
    })

# ══════════════════════════════════════════
# AGENT 1: Product Discovery (1 API call, uses scraped data)
# ══════════════════════════════════════════

DISCOVERY_KEYWORDS = [
    "上線", "開測", "封測", "公測", "預約", "launch", "beta", "release", "announce",
    "new game", "新作", "復刻", "重啟", "回歸", "手遊化", "IP改編", "代理", "代理權",
    "二游", "二次元", "抽卡", "版號", "定檔", "續作",
]

def agent1(articles):
    print("\n🔍 Agent 1: Product Discovery")
    config, my = get_config()
    products = load(PRODUCTS, [])
    existing = {g["name"] for g in products}
    mid = max((g["id"] for g in products), default=0)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    game_articles = [a for a in articles if any(kw in a["title"] for kw in DISCOVERY_KEYWORDS)]
    # Don't starve Gemini of raw material — fall back to the full scraped set
    # if the keyword pass came back thin (better to give it more signal than
    # to silently under-feed the discovery step).
    if len(game_articles) < 8:
        game_articles = articles
    article_text = "\n".join([f"- [{a['source']}] {a['title']}" for a in game_articles[:30]])
    current = ", ".join(sorted(existing))  # full list — no truncation, avoid re-suggesting duplicates
    existing_norm = {normalize_name(g["name"]) for g in products} | {normalize_name(g.get("nameEn", "")) for g in products if g.get("nameEn")}
    watch_context = build_watch_context(config, my)

    prompt = f"""從以下新聞與你自身知識（含網路搜尋）中，找出「還沒被我們追蹤」的新手遊。

{watch_context}

請特別優先找這幾類產品（這些是最容易被一般新聞漏掉、但威脅性最高的類型)：
1. 二次元/抽卡手遊（原神、崩壞類競品，或新公布的二游）
2. 大型IP手遊化 / 經典IP重啟或復刻（含童年向、經典端遊改手遊，例如「賽爾號」這類回憶向產品)
3. 大廠（騰訊、網易、米哈遊、莉莉絲、疊紙、鷹角、Nexon、Netmarble等）公告的新作
4. 中國版號公示名單中新出現、但市場上還沒被廣泛報導的遊戲

請避免收錄以下類型，除非它是現象級大作（critical等級)：西方/日系小型獨立遊戲、視覺小說、單人PC/主機解謎或平台跳躍遊戲。這類產品雜訊會佔用發現名額，稀釋真正該追蹤的手遊/二游訊號，只有在它是家喻戶曉的大作（例如GTA、戰神等級)時才收錄。

新聞素材：
{article_text}

已追蹤清單（避免重複建議，共{len(existing)}款)：
{current}

找 8-12 款新遊戲，每款輸出：
- "name": 繁體中文名稱
- "nameEn": 英文/原文名稱
- "developer": 開發商
- "genre": 類型（中文，2-3字）
- "platform": ["Mobile"] 或 ["PC","Mobile"]
- "region": 目前已知或推估的上線/營運地區，例如 "CN/Global"、"TW/HK/MO"、"JP"、"KR/Global"、"US"、"Global"——不確定就盡量推估開發商/發行商所屬市場，避免直接填「待確認」
- "launchRegions": 逐地區的上線狀態陣列，每筆格式 {{"region":"CN","status":"已上線/封測中/預註冊/未上線","date":"2026-07-09"}}，region 用 "CN"/"TW/HK/MO"/"JP"/"KR"/"Global" 這幾種標準寫法。只列出實際有查到資訊的地區，沒有把握的地區不要編造，找不到任何地區資訊就給空陣列 []
- "stage": "announced"/"pre-reg"/"cbt"/"live-ops"
- "launchEst": "2026-07" 或 "2026-Q3"
- "desc": 一句話繁體中文描述
- "threat": "critical"/"high"/"medium"/"low"，判斷標準：
  - critical: 現象級大作——國民級懷念IP復刻（例如「賽爾號」這類全民童年記憶產品)、頭部大廠（米哈遊/騰訊/網易等)自研旗艦新IP、或話題度極高的頭部二游，預期會大規模搶佔市場注意力
  - high: 二游/大IP/回憶向/大廠自研，規模明顯但未達現象級
  - medium: 中等規模、仍待觀察
  - low: 小型/長尾產品
- "threatReason": 一句話說明威脅等級理由
- "sourceType": "新聞報導"/"版號公示"/"官方公告"/"模型知識推論"

Output ONLY valid JSON array, no markdown."""
    r = gemini(prompt, search=True, tokens=12000)
    p = parse_json(r)
    new_p = []
    high_threat = []
    if p and isinstance(p, list):
        print(f" Found {len(p)} candidates")
        seen = set()
        for d in p:
            n = d.get("name", "")
            n_norm = normalize_name(n)
            nen_norm = normalize_name(d.get("nameEn", ""))
            if not n or n in existing or n_norm in seen or n_norm in existing_norm or (nen_norm and nen_norm in existing_norm):
                if n and (n in existing or n_norm in existing_norm):
                    print(f" ⏭ {n} (exists)")
                continue
            seen.add(n_norm)
            mid += 1
            threat = d.get("threat", "medium")
            reason = d.get("threatReason", "")
            source_type = d.get("sourceType", "模型知識推論")
            entry = {
                "id": mid, "name": n, "nameEn": d.get("nameEn", ""),
                "developer": d.get("developer", "未知"), "studio": d.get("developer", "未知"),
                "publisher": d.get("developer", "未知"), "genre": d.get("genre", "未知"),
                "platform": normalize_platform(d.get("platform", ["Mobile"])), "region": d.get("region", "待確認"), "model": "F2P",
                "stage": d.get("stage", "announced"), "threat": threat,
                "threatReason": reason, "prereg": None, "sentiment": 70,
                "launchEst": d.get("launchEst", "待定"), "desc": d.get("desc", ""), "tags": [source_type],
                # Seed threatAnalysis with the discovery reason so it's visible in the
                # Modal/ThreatView immediately — otherwise that panel stays empty until
                # Agent2 happens to rotate onto this product and overwrites it with a
                # fuller analysis.
                "threatAnalysis": f"【自動發現・{source_type}】{reason}" if reason else "",
                "verified": f"Agent 發現 ({today})",
                "category": "active", "testType": "待確認", "testDateStart": "待確認",
                "testDateEnd": "待確認", "sourceLinks": [], "launchRegions": d.get("launchRegions", []),
                "history": [{"date": datetime.now(timezone.utc).strftime("%Y-%m"), "s": d.get("stage", "announced")}],
                "updatedAt": today, "addedAt": today, "lastChecked": "2000-01-01", "autoDiscovered": True,
                "sourceType": source_type,
            }
            new_p.append(entry)
            if entry["threat"] in ("critical", "high"):
                high_threat.append(entry)
            print(f" + {n} ({d.get('developer','?')}) [{d.get('stage','')}] threat={entry['threat']}")
    else:
        print(f" ⚠️ Parse failed: {str(r)[:200] if r else 'None'}")

    if new_p:
        products.extend(new_p)
        save(PRODUCTS, products)
        print(f" ✅ +{len(new_p)} new products (total: {len(products)})")
    else:
        print(" No new products")
    return new_p, high_threat

# ══════════════════════════════════════════
# AGENT 4: 版號雷達 — China license-registry radar
# Runs every N days (config: licenseRadarIntervalDays, default 3) since
# license batches are published in bulk, not daily. This is the earliest
# public signal for Chinese mobile games and tends to surface 二游/大IP
# titles before general game media picks them up.
# ══════════════════════════════════════════

def agent4():
    print("\n🛰️ Agent 4: 版號雷達 (license radar)")
    config, my = get_config()
    products = load(PRODUCTS, [])
    existing = {g["name"] for g in products}
    mid = max((g["id"] for g in products), default=0)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    current = ", ".join(sorted(existing))
    existing_norm = {normalize_name(g["name"]) for g in products} | {normalize_name(g.get("nameEn", "")) for g in products if g.get("nameEn")}
    watch_context = build_watch_context(config, my)

    prompt = f"""請搜尋最近一批中國國家新闻出版署公布的遊戲版號審批名單（游戏版号公示），
找出名單中「明顯屬於二次元/抽卡/大型IP改編/知名大廠」的新遊戲，且不在下方已追蹤清單中。

{watch_context}

已追蹤清單（避免重複)：
{current}

找 5-10 款，每款輸出：
- "name": 繁體中文名稱
- "developer": 送審/開發公司
- "genre": 類型
- "platform": ["Mobile"]
- "region": 目前已知或推估的地區，例如 "CN"、"CN/Global"——版號公示對象通常至少確定是 CN
- "launchRegions": 逐地區上線狀態陣列，格式同 {{"region":"CN","status":"...","date":"..."}}，沒把握就給空陣列 []
- "stage": "announced"
- "launchEst": "待定" 或推估時間
- "desc": 一句話描述
- "threat": "critical"/"high"/"medium"/"low"（critical=國民級懷念IP復刻或頭部大廠自研旗艦新IP等現象級大作)
- "threatReason": 一句話理由
- "sourceType": "版號公示"

若找不到明確資料，回傳空陣列 []。
Output ONLY valid JSON array, no markdown."""
    r = gemini(prompt, search=True, tokens=10000)
    p = parse_json(r)
    new_p = []
    high_threat = []
    if p and isinstance(p, list):
        print(f" Found {len(p)} license candidates")
        seen = set()
        for d in p:
            n = d.get("name", "")
            n_norm = normalize_name(n)
            nen_norm = normalize_name(d.get("nameEn", ""))
            if not n or n in existing or n_norm in seen or n_norm in existing_norm or (nen_norm and nen_norm in existing_norm):
                continue
            seen.add(n_norm)
            mid += 1
            threat = d.get("threat", "medium")
            reason = d.get("threatReason", "")
            entry = {
                "id": mid, "name": n, "nameEn": d.get("nameEn", ""),
                "developer": d.get("developer", "未知"), "studio": d.get("developer", "未知"),
                "publisher": d.get("developer", "未知"), "genre": d.get("genre", "未知"),
                "platform": normalize_platform(d.get("platform", ["Mobile"])), "region": d.get("region", "中國"), "model": "F2P",
                "stage": "announced", "threat": threat,
                "threatReason": reason, "prereg": None, "sentiment": 70,
                "launchEst": d.get("launchEst", "待定"), "desc": d.get("desc", ""), "tags": ["版號公示"],
                "threatAnalysis": f"【自動發現・版號公示】{reason}" if reason else "",
                "verified": f"版號雷達 ({today})",
                "category": "active", "testType": "待確認", "testDateStart": "待確認",
                "testDateEnd": "待確認", "sourceLinks": [], "launchRegions": d.get("launchRegions", []),
                "history": [{"date": datetime.now(timezone.utc).strftime("%Y-%m"), "s": "announced"}],
                "updatedAt": today, "addedAt": today, "lastChecked": "2000-01-01", "autoDiscovered": True,
                "sourceType": "版號公示",
            }
            new_p.append(entry)
            if entry["threat"] in ("critical", "high"):
                high_threat.append(entry)
            print(f" + {n} ({d.get('developer','?')}) threat={entry['threat']}")
    else:
        print(f" ⚠️ Parse failed or empty: {str(r)[:200] if r else 'None'}")

    if new_p:
        products.extend(new_p)
        save(PRODUCTS, products)
        print(f" ✅ +{len(new_p)} new products from license radar")
    return new_p, high_threat

# ══════════════════════════════════════════

def main():
    print("=" * 55)
    print("🎮 Game Market Intelligence — 4-Agent v5")
    now = datetime.now(timezone.utc)
    print(f" {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(" Architecture: Scrape(free) → Analyze(3~4 API calls)")
    print("=" * 55)

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("\n⚠️ No GEMINI_API_KEY")
        return
    DATA.mkdir(parents=True, exist_ok=True)
    config, _ = get_config()

    articles = scrape_rss()
    agent3(articles)
    agent2()
    _, high1 = agent1(articles)

    interval = config.get("licenseRadarIntervalDays", 3)
    dy = now.timetuple().tm_yday
    high4 = []
    if dy % interval == 0 or "--all" in sys.argv:
        _, high4 = agent4()
    else:
        print(f"\n⏭ Agent4 (版號雷達) skipped — runs every {interval} days")

    push_urgent_brief(high1 + high4)
    print("\n✅ Done!")

if __name__ == "__main__":
    main()
