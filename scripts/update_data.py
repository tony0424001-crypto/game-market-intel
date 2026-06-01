#!/usr/bin/env python3
"""
Game Market Intelligence — 3-Agent Automated System v2
Uses Google Gemini API (free tier).

Agent 1: Product Discovery — finds new games, auto-fills ALL fields including PM analysis
Agent 2: Status Update + PM Analysis — checks status, auto-generates threat analysis
Agent 3: Daily Brief + Industry Trends — news + industry dynamics

Runs daily via GitHub Actions at 08:30 TST.
Env: GEMINI_API_KEY (required)
"""
import json,os,sys,hashlib,re,time
from datetime import datetime,timezone,timedelta
from pathlib import Path
try:
    import requests
except ImportError:
    requests=None
try:
    import feedparser
except ImportError:
    feedparser=None

DATA_DIR=Path(__file__).parent.parent/"data"
PRODUCTS_FILE=DATA_DIR/"products.json"
DAILY_FILE=DATA_DIR/"daily-brief.json"
QUARTERLY_FILE=DATA_DIR/"quarterly.json"
AUDIT_FILE=DATA_DIR/"audit-report.json"
INDUSTRY_FILE=DATA_DIR/"industry-trends.json"
MAX_DAILY=20
MODEL="gemini-2.5-flash"

def gemini(prompt,search=False,tokens=4000):
    key=os.environ.get("GEMINI_API_KEY")
    if not key or not requests:return None
    url=f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={key}"
    
    # Try with search first, fallback to without search if 429
    attempts = []
    if search:
        attempts.append(True)   # first try with search
    attempts.append(False)      # fallback without search
    
    for use_search in attempts:
        body={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"maxOutputTokens":tokens,"temperature":0.2}}
        if use_search:
            body["tools"]=[{"google_search":{}}]
        
        for retry in range(3):
            try:
                r=requests.post(url,json=body,timeout=60)
                if r.status_code==200:
                    d=r.json();c=d.get("candidates",[])
                    if c:
                        return " ".join(p.get("text","") for p in c[0].get("content",{}).get("parts",[]) if "text" in p).strip()
                    return None
                elif r.status_code==429:
                    if use_search and retry==0:
                        print(f"    ⚠️ Search grounding blocked, falling back to plain Gemini...")
                        break
                    wait=15*(retry+1)
                    print(f"    ⏳ Rate limited, waiting {wait}s...")
                    time.sleep(wait)
                elif r.status_code==503:
                    wait=20*(retry+1)
                    print(f"    ⏳ Server busy (503), waiting {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"    Gemini {r.status_code}: {r.text[:200]}")
                    return None
            except Exception as e:
                print(f"    Gemini fail: {e}")
                return None
    
    print("    ❌ All attempts failed")
    return None

def parse_json(text):
    if not text:return None
    text=re.sub(r"```json\s*","",text);text=re.sub(r"```\s*","",text)
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

def load(p,d=None):
    try:
        with open(p,"r",encoding="utf-8") as f:return json.load(f)
    except:return d if d is not None else {}

def save(p,d):
    with open(p,"w",encoding="utf-8") as f:json.dump(d,f,ensure_ascii=False,indent=2)
    print(f"  ✓ {p.name}")

# ══ AGENT 1: Product Discovery ══
def agent1():
    print("\n🔍 Agent 1: Product Discovery")
    products=load(PRODUCTS_FILE,[])
    existing={g["name"] for g in products}
    mid=max((g["id"] for g in products),default=0)
    today=datetime.now(timezone.utc).strftime("%Y-%m-%d")
    queries=[
        "2026年6月7月 手遊新作 開測 上線 預約 封測 TapTap 九遊 17173 GameRes mobile game beta launch",
    ]
    all_disc=[]
    for q in queries:
        prompt=f"""You are a game industry analyst. Search for NEW mobile/console game releases, betas, and announcements.
Context: {q}
Find games that are: (1) Announced/beta/launched within last 3 months or next 6 months (2) Include BOTH major AAA games AND smaller indie/mid-tier games from 九遊/17173 test schedules
For each game output JSON array, each item:
- "name": Chinese name (繁體中文), "nameEn": English name
- "developer": developer, "publisher": publisher
- "genre": genre in Chinese, "platform": ["Mobile"] etc
- "region": "CN/Global" etc, "model": "F2P"/"Buy-to-Play"
- "stage": "announced"/"pre-reg"/"cbt"/"soft-launch"/"live-ops"
- "threat": "critical"/"high"/"medium"/"low"
- "launchEst": "2026-07" or "2026-Q3"
- "desc": 1-2 sentence 繁體中文
- "tags": array Chinese tags
- "testType": "不刪檔公測"/"刪檔計費"/"刪檔不計費" etc
- "testDateStart": start date, "testDateEnd": end date
- "threatAnalysis": PM-actionable analysis in 繁體中文, format: "【威脅類型】description\\n\\n▶ 建議行動：(1)... (2)... (3)..."
- "sourceLinks": [{{"label":"TapTap","url":"https://..."}}] actual URLs found in search
IMPORTANT: Only real games. Include mid/small games too. Output ONLY valid JSON array."""
        r=gemini(prompt,search=True)
        p=parse_json(r)
        if p and isinstance(p,list):
            all_disc.extend(p);print(f"    Found {len(p)} from search")
        time.sleep(15)
    new_p=[];seen=set()
    for d in all_disc:
        n=d.get("name","")
        if not n or n in existing or n in seen:continue
        seen.add(n);mid+=1
        new_p.append({"id":mid,"name":n,"nameEn":d.get("nameEn",""),"developer":d.get("developer","未知"),"studio":d.get("developer","未知"),"publisher":d.get("publisher","未知"),"genre":d.get("genre","未知"),"platform":d.get("platform",["Mobile"]),"region":d.get("region","未知"),"model":d.get("model","F2P"),"stage":d.get("stage","announced"),"threat":d.get("threat","medium"),"prereg":None,"sentiment":70,"launchEst":d.get("launchEst","待定"),"desc":d.get("desc",""),"tags":d.get("tags",[]),"threatAnalysis":d.get("threatAnalysis",""),"verified":f"AI Agent 自動發現 ({today})","category":"active","testType":d.get("testType","未知"),"testDateStart":d.get("testDateStart","待確認"),"testDateEnd":d.get("testDateEnd","待確認"),"sourceLinks":d.get("sourceLinks",[]),"launchRegions":[],"history":[{"date":datetime.now(timezone.utc).strftime("%Y-%m"),"s":d.get("stage","announced")}],"updatedAt":today,"autoDiscovered":True})
    if new_p:
        products.extend(new_p);save(PRODUCTS_FILE,products)
        print(f"  ✅ +{len(new_p)} products")
        for p in new_p:print(f"    + {p['name']} ({p['developer']})")
    else:print("  No new products")
    return len(new_p)

# ══ AGENT 2: Status Update + Auto PM Analysis ══
def agent2():
    print("\n🔄 Agent 2: Status Update + PM Analysis")
    products=load(PRODUCTS_FILE,[])
    if not products:print("  Empty");return 0
    today=datetime.now(timezone.utc).strftime("%Y-%m-%d")
    updates=0;findings=[]
    stale=[g for g in products if g.get('updatedAt','2000-01-01')<(datetime.now(timezone.utc)-timedelta(days=2)).strftime('%Y-%m-%d')]
    if not stale:stale=products[:10]
    print(f'  Checking {len(stale)} stale products (of {len(products)} total)')
    for i in range(0,len(stale),10):
        batch=stale[i:i+10]
        info="\n".join([f"- {g['name']} | 狀態:{g['stage']} | 開發商:{g.get('developer','?')} | 上線:{g.get('launchEst','?')} | 品類:{g['genre']} | 現有分析:{g.get('threatAnalysis','無')[:50]}" for g in batch])
        prompt=f"""You are a senior game PM. Today is {today}. Check these games and provide updates.

Games:
{info}

For each game, search for latest news and output JSON array:
- "name": exact name as provided
- "statusChanged": true/false
- "newStage": new stage if changed, else null
- "newLaunchEst": new date if changed, else null
- "recentEvent": major event description or null
- "shouldBeTracking": true if live 1+ year
- "shouldRemove": true if dead/shutdown
- "removeReason": reason if removing
- "pmAnalysis": ALWAYS provide a PM-actionable threat analysis in 繁體中文. Format MUST be:
  "【威脅類型（如：結構性威脅/直接競品/IP降維/市場擠壓/品類攪局）】具體威脅描述。\\n\\n▶ 建議行動：\\n(1) 具體建議一\\n(2) 具體建議二\\n(3) 具體建議三"
  
  The analysis should answer: What threat does this pose to MY product? What should I DO about it? When should I act?

Output ONLY valid JSON array."""
        r=gemini(prompt,search=True,tokens=4000)
        p=parse_json(r)
        if p and isinstance(p,list):
            for u in p:
                n=u.get("name","")
                prod=next((g for g in products if g["name"]==n),None)
                if not prod:continue
                if u.get("statusChanged") and u.get("newStage"):
                    old=prod["stage"];prod["stage"]=u["newStage"];prod["updatedAt"]=today
                    if prod.get("history"):prod["history"].append({"date":datetime.now(timezone.utc).strftime("%Y-%m"),"s":u["newStage"]})
                    updates+=1;findings.append({"name":n,"status":"warning","issue":f"{old}→{u['newStage']}","suggestion":"已自動更新","confidence":"high","autoFixed":True})
                    print(f"    ✏️ {n}: {old}→{u['newStage']}")
                if u.get("newLaunchEst") and u["newLaunchEst"]!=prod.get("launchEst"):
                    prod["launchEst"]=u["newLaunchEst"];prod["updatedAt"]=today;updates+=1
                if u.get("pmAnalysis") and len(u["pmAnalysis"])>20:
                    prod["threatAnalysis"]=u["pmAnalysis"];prod["updatedAt"]=today;updates+=1
                if u.get("shouldBeTracking") and prod.get("category")!="tracking":
                    prod["category"]="tracking";prod["updatedAt"]=today;updates+=1
                    if u.get("recentEvent"):prod["currentEvent"]=u["recentEvent"]
                if u.get("recentEvent") and prod.get("category")=="tracking":
                    prod["currentEvent"]=u["recentEvent"]
                    ha=any(k in u["recentEvent"] for k in ["週年","慶典","新版本","聯動","大型"])
                    prod["eventNote"]=("⚡ " if ha else "")+u["recentEvent"]
                if u.get("shouldRemove"):
                    findings.append({"name":n,"status":"error","issue":f"建議移除: {u.get('removeReason','')}","suggestion":"需人工確認","confidence":"medium","autoFixed":False})
        time.sleep(15)
    if updates:save(PRODUCTS_FILE,products)
    print(f"  ✅ {updates} updates")
    now=datetime.now(timezone.utc)
    save(AUDIT_FILE,{"lastAudit":now.isoformat(),"totalProducts":len(products),"issuesFound":len(findings),"updatesApplied":updates,"findings":findings,"nextAudit":(now+timedelta(days=1)).strftime("%Y-%m-%d")})
    return updates

# ══ AGENT 3: Daily Brief + Industry Trends ══
def agent3():
    print("\n📋 Agent 3: Daily Brief + Industry Trends")
    now=datetime.now(timezone.utc);today=now.strftime("%Y-%m-%d")
    existing=load(DAILY_FILE,{"items":[],"fetchCount":0})
    eids={i["id"] for i in existing.get("items",[])}
    # RSS
    rss=[]
    if feedparser:
        for feed in [{"url":"https://www.ign.com/articles.rss","n":"IGN"},{"url":"https://www.gamespot.com/feeds/news/","n":"GameSpot"},{"url":"https://www.gematsu.com/feed","n":"Gematsu"}]:
            try:
                f=feedparser.parse(feed["url"])
                for e in f.entries[:10]:
                    t=e.get("title","").strip();s=re.sub(r"<[^>]+>","",e.get("summary",""))[:300]
                    GK=["game","launch","beta","trailer","announce","mobile","console","rpg"]
                    if any(k in f"{t} {s}".lower() for k in GK):rss.append({"title":t,"summary":s,"source":feed["n"]})
            except:pass
    print(f"  RSS: {len(rss)} articles")
    # AI news search
    prompt=f"""Today is {today}. Search for latest Chinese and global gaming industry news.
Focus on: (1) New game announcements/launches (2) Major updates/anniversaries (3) Industry business (earnings, acquisitions) (4) Market trends (版號, 出海, AI)
Output JSON array of top 10 items: "title" (繁體中文), "detail" (繁體中文), "action" (PM建議 繁體中文), "priority" ("urgent"/"watch"/"info"), "platform" ("mobile"/"console"), "source" (來源名)
Output ONLY valid JSON array."""
    ai=[];r=gemini(prompt,search=True);p=parse_json(r)
    if p and isinstance(p,list):ai=p;print(f"  AI: {len(ai)} items")
    icons={"urgent":"🚨","watch":"👁","info":"ℹ️"}
    new_items=[]
    for it in ai:
        iid=f"brief-{today.replace('-','')}-{hashlib.md5(it.get('title','').encode()).hexdigest()[:8]}"
        if iid not in eids:
            new_items.append({"id":iid,"priority":it.get("priority","info"),"icon":icons.get(it.get("priority"),"📰"),"title":it.get("title",""),"detail":it.get("detail",""),"action":it.get("action","持續觀察"),"platform":it.get("platform","mobile"),"source":it.get("source","AI"),"fetchedAt":now.isoformat()})
    for e in rss[:5]:
        iid=f"brief-{today.replace('-','')}-{hashlib.md5(e['title'].encode()).hexdigest()[:8]}"
        if iid not in eids and iid not in{i["id"]for i in new_items}:
            new_items.append({"id":iid,"priority":"info","icon":"📰","title":e["title"][:80],"detail":e["summary"][:200],"action":"待審閱","platform":"console","source":e["source"],"fetchedAt":now.isoformat()})
    all_i=new_items+existing.get("items",[]);all_i=all_i[:MAX_DAILY]
    po={"urgent":0,"watch":1,"info":2}
    all_i.sort(key=lambda x:x.get("fetchedAt",""),reverse=True)
    all_i.sort(key=lambda x:po.get(x.get("priority","info"),3))
    save(DAILY_FILE,{"lastUpdated":now.isoformat(),"fetchCount":existing.get("fetchCount",0)+1,"items":all_i})
    print(f"  ✅ +{len(new_items)} items, total {len(all_i)}")

    # ── Industry Trends (NEW) ──
    print("  Fetching industry trends...")
    trend_prompt=f"""Today is {today}. Search for the latest gaming INDUSTRY news (not individual game news).
Focus on: (1) Major company earnings/financials (騰訊/網易/米哈遊財報) (2) 版號 license approvals in China (3) Market data and trends (出海/東南亞/AI) (4) Major acquisitions or partnerships (5) New technology adoption (AI/UE5/cloud gaming)
Output JSON array of 5-8 items: "title" (繁體中文), "detail" (2-3 sentences 繁體中文), "category" ("財報"/"版號"/"市場"/"收購"/"技術"/"全球化"), "pmInsight" (PM should do what about this, 繁體中文), "source" (source name), "date" (approximate date)
Output ONLY valid JSON array."""
    tr=gemini(trend_prompt,search=True);tp=parse_json(tr)
    if tp and isinstance(tp,list):
        save(INDUSTRY_FILE,{"lastUpdated":now.isoformat(),"trends":tp})
        print(f"  ✅ {len(tp)} industry trends")
    else:
        print("  No industry trends fetched")

    # Quarterly
    q=load(QUARTERLY_FILE,{});cy=now.year
    if q.get("year")!=cy:
        q={"year":cy,"resetAt":f"{cy}-01-01T00:00:00Z","quarters":{f"Q{i}":{"label":f"{cy} Q{i}","events":[]} for i in range(1,5)}}
    qk=f"Q{(now.month-1)//3+1}"
    if qk not in q.get("quarters",{}):q["quarters"][qk]={"label":f"{cy} {qk}","events":[]}
    sigs=set()
    for qv in q.get("quarters",{}).values():
        for ev in qv.get("events",[]):sigs.add(f"{ev.get('date')}-{ev.get('product','')}")
    for it in new_items:
        if it["priority"] in("urgent","watch"):
            sig=f"{today}-{it['title'][:20]}"
            if sig not in sigs:q["quarters"][qk]["events"].append({"date":today,"product":it["title"][:30],"type":"media","detail":it["detail"][:100],"platform":it["platform"]})
    q["lastUpdated"]=now.isoformat();save(QUARTERLY_FILE,q)
    return len(new_items)

# ══ MAIN ══
def main():
    print("="*55)
    print("🎮 Game Market Intelligence — 3-Agent v2")
    now=datetime.now(timezone.utc)
    print(f"   {now.strftime('%Y-%m-%d %H:%M UTC')} ({now.strftime('%A')})")
    print("="*55)
    key=os.environ.get("GEMINI_API_KEY")
    if not key:
        print("\n⚠️ GEMINI_API_KEY not set! Limited mode.")
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    agent3()
    if key:
        agent2()
        dy=now.timetuple().tm_yday
        if dy%5==0 or "--discover" in sys.argv or "--all" in sys.argv:
            agent1()
        else:
            print(f"\n⏭ Agent 1 skipped (every 3 days)")
    print("\n✅ All agents complete!")

if __name__=="__main__":
    main()
