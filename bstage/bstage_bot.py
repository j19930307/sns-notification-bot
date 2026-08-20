import json
import os
import random
import re
import time
from typing import Any

from sns_core.clients.discord_messages import post_message, build_embeds
from sns_core.utils.media import download_video_to_local, cleanup_local_files, download_m3u8_to_mp4

import requests
from dateutil import parser
from fake_useragent import UserAgent
from sns_core import (
    FirestoreSubscriptionStore,
    PostAuthor,
    SocialPlatform,
    SocialPost,
)

ua = UserAgent()
user_agent = ua.random
headers = {'user-agent': user_agent}
JsonDict = dict[str, Any]


def convert_to_datetime(date_string):
    return parser.isoparse(date_string)


class BstageBot:
    def __init__(self, firestore: FirestoreSubscriptionStore):
        self.__firestore = firestore
        self.__mnet_plus_build_id = None
        self.__bstage_build_ids = {}

    def _fetch_mnet_plus_build_id(self):
        response = requests.get("https://artist.mnetplus.world", headers=headers, timeout=20)
        response.raise_for_status()

        build_id_match = re.search(r'"buildId":"([^"]+)"', response.text)
        if build_id_match is None:
            raise ValueError("找不到 Mnet Plus buildId")
        return build_id_match.group(1)

    def _get_mnet_plus_build_id(self):
        if self.__mnet_plus_build_id is None:
            self.__mnet_plus_build_id = self._fetch_mnet_plus_build_id()
        return self.__mnet_plus_build_id

    def _fetch_bstage_build_id(self, artist):
        response = requests.get(f"https://{artist}.bstage.in", headers=headers, timeout=20)
        response.raise_for_status()

        build_id_match = re.search(r'"buildId":"([^"]+)"', response.text)
        if build_id_match is None:
            raise ValueError("找不到 b.stage buildId")
        return build_id_match.group(1)

    def _get_bstage_build_id(self, artist):
        if artist not in self.__bstage_build_ids:
            self.__bstage_build_ids[artist] = self._fetch_bstage_build_id(artist)
        return self.__bstage_build_ids[artist]

    def _fetch_post_detail(self, detail_url: str, *, params: dict[str, str] | None = None) -> JsonDict:
        response = requests.get(
            detail_url,
            headers=headers,
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        result = json.loads(response.text)
        return result["pageProps"]["post"]

    def _fetch_mnet_plus_post_detail(self, artist: str, post_id: str) -> JsonDict:
        build_id = self._get_mnet_plus_build_id()
        detail_url = (
            f"https://artist.mnetplus.world/_next/data/{build_id}/ko/main/stg/"
            f"{artist}/story/feed/{post_id}.json"
        )
        try:
            return self._fetch_post_detail(
                detail_url,
                params={"basePath": artist, "id": post_id},
            )
        except requests.HTTPError as error:
            if error.response is None or error.response.status_code != 404:
                raise
            self.__mnet_plus_build_id = self._fetch_mnet_plus_build_id()
            detail_url = (
                f"https://artist.mnetplus.world/_next/data/{self.__mnet_plus_build_id}/ko/main/stg/"
                f"{artist}/story/feed/{post_id}.json"
            )
            return self._fetch_post_detail(
                detail_url,
                params={"basePath": artist, "id": post_id},
            )

    def _fetch_bstage_post_detail(self, artist: str, post_id: str) -> JsonDict:
        build_id = self._get_bstage_build_id(artist)
        detail_url = f"https://{artist}.bstage.in/_next/data/{build_id}/ko/story/feed/{post_id}.json"
        try:
            return self._fetch_post_detail(detail_url)
        except requests.HTTPError as error:
            if error.response is None or error.response.status_code != 404:
                raise
            self.__bstage_build_ids[artist] = self._fetch_bstage_build_id(artist)
            detail_url = f"https://{artist}.bstage.in/_next/data/{self.__bstage_build_ids[artist]}/ko/story/feed/{post_id}.json"
            return self._fetch_post_detail(detail_url)

    def _build_mnet_plus_social_post(self, artist: str, post_id: str):
        images = []
        videos = []
        post: JsonDict | None = None

        try:
            post = self._fetch_mnet_plus_post_detail(artist, post_id)
        except Exception as error:
            print(f"Mnet Plus 詳細貼文抓取失敗 {artist}/{post_id}: {error}")

        if post is None:
            raise ValueError(f"無法取得 Mnet Plus 貼文詳細資料: {artist}/{post_id}")

        images.extend(post.get("images") or [])

        video = post.get("video")
        if isinstance(video, dict):
            videos.append(video.get("hlsPath"))

        description = post.get("body") or ""
        author = post["author"]

        return SocialPost(
            post_link=f"https://artist.mnetplus.world/main/stg/{artist}/story/feed/{post_id}",
            author=PostAuthor(author["nickname"], author["avatarImgPath"]),
            text=description,
            images=images,
            videos=videos,
            created_at=convert_to_datetime(post["publishedAt"]),
        )

    def _build_bstage_social_post(self, artist: str, post_id: str):
        post = self._fetch_bstage_post_detail(artist, post_id)

        images = list(post.get("images") or [])
        file_paths = []
        video = post.get("video")
        if isinstance(video, dict):
            hls_path = video.get("hlsPath")
            if isinstance(hls_path, dict) and hls_path.get("path"):
                file_path = download_video_to_local(
                    video_url=f"https://media.static.bstage.in/{artist}{hls_path['path']}",
                    filename=f"{time.time()}.mp4"
                )
                file_paths.append(file_path)

        author = post["author"]
        return SocialPost(
            post_link=f"https://{artist}.bstage.in/story/feed/{post_id}",
            author=PostAuthor(author["nickname"], author["avatarImgPath"]),
            text=post.get("body") or "",
            images=images,
            file_paths=file_paths,
            created_at=convert_to_datetime(post["publishedAt"]),
        )

    async def execute(self):
        bstage_subscribed_list = await self.__firestore.get_subscribed_list(SocialPlatform.BSTAGE)
        for doc in bstage_subscribed_list:
            # 每隔 3 ~ 5 秒執行
            random_sleep_time = random.uniform(3, 5)
            time.sleep(random_sleep_time)
            artist = doc.id
            discord_channel_id = doc.get("discord_channel_id")
            # 取得上次最新發文時間
            last_updated = doc.get("updated_at")
            print(f"{artist} 上次發文時間: {last_updated}")
            print("開始抓取資料...")
            request = requests.get(headers=headers,
                                   url=f"https://{artist}.bstage.in/svc/home/api/v1/home/star?page=1&pageSize=10")
            data = json.loads(request.text)

            social_posts = []
            for item in data["feeds"]["items"]:
                # 跳過付費文章
                if item["type"] == "FEED_ITEM_STAR_POST_PAID":
                    continue
                published_at_datetime = convert_to_datetime(item["publishedAt"])
                if last_updated < published_at_datetime:
                    social_post = self._build_bstage_social_post(artist=artist, post_id=item["typeId"])
                    social_posts.append(social_post)
                else:
                    break

            post_count = len(social_posts)
            if post_count != 0:
                print(f"有 {post_count} 則發文")
                for social_post in reversed(social_posts):
                    print(social_post)
                    post_message(
                        channel_id=discord_channel_id,
                        content=social_post.post_link,
                        embeds=build_embeds(social_post),
                        file_paths=social_post.file_paths
                    )
                    cleanup_local_files(social_post.file_paths)
                # 儲存最新發文時間
                updated_at = max([social_post.created_at for social_post in social_posts])
                print(f"更新最後發文時間: {updated_at}")
                await self.__firestore.set_updated_at(SocialPlatform.BSTAGE, artist, updated_at)
            else:
                print("無新發文")
            print("抓取結束")

        mnet_plus_subscribed_list = await self.__firestore.get_subscribed_list(SocialPlatform.MNET_PLUS)
        for doc in mnet_plus_subscribed_list:
            # 每隔 3 ~ 5 秒執行
            random_sleep_time = random.uniform(3, 5)
            time.sleep(random_sleep_time)
            artist = doc.id
            discord_channel_id = doc.get("discord_channel_id")
            # 取得上次最新發文時間
            last_updated = doc.get("updated_at")
            print(f"{artist} 上次發文時間: {last_updated}")
            print("開始抓取資料...")
            request = requests.get(headers=headers,
                                   url=f"https://artist.mnetplus.world/svc/stg/{artist}/home/api/v1/home/star/feeds")
            data = json.loads(request.text)

            social_posts = []
            for item in data["items"]:
                # 跳過付費文章
                if item["type"] == "FEED_ITEM_STAR_POST_PAID":
                    continue
                published_at_datetime = convert_to_datetime(item["publishedAt"])
                if last_updated < published_at_datetime:
                    social_post = self._build_mnet_plus_social_post(artist=artist, post_id=item["typeId"])
                    social_posts.append(social_post)
                else:
                    break

            post_count = len(social_posts)
            if post_count != 0:
                print(f"有 {post_count} 則發文")
                for social_post in reversed(social_posts):
                    print(social_post)
                    post_message(
                        channel_id=discord_channel_id,
                        content=social_post.post_link,
                        embeds=build_embeds(social_post)
                    )

                    videos = social_post.videos
                    if videos is not None and len(videos) > 0:
                        file_paths = []
                        for video in videos:
                            file_path = download_m3u8_to_mp4(video, f"{time.time()}.mp4")
                            file_paths.append(file_path)

                        # 直接傳入檔案路徑列表
                        post_message(
                            channel_id=discord_channel_id,
                            content="",
                            file_paths=file_paths  # 直接傳入路徑列表
                        )

                        # 清理暫存檔案
                        for file_path in file_paths:
                            if os.path.exists(file_path):
                                os.remove(file_path)
                # 儲存最新發文時間
                updated_at = max([social_post.created_at for social_post in social_posts])
                print(f"更新最後發文時間: {updated_at}")
                await self.__firestore.set_updated_at(SocialPlatform.MNET_PLUS, artist, updated_at)
            else:
                print("無新發文")
            print("抓取結束")


if __name__ == '__main__':
    file_paths = []
    file_path = download_m3u8_to_mp4(
        "https://media.static.bstage.in/limelight/media/68c166446b9b4960d49eac08/hls/ori.m3u8", f"{time.time()}.mp4")
    file_paths.append(file_path)
