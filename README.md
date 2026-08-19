# 🎮 Game Market Intelligence Platform

AI 驅動的遊戲市場競品情報平台，部署於 GitHub Pages。核心是一套 **4-Agent** 系統：每天自動爬新聞、更新現有產品狀態、發現新產品、並定期掃描中國版號公示，全部寫成靜態 JSON，由前端直接讀取渲染。

## 系統架構

```
Step 1  RSS 爬蟲（免費，不耗 API 額度）
Step 2  Agent 3：把爬到的新聞整理成每日簡報 + 產業趨勢（1 次 Gemini call）
Step 3  Agent 2：輪值檢查現有產品狀態是否有變化（1 次 Gemini call，含網路搜尋）
Step 4  Agent 1：從新聞 + 模型知識中發現新產品（1 次 Gemini call，含網路搜尋）
Step 5  Agent 4：版號雷達，每 N 天掃描中國版號公示名單（1 次 Gemini call，含網路搜尋）
```

每次執行約 3～4 次 Gemini API 呼叫。**Agent 1 跟 Agent 4 發現的高威脅（`critical`/`high`)新品會自動寫進每日簡報的 urgent 區塊**，不用等你自己去產品列表裡翻到。

### ⚠️ 環境變數是必要的，不是進階選項
整個腳本一開始就會檢查 `GEMINI_API_KEY`，**沒有這個 key 的話，連免費的 RSS 爬蟲都不會執行**——不是「基本版可以跑、進階版才需要」的關係，是完全不會動。

- 進入 repo → **Settings** → **Secrets and variables** → **Actions**
- 新增 Secret，名稱必須是 **`GEMINI_API_KEY`**（不是 `ANTHROPIC_API_KEY`，這個系統用的是 Google Gemini API，不是 Claude）
- 值填你的 Gemini API Key

## 前端模組（`index.html`）

前端是純靜態 React（CDN 版，無建構步驟），分「📱 手機遊戲」「🎮 主機/PC」兩個平台頁籤，每個頁籤下有 12 個模組：

| 模組 | 說明 |
|------|------|
| 📋 產品總覽 | 依威脅等級分組的卡片列表 |
| 🛰 階段看板 | Kanban 視圖，按開發階段分欄 |
| ⚠️ 威脅分析 | 只顯示 `critical`/`high` 威脅產品，附威脅理由 |
| 📡 追蹤項目 | 上線超過一年的長期營運產品，僅在有重大事件時特別標注 |
| 📊 甘特圖 | 各產品開發時程橫向排列 |
| 📦 季度產品 | 依預計上線季度分組 |
| 📈 趨勢預測 | 品類分佈與飽和度分析 |
| 🚀 上線時機 | 月度上線密度、競爭熱區 |
| ☀️ 每日簡報 | Agent 3 自動產出，含 Agent 1/4 推送的高威脅新品提醒 |
| 📅 季度事件 | Q1～Q4 事件歸檔 |
| 🏢 產業動態 | 財報/版號/收購等產業級新聞 |
| 🔍 審計報告 | Agent 2 每次輪值檢查後的結果摘要 |

### 分類邏輯
- **📱 手機遊戲** — `platform` 陣列中包含 `"Mobile"` 即歸入（大小寫必須完全一致，這是 `normalize_platform()` 存在的原因）
- **🎮 主機/PC 遊戲** — 不含 `"Mobile"` 的其餘產品，平台名稱保留原樣（`"PS5"`、`"Switch 2"`、`"Xbox Series"` 等具體名稱不會被簡化）

---

## 快速部署

### 1. 建立 GitHub Repository

```bash
git init game-market-intel
cd game-market-intel

# 確認結構如下：
#   index.html
#   config.json
#   data/products.json
#   data/daily-brief.json
#   data/quarterly.json
#   data/industry-trends.json
#   data/audit-report.json
#   scripts/update_data.py
#   .github/workflows/daily-update.yml
#   README.md

git add .
git commit -m "🎮 Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/game-market-intel.git
git push -u origin main
```

### 2. 啟用 GitHub Pages
1. 進入 repo → **Settings** → **Pages**
2. Source 選 `Deploy from a branch`
3. Branch 選 `main`，資料夾選 `/ (root)`
4. 等待 1-2 分鐘後上線於 `https://YOUR_USERNAME.github.io/game-market-intel/`

### 3. 設定 `GEMINI_API_KEY`（必要）
見上方「環境變數是必要的」一節。沒設定，整套系統什麼都不會做，只會在 log 印一行警告。

### 4. GitHub Actions 排程
`.github/workflows/daily-update.yml` 已預設：
- **每天 UTC 00:30（台灣時間 08:30）自動執行**一次完整流程
- 可在 repo → **Actions** → **Daily Game Intel Update** → **Run workflow** 手動觸發
- 手動觸發時勾選 `run_all` 會加上 `--all` 參數，強制當天也跑 Agent 4（版號雷達），不受間隔天數限制

---

## `config.json` 設定

```json
{
  "myProduct": {
    "name": "（請填入你的產品名稱）",
    "genre": "（請填入品類，例如：開放世界 RPG）",
    "platform": ["Mobile"],
    "targetMarket": "TW/HK/MO",
    "launchWindow": "（請填入預計上線時間，例如：2026-Q4）",
    "coreFeatures": "（請填入核心賣點，例如：即時戰鬥+公會系統）"
  },
  "watchGenres": ["開放世界 RPG", "動作 RPG", "MMORPG", "二次元/抽卡", "IP改編/回憶向"],
  "watchRegions": ["TW/HK/MO", "CN", "Global"],
  "agent2BatchSize": 6,
  "licenseRadarIntervalDays": 3,
  "rssFeeds": [ { "url": "...", "name": "...", "lang": "...", "trusted": false } ]
}
```

### `myProduct`（選填，彈性開關）
這套系統**不要求**你有一款特定產品要比較——`myProduct` 留著預留字（`"（請填入...）"`）時，Agent 1/4 會用**通用威脅標準**判斷（二游/大IP/回憶向IP/大廠自研 = 高威脅或現象級，跟任何特定產品無關）。

如果哪天你真的有自己的產品要對比，把 `name` 跟 `genre` 填上實際內容，系統會自動偵測到並額外把「是否搶佔同一批受眾/檔期」納入威脅評分——不用改任何程式碼。

### `watchGenres` / `watchRegions`
提示 Agent 1/4 你重點關注的品類與市場，會直接組進 prompt。跟 `myProduct` 無關，永遠生效。

### `agent2BatchSize`（預設 6）
Agent 2 每次輪值檢查幾款現有產品的狀態（測試日期/上線確認/是否該移除等）。產品數量變多時可以調高，讓每款產品被重新檢查的週期不要拉太長。

### `licenseRadarIntervalDays`（預設 3）
Agent 4（版號雷達）每幾天跑一次。版號是批次公告，不需要每天查。這個邏輯是用「當年第幾天 % 間隔天數 == 0」判斷，**假設 GitHub Actions 是每天跑一次**——如果你把排程改成非每天執行，這個判斷會失準，需要改成存日期記錄比對。

### `rssFeeds`（⚠️ 會整個覆蓋，不是合併)
只要 `config.json` 裡有 `rssFeeds` 這個 key，`update_data.py` 裡寫死的預設清單就完全不會生效——不是疊加，是取代。目前預設清單（供參考，實際看 `config.json`)：

| 來源 | 語言 | `trusted` |
|---|---|---|
| 巴哈姆特GNN | zh-TW | false |
| 4Gamers | zh-TW | false |
| Gematsu | en | false |
| IGN | en | false |
| Automaton | en | false |
| GameLook | zh-CN | true |
| 遊資網 | zh-CN | true |
| 遊民星空 | zh-CN | true |

`trusted: true` 的來源會**跳過關鍵字過濾**，因為它們本身就是遊戲媒體，不需要再篩一次；`trusted: false` 的來源（通用/歐美媒體）會先用關鍵字清單篩過才收錄，避免雜訊。新增來源前建議先手動確認 RSS 網址還能正常解析。

---

## 威脅等級

Agent 1/2/4 都會標記 `threat` 欄位，四級：

| 等級 | 標準 |
|---|---|
| `critical` | 現象級大作：國民懷念IP復刻（例如「賽爾號」這類全民童年記憶產品)、頭部大廠自研旗艦新IP、話題度極高的頭部二游 |
| `high` | 二游/大IP/回憶向/大廠自研，規模明顯但未達現象級 |
| `medium` | 中等規模、仍待觀察 |
| `low` | 小型/長尾產品 |

Agent 1/4 的探索 prompt 明確要求**排除西方/日系小型獨立遊戲、視覺小說類雜訊**（除非是 GTA、戰神等級的現象級大作），避免探索名額被無關的獨立遊戲佔滿，稀釋掉真正該追蹤的手遊/二游訊號。

---

## 資料結構

### `data/products.json` — 產品資料庫
可手動維護，也會被 Agent 1/2/4 自動寫入（`autoDiscovered: true` 標記自動發現的產品）。

```json
{
  "id": 17,
  "name": "遊戲名稱",
  "nameEn": "English Name",
  "developer": "開發商",
  "studio": "開發商",
  "publisher": "發行商",
  "genre": "品類",
  "platform": ["PC", "Mobile"],
  "region": "CN/Global",
  "model": "F2P",
  "stage": "announced",        // announced | pre-reg | cbt | soft-launch | live-ops
  "threat": "medium",          // critical | high | medium | low
  "threatReason": "一句話說明威脅理由（Agent 自動填入）",
  "threatAnalysis": "詳細分析（Agent 2 更新時填入，格式含【威脅類型】與▶建議行動)",
  "sourceType": "新聞報導 | 版號公示 | 官方公告 | 模型知識推論",
  "prereg": 500000,
  "sentiment": 75,
  "launchEst": "2026-Q4",
  "desc": "產品描述...",
  "tags": ["標籤1", "標籤2"],
  "history": [{"date": "2026-05", "s": "announced"}],
  "verified": "Agent 發現 (2026-08-19)",
  "autoDiscovered": true,
  "updatedAt": "2026-05-28",
  "lastChecked": "2000-01-01"  // 新發現的產品預設此值，等 Agent 2 排到才會更新
}
```

### `data/daily-brief.json` — 每日簡報（自動更新)
```json
{
  "lastUpdated": "ISO timestamp",
  "fetchCount": 42,
  "items": [
    {
      "id": "brief-20260819-abc123",
      "priority": "urgent | watch | info",
      "icon": "🚨",
      "title": "標題",
      "detail": "摘要...",
      "action": "建議行動...",
      "platform": "mobile | console",   // 必須是這兩個精確字串，否則兩個頁籤都不會顯示
      "source": "來源",
      "fetchedAt": "ISO timestamp"
    }
  ]
}
```
最多保留 20 條，新項目排最前面。Agent 1/4 發現的高威脅新品會用 `🚨新品雷達` 標籤自動插入這裡。

### `data/quarterly.json` — 季度追蹤（自動更新 + 年度重置）
每年 1 月 1 日腳本偵測到新年度時自動清空並重建 4 個空季度。

### `data/audit-report.json` — 審計報告（Agent 2 每次執行後寫入）
```json
{
  "lastAudit": "ISO timestamp",
  "totalProducts": 128,
  "updatesApplied": 3,
  "issuesFound": 1,
  "nextAudit": "下次執行時將檢查：xxx、yyy",
  "findings": [...],
  "checked": ["這次檢查的產品名稱..."],
  "nextBatch": ["下次要檢查的產品名稱..."]
}
```
Agent 2 是**每天輪值制**，不是週期性整批審計——每次挑 `agent2BatchSize` 款最久沒被檢查的產品，不是一次審完全部。

---

## 已知限制

- **舊資料裡已存在近似重複的產品**（例如同一款遊戲因為中文名稍微加了空格/標點被 Agent 判定成「新產品」發現兩次）。這是因為 Agent 1/4 原本的去重只做完全字串比對，改動後的版本已經改成正規化比對（去除空格/標點後再比較)，能防止未來再發生，但**現有資料庫裡已經混進去的重複項目不會自動清掉**。跑一次 `scripts/dedupe_products.py`（先不加 `--apply` 看報告，確認沒問題再加 `--apply` 寫入)可以找出並清理這類重複，也會順便標出被複製貼上到錯誤產品上的來源連結（同一個網址出現在兩個不同名稱的產品下，通常代表 AI 幻覺造成的錯誤連結，腳本只會標出來，不會自動改，因為無法自動判斷哪個才是正確連結）。
- `shouldRemove` 只會出現在 `findings` 裡當一筆建議，**不會自動把產品從 `products.json` 刪除**——需要人工複查後手動刪除。
- Agent 4（版號雷達）的間隔判斷假設 GitHub Actions 每天執行；排程改動需連動調整判斷邏輯。
- RSS 來源目前有 3 個中國/手遊向站點（GameLook、遊資網、遊民星空）尚未實測驗證 feed 網址是否穩定，首次啟用後建議檢查 Action log 確認有抓到文章。

---

## 技術架構

```
index.html          ← GitHub Pages 靜態網頁（React 18 + Babel CDN，零建構步驟）
config.json          ← 探索範圍/RSS來源/威脅比較基準設定
data/
  products.json      ← 產品資料庫（手動 + Agent 1/2/4 自動寫入）
  daily-brief.json   ← Agent 3 自動更新，含 Agent 1/4 推送的 urgent 項目
  quarterly.json     ← Agent 3 自動更新，年度自動重置
  industry-trends.json ← Agent 3 自動更新
  audit-report.json  ← Agent 2 每次執行後寫入
scripts/
  update_data.py     ← 4-Agent 邏輯本體
.github/workflows/
  daily-update.yml   ← 每日排程設定
```

**後端**：無伺服器 — GitHub Actions 負責資料抓取與 AI 分析，結果存為靜態 JSON
**AI 引擎**：Google Gemini API（`gemini-2.5-flash`，含網路搜尋 grounding）
**更新流程**：cron job → Python 腳本（4 個 agent）→ commit JSON → GitHub Pages 自動部署

---

## License

MIT
