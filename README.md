# 🎮 Game Market Intelligence Platform

AI 驅動的遊戲市場競品情報平台，部署於 GitHub Pages，支援自動每日資料更新。

## 功能模組

| 模組 | 說明 | 資料更新 |
|------|------|----------|
| 📋 產品總覽 | 卡片式瀏覽所有追蹤中產品 | 手動編輯 `products.json` |
| 🛰 階段看板 | Kanban 視圖按開發階段分欄 | 同上 |
| 🎯 競品排行 | 依預註冊數/威脅等級排序 | 同上 |
| 📅 上線時程 | 月曆式競品上線排程 | 同上 |
| ☀️ 每日簡報 | AI 自動抓取遊戲新聞 TOP 20 | **GitHub Actions 每日自動** |
| 📊 季度追蹤 | Q1~Q4 產品事件歸檔 | **GitHub Actions 每日自動** |

### 分類邏輯
- **📱 手機遊戲** — 只要產品支援的平台中包含 `Mobile`，即歸入此類（含跨平台產品）
- **🎮 主機/PC 遊戲** — 僅支援 PC 和/或主機平台，不含任何行動端版本

### 資料更新規則
- **每日簡報** — 每天 08:30 TST 自動從 RSS 抓取，保留最新 20 條，新增項目置頂
- **季度追蹤** — 產品事件依發生日期歸入 Q1~Q4，**每年 1 月 1 日自動清空並重建新年度**

---

## 快速部署

### 1. 建立 GitHub Repository

```bash
# 在本地 clone 或直接在 GitHub 建立新 repo
git init game-market-intel
cd game-market-intel

# 將所有檔案複製進來（保持目錄結構）
# 確認結構如下：
#   index.html
#   data/products.json
#   data/daily-brief.json
#   data/quarterly.json
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
4. 點擊 Save
5. 等待 1-2 分鐘後，網站即上線於：
   `https://YOUR_USERNAME.github.io/game-market-intel/`

### 3. 設定自動更新（選用）

#### 基本版（RSS 抓取）
GitHub Actions workflow 已預設配置，推送到 main 後會自動啟用：
- 每天 UTC 00:30（台灣時間 08:30）自動執行
- 從 IGN、GameSpot、PC Gamer 等 RSS 來源抓取遊戲新聞
- 自動分類、寫入 `data/daily-brief.json` 和 `data/quarterly.json`
- 自動 commit & push 更新

#### 進階版（AI 摘要）
如果你有 Anthropic API Key，可以啟用 AI 自動摘要：
1. 進入 repo → **Settings** → **Secrets and variables** → **Actions**
2. 新增 Secret：`ANTHROPIC_API_KEY` = 你的 API Key
3. 腳本會自動偵測，將英文新聞翻譯成繁體中文摘要並分類

### 4. 手動觸發更新

除了每日自動執行，你也可以手動觸發：
1. 進入 repo → **Actions** → **Daily Game Intel Update**
2. 點擊 **Run workflow** → **Run workflow**

---

## 資料結構

### `data/products.json` — 產品資料庫
手動維護的核心資料。新增產品時確認以下欄位：
```json
{
  "id": 17,
  "name": "遊戲名稱",
  "studio": "開發商",
  "publisher": "發行商",
  "genre": "品類",
  "platform": ["PC", "Mobile", "Console"],  // ← 決定分類
  "region": "CN/Global",
  "model": "F2P",
  "stage": "announced",  // announced | pre-reg | cbt | soft-launch | live-ops
  "threat": "medium",    // critical | high | medium | low
  "prereg": 500000,
  "sentiment": 75,
  "launchEst": "2026-Q4",
  "desc": "產品描述...",
  "tags": ["標籤1", "標籤2"],
  "history": [{"date": "2026-05", "s": "announced"}],
  "updatedAt": "2026-05-28"
}
```

### `data/daily-brief.json` — 每日簡報（自動更新）
```json
{
  "lastUpdated": "ISO timestamp",
  "fetchCount": 42,
  "items": [
    {
      "id": "brief-20260528-abc123",
      "priority": "urgent | watch | info",
      "icon": "🚨",
      "title": "標題",
      "detail": "摘要...",
      "action": "建議行動...",
      "platform": "mobile | console",
      "source": "來源",
      "fetchedAt": "ISO timestamp"
    }
  ]
}
```
- 最多保留 **20 條**，超過時自動移除最舊的
- 新項目永遠排在**最前面**

### `data/quarterly.json` — 季度追蹤（自動更新 + 年度重置）
```json
{
  "year": 2026,
  "resetAt": "2026-01-01T00:00:00Z",
  "quarters": {
    "Q1": { "label": "2026 Q1 (1月-3月)", "events": [...] },
    "Q2": { ... },
    "Q3": { ... },
    "Q4": { ... }
  }
}
```
- 每年 **1 月 1 日** 腳本偵測到新年度時自動清空所有事件
- 重建 4 個空季度並開始累積新年度資料

---

## 自訂 RSS 來源

編輯 `scripts/update_data.py` 中的 `RSS_FEEDS` 列表：

```python
RSS_FEEDS = [
    {"url": "https://www.ign.com/articles.rss", "name": "IGN", "lang": "en"},
    # 新增你的來源：
    {"url": "https://your-source.com/rss", "name": "Your Source", "lang": "zh"},
]
```

---

## 技術架構

```
index.html          ← GitHub Pages 靜態網頁（React + Babel）
data/
  products.json     ← 手動維護的產品資料庫
  daily-brief.json  ← 自動更新的每日簡報
  quarterly.json    ← 自動更新的季度追蹤
scripts/
  update_data.py    ← GitHub Actions 執行的更新腳本
.github/workflows/
  daily-update.yml  ← 每日排程設定
```

**前端**: React 18 + Babel (CDN)，純靜態 HTML，零建構步驟
**後端**: 無伺服器 — GitHub Actions 負責資料抓取，結果存為 JSON
**更新**: GitHub Actions cron job → Python 腳本 → commit JSON → Pages 自動部署

---

## License

MIT
