import logging
import logging.handlers
import discord
from discord.ext import commands

import database as db
from config import Config

import os

log_level = logging.DEBUG if Config.BOT_MODE == "test" else logging.INFO

os.makedirs("logs", exist_ok=True)
os.makedirs("data", exist_ok=True)

logging.basicConfig(
    level=log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            "logs/tina.log", 
            maxBytes=5*1024*1024,
            backupCount=3,
            encoding="utf-8"
        )
    ]
)
logger = logging.getLogger("tina")

# discord.py 내부 로그가 너무 많아지는 것을 방지 (테스트 모드라도 discord 라이브러리 자체는 INFO 유지)
logging.getLogger("discord").setLevel(logging.INFO)
logging.getLogger("discord.http").setLevel(logging.WARNING)
logging.getLogger("aiosqlite").setLevel(logging.INFO)

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
            embed = discord.Embed(
                title="안녕하세요! 저는 티나예요!",
                description=(
                    "블로그 포스팅을 자동으로 감지하고 알려주는 봇이에요.\n\n"
                    "**시작하려면 아래 명령어를 사용해주세요:**\n"
                    "1. `/채널설정 #채널` — 알림을 받을 채널 설정\n"
                    "2. `/멤버등록 @유저 블로그URL` — 멤버 등록\n"
                    "3. `/도움말` — 전체 사용법 보기\n\n"
                    "등록 후 **1분마다** 새 글을 확인하고 알려드릴게요!"
                ),
                color=0x00D4AA
            )
            embed.set_footer(text="티나 • 블로그 포스팅 알림 봇")
            await channel.send(embed=embed)


def main():
    if not Config.DISCORD_TOKEN or Config.DISCORD_TOKEN == "your_bot_token_here":
        logger.error("DISCORD_TOKEN이 설정되지 않았습니다. .env 파일을 확인해주세요.")
        logger.error(".env.example을 복사하여 .env를 만들고 토큰을 입력하세요.")
        return

    bot = TinaBot()
    bot.run(Config.DISCORD_TOKEN)


if __name__ == "__main__":
    main()
