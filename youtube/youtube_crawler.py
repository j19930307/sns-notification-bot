import json
import os
from datetime import datetime, timezone
from typing import Optional

import aiohttp
from lxml import html


class YouTubeCrawler:
    """YouTube 頻道爬蟲類，用於獲取頻道的影片、短影片和直播資訊"""

    def __init__(self, channel_handle: str, api_key: Optional[str] = None,
                 session: Optional[aiohttp.ClientSession] = None):
        self.channel_handle = channel_handle
        self.api_key = api_key or os.environ.get("YOUTUBE_DATA_API_KEY")
        self.session = session

    async def _fetch_text(self, url: str) -> str:
        async with self.session.get(url) as response:
            return await response.text()

    # ========= 抓影片 ID =========

    async def get_latest_video_ids(self, latest_video_id: str):
        videos_id = []
        found = False
        try:
            text = await self._fetch_text(
                f'https://www.youtube.com/@{self.channel_handle}/videos'
            )

            tree = html.fromstring(text)
            ytVariableDeclaration = 'ytInitialData = '

            ytVariableData = None
            for script in tree.xpath('//script'):
                scriptContent = script.text_content()
                if ytVariableDeclaration in scriptContent:
                    ytVariableData = json.loads(
                        scriptContent.split(ytVariableDeclaration)[1][:-1]
                    )
                    break

            if not ytVariableData:
                return videos_id, found

            tabs = ytVariableData['contents']['twoColumnBrowseResultsRenderer']['tabs']

            for tab in tabs:
                tabRenderer = tab.get('tabRenderer')
                if not tabRenderer:
                    continue

                url = tabRenderer.get('endpoint', {}).get('commandMetadata', {}).get('webCommandMetadata', {}).get('url', '')
                path = url.split('?')[0].rstrip('/')
                if path.endswith("videos"):
                    contents = tabRenderer['content']['richGridRenderer']['contents']
                    for content in contents:
                        richItemRenderer = content.get('richItemRenderer')
                        if not richItemRenderer:
                            continue

                        content_data = richItemRenderer.get('content', {})
                        video_id = None

                        if 'videoRenderer' in content_data:
                            video_id = content_data['videoRenderer']['videoId']
                        elif 'lockupViewModel' in content_data:
                            video_id = content_data['lockupViewModel'].get('contentId')

                        if not video_id:
                            continue

                        if latest_video_id and video_id == latest_video_id:
                            found = True
                            break
                        videos_id.append(video_id)

            return videos_id, found

        except Exception as e:
            print(e)
            return videos_id, found

    async def get_latest_short_ids(self, latest_short_id: str):
        videos_id = []
        found = False
        try:
            text = await self._fetch_text(
                f'https://www.youtube.com/@{self.channel_handle}/shorts'
            )

            tree = html.fromstring(text)
            ytVariableDeclaration = 'ytInitialData = '

            ytVariableData = None
            for script in tree.xpath('//script'):
                scriptContent = script.text_content()
                if ytVariableDeclaration in scriptContent:
                    ytVariableData = json.loads(
                        scriptContent.split(ytVariableDeclaration)[1][:-1]
                    )
                    break

            if not ytVariableData:
                return videos_id, found

            tabs = ytVariableData['contents']['twoColumnBrowseResultsRenderer']['tabs']

            for tab in tabs:
                tabRenderer = tab.get('tabRenderer')
                if not tabRenderer:
                    continue

                url = tabRenderer.get('endpoint', {}).get('commandMetadata', {}).get('webCommandMetadata', {}).get('url', '')
                path = url.split('?')[0].rstrip('/')
                if path.endswith("shorts"):
                    contents = tabRenderer['content']['richGridRenderer']['contents']

                    for content in contents:
                        richItemRenderer = content.get('richItemRenderer')
                        if not richItemRenderer:
                            continue

                        reelItemRenderer = richItemRenderer['content'].get('reelItemRenderer')
                        shortsLockupViewModel = richItemRenderer['content'].get('shortsLockupViewModel')

                        if reelItemRenderer:
                            video_id = reelItemRenderer['videoId']
                        elif shortsLockupViewModel:
                            video_id = shortsLockupViewModel['onTap']['innertubeCommand']['reelWatchEndpoint'][
                                'videoId']
                        else:
                            continue

                        if latest_short_id and video_id == latest_short_id:
                            found = True
                            break

                        videos_id.append(video_id)

            return videos_id, found

        except Exception as e:
            print(e)
            return videos_id, found

    async def get_latest_stream_ids(self, latest_stream_id: str):
        videos_id = []
        found = False
        try:
            text = await self._fetch_text(
                f'https://www.youtube.com/@{self.channel_handle}/streams'
            )

            tree = html.fromstring(text)
            ytVariableDeclaration = 'ytInitialData = '

            ytVariableData = None
            for script in tree.xpath('//script'):
                scriptContent = script.text_content()
                if ytVariableDeclaration in scriptContent:
                    ytVariableData = json.loads(
                        scriptContent.split(ytVariableDeclaration)[1][:-1]
                    )
                    break

            if not ytVariableData:
                return videos_id, found

            tabs = ytVariableData['contents']['twoColumnBrowseResultsRenderer']['tabs']

            for tab in tabs:
                tabRenderer = tab.get('tabRenderer')
                if not tabRenderer:
                    continue

                url = tabRenderer.get('endpoint', {}).get('commandMetadata', {}).get('webCommandMetadata', {}).get('url', '')
                path = url.split('?')[0].rstrip('/')
                if path.endswith("streams"):
                    contents = tabRenderer['content']['richGridRenderer']['contents']

                    for content in contents:
                        richItemRenderer = content.get('richItemRenderer')
                        if not richItemRenderer:
                            continue

                        content_data = richItemRenderer.get('content', {})
                        video_id = None

                        if 'videoRenderer' in content_data:
                            video_renderer = content_data['videoRenderer']
                            if video_renderer.get('upcomingEventData'):
                                continue
                            video_id = video_renderer['videoId']
                        elif 'lockupViewModel' in content_data:
                            # Assuming lockupViewModel doesn't show upcoming events on this tab
                            video_id = content_data['lockupViewModel'].get('contentId')

                        if not video_id:
                            continue

                        if latest_stream_id and video_id == latest_stream_id:
                            found = True
                            break

                        videos_id.append(video_id)

            return videos_id, found

        except Exception as e:
            print(e)
            return videos_id, found

    # ========= 影片詳細資訊 =========

    async def get_videos_info(self, video_ids: list):
        if not video_ids:
            return []

        url = "https://www.googleapis.com/youtube/v3/videos"
        params = {
            "part": "snippet",
            "id": ",".join(video_ids)
        }
        if self.api_key:
            params["key"] = self.api_key

        try:
            async with self.session.get(url, params=params) as response:
                if response.status != 200:
                    text = await response.text()
                    print(f"YouTube API 請求失敗，狀態碼: {response.status}。請檢查您的 YOUTUBE_DATA_API_KEY。回應內容: {text}")
                    return None
                data = await response.json()
                return data.get("items", [])
        except Exception as e:
            print(f"獲取影片資訊時發生錯誤: {e}")
            return None

    # ========= 發佈時間處理 =========

    def get_video_published_at(self, item):
        snippet = item['snippet']
        live_broadcast_content = snippet['liveBroadcastContent']

        if live_broadcast_content == 'live':
            return datetime.now(timezone.utc)

        published_at = snippet['publishedAt']
        return datetime.strptime(
            published_at,
            "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
