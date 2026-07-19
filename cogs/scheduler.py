import logging
import discord
from discord.ext import commands, tasks
from datetime import datetime, time
import aiohttp
import xml.etree.ElementTree as ET
import re

import database as db
from utils.time_utils import get_week_range, get_last_week_range, get_last_month_range, get_kst_now, KST
from utils.embed_builder import weekly_report_embed, remind_embed, missed_post_embed, monthly_report_embed
from utils.blog_utils import parse_published_at_from_html, _IGNORE_PATTERNS

logger = logging.getLogger(__name__)

SCAN_TIME = time(hour=0, minute=0, tzinfo=KST)  # 매일 자정 스캔


class Scheduler(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.main_scheduler.is_running():
            self.main_scheduler.start()
            logger.info("메인 스케줄러 루프 시작 (1분 단위 체크)")

        if not self.daily_sitemap_scan.is_running():
            self.daily_sitemap_scan.start()
            logger.info("일일 누락 방지 스캔 스케줄러 시작 (매일 %s)", SCAN_TIME)

    def cog_unload(self):
        self.main_scheduler.cancel()
        self.daily_sitemap_scan.cancel()

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

        fallback = next(
            (c for c in guild.text_channels if c.permissions_for(guild.me).send_messages),
            None,
        )
        if fallback:
            logger.info("폴백 채널로 알림 발송 [Guild: %s, Channel: #%s]", guild_id, fallback.name)
        else:
            logger.error("알림 발송 가능한 채널 없음 [Guild: %s]", guild_id)
        return fallback

    @tasks.loop(minutes=1)
    async def main_scheduler(self):
        """매 분마다 실행되며, 각 서버의 초기화/리마인드 시간에 도달했는지 확인"""
        now = get_kst_now()
        guild_ids = await db.get_all_guild_ids()

        for guild_id in guild_ids:
            try:
                r_day, r_hour, r_min = await db.get_reset_time(guild_id)
                
                # 1. 초기화 및 리포트 시간 체크
                if now.weekday() == r_day and now.hour == r_hour and now.minute == r_min:
                    logger.info("주간 리포트 발송 조건 충족 [Guild: %s]", guild_id)
                    await self._send_weekly_report(guild_id, r_day, r_hour, r_min)
                
                # 2. 리마인드 시간 체크 (정확히 24시간 전)
                remind_day = (r_day - 1) % 7
                if now.weekday() == remind_day and now.hour == r_hour and now.minute == r_min:
                    logger.info("마감 리마인드 발송 조건 충족 [Guild: %s]", guild_id)
                    await self._send_remind(guild_id, r_day, r_hour, r_min)

                # 3. 월간 리포트 (매달 1일, 초기화 시간)
                if now.day == 1 and now.hour == r_hour and now.minute == r_min:
                    logger.info("월간 리포트 발송 조건 충족 [Guild: %s]", guild_id)
                    await self._send_monthly_report(guild_id, now)
                    
            except Exception as e:
                logger.error("메인 스케줄러 오류 [Guild: %s]: %s", guild_id, e)

    async def _send_weekly_report(self, guild_id: str, r_day: int, r_hour: int, r_min: int):
        channel = await self._resolve_channel(guild_id)
        if not channel:
            return

        week_start, week_end = get_last_week_range(reset_weekday=r_day, reset_hour=r_hour, reset_minute=r_min)
        members = await db.get_all_members(guild_id)

        penalty_amount_str = await db.get_setting("penalty_amount", default="5000", guild_id=guild_id)
        penalty_amount = int(penalty_amount_str)

        is_paused = await db.get_setting("penalty_paused", default="0", guild_id=guild_id) == "1"

        if not is_paused:
            paused_until_str = await db.get_setting("penalty_paused_until", default="", guild_id=guild_id)
            if paused_until_str:
                try:
                    paused_until = datetime.strptime(paused_until_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
                    if get_kst_now() <= paused_until:
                        is_paused = True
                    else:
                        await db.set_setting(guild_id, "penalty_paused_until", "")
                except Exception:
                    pass

        # discord_id별로 포스팅 집계 (멀티 플랫폼 합산)
        stats_by_discord = {}
        for member in members:
            did = member["discord_id"]
            posts = await db.get_posts_in_range(member["id"], week_start, week_end)
            if did not in stats_by_discord:
                stats_by_discord[did] = {
                    "discord_id": did,
                    "discord_name": member["discord_name"],
                    "member_ids": [],
                    "posts": [],
                    "post_count": 0,
                }
            stats_by_discord[did]["member_ids"].append(member["id"])
            stats_by_discord[did]["posts"].extend(posts)
            stats_by_discord[did]["post_count"] += len(posts)

        member_stats = list(stats_by_discord.values())

        # 벌금은 discord_id별로 한 번만 부과
        for stat in member_stats:
            if stat["post_count"] == 0 and not is_paused:
                first_member_id = stat["member_ids"][0]
                already_exists = await db.is_penalty_exists(first_member_id, week_start)
                if not already_exists:
                    await db.add_penalty(first_member_id, week_start, week_end, penalty_amount)
                    logger.info("벌금 부과: %s (%s원)", stat["discord_name"], penalty_amount)

        best = await db.get_top_scored_post(guild_id, week_start, week_end)
        embed = weekly_report_embed(week_start, week_end, member_stats, penalty_amount, is_paused, best_post=best)
        await channel.send(embed=embed)
        logger.info("주간 리포트 발송 완료 [Guild: %s]", guild_id)

    @main_scheduler.before_loop
    async def before_main_scheduler(self):
        await self.bot.wait_until_ready()

    async def _send_remind(self, guild_id: str, r_day: int, r_hour: int, r_min: int):
        channel = await self._resolve_channel(guild_id)
        if not channel:
            return

        week_start, week_end = get_week_range(reset_weekday=r_day, reset_hour=r_hour, reset_minute=r_min)
        members = await db.get_all_members(guild_id)

        # discord_id별로 합산 후 0편인 사람만 추출
        counts_by_discord = {}
        for member in members:
            did = member["discord_id"]
            count = await db.get_post_count_in_range(member["id"], week_start, week_end)
            if did not in counts_by_discord:
                counts_by_discord[did] = {"member": member, "count": 0}
            counts_by_discord[did]["count"] += count

        members_without_posts = [v["member"] for v in counts_by_discord.values() if v["count"] == 0]

        if not members_without_posts:
            return

        embed = remind_embed(members_without_posts)
        await channel.send(embed=embed)
        logger.info("리마인드 발송 완료 [Guild: %s] (대상: %d명)", guild_id, len(members_without_posts))

    @tasks.loop(time=SCAN_TIME)
    async def daily_sitemap_scan(self):
        """매일 자정에 사이트맵을 스캔하여 누락된 포스팅을 DB에 추가하고 알림"""
        logger.debug("일일 누락 방지 사이트맵 스캔 태스크 작동 확인됨")
        guild_ids = await db.get_all_guild_ids()

        async with aiohttp.ClientSession() as session:
            for guild_id in guild_ids:
                try:
                    await self._scan_sitemap_for_guild(session, guild_id)
                except Exception as e:
                    logger.error("사이트맵 스캔 실패 [Guild: %s]: %s", guild_id, e)

    async def _scan_sitemap_for_guild(self, session: aiohttp.ClientSession, guild_id: str):
        channel = await self._resolve_channel(guild_id)
        if not channel:
            return

        members = await db.get_all_members(guild_id)
        for member in members:
            blog_url = member["blog_url"].rstrip("/")
            sitemap_url = f"{blog_url}/sitemap.xml"

            try:
                logger.debug("사이트맵 스캔 중: [%s] %s", member["discord_name"], sitemap_url)
                async with session.get(sitemap_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        continue
                    xml_data = await resp.text()
                    root = ET.fromstring(xml_data)
                    
                    NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
                    url_elems = [
                        elem for elem in root.findall(f"{NS}url")
                        if (loc := elem.findtext(f"{NS}loc"))
                        and not any(p in loc for p in _IGNORE_PATTERNS)
                        and "/m/" not in loc
                        and loc != blog_url
                    ]

                    for url_elem in url_elems:
                        link = url_elem.findtext(f"{NS}loc")
                        if await db.is_post_exists(link, member["id"]):
                            logger.debug("이미 저장된 글 건너뜀: [%s] %s", member["discord_name"], link)
                            continue

                        # 페이지에서 제목과 실제 작성일을 함께 추출
                        title = "누락된 블로그 포스팅"
                        published_str = None
                        try:
                            async with session.get(link, timeout=aiohttp.ClientTimeout(total=5)) as post_resp:
                                if post_resp.status == 200:
                                    html = await post_resp.text()
                                    title_match = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html, re.IGNORECASE)
                                    if title_match:
                                        title = title_match.group(1).replace('&#39;', "'").replace('&quot;', '"')
                                    published_str = parse_published_at_from_html(html)
                        except Exception:
                            pass

                        if not published_str:
                            # 페이지 fetch 실패 시 lastmod로 폴백 (get_kst_now() 대신)
                            lastmod = url_elem.findtext(f"{NS}lastmod")
                            try:
                                published_str = datetime.fromisoformat(lastmod).strftime("%Y-%m-%d %H:%M:%S") if lastmod else get_kst_now().strftime("%Y-%m-%d %H:%M:%S")
                            except Exception:
                                published_str = get_kst_now().strftime("%Y-%m-%d %H:%M:%S")
                        
                        # is_initial=False 로 저장해야 이번 주 통계에 카운트됨
                        saved = await db.add_post(
                            member_id=member["id"],
                            title=title,
                            link=link,
                            published_at=published_str,
                            is_initial=False
                        )

                        if saved:
                            mention = f"<@{member['discord_id']}>"
                            embed = missed_post_embed(
                                author_name=mention,
                                title=title,
                                link=link,
                                published_at=published_str
                            )
                            await channel.send(embed=embed)
                            logger.info("누락 글 감지 및 추가: [%s] %s", member["discord_name"], title)
            except Exception as e:
                logger.error("사이트맵 파싱 오류 [%s]: %s", member["discord_name"], e)

    @daily_sitemap_scan.before_loop
    async def before_daily_sitemap_scan(self):
        await self.bot.wait_until_ready()

    async def _send_monthly_report(self, guild_id: str, now):
        channel = await self._resolve_channel(guild_id)
        if not channel:
            return

        month_start, month_end = get_last_month_range()
        # 지난 달 (1일 기준 → 전달)
        prev_month = now.month - 1 if now.month > 1 else 12
        prev_year = now.year if now.month > 1 else now.year - 1

        members = await db.get_all_members(guild_id)
        stats_by_discord = {}
        for member in members:
            did = member["discord_id"]
            count = await db.get_post_count_in_range(member["id"], month_start, month_end)
            if did not in stats_by_discord:
                stats_by_discord[did] = {
                    "discord_id": did,
                    "discord_name": member["discord_name"],
                    "post_count": 0,
                }
            stats_by_discord[did]["post_count"] += count
        member_stats = list(stats_by_discord.values())

        best = await db.get_top_scored_post(guild_id, month_start, month_end)
        embed = monthly_report_embed(prev_year, prev_month, member_stats, best_post=best)
        await channel.send(embed=embed)
        logger.info("월간 리포트 발송 완료 [Guild: %s] (%d년 %d월)", guild_id, prev_year, prev_month)


async def setup(bot: commands.Bot):
    await bot.add_cog(Scheduler(bot))
