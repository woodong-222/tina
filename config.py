import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
    BOT_MODE = os.getenv("BOT_MODE", "prod").lower() # test 또는 prod
    RSS_POLL_INTERVAL = int(os.getenv("RSS_POLL_INTERVAL", "1"))
    DATABASE_URL = os.getenv("DATABASE_URL", "")
    # AI 요약 (선택): 키가 없으면 요약 기능 전체 비활성화
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
