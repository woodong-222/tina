import logging
import feedparser
import discord
from discord.ext import commands, tasks
from datetime import datetime
from utils.time_utils import get_kst_now

import database as db
from config import Config
from utils.embed_builder import new_post_embed

logger = logging.getLogger(__name__)


class RSSMonitor(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        await self.bot.wait_until_ready()
        
        # 시작 시 현재 DB 상태 출력
        await self._log_initial_state()
        
        if not self.poll_rss.is_running():
            self.poll_rss.start()
            logger.info("RSS 폴링 루틴 시작 (간격: %d분)", Config.RSS_POLL_INTERVAL)

    def cog_unload(self):
        self.poll_rss.cancel()

    @tasks.loop(minutes=Config.RSS_POLL_INTERVAL)
    async def poll_rss(self):
        """등록된 모든 서버의 멤버 RSS 피드를 확인하여 새 글을 감지"""
        guild_ids = await db.get_all_guild_ids()
        
        if not guild_ids:
            return  # 등록된 멤버가 있는 서버가 없으면 아무것도 하지 않음
            
        logger.debug("RSS 폴링 동작 확인 중... (총 %d개 서버 스캔)", len(guild_ids))

        for guild_id in guild_ids:
            channel_id = await db.get_setting("notification_channel_id", guild_id=guild_id)
            if not channel_id:
                logger.debug("서버(%s): 알림 채널 미설정으로 스킵", guild_id)
                continue

            channel = self.bot.get_channel(int(channel_id))
            if not channel:
                logger.debug("서버(%s): 알림 채널을 찾을 수 없음 스킵", guild_id)
                continue

            members = await db.get_all_members(guild_id)
            if not members:
                logger.debug("서버(%s): 아무도 없네요 저 티나랑 놀아주세요", guild_id)
                continue

            logger.debug("서버(%s) 멤버 %d명 스캔 시작...", guild_id, len(members))
            for member in members:
                try:
                    new_count = await self._check_feed(member, channel)
                    if new_count == 0:
                        logger.debug("   - 스캔 완료: %s 님의 블로그 (변경사항 없음)", member["discord_name"])
                    else:
                        logger.debug("   - 스캔 완료: %s 님의 블로그 (새 글 %d편 감지!)", member["discord_name"], new_count)
                except Exception as e:
                    logger.error("RSS 확인 실패 [%s]: %s", member["discord_name"], e)

    @poll_rss.before_loop
    async def before_poll_rss(self):
        await self.bot.wait_until_ready()

    async def _check_feed(self, member: dict, channel: discord.TextChannel) -> int:
        """개별 멤버의 RSS 피드를 파싱하여 새 글을 찾고 알림. 감지한 새 글 수를 반환."""
        feed = feedparser.parse(member["rss_url"])
        new_count = 0

        if feed.bozo and not feed.entries:
            logger.warning("RSS 파싱 실패 [%s]: %s", member["discord_name"], feed.bozo_exception)
            return 0

        for entry in feed.entries:
            link = entry.get("link", "").strip()
            if not link:
                continue

            if await db.is_post_exists(link):
                continue

            title = entry.get("title", "제목 없음").strip()

            try:
                published_dt = datetime(*entry.published_parsed[:6]) if entry.get("published_parsed") else get_kst_now()
                published_str = published_dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                published_str = get_kst_now().strftime("%Y-%m-%d %H:%M:%S")

            saved = await db.add_post(
                member_id=member["id"],
                title=title,
                link=link,
                published_at=published_str
            )

            if saved:
                mention = f"<@{member['discord_id']}>"
                embed = new_post_embed(
                    author_name=mention,
                    title=title,
                    link=link,
                    published_at=published_str
                )
                await channel.send(content=f"{mention}님이 새 글을 올렸어요!", embed=embed)
                logger.info("새 글 감지: [%s] %s", member["discord_name"], title)
                new_count += 1

        return new_count

    async def manual_poll(self, guild_id: str, target_discord_id: str = None) -> int:
        """수동 폴링 (명령어에서 호출). 감지한 새 글 수를 반환."""
        members = await db.get_all_members(guild_id)
        if target_discord_id:
            members = [m for m in members if m["discord_id"] == target_discord_id]
            
        new_count = 0

        channel_id = await db.get_setting("notification_channel_id", guild_id=guild_id)
        if not channel_id:
            return 0

        channel = self.bot.get_channel(int(channel_id))
        if not channel:
            return 0

        for member in members:
            try:
                feed = feedparser.parse(member["rss_url"])
                for entry in feed.entries:
                    link = entry.get("link", "").strip()
                    if not link or await db.is_post_exists(link):
                        continue

                    title = entry.get("title", "제목 없음").strip()
                    try:
                        published_dt = datetime(*entry.published_parsed[:6]) if entry.get("published_parsed") else get_kst_now()
                        published_str = published_dt.strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        published_str = get_kst_now().strftime("%Y-%m-%d %H:%M:%S")

                    saved = await db.add_post(member["id"], title, link, published_str)
                    if saved:
                        mention = f"<@{member['discord_id']}>"
                        embed = new_post_embed(mention, title, link, published_str)
                        await channel.send(content=f"{mention}님이 새 글을 올렸어요!", embed=embed)
                        new_count += 1
            except Exception as e:
                logger.error("수동 폴링 실패 [%s]: %s", member["discord_name"], e)

        return new_count

    async def _log_initial_state(self):
        """현재 DB에 등록된 서버와 멤버 현황을 로그로 출력"""
        guild_ids = await db.get_all_guild_ids()
        
        if not guild_ids:
            logger.info("현재 등록된 멤버가 있는 서버가 없습니다.")
            return

        logger.info("┌──────────────────────────────────────────┐")
        logger.info("│           현재 봇 운영 현황              │")
        logger.info("├──────────────────────────────────────────┤")
        
        for guild_id in guild_ids:
            guild = self.bot.get_guild(int(guild_id))
            guild_name = guild.name if guild else f"알 수 없는 서버({guild_id})"
            
            channel_id = await db.get_setting("notification_channel_id", guild_id=guild_id)
            channel = self.bot.get_channel(int(channel_id)) if channel_id else None
            channel_name = f"#{channel.name}" if channel else "미설정"
            
            members = await db.get_all_members(guild_id)
            member_names = [m['discord_name'] for m in members]
            
            logger.info(f" 서버 : {guild_name}")
            logger.info(f" 채널 : {channel_name}")
            logger.info(f" 멤버 : {', '.join(member_names) if member_names else '없음'} ({len(members)}명)")
            logger.info("├──────────────────────────────────────────┤")
            
        logger.info("└──────────────────────────────────────────┘")


async def setup(bot: commands.Bot):
    await bot.add_cog(RSSMonitor(bot))
