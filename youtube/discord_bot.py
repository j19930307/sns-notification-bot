import json
import os
import requests
from discord import Embed


class DiscordBot:
    def __init__(self):
        pass

    def send_message(self, discord_channel_id: str, content: str = "", embeds=None, files=None):
        url = f"https://discord.com/api/channels/{discord_channel_id}/messages"
        headers = {
            'Authorization': f'Bot {os.environ["BOT_TOKEN"]}',
        }

        embed_dicts = [embed.to_dict() for embed in embeds] if embeds else []

        if files:
            # 當有檔案附件時使用 multipart/form-data
            data = {
                "payload_json": json.dumps({
                    "content": content,
                    "embeds": embed_dicts
                })
            }
            # files 格式: [('files[0]', (filename, file_bytes, content_type)), ...]
            return requests.post(url, headers=headers, data=data, files=files)
        else:
            # 無檔案時使用標準 application/json
            headers['Content-Type'] = 'application/json'
            payload = json.dumps({
                "content": content,
                "embeds": embed_dicts
            })
            return requests.post(url, headers=headers, data=payload)
