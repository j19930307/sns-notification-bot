import json
import random
import asyncio

from sns_core.clients.discord_messages import post_message, build_embeds

import requests
from fake_useragent import UserAgent
from sns_core import SocialPost, SocialPlatform, PostAuthor, FirestoreSubscriptionStore

from datetime import datetime, timezone


class BerrizBot:
    def __init__(self, firebase: FirestoreSubscriptionStore):
        self.__firestore = firebase

    async def execute(self):
        subscribed_list = await self.__firestore.get_subscribed_list(SocialPlatform.BERRIZ)
        for doc in subscribed_list:
            # 每隔 3 ~ 5 秒執行
            random_sleep_time = random.uniform(3, 5)
            await asyncio.sleep(random_sleep_time)

            artist = doc.id
            community_id = doc.get("community_id")
            board_id = doc.get("board_id")
            discord_channel_id = doc.get("discord_channel_id")
            # 取得上次最新發文時間
            last_updated = doc.get("updated_at")
            print(f"{artist} 上次發文時間: {last_updated}")
            print("開始抓取資料...")
            posts = self._extract_posts_data(group_name=artist, community_id=community_id, board_id=board_id,
                                             last_updated=last_updated)
            if posts:
                for post in reversed(posts):
                    post_message(
                        channel_id=discord_channel_id,
                        content=post.post_link,
                        embeds=build_embeds(post)
                    )
                # 儲存最新發文時間
                updated_at = max([post.created_at for post in posts if post.created_at is not None])
                print(f"更新最後發文時間: {updated_at}")
                await self.__firestore.set_updated_at(SocialPlatform.BERRIZ, artist, updated_at)
            else:
                print("沒有新的貼文")

    def _extract_posts_data(self, group_name: str, community_id: str, board_id: str, last_updated: datetime) -> list[
        SocialPost]:
        url = f"https://svc-api.berriz.in/service/v1/community/{community_id}/boards/{board_id}/feed?pageSize=10"
        ua = UserAgent()
        user_agent = ua.random
        headers = {'user-agent': user_agent}
        response = requests.get(url=url, headers=headers)
        data = json.loads(response.text)

        posts_data = []
        # 取得所有貼文內容
        contents = data['data']['contents']

        for content in contents:
            post = content['post']
            writer = content['writer']
            created_at = datetime.strptime(post['createdAt'], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if created_at <= last_updated:
                continue

            # 提取所有照片的URL
            images = []
            if 'media' in post and 'photo' in post['media']:
                for photo in post['media']['photo']:
                    images.append(photo['imageUrl'])

            social_post = SocialPost(
                post_link=f"https://berriz.in/en/{group_name}/board/{board_id}/post/{post['postId']}/",
                author=PostAuthor(name=writer['name'], url=writer['imageUrl']), text=post['body'], images=images,
                created_at=created_at)
            posts_data.append(social_post)

        return posts_data
