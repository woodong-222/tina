import asyncio
import logging
import ssl
import feedparser
import aiohttp
import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone
from utils.time_utils import get_kst_now, KST

import database as db
from config import Config
from utils.embed_builder import new_post_embed

logger = logging.getLogger(__name__)

FEED_TIMEOUT = aiohttp.ClientTimeout(total=15)
FEED_HEADERS = {"User-Agent": "TinaBot/1.0 (RSS Monitor)"}
FEED_MAX_RETRIES = 2


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

    async def _resolve_channel(self, guild_id: str) -> discord.TextChannel | None:
        """알림 채널 반환. 설정된 채널이 없거나 삭제됐으면 position 순 첫 번째 채널로 폴백."""
        guild = self.bot.get_guild(int(guild_id))
        if not guild:
            return None

        channel_id = await db.get_setting("notification_channel_id", guild_id=guild_id)
        if channel_id:
            channel = self.bot.get_channel(int(channel_id))
            if not channel:
                try:
                    channel = await self.bot.fetch_channel(int(channel_id))
                except discord.NotFound:
                    logger.warning("알림 채널이 삭제됨 - 폴백 채널 탐색 [Guild: %s]", guild_id)
                    channel = None
                except Exception as e:
                    logger.warning("알림 채널 접근 실패 - 폴백 채널 탐색 [Guild: %s]: %s", guild_id, e)
                    channel = None
            if channel:
                return channel

        return next(
            (c for c in guild.text_channels if c.permissions_for(guild.me).send_messages),
            None,
        )

    @tasks.loop(minutes=Config.RSS_POLL_INTERVAL)
    async def poll_rss(self):
        """등록된 모든 서버의 멤버 RSS 피드를 확인하여 새 글을 감지"""
        guild_ids = await db.get_all_guild_ids()
        
        if not guild_ids:
            return  # 등록된 멤버가 있는 서버가 없으면 아무것도 하지 않음
            
        logger.debug("RSS 폴링 동작 확인 중... (총 %d개 서버 스캔)", len(guild_ids))

        for guild_id in guild_ids:
            channel = await self._resolve_channel(guild_id)
            if not channel:
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

    async def _fetch_feed(self, url: str, name: str) -> feedparser.FeedParserDict | None:
        """aiohttp로 RSS XML을 가져온 뒤 feedparser로 파싱. 실패 시 재시도."""
        ssl_ctx = ssl.create_default_context()

        for attempt in range(1, FEED_MAX_RETRIES + 1):
            try:
                async with aiohttp.ClientSession(timeout=FEED_TIMEOUT) as session:
                    async with session.get(url, headers=FEED_HEADERS, ssl=ssl_ctx) as resp:
                        if resp.status != 200:
                            logger.warning("RSS HTTP %d [%s] (시도 %d/%d)", resp.status, name, attempt, FEED_MAX_RETRIES)
                            await asyncio.sleep(2 * attempt)
                            continue
                        xml = await resp.text()
                return feedparser.parse(xml)
            except (aiohttp.ClientError, ssl.SSLError, asyncio.TimeoutError) as e:
                logger.warning("RSS 가져오기 실패 [%s] (시도 %d/%d): %s", name, attempt, FEED_MAX_RETRIES, e)
                if attempt < FEED_MAX_RETRIES:
                    await asyncio.sleep(2 * attempt)

        logger.error("RSS 최종 실패 [%s]: %d회 재시도 후 포기", name, FEED_MAX_RETRIES)
        return None

    async def _check_feed(self, member: dict, channel: discord.TextChannel) -> int:
        """개별 멤버의 RSS 피드를 파싱하여 새 글을 찾고 알림. 감지한 새 글 수를 반환."""
        feed = await self._fetch_feed(member["rss_url"], member["discord_name"])
        new_count = 0

        if feed is None or (feed.bozo and not feed.entries):
            return 0

        for entry in feed.entries:
            link = entry.get("link", "").strip()
            if not link:
                continue

            logger.debug("RSS 항목 확인: [%s] %s", member["discord_name"], link)

            if await db.is_post_exists(link, member["id"]):
                logger.debug("이미 저장된 글 건너뜀: [%s] %s", member["discord_name"], link)
                continue

            title = entry.get("title", "제목 없음").strip()

            try:
                if entry.get("published_parsed"):
                    utc_dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    published_str = utc_dt.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")
                else:
                    published_str = get_kst_now().strftime("%Y-%m-%d %H:%M:%S")
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
                await channel.send(embed=embed)
                logger.info("새 글 감지: [%s] %s", member["discord_name"], title)
                new_count += 1

        return new_count

    async def manual_poll(self, guild_id: str, target_discord_id: str = None) -> int:
        """수동 폴링 (명령어에서 호출). 감지한 새 글 수를 반환."""
        logger.info("수동 폴링 시작 [Guild: %s] (대상: %s)", guild_id, target_discord_id or "전체")
        members = await db.get_all_members(guild_id)
        if target_discord_id:
            members = [m for m in members if m["discord_id"] == target_discord_id]

        new_count = 0

        channel = await self._resolve_channel(guild_id)
        if not channel:
            return 0

        for member in members:
            try:
                logger.debug("수동 폴링 중: [%s] %s", member["discord_name"], member["rss_url"])
                feed = await self._fetch_feed(member["rss_url"], member["discord_name"])
                if feed is None:
                    continue
                for entry in feed.entries:
                    link = entry.get("link", "").strip()
                    if not link or await db.is_post_exists(link, member["id"]):
                        continue

                    title = entry.get("title", "제목 없음").strip()
                    try:
                        if entry.get("published_parsed"):
                            utc_dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                            published_str = utc_dt.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")
                        else:
                            published_str = get_kst_now().strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        published_str = get_kst_now().strftime("%Y-%m-%d %H:%M:%S")

                    saved = await db.add_post(member["id"], title, link, published_str)
                    if saved:
                        mention = f"<@{member['discord_id']}>"
                        embed = new_post_embed(mention, title, link, published_str)
                        await channel.send(embed=embed)
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
            
            logger.info(" 서버 : %s", guild_name)
            logger.info(" 채널 : %s", channel_name)
            logger.info(" 멤버 : %s (%d명)", ", ".join(member_names) if member_names else "없음", len(members))
            logger.info("├──────────────────────────────────────────┤")
            
        logger.info("└──────────────────────────────────────────┘")


async def setup(bot: commands.Bot):
    await bot.add_cog(RSSMonitor(bot))
