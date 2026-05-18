import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
    BOT_MODE = os.getenv("BOT_MODE", "prod").lower() # test 또는 prod
    RSS_POLL_INTERVAL = int(os.getenv("RSS_POLL_INTERVAL", "1"))
    DATABASE_URL = os.getenv("DATABASE_URL", "")
