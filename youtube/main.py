import asyncio
import os
import sys
import time
from itertools import islice

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

import aiohttp
from dotenv import load_dotenv

from youtube.youtube_crawler import YouTubeCrawler
from youtube.discord_bot import DiscordBot
from shared.firebase import Firebase

MAX_CONCURRENT_CHANNEL = 3
MAX_CONCURRENT_API = 10
YOUTUBE_BATCH_SIZE = 50

channel_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHANNEL)
api_semaphore = asyncio.Semaphore(MAX_CONCURRENT_API)


def chunked(iterable, size):
    it = iter(iterable)
    while True:
        chunk = list(islice(it, size))
        if not chunk:
            return
        yield chunk


async def safe_call(coro):
    try:
        return await coro
    except Exception as e:
        print(f"Error: {e}")
        return None


async def fetch_all_video_info(youtube_crawler, video_ids):
    if not video_ids:
        return []

    results = []
    has_success = False

    for batch in chunked(video_ids, YOUTUBE_BATCH_SIZE):
        async with api_semaphore:
            info = await safe_call(
                youtube_crawler.get_videos_info(video_ids=batch)
            )
            if info is not None:
                has_success = True
                results.extend(info)

    return results if has_success else None


async def process_channel(channel, youtube_crawler, firebase, discord_bot):
    async with channel_semaphore:

        if not channel.exists:
            return

        channel_name = channel.get("channel_name")

        print(f"{channel_name} 開始處理...")

        # ========= 抓 ID =========

        latest_short = channel.get("latest_short")
        short_ids, short_id_found = await youtube_crawler.get_latest_short_ids(
            latest_short_id=latest_short.get("id")
        )

        latest_video = channel.get("latest_video")
        video_ids, video_id_found = await youtube_crawler.get_latest_video_ids(
            latest_video_id=latest_video.get("id")
        )

        latest_stream = channel.get("latest_stream")
        stream_ids, stream_id_found = await youtube_crawler.get_latest_stream_ids(
            latest_stream_id=latest_stream.get("id")
        )

        all_video_ids = stream_ids + video_ids + short_ids

        if not all_video_ids:
            print(f"{channel_name} 沒有新影片")
            return

        # ========= 取得影片資訊 =========

        all_video_info = await fetch_all_video_info(
            youtube_crawler, all_video_ids
        )

        if all_video_info is None:
            print(f"{channel_name} 獲取影片詳細資訊失敗，跳過此頻道處理")
            return

        shorts_info = [info for info in all_video_info if info.get("id") in short_ids]
        videos_info = [info for info in all_video_info if info.get("id") in video_ids]
        streams_info = [info for info in all_video_info if info.get("id") in stream_ids]

        # ========= 處理短影片 =========

        latest_short_published_at = latest_short.get("published_at")
        updated_shorts_info = [
            {**info, "published_at": youtube_crawler.get_video_published_at(info)}
            for info in shorts_info
        ]
        updated_shorts_info.sort(key=lambda x: x.get("published_at"), reverse=True)

        filtered_short_info = [
            info for info in updated_shorts_info
            if latest_short_published_at is None
               or info.get("published_at") > latest_short_published_at
        ]

        if filtered_short_info:
            videos_url = [f"https://youtu.be/{info.get('id')}" for info in filtered_short_info]
            videos_joined = "\n".join(videos_url)
            content = f'{channel_name}上傳了短影片\n{videos_joined}'
            response = discord_bot.send_message(discord_channel_id=channel.get("discord_channel_id"),
                                                content=content)
            if response.status_code == 200:
                print(f"{channel_name} 短影片發送到 Discord 頻道成功")
                firebase.set_latest_short_info(channel_handle=channel.id, short_id=filtered_short_info[0].get("id"),
                                               published_at=filtered_short_info[0].get("published_at"))
            else:
                print(f"{channel_name} 短影片發送到 Discord 頻道失敗")
        else:
            print(f"{channel_name} 沒有新短影片")
            if not short_id_found and updated_shorts_info and latest_short.get("id"):
                exists = await youtube_crawler.get_videos_info([latest_short.get("id")])
                if exists is not None and not exists:
                    newest_short = updated_shorts_info[0]
                    firebase.set_latest_short_info(
                        channel_handle=channel.id,
                        short_id=newest_short.get("id"),
                        published_at=newest_short.get("published_at")
                    )
                    print(f"{channel_name} 偵測到已儲存的短影片 ID 被刪除，更新最新短影片為: {newest_short.get('id')}")
                else:
                    print(f"{channel_name} 已儲存的短影片 ID {latest_short.get('id')} 未在列表中，但 API 確認其依然存在，不更新最新短影片")

        # ========= 處理影片 =========

        latest_video_published_at = latest_video.get("published_at")
        updated_videos_info = [
            {**info, "published_at": youtube_crawler.get_video_published_at(info)}
            for info in videos_info
        ]
        updated_videos_info.sort(key=lambda x: x.get("published_at"), reverse=True)

        filtered_video_info = [
            info for info in updated_videos_info
            if latest_video_published_at is None
               or info.get("published_at") > latest_video_published_at
        ]

        if filtered_video_info:
            videos_url = [f"https://youtu.be/{info.get('id')}" for info in filtered_video_info]
            videos_joined = "\n".join(videos_url)
            content = f'{channel_name}上傳了影片\n{videos_joined}'
            response = discord_bot.send_message(discord_channel_id=channel.get("discord_channel_id"),
                                                content=content)
            if response.status_code == 200:
                print(f"{channel_name} 影片發送到 Discord 頻道成功")
                firebase.set_latest_video_info(channel_handle=channel.id, video_id=filtered_video_info[0].get("id"),
                                               published_at=filtered_video_info[0].get("published_at"))
            else:
                print(f"{channel_name} 影片發送到 Discord 頻道失敗")
        else:
            print(f"{channel_name} 沒有新影片")
            if not video_id_found and updated_videos_info and latest_video.get("id"):
                exists = await youtube_crawler.get_videos_info([latest_video.get("id")])
                if exists is not None and not exists:
                    newest_video = updated_videos_info[0]
                    firebase.set_latest_video_info(
                        channel_handle=channel.id,
                        video_id=newest_video.get("id"),
                        published_at=newest_video.get("published_at")
                    )
                    print(f"{channel_name} 偵測到已儲存的影片 ID 被刪除，更新最新影片為: {newest_video.get('id')}")
                else:
                    print(f"{channel_name} 已儲存的影片 ID {latest_video.get('id')} 未在列表中，但 API 確認其依然存在，不更新最新影片")

        # ========= 處理直播 =========

        latest_stream_published_at = latest_stream.get("published_at")
        updated_streams_info = [
            {**info, "published_at": youtube_crawler.get_video_published_at(info)}
            for info in streams_info
        ]
        updated_streams_info.sort(key=lambda x: x.get("published_at"), reverse=True)

        filtered_stream_info = [
            info for info in updated_streams_info
            if latest_stream_published_at is None
               or info.get("published_at") > latest_stream_published_at
        ]

        if filtered_stream_info:
            videos_url = [f"https://www.youtube.com/live/{info.get('id')}" for info in filtered_stream_info]
            videos_joined = "\n".join(videos_url)
            content = f'{channel_name}直播中\n{videos_joined}'
            response = discord_bot.send_message(discord_channel_id=channel.get("discord_channel_id"),
                                                content=content)
            if response.status_code == 200:
                print(f"{channel_name} 直播發送到 Discord 頻道成功")
                firebase.set_latest_stream_info(channel_handle=channel.id, stream_id=filtered_stream_info[0].get("id"),
                                                published_at=filtered_stream_info[0].get("published_at"))
            else:
                print(f"{channel_name} 直播發送到 Discord 頻道失敗")
        else:
            print(f"{channel_name} 沒有新直播")
            if not stream_id_found and updated_streams_info and latest_stream.get("id"):
                exists = await youtube_crawler.get_videos_info([latest_stream.get("id")])
                if exists is not None and not exists:
                    newest_stream = updated_streams_info[0]
                    firebase.set_latest_stream_info(
                        channel_handle=channel.id,
                        stream_id=newest_stream.get("id"),
                        published_at=newest_stream.get("published_at")
                    )
                    print(f"{channel_name} 偵測到已儲存的直播 ID 被刪除，更新最新直播為: {newest_stream.get('id')}")
                else:
                    print(f"{channel_name} 已儲存的直播 ID {latest_stream.get('id')} 未在列表中，但 API 確認其依然存在，不更新最新直播")


async def main():
    load_dotenv()

    firebase = Firebase()
    discord_bot = DiscordBot()

    session = aiohttp.ClientSession()

    channels = firebase.get_channel_list()

    tasks = [
        process_channel(
            channel=channel,
            youtube_crawler=YouTubeCrawler(
                channel_handle=channel.id,
                api_key=os.environ.get("YOUTUBE_DATA_API_KEY"),
                session=session
            ),
            firebase=firebase,
            discord_bot=discord_bot
        )
        for channel in channels
    ]

    await asyncio.gather(*tasks)

    await session.close()


if __name__ == "__main__":
    start_time = time.perf_counter()
    asyncio.run(main())
    end_time = time.perf_counter()
    print(f"\nYouTube 爬蟲全部執行完成，總耗時: {end_time - start_time:.2f} 秒")
