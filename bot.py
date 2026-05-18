import logging
import logging.handlers
import datetime
from zoneinfo import ZoneInfo
import discord
from discord.ext import commands

import database as db
from config import Config
from utils.embed_builder import bot_welcome_embed

import os

log_level = logging.DEBUG if Config.BOT_MODE == "test" else logging.INFO

os.makedirs("logs", exist_ok=True)

_KST = ZoneInfo("Asia/Seoul")


class _KSTFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.datetime.fromtimestamp(record.created, tz=_KST)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")


_formatter = _KSTFormatter(
    fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(_formatter)

_file_handler = logging.handlers.RotatingFileHandler(
    "logs/tina.log",
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
_file_handler.setFormatter(_formatter)

logging.basicConfig(level=log_level, handlers=[_stream_handler, _file_handler])
logger = logging.getLogger("tina")

# discord.py 내부 로그가 너무 많아지는 것을 방지 (테스트 모드라도 discord 라이브러리 자체는 INFO 유지)
logging.getLogger("discord").setLevel(logging.INFO)
logging.getLogger("discord.http").setLevel(logging.WARNING)


COGS = [
    "cogs.rss_monitor",
    "cogs.scheduler",
    "cogs.commands",
    "cogs.admin",
    "cogs.events",
]


class TinaBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await db.init_db()
        logger.info("데이터베이스 초기화 완료")

        for cog in COGS:
            try:
                await self.load_extension(cog)
                logger.info("Cog 로드: %s", cog)
            except Exception as e:
                logger.error("Cog 로드 실패 [%s]: %s", cog, e)

        await self.tree.sync()
        logger.info("슬래시 명령어 글로벌 동기화 완료")

    async def on_ready(self):
        logger.info("봇 온라인: %s (ID: %s)", self.user.name, self.user.id)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="블로그 포스팅 감시 중"
            )
        )

    async def on_guild_join(self, guild: discord.Guild):
        """봇이 새 서버에 초대되었을 때 안내 메시지 전송"""
        logger.info("새 서버 가입: %s (ID: %s)", guild.name, guild.id)

        await self.tree.sync(guild=discord.Object(id=guild.id))

        channel = guild.system_channel
        if channel is None:
            for ch in guild.text_channels:
                if ch.permissions_for(guild.me).send_messages:
                    channel = ch
                    break

        if channel:
            await channel.send(embed=bot_welcome_embed())


def main():
    if not Config.DISCORD_TOKEN or Config.DISCORD_TOKEN == "your_bot_token_here":
        logger.error("DISCORD_TOKEN이 설정되지 않았습니다. .env 파일을 확인해주세요.")
        logger.error(".env.example을 복사하여 .env를 만들고 토큰을 입력하세요.")
        return

    bot = TinaBot()
    bot.run(Config.DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
