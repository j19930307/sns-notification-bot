# SNS Notification Bot

集中管理 YouTube、Cosmo、b.stage 與 Berriz 的通知與資料同步服務，將新內容傳送到 Discord，並使用 Firestore 保存訂閱與處理進度。

## 專案結構

| 目錄 | 用途 | 執行指令 |
| --- | --- | --- |
| `youtube/` | YouTube 影片、Shorts 與直播通知 | `python -m youtube.main` |
| `cosmo/` | Cosmo Room 通知，以及公告、行程與歷史資料同步 | `python -m cosmo.main` |
| `bstage/` | b.stage 與 Mnet Plus 貼文通知 | `python -m bstage.main` |
| `berriz/` | Berriz 貼文通知 | `python -m berriz.main` |
| `shared/` | YouTube 與 Cosmo 共用的 Firebase 存取程式 | — |

## 本機執行

需要 Python 3.12、ffmpeg，以及可存取 Firebase、Discord 和各服務 API 的環境變數。

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows
# .venv/bin/pip install -r requirements.txt     # Linux/macOS
```

在專案根目錄設定 `.env` 後，依需求執行個別服務：

```bash
python -m youtube.main
python -m cosmo.main
python -m bstage.main
python -m berriz.main
```

`bstage` 會下載影片附件，因此需要系統已安裝 `ffmpeg`。

## 環境變數

| 變數 | 用途 |
| --- | --- |
| `FIREBASE_ADMIN_KEY` | Base64 編碼的 Firebase service-account JSON |
| `BOT_TOKEN` | Discord Bot token |
| `YOUTUBE_DATA_API_KEY` | YouTube Data API 金鑰（僅 YouTube 服務） |
| `COSMO_USER_SESSION` | Cosmo Room 讀取用 session（僅 Cosmo 服務） |
| `COSMO_ROOM_DISCORD_CHANNEL_ID` | Cosmo Room 通知頻道 ID |
| `SUPABASE_URL`、`SUPABASE_KEY` | Cosmo 資料同步目標 |

請勿將 `.env` 或任何密鑰提交到版本庫。

## 排程與部署

- GitHub Actions 每 5 分鐘執行 YouTube、b.stage 與 Berriz。
- 推送至 `main` 後，GCE 部署工作流程會更新服務並設定 cron：YouTube 每 3 分鐘、Cosmo 每 5 分鐘、b.stage 每 5 分鐘、Berriz 每 5 分鐘（錯開 2 分鐘）。

GCE 部署需要儲存在 GitHub Secrets 的 `GCP_SA_KEY`；GitHub Actions 執行通知服務時使用 `BOT_TOKEN`、`FIREBASE_ADMIN_KEY` 與 `YOUTUBE_DATA_API_KEY`。