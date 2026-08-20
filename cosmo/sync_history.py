import asyncio
import os
import sys
import time

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

import aiohttp
import requests
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
COSMO_USER_SESSION = os.environ.get("COSMO_USER_SESSION")


def upsert_to_supabase(posts_data: list[dict]) -> bool:
    if not posts_data or not SUPABASE_URL or not SUPABASE_KEY:
        if not SUPABASE_URL or not SUPABASE_KEY:
            print("⚠️ 未設定 SUPABASE_URL 或 SUPABASE_KEY，跳過 Supabase 同步")
        return True

    headers_supabase = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal"
    }

    url = f"{SUPABASE_URL}/rest/v1/room_posts"
    try:
        resp = requests.post(url, headers=headers_supabase, json=posts_data, timeout=30)
        if resp.status_code in (200, 201, 204):
            return True
        else:
            print(f"❌ Supabase UPSERT 失敗 Status {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        print(f"❌ 寫入 Supabase 發生例外: {e}")
        return False


async def fetch_and_sync_all_history():
    if not COSMO_USER_SESSION:
        print("⚠️ 未設定 COSMO_USER_SESSION，無法拉取 Cosmo 歷史貼文")
        return

    artist_id = "tripleS"
    take = 30
    skip = 0
    total_synced = 0
    live_clip_count = 0
    bts_count = 0
    post_count = 0

    print(f"🚀 開始全量同步 Cosmo Room Posts ({artist_id}) 歷史貼文（包含 channels, accessType, duration 與 raw_data）至 Supabase...")

    headers_cosmo = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Cookie": f"user-session={COSMO_USER_SESSION}"
    }

    async with aiohttp.ClientSession() as session:
        while True:
            cosmo_url = f"https://shop.cosmo.fans/bff/v4/room-posts?artistId={artist_id}&take={take}&skip={skip}"
            try:
                async with session.get(cosmo_url, headers=headers_cosmo) as resp:
                    if resp.status != 200:
                        print(f"❌ 請求 Cosmo API 失敗 Status {resp.status} (skip={skip})")
                        break
                    data = await resp.json()
            except Exception as e:
                print(f"❌ 請求 Cosmo API 發生例外 (skip={skip}): {e}")
                break

            posts = data.get("posts", [])
            if not posts:
                print(f"🏁 翻頁完成！(skip={skip})")
                break

            batch_to_upsert = []
            for post in posts:
                post_id = post.get("id")
                kind = post.get("kind", "post")
                author = post.get("author", {})
                author_name = author.get("nickname", "Artist")
                author_avatar = author.get("profileImage", "")
                content = post.get("content", "")
                created_at = post.get("createdAt")
                media = post.get("media", [])
                aspect_ratio = post.get("mediaAspectRatio", "")
                
                # 擷取 live-clip 參與的 channels、accessType 與 duration (秒數)
                video_item = post.get("videoItem") or {}
                channels = video_item.get("channels", [])
                access_type = video_item.get("accessType", "")
                duration = video_item.get("duration", 0)

                if kind == "live-clip":
                    if access_type == "all":
                        bts_count += 1
                    else:
                        live_clip_count += 1
                else:
                    post_count += 1

                batch_to_upsert.append({
                    "id": post_id,
                    "artist_id": artist_id,
                    "kind": kind,
                    "author_name": author_name,
                    "author_avatar": author_avatar,
                    "content": content,
                    "media": media,
                    "channels": channels,
                    "access_type": access_type,
                    "duration": duration,
                    "raw_data": post,
                    "media_aspect_ratio": aspect_ratio,
                    "created_at": created_at
                })

            success = upsert_to_supabase(batch_to_upsert)
            if success:
                total_synced += len(batch_to_upsert)
                print(f"✅ 成功同步 {len(batch_to_upsert)} 篇 (skip={skip}) | 累計: {total_synced} 篇 (正片Live: {live_clip_count}, 幕後: {bts_count}, 貼文: {post_count})")
            else:
                print(f"⚠️ 跳過當前批次 (skip={skip})")

            if len(posts) < take:
                break

            skip += take
            await asyncio.sleep(0.3)

    print(f"\n🎉 歷史資料全量同步完成！")
    print(f"   總同步數量: {total_synced} 篇")
    print(f"   🎬 正片 Live-Clip 數量 (connected): {live_clip_count} 篇")
    print(f"   🎞️ 幕後花絮 數量 (all): {bts_count} 篇")
    print(f"   📝 一般貼文 數量: {post_count} 篇")


if __name__ == "__main__":
    start_time = time.perf_counter()
    asyncio.run(fetch_and_sync_all_history())
    end_time = time.perf_counter()
    print(f"⏱️ 耗時: {end_time - start_time:.2f} 秒")
