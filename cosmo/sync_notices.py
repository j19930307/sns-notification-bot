import sys
import os
import time
from dotenv import load_dotenv

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

from cosmo.main import sync_notices_to_supabase

def main():
    load_dotenv()
    start_time = time.perf_counter()
    print("🚀 手動觸發 Cosmo 官方公告全量同步至 Supabase...")
    sync_notices_to_supabase(artist_id="tripleS")
    end_time = time.perf_counter()
    print(f"✅ 公告同步完成，總耗時: {end_time - start_time:.2f} 秒")

if __name__ == "__main__":
    main()
