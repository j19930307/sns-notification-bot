import asyncio
import json
import os
import random
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from dateutil import parser
from fake_useragent import UserAgent
from sns_core import FirestoreSubscriptionStore, PostAuthor, SocialPlatform, SocialPost
from sns_core.clients.discord_messages import build_embeds, build_text_embed, post_message
from sns_core.utils.media import cleanup_local_files, download_m3u8_to_mp4


DISCORD_MAX_FILE_SIZE = 25 * 1024 * 1024
DISCORD_FILE_BATCH_SIZE = 10
VIDEO_URL_KEYS = ("videoUrl", "url", "hlsPath", "playUrl", "sourceUrl")


class BerrizBot:
    def __init__(self, firestore: FirestoreSubscriptionStore):
        self.__firestore = firestore

    def _request_headers(self) -> dict[str, str]:
        return {"user-agent": UserAgent().random}

    def _download_image(self, media_url: str, destination_dir: str, index: int) -> str | None:
        filename = Path(urlparse(media_url).path).name or f"image_{index}.jpg"
        output_path = os.path.join(destination_dir, f"image_{index}_{filename}")
        downloaded_size = 0
        oversized = False

        with requests.get(media_url, headers=self._request_headers(), stream=True, timeout=30) as response:
            response.raise_for_status()
            with open(output_path, "wb") as file:
                for chunk in response.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    downloaded_size += len(chunk)
                    if downloaded_size > DISCORD_MAX_FILE_SIZE:
                        oversized = True
                        break
                    file.write(chunk)

        if oversized:
            os.remove(output_path)
            print(f"媒體超過 Discord 25MB 限制，改傳連結: {media_url}")
            return None
        return output_path

    def _download_video(self, media_url: str, destination_dir: str, index: int) -> str | None:
        output_path = os.path.join(destination_dir, f"video_{index}.mp4")
        file_path = download_m3u8_to_mp4(media_url, output_path)
        if file_path is None or not os.path.exists(file_path):
            return None
        if os.path.getsize(file_path) > DISCORD_MAX_FILE_SIZE:
            cleanup_local_files([file_path])
            print(f"媒體超過 Discord 25MB 限制，改傳連結: {media_url}")
            return None
        return file_path

    def _send_social_post_with_all_media(self, channel_id: str, social_post: SocialPost) -> None:
        """Send the post card followed by every downloadable image and video attachment."""
        if len(social_post.images or []) <= 4 and not social_post.videos:
            post_message(
                channel_id=channel_id,
                content=social_post.post_link,
                embeds=build_embeds(social_post),
            )
            return

        post_message(
            channel_id=channel_id,
            content=social_post.post_link,
            embeds=build_text_embed(social_post),
        )

        media_dir = tempfile.mkdtemp(prefix="sns-media-")
        downloaded_files: list[str] = []
        media_links: list[str] = []
        try:
            for index, image_url in enumerate(social_post.images or [], start=1):
                try:
                    file_path = self._download_image(image_url, media_dir, index)
                    if file_path:
                        downloaded_files.append(file_path)
                    else:
                        media_links.append(image_url)
                except requests.RequestException as error:
                    print(f"圖片下載失敗，改傳連結 {image_url}: {error}")
                    media_links.append(image_url)

            for index, video_url in enumerate(social_post.videos or [], start=1):
                try:
                    file_path = self._download_video(video_url, media_dir, index)
                    if file_path:
                        downloaded_files.append(file_path)
                    else:
                        media_links.append(video_url)
                except Exception as error:
                    print(f"影片下載失敗，改傳連結 {video_url}: {error}")
                    media_links.append(video_url)

            for start in range(0, len(downloaded_files), DISCORD_FILE_BATCH_SIZE):
                post_message(
                    channel_id=channel_id,
                    file_paths=downloaded_files[start:start + DISCORD_FILE_BATCH_SIZE],
                )

            if media_links:
                post_message(channel_id=channel_id, content="\n".join(media_links))
        finally:
            cleanup_local_files(downloaded_files)
            try:
                os.rmdir(media_dir)
            except OSError:
                pass

    @staticmethod
    def _extract_video_urls(media: dict) -> list[str]:
        video_items = media.get("video") or media.get("videos") or []
        if isinstance(video_items, (str, dict)):
            video_items = [video_items]

        video_urls = []
        for item in video_items:
            if isinstance(item, str) and item:
                video_urls.append(item)
                continue
            if not isinstance(item, dict):
                continue
            for key in VIDEO_URL_KEYS:
                value = item.get(key)
                if isinstance(value, str) and value:
                    video_urls.append(value)
                    break
        return video_urls

    async def execute(self) -> None:
        subscribed_list = await self.__firestore.get_subscribed_list(SocialPlatform.BERRIZ)
        for doc in subscribed_list:
            await asyncio.sleep(random.uniform(3, 5))

            artist = doc.id
            community_id = doc.get("community_id")
            board_id = doc.get("board_id")
            discord_channel_id = doc.get("discord_channel_id")
            last_updated = doc.get("updated_at")
            print(f"{artist} 上次發文時間: {last_updated}")
            print("開始抓取 Berriz 資料...")
            posts = self._extract_posts_data(
                group_name=artist,
                community_id=community_id,
                board_id=board_id,
                last_updated=last_updated,
            )
            if not posts:
                print("沒有新的 Berriz 貼文")
                continue

            for post in reversed(posts):
                self._send_social_post_with_all_media(discord_channel_id, post)

            updated_at = max(post.created_at for post in posts if post.created_at is not None)
            print(f"更新最後發文時間: {updated_at}")
            await self.__firestore.set_updated_at(SocialPlatform.BERRIZ, artist, updated_at)

    def _extract_posts_data(
        self,
        group_name: str,
        community_id: str,
        board_id: str,
        last_updated: datetime,
    ) -> list[SocialPost]:
        url = f"https://svc-api.berriz.in/service/v1/community/{community_id}/boards/{board_id}/feed?pageSize=10"
        response = requests.get(url=url, headers=self._request_headers(), timeout=20)
        response.raise_for_status()
        contents = response.json()["data"]["contents"]

        posts = []
        for content in contents:
            post = content["post"]
            writer = content["writer"]
            created_at = parser.isoparse(post["createdAt"]).astimezone(timezone.utc)
            if created_at <= last_updated:
                continue

            media = post.get("media") or {}
            images = [
                photo["imageUrl"]
                for photo in media.get("photo") or []
                if isinstance(photo, dict) and photo.get("imageUrl")
            ]
            posts.append(
                SocialPost(
                    post_link=f"https://berriz.in/en/{group_name}/board/{board_id}/post/{post['postId']}/",
                    author=PostAuthor(name=writer["name"], url=writer["imageUrl"]),
                    text=post.get("body") or "",
                    images=images,
                    videos=self._extract_video_urls(media),
                    created_at=created_at,
                )
            )
        return posts
