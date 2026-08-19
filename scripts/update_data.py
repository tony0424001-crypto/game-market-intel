#!/usr/bin/env python3
"""
Game Market Intelligence — 3-Agent v4 (Scrape First, AI Analyze)
Architecture: Free RSS scraping → Gemini analysis (3 API calls only)

Step 1: Scrape RSS feeds from 巴哈GNN, 4Gamers, Gematsu, IGN etc (FREE)
Step 2: Gemini Call 1 → Process scraped data into new products + daily brief
Step 3: Gemini Call 2 → Update 5 existing products (rotating)
Step 4: Gemini Call 3 → Discover new products (every 2 days)
"""
import json,os,sys,hashlib,re,time
from datetime import datetime,timezone,timedelta
from pathlib import Path
try:
    import requests as req
except ImportError:
    req=None
try:
    import feedparser
except ImportError:
    feedparser=None

DATA=Path(__file__).parent.parent/"data"
CONFIG=Path(__file__).parent.parent/"config.json"
PRODUCTS=DATA/"products.json"
DAILY=DATA/"daily-brief.json"
QUARTERLY=DATA/"quarterly.json"
AUDIT=DATA/"audit-report.json"
INDUSTRY=DATA/"industry-trends.json"
MODEL="gemini-2.5-flash"

def load(p,d=None):
    try:
        with open(p,"r",encoding="utf-8") as f:return json.load(f)
    except:return d if d is not None else {}

def save(p,d):
    with open(p,"w",encoding="utf-8") as f:json.dump(d,f,ensure_ascii=False,indent=2)
    print(f"  ✓ {p.name}")

def get_config():
    c=load(CONFIG,{})
    mp=c.get("myProduct",{})
    return c, mp

def gemini(prompt,search=False,tokens=8000):
    key=os.environ.get("GEMINI_API_KEY")
    if not key or not req:return None
    url=f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={key}"
    attempts=[]
    if search:attempts.append(True)
    attempts.append(False)
    for use_s in attempts:
        body={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"maxOutputTokens":tokens,"temperature":0.2}}
        if use_s:body["tools"]=[{"google_search":{}}]
        for retry in range(3):
            try:
                r=req.post(url,json=body,timeout=90)
                if r.status_code==200:
                    d=r.json();c=d.get("candidates",[])
                    if c:
                        text=" ".join(p.get("text","") for p in c[0].get("content",{}).get("parts",[]) if "text" in p).strip()
                        print(f"    ✅ Gemini: {len(text)} chars")
                        return text
                    return None
                elif r.status_code==429:
                    if use_s and retry==0:print("    ⚠️ Search blocked, fallback...");break
                    w=15*(retry+1);print(f"    ⏳ 429 {w}s...");time.sleep(w)
                elif r.status_code==503:
                    w=20*(retry+1);print(f"    ⏳ 503 {w}s...");time.sleep(w)
                else:print(f"    ❌ {r.status_code}");return None
            except Exception as e:print(f"    ❌ {e}");return None
    return None

def parse_json(text):
    if not text:return None
    text=re.sub(r"```json\s*","",text);text=re.sub(r"```\s*","",text);text=text.strip()
    if text.startswith("[") and not text.endswith("]"):
        last=text.rfind("}");
        if last>0:text=text[:last+1]+"]";print("    🔧 Repaired truncated array")
    elif text.startswith("{") and not text.endswith("}"):
        # Try to extract the array inside the object
        arr_start=text.find("[")
        if arr_start>0:
            last=text.rfind("}");
            if last>arr_start:text=text[arr_start:last+1]+"]";print("    🔧 Extracted array from truncated object")
    for i,c in enumerate(text):
        if c in "[{":
            d=0
            for j in range(i,len(text)):
                if text[j] in "[{":d+=1
                elif text[j] in "]}":
                    d-=1
                    if d==0:
                        try:return json.loads(text[i:j+1])
                        except:break
            break
    return None

# ══════════════════════════════════════════
# STEP 1: Free RSS Scraping (NO API cost)
# ══════════════════════════════════════════
def scrape_rss():
    """Scrape all configured RSS feeds. Returns list of articles."""
    print("\n📡 Step 1: RSS Scraping (free)")
    config,_=get_config()
    feeds=config.get("rssFeeds",[
        {"url":"https://gnn.gamer.com.tw/rss.xml","name":"巴哈GNN"},
        {"url":"https://www.4gamers.com.tw/rss/latest-news","name":"4Gamers"},
        {"url":"https://www.gematsu.com/feed","name":"Gematsu"},
        {"url":"https://www.ign.com/articles.rss","name":"IGN"},
        {"url":"https://automaton-media.com/en/feed/","name":"Automaton"},
    ])
    
    all_articles=[]
    if not feedparser:
        print("  ⚠️ feedparser not installed");return all_articles
    
    game_kw=["遊戲","手遊","上線","封測","開測","公測","預約","事前登錄","新作",
             "game","launch","beta","trailer","announce","mobile","console","rpg","mmo",
             "mmorpg","update","release","pre-register"]
    
    for feed in feeds:
        try:
            f=feedparser.parse(feed["url"])
            count=0
            for e in f.entries[:20]:
                title=e.get("title","").strip()
                summary=re.sub(r"<[^>]+>","",e.get("summary",""))[:400]
                combined=f"{title} {summary}".lower()
                if any(kw in combined for kw in game_kw):
                    pub=None
                    if hasattr(e,"published_parsed") and e.published_parsed:
                        pub=datetime(*e.published_parsed[:6],tzinfo=timezone.utc).isoformat()
                    all_articles.append({
                        "title":title,"summary":summary,
                        "source":feed["name"],
                        "link":e.get("link",""),
                        "date":pub or datetime.now(timezone.utc).isoformat()
                    })
                    count+=1
            print(f"  {feed['name']}: {count} game articles")
        except Exception as ex:
            print(f"  ❌ {feed['name']}: {ex}")
    
    print(f"  Total scraped: {len(all_articles)} articles")
    return all_articles

# ══════════════════════════════════════════
# AGENT 3: Daily Brief from scraped data (1 API call)
# ══════════════════════════════════════════
def agent3(articles):
    print("\n📋 Agent 3: Daily Brief + Industry")
    now=datetime.now(timezone.utc);today=now.strftime("%Y-%m-%d")
    existing=load(DAILY,{"items":[],"fetchCount":0})
    eids={i["id"] for i in existing.get("items",[])}
    config,my=get_config()
    
    # Prepare scraped content for Gemini
    article_text="\n".join([f"[{a['source']}] {a['title']}\n{a['summary'][:150]}" for a in articles[:25]])
    
    prompt=f"""Today is {today}. Here are today's game news headlines:

{article_text}

Pick the 6 most important items. Output a JSON array (NOT object). Each item:
{{"title":"繁中標題(15字內)","detail":"繁中摘要(30字內)","action":"建議(15字內)","priority":"urgent/watch/info","platform":"mobile/console","source":"來源"}}

Keep VERY short. Output ONLY valid JSON array, no markdown."""

    r=gemini(prompt)  # No search needed - we already have the data!
    p=parse_json(r)
    
    icons={"urgent":"🚨","watch":"👁","info":"ℹ️"}
    new_items=[]
    
    if p and isinstance(p,list):
        for it in p:
            iid=f"brief-{today.replace('-','')}-{hashlib.md5(it.get('title','').encode()).hexdigest()[:8]}"
            if iid not in eids:
                new_items.append({"id":iid,"priority":it.get("priority","info"),"icon":icons.get(it.get("priority"),"📰"),"title":it.get("title",""),"detail":it.get("detail",""),"action":it.get("action",""),"platform":it.get("platform","mobile"),"source":it.get("source",""),"fetchedAt":now.isoformat()})
    else:
        print(f"  ⚠️ Parse failed: {str(r)[:200] if r else 'None'}")
    
    all_i=new_items+existing.get("items",[]);all_i=all_i[:20]
    po={"urgent":0,"watch":1,"info":2}
    all_i.sort(key=lambda x:x.get("fetchedAt",""),reverse=True)
    all_i.sort(key=lambda x:po.get(x.get("priority","info"),3))
    save(DAILY,{"lastUpdated":now.isoformat(),"fetchCount":existing.get("fetchCount",0)+1,"items":all_i})
    print(f"  ✅ +{len(new_items)} items, total {len(all_i)}")
    
    # Industry trends from news
    if new_items:
        ind_kw=["財報","版號","收購","市場","AI","出海","融資","營收","投資","合併"]
        ind=[{"title":it["title"],"detail":it["detail"],"category":"市場","pmInsight":it.get("action",""),"source":it.get("source",""),"date":today} for it in new_items if any(k in it.get("title","") for k in ind_kw)]
        if ind:
            save(INDUSTRY,{"lastUpdated":now.isoformat(),"trends":ind})
            print(f"  ✅ {len(ind)} industry trends")

    # Quarterly
    q=load(QUARTERLY,{});cy=now.year
    if q.get("year")!=cy:q={"year":cy,"resetAt":f"{cy}-01-01T00:00:00Z","quarters":{f"Q{i}":{"label":f"{cy} Q{i}","events":[]} for i in range(1,5)}}
    qk=f"Q{(now.month-1)//3+1}"
    if qk not in q.get("quarters",{}):q["quarters"][qk]={"label":f"{cy} {qk}","events":[]}
    q["lastUpdated"]=now.isoformat();save(QUARTERLY,q)

# ══════════════════════════════════════════
# AGENT 2: Status Update (1 API call, 5 products rotating)
# ══════════════════════════════════════════
def agent2():
    print("\n🔄 Agent 2: Status Update (5 products)")
    products=load(PRODUCTS,[])
    if not products:return
    today=datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now=datetime.now(timezone.utc)
    for g in products:
        if "lastChecked" not in g:g["lastChecked"]="2000-01-01"
    products.sort(key=lambda g:g["lastChecked"])
    batch=products[:5]
    
    print(f"  Checking: {', '.join(g['name'] for g in batch)}")
    
    info="\n".join([f"- {g['name']} | 狀態:{g['stage']} | 開發商:{g.get('developer','?')} | 上線:{g.get('launchEst','?')} | 測試:{g.get('testType','?')} {g.get('testDateStart','?')}~{g.get('testDateEnd','?')}" for g in batch])
    
    prompt=f"""Today is {today}. Check these 5 games for concrete status changes.

Games:
{info}

For EACH game, search and check:
1. Has beta/test ENDED? When exactly?
2. Has LAUNCH DATE been confirmed or changed?  
3. Has the game LAUNCHED? On which platforms/regions?
4. Any delay, shutdown, or new test phase?

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
- "pmAnalysis": 繁體中文 PM analysis, format: "【威脅類型】描述\\n\\n▶ 建議行動：\\n(1)...\\n(2)...\\n(3)..."
- "directLinks": [{{"label":"TapTap","url":"real URL"}},{{"label":"官網","url":"URL"}}]

Output ONLY valid JSON array."""

    r=gemini(prompt,search=True)
    p=parse_json(r)
    updates=0;findings=[]
    
    if p and isinstance(p,list):
        print(f"  Got {len(p)} results")
        for u in p:
            n=u.get("name","")
            prod=next((g for g in products if g["name"]==n),None)
            if not prod:continue
            prod["lastChecked"]=today
            
            if u.get("statusChanged") and u.get("newStage"):
                old=prod["stage"];prod["stage"]=u["newStage"];prod["updatedAt"]=today
                if prod.get("history"):prod["history"].append({"date":now.strftime("%Y-%m"),"s":u["newStage"]})
                updates+=1;findings.append({"name":n,"status":"warning","issue":f"狀態: {old}→{u['newStage']}","autoFixed":True})
                print(f"    ✏️ {n}: {old} → {u['newStage']}")
            if u.get("newLaunchEst") and u["newLaunchEst"]!=prod.get("launchEst"):
                old=prod.get("launchEst","");prod["launchEst"]=u["newLaunchEst"];prod["updatedAt"]=today
                updates+=1;print(f"    📅 {n}: {old} → {u['newLaunchEst']}")
            if u.get("testDateUpdate"):
                prod["testDateEnd"]=u["testDateUpdate"];prod["updatedAt"]=today
                updates+=1;print(f"    🧪 {n}: end → {u['testDateUpdate']}")
            if u.get("pmAnalysis") and len(u["pmAnalysis"])>30:
                prod["threatAnalysis"]=u["pmAnalysis"];prod["updatedAt"]=today;updates+=1
            if u.get("directLinks") and isinstance(u["directLinks"],list) and len(u["directLinks"])>0:
                prod["sourceLinks"]=u["directLinks"];prod["updatedAt"]=today;updates+=1
            if u.get("shouldBeTracking") and prod.get("category")!="tracking":
                prod["category"]="tracking";prod["updatedAt"]=today;updates+=1
                if u.get("recentEvent"):prod["currentEvent"]=u["recentEvent"]
            if u.get("recentEvent") and prod.get("category")=="tracking":
                prod["currentEvent"]=u["recentEvent"]
                prod["eventNote"]=("⚡ " if any(k in u["recentEvent"] for k in ["週年","慶典","新版本","聯動","大型"]) else "")+u["recentEvent"]
            if u.get("shouldRemove"):
                findings.append({"name":n,"status":"error","issue":f"移除: {u.get('removeReason','')}","autoFixed":False})
    else:
        print(f"  ⚠️ Parse failed: {str(r)[:200] if r else 'None'}")
    
    products.sort(key=lambda g:g.get("id",0))
    if updates:save(PRODUCTS,products)
    print(f"  ✅ {updates} updates")
    save(AUDIT,{"lastAudit":now.isoformat(),"totalProducts":len(products),"updatesApplied":updates,"findings":findings,"checked":[g["name"] for g in batch],"nextBatch":[g["name"] for g in products if g["lastChecked"]<today][:5]})

# ══════════════════════════════════════════
# AGENT 1: Product Discovery (1 API call, uses scraped data)
# ══════════════════════════════════════════
def agent1(articles):
    print("\n🔍 Agent 1: Product Discovery")
    products=load(PRODUCTS,[])
    existing={g["name"] for g in products}
    mid=max((g["id"] for g in products),default=0)
    today=datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    # Use scraped articles as source material
    game_articles=[a for a in articles if any(kw in a["title"].lower() for kw in ["上線","開測","封測","公測","預約","launch","beta","release","announce","new game","新作"])]
    article_text="\n".join([f"- [{a['source']}] {a['title']}" for a in game_articles[:20]])
    
    current=", ".join(list(existing)[:40])
    
    prompt=f"""From these game news articles AND your own knowledge, find NEW games not in our database.

News articles mentioning new games:
{article_text}

Games we ALREADY track (skip these):
{current}

Find 5-10 NEW games (台灣/中國/韓國/日本/Global) that we don't have yet.
Each item:
- "name": 繁體中文 name
- "nameEn": English name
- "developer": studio
- "genre": genre (Chinese, 2-3 words)
- "platform": ["Mobile"] or ["PC","Mobile"]
- "stage": "announced"/"pre-reg"/"cbt"/"live-ops"
- "launchEst": "2026-07" or "2026-Q3"
- "desc": ONE sentence 繁體中文

Keep items SHORT. Output ONLY valid JSON array."""

    r=gemini(prompt,search=True,tokens=8000)
    p=parse_json(r)
    new_p=[]
    
    if p and isinstance(p,list):
        print(f"  Found {len(p)} candidates")
        seen=set()
        for d in p:
            n=d.get("name","")
            if not n or n in existing or n in seen:
                if n and n in existing:print(f"    ⏭ {n} (exists)")
                continue
            seen.add(n);mid+=1
            new_p.append({"id":mid,"name":n,"nameEn":d.get("nameEn",""),"developer":d.get("developer","未知"),"studio":d.get("developer","未知"),"publisher":d.get("developer","未知"),"genre":d.get("genre","未知"),"platform":d.get("platform",["Mobile"]),"region":"待確認","model":"F2P","stage":d.get("stage","announced"),"threat":"medium","prereg":None,"sentiment":70,"launchEst":d.get("launchEst","待定"),"desc":d.get("desc",""),"tags":[],"threatAnalysis":"","verified":f"Agent 發現 ({today})","category":"active","testType":"待確認","testDateStart":"待確認","testDateEnd":"待確認","sourceLinks":[],"launchRegions":[],"history":[{"date":datetime.now(timezone.utc).strftime("%Y-%m"),"s":d.get("stage","announced")}],"updatedAt":today,"lastChecked":"2000-01-01","autoDiscovered":True})
            print(f"    + {n} ({d.get('developer','?')}) [{d.get('stage','')}]")
    else:
        print(f"  ⚠️ Parse failed: {str(r)[:200] if r else 'None'}")
    
    if new_p:
        products.extend(new_p);save(PRODUCTS,products)
        print(f"  ✅ +{len(new_p)} new products (total: {len(products)+len(new_p)})")
    else:
        print("  No new products")

# ══════════════════════════════════════════
def main():
    print("="*55)
    print("🎮 Game Market Intelligence — 3-Agent v4")
    now=datetime.now(timezone.utc)
    print(f"   {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"   Architecture: Scrape(free) → Analyze(3 API calls)")
    print("="*55)
    
    key=os.environ.get("GEMINI_API_KEY")
    if not key:print("\n⚠️ No GEMINI_API_KEY");return
    DATA.mkdir(parents=True,exist_ok=True)
    
    # Step 1: FREE scraping
    articles=scrape_rss()
    
    # Step 2: Agent 3 - process scraped data (Call 1)
    agent3(articles)
    
    # Step 3: Agent 2 - update 5 products (Call 2)
    agent2()
    
    # Step 4: Agent 1 - discover from scraped + search (Call 3, every 2 days)
    dy=now.timetuple().tm_yday
    if True:  # always run
        agent1(articles)
    else:
        print(f"\n⏭ Agent 1 skipped")
    
    print(f"\n✅ Done! API calls: {'3' if dy%2==0 or '--all' in sys.argv else '2'}")

if __name__=="__main__":
    main()
