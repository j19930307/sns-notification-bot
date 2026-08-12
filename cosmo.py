import asyncio
import io
import os
import sys
import tempfile
import time
from datetime import datetime, timezone

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

import aiohttp
import requests
from dotenv import load_dotenv

import sns_core.clients.discord_messages as dm
from sns_core import build_embeds, build_text_embed
from sns_core.models import SocialPost, PostAuthor
from sns_core.clients.discord_messages import post_message

from firebase import Firebase

# 註冊 Cosmo Room 來源圖示與名稱
dm._SOURCE_MAP["shop.cosmo.fans"] = ("Cosmo Room", "https://static.cosmo.fans/assets/triples-logo.png")
dm._SOURCE_MAP["cosmo.fans"] = ("Cosmo Room", "https://static.cosmo.fans/assets/triples-logo.png")


def sync_posts_to_supabase(posts: list[dict], artist_id: str = "tripleS"):
    """將 Cosmo 貼文全量同步至 Supabase，包含 videoItem.channels、accessType、duration 與 raw_data"""
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")

    if not supabase_url or not supabase_key:
        return

    url = f"{supabase_url}/rest/v1/room_posts"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal"
    }

    batch = []
    for post in posts:
        video_item = post.get("videoItem") or {}
        channels = video_item.get("channels", [])
        access_type = video_item.get("accessType", "")
        duration = video_item.get("duration", 0)

        batch.append({
            "id": post.get("id"),
            "artist_id": artist_id,
            "kind": post.get("kind", "post"),
            "author_name": post.get("author", {}).get("nickname", "Artist"),
            "author_avatar": post.get("author", {}).get("profileImage", ""),
            "content": post.get("content", ""),
            "media": post.get("media", []),
            "channels": channels,
            "access_type": access_type,
            "duration": duration,
            "raw_data": post,
            "media_aspect_ratio": post.get("mediaAspectRatio", ""),
            "created_at": post.get("createdAt")
        })

    try:
        resp = requests.post(url, headers=headers, json=batch, timeout=15)
        if resp.status_code in (200, 201, 204):
            print(f"⚡ 成功同步 {len(batch)} 篇貼文至 Supabase (包含 raw_data 完整 JSON)")
        else:
            print(f"⚠️ Supabase 同步失敗 Status {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"⚠️ 寫入 Supabase 時發生例外: {e}")


async def process_cosmo_room_posts(firebase: Firebase, session: aiohttp.ClientSession):
    user_session = os.environ.get("COSMO_USER_SESSION")
    discord_channel_id = os.environ.get("COSMO_ROOM_DISCORD_CHANNEL_ID")
    artist_id = "tripleS"

    if not discord_channel_id:
        print("⚠️ 未設定 COSMO_ROOM_DISCORD_CHANNEL_ID，跳過 Cosmo Room Posts 檢查")
        return

    if not user_session:
        print("⚠️ 未設定 COSMO_USER_SESSION，跳過 Cosmo Room Posts 檢查")
        return

    url = f"https://shop.cosmo.fans/bff/v4/room-posts?artistId={artist_id}&take=10&skip=0"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Cookie": f"user-session={user_session}"
    }

    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                print(f"❌ 取得 Cosmo Room Posts 失敗 (Status {resp.status})")
                return
            data = await resp.json()
    except Exception as e:
        print(f"❌ 請求 Cosmo Room Posts 時發生錯誤: {e}")
        return

    posts = data.get("posts", [])
    if not posts:
        print(f"Cosmo Room Posts 沒有資料")
        return

    # 1. 將本輪取得的所有 Room Posts 同步至 Supabase
    sync_posts_to_supabase(posts, artist_id=artist_id)

    # 2. 檢查是否有需要發送 Discord 通知的「一般貼文 (kind == post)」
    latest_info = firebase.get_latest_cosmo_room_post_info(artist_id=artist_id)
    latest_saved_id = latest_info.get("id", 0)

    new_posts = [p for p in posts if p.get("id") > latest_saved_id and p.get("kind") == "post"]

    if not new_posts:
        print(f"Cosmo Room Posts ({artist_id}) 沒有新貼文通知")
        return

    new_posts.sort(key=lambda x: x.get("id"))
    print(f"🎉 偵測到 {len(new_posts)} 篇 Cosmo Room 新貼文！準備使用 sns_core 發送至 Discord...")

    for post in new_posts:
        post_id = post.get("id")
        author_info = post.get("author", {})
        author_name = author_info.get("nickname", "Artist")
        profile_img = author_info.get("profileImage", "")
        content = post.get("content", "")
        created_at_str = post.get("createdAt")
        media_list = post.get("media", [])

        dt = None
        if created_at_str:
            try:
                dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            except Exception:
                dt = datetime.now(timezone.utc)

        author_obj = PostAuthor(name=author_name, url=profile_img)
        images = [m.get("url") for m in media_list if m.get("kind") == "image" and m.get("url")]
        videos = [m.get("url") for m in media_list if m.get("kind") == "video" and m.get("url")]

        social_post = SocialPost(
            post_link=f"https://shop.cosmo.fans/bff/v4/room-posts/{post_id}",
            author=author_obj,
            text=content,
            images=images,
            videos=videos,
            created_at=dt
        )

        use_build_embeds = (len(images) <= 4 and len(videos) == 0)

        try:
            if use_build_embeds:
                embeds = build_embeds(social_post)
                post_message(
                    channel_id=discord_channel_id,
                    embeds=embeds,
                    show_all=False
                )
            else:
                embeds = build_text_embed(social_post)

                # 1. 先發送文字 Embed 卡片
                post_message(
                    channel_id=discord_channel_id,
                    embeds=embeds,
                    show_all=False
                )

                # 2. 下載所有實體媒體檔案並接在 Embed 後面發送
                temp_dir = tempfile.mkdtemp()
                downloaded_files = []
                oversized_media_urls = []
                all_media_urls = images + videos

                for m_idx, media_url in enumerate(all_media_urls):
                    try:
                        async with session.get(media_url) as media_resp:
                            if media_resp.status == 200:
                                file_data = await media_resp.read()
                                filename = media_url.split("/")[-1].split("?")[0]
                                if not filename:
                                    filename = f"media_{m_idx + 1}.jpg"

                                if len(file_data) > 25 * 1024 * 1024:
                                    print(f"⚠️ 媒體檔案容量 ({len(file_data) / (1024*1024):.2f}MB) 超過 Discord 25MB 限制，改用 URL 連結發送")
                                    oversized_media_urls.append(media_url)
                                else:
                                    temp_file_path = os.path.join(temp_dir, filename)
                                    with open(temp_file_path, "wb") as f:
                                        f.write(file_data)
                                    downloaded_files.append(temp_file_path)
                    except Exception as e:
                        print(f"下載 Cosmo 媒體檔案失敗 {media_url}: {e}")

                try:
                    if downloaded_files:
                        chunk_size = 10
                        for i in range(0, len(downloaded_files), chunk_size):
                            chunk_files = downloaded_files[i:i + chunk_size]
                            post_message(
                                channel_id=discord_channel_id,
                                file_paths=chunk_files,
                                show_all=False
                            )
                            await asyncio.sleep(1.0)

                    if oversized_media_urls:
                        links_text = "\n".join([f"{url}" for url in oversized_media_urls])
                        post_message(
                            channel_id=discord_channel_id,
                            content=links_text,
                            show_all=False
                        )
                finally:
                    for fp in downloaded_files:
                        try:
                            os.remove(fp)
                        except Exception:
                            pass
                    try:
                        os.rmdir(temp_dir)
                    except Exception:
                        pass

            print(f"✅ Cosmo Room 貼文 #{post_id} ({author_name}) 成功發送到 Discord 頻道 (use_build_embeds={use_build_embeds})")
            firebase.set_latest_cosmo_room_post_info(
                artist_id=artist_id,
                post_id=post_id,
                published_at=created_at_str,
                author=author_name,
                content=content
            )

        except Exception as e:
            print(f"❌ Cosmo Room 貼文 #{post_id} 發送至 Discord 失敗: {e}")

        await asyncio.sleep(1.0)


def sync_schedules_to_supabase(artist_id: str = "tripleS"):
    """將 Cosmo 藝人行程 (Artist Schedules) 同步至 Supabase artist_schedules 資料表"""
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")

    if not supabase_url or not supabase_key:
        print("⚠️ 未設定 SUPABASE_URL 或 SUPABASE_KEY，跳過行程同步")
        return

    url_list = f"https://shop.cosmo.fans/bff/v3/artist-schedules?artistId={artist_id}&take=100"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    }

    user_session = os.environ.get("COSMO_USER_SESSION")
    if user_session:
        headers["Cookie"] = f"user-session={user_session}"

    try:
        resp = requests.get(url_list, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"⚠️ 取得藝人行程列表失敗 Status {resp.status_code}")
            return
        data = resp.json()
        schedules = data.get("items", [])
    except Exception as e:
        print(f"⚠️ 請求藝人行程列表發生例外: {e}")
        return

    if not schedules:
        print("ℹ️ 未取得任何藝人行程資料")
        return

    print(f"📅 成功取得 {len(schedules)} 筆行程，開始補全場館地點與參演成員...")

    batch = []
    for item in schedules:
        sid = item.get("id")
        title = item.get("title", "")
        content = item.get("content", "")
        start_at = item.get("startAt")
        end_at = item.get("endAt")
        place = item.get("place", "")
        members = item.get("members", [])

        if sid and (not place or not members):
            try:
                detail_url = f"https://shop.cosmo.fans/bff/v3/artist-schedules/{sid}"
                d_resp = requests.get(detail_url, headers=headers, timeout=5)
                if d_resp.status_code == 200:
                    d_data = d_resp.json()
                    place = d_data.get("place") or place
                    members = d_data.get("members") or members
            except Exception:
                pass

        batch.append({
            "id": sid,
            "artist_id": artist_id,
            "title": title,
            "content": content,
            "start_at": start_at,
            "end_at": end_at,
            "place": place,
            "members": members
        })

    supa_endpoint = f"{supabase_url}/rest/v1/artist_schedules"
    supa_headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal"
    }

    try:
        res_sync = requests.post(supa_endpoint, headers=supa_headers, json=batch, timeout=15)
        if res_sync.status_code in (200, 201, 204):
            print(f"⚡ 成功同步 {len(batch)} 筆藝人行程至 Supabase (artist_schedules)")
        else:
            print(f"⚠️ 行程同步至 Supabase 失敗 Status {res_sync.status_code}: {res_sync.text}")
    except Exception as e:
        print(f"⚠️ 寫入 Supabase 行程資料表時發生例外: {e}")


def sync_notices_to_supabase(artist_id: str = "tripleS"):
    """將 Cosmo 官方公告 (Notices) 全量分頁同步至 Supabase notices 資料表"""
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")

    if not supabase_url or not supabase_key:
        print("⚠️ 未設定 SUPABASE_URL 或 SUPABASE_KEY，跳過公告同步")
        return

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    }

    user_session = os.environ.get("COSMO_USER_SESSION")
    if user_session:
        headers["Cookie"] = f"user-session={user_session}"

    take = 100
    skip = 0
    all_raw_notices = []

    print(f"📢 開始透過分頁 (take={take}) 全量抓取 Cosmo 官方公告...")

    while True:
        url_notices = f"https://shop.cosmo.fans/bff/v3/notices?artistId={artist_id}&take={take}&skip={skip}"
        try:
            resp = requests.get(url_notices, headers=headers, timeout=15)
            if resp.status_code != 200:
                print(f"⚠️ 取得官方公告失敗 Status {resp.status_code} at skip={skip}")
                break
            data = resp.json()
            items = data.get("result", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            if not items:
                break
            all_raw_notices.extend(items)
            print(f"   已取得 {len(items)} 則公告 (累計: {len(all_raw_notices)} 則)...")
            if len(items) < take:
                break
            skip += take
        except Exception as e:
            print(f"⚠️ 請求官方公告發生例外: {e}")
            break

    if not all_raw_notices:
        print("ℹ️ 未取得任何官方公告資料")
        return

    print(f"⚡ 成功取得共 {len(all_raw_notices)} 則官方公告，開始補全內文與圖片細節並同步至 Supabase...")

    batch = []
    for idx, item in enumerate(all_raw_notices):
        nid = item.get("id")
        title = item.get("title", "")
        category = item.get("category", "")
        content = item.get("content", "")
        image_url_list = item.get("imageUrlList") or []
        active_at = item.get("activeAt") or item.get("createdAt")
        is_pinned = "[PINNED]" in title or bool(item.get("isPinned"))

        # 呼叫詳情 API 補全完整內文 content 與圖片列表 (imageUrlList)
        if nid:
            try:
                detail_url = f"https://shop.cosmo.fans/bff/v3/notices/{nid}"
                d_resp = requests.get(detail_url, headers=headers, timeout=5)
                if d_resp.status_code == 200:
                    d_res = d_resp.json().get("result", {})
                    content = d_res.get("content") or content
                    image_url_list = d_res.get("imageUrlList") or image_url_list
            except Exception:
                pass

        batch.append({
            "id": nid,
            "artist_id": artist_id,
            "title": title,
            "content": content,
            "image_url_list": image_url_list,
            "is_pinned": is_pinned,
            "order_index": idx,
            "created_at": active_at
        })

    supa_endpoint = f"{supabase_url}/rest/v1/notices"
    supa_headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal"
    }

    try:
        # 分批寫入 Supabase (每批 100 筆)
        batch_size = 100
        for i in range(0, len(batch), batch_size):
            sub_batch = batch[i:i+batch_size]
            res_sync = requests.post(supa_endpoint, headers=supa_headers, json=sub_batch, timeout=15)
            if res_sync.status_code not in (200, 201, 204):
                print(f"⚠️ 公告批次 {i}~{i+len(sub_batch)} 同步至 Supabase 失敗 Status {res_sync.status_code}: {res_sync.text}")

        print(f"⚡ 成功同步全數 {len(batch)} 則官方公告至 Supabase (notices)")
    except Exception as e:
        print(f"⚠️ 寫入 Supabase 公告資料表時發生例外: {e}")


async def main():
    load_dotenv()

    firebase = Firebase()

    start_time = time.perf_counter()
    print("🚀 [Cosmo Room Posts & Notices] 開始執行貼文、行程與公告檢查...")

    # 同步藝人行程與官方公告至 Supabase
    sync_schedules_to_supabase(artist_id="tripleS")
    sync_notices_to_supabase(artist_id="tripleS")

    async with aiohttp.ClientSession() as session:
        await process_cosmo_room_posts(firebase, session)

    end_time = time.perf_counter()
    print(f"✅ [Cosmo Room Posts & Schedules] 檢查完成，總耗時: {end_time - start_time:.2f} 秒")


if __name__ == "__main__":
    asyncio.run(main())

