import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
    BOT_MODE = os.getenv("BOT_MODE", "prod").lower() # test 또는 prod
    DATABASE_URL = os.getenv("DATABASE_URL", "")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

    # 코드 고정값 (환경변수 X)
    RSS_POLL_INTERVAL = 1          # RSS 폴링 간격(분)
    GEMINI_MODEL = "gemini-3.5-flash"  # 요약 모델
