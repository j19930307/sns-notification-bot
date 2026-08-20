import asyncio
import os

from dotenv import load_dotenv
from sns_core import FirestoreSubscriptionStore, decode_base64_json

from bstage.bstage_bot import BstageBot


async def main() -> None:
    load_dotenv()
    firebase_admin_key = os.getenv("FIREBASE_ADMIN_KEY")
    if not firebase_admin_key:
        raise ValueError("環境變數中找不到 FIREBASE_ADMIN_KEY！")

    firestore = FirestoreSubscriptionStore(decode_base64_json(firebase_admin_key))
    await BstageBot(firestore).execute()


if __name__ == "__main__":
    asyncio.run(main())