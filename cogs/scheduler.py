import logging
import discord
from discord.ext import commands, tasks
from datetime import datetime, time
import aiohttp
import xml.etree.ElementTree as ET
import re

import re

import database as db
from utils.time_utils import get_week_range, get_last_week_range, get_kst_now, KST
from utils.embed_builder import weekly_report_embed, remind_embed, missed_post_embed

logger = logging.getLogger(__name__)

REPORT_TIME = time(hour=9, minute=0, tzinfo=KST)
REMIND_TIME = time(hour=10, minute=0, tzinfo=KST)
SCAN_TIME = time(hour=0, minute=0, tzinfo=KST) # 매일 자정 스캔


class Scheduler(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.weekly_report_task.is_running():
            self.weekly_report_task.start()
            logger.info("주간 리포트 스케줄러 시작 (매주 월요일 %s)", REPORT_TIME)

        if not self.remind_task.is_running():
            self.remind_task.start()
            logger.info("리마인드 스케줄러 시작 (매주 일요일 %s)", REMIND_TIME)

        if not self.daily_sitemap_scan.is_running():
            self.daily_sitemap_scan.start()
            logger.info("일일 누락 방지 스캔 스케줄러 시작 (매일 %s)", SCAN_TIME)

    def cog_unload(self):
        self.weekly_report_task.cancel()
        self.remind_task.cancel()
        self.daily_sitemap_scan.cancel()

    @tasks.loop(time=REPORT_TIME)
    async def weekly_report_task(self):
        """매주 월요일 09:00에 모든 서버에 주간 리포트 발송 + 벌금 자동 부과"""
        if get_kst_now().weekday() != 0:
            logger.debug("오늘은 월요일이 아니므로 주간 리포트 및 벌금 정산을 건너뜁니다.")
            return

        logger.info("주간 리포트 생성 시작")
        guild_ids = await db.get_all_guild_ids()

        for guild_id in guild_ids:
            try:
                await self._send_weekly_report(guild_id)
            except Exception as e:
                logger.error("주간 리포트 실패 [Guild: %s]: %s", guild_id, e)

    async def _send_weekly_report(self, guild_id: str):
        channel_id = await db.get_setting("notification_channel_id", guild_id=guild_id)
        if not channel_id:
            return

        channel = self.bot.get_channel(int(channel_id))
        if not channel:
            return

        week_start, week_end = get_last_week_range()
        members = await db.get_all_members(guild_id)

        penalty_amount_str = await db.get_setting("penalty_amount", default="5000", guild_id=guild_id)
        penalty_amount = int(penalty_amount_str)

        member_stats = []
        for member in members:
            posts = await db.get_posts_in_range(member["id"], week_start, week_end)
            count = len(posts)
            member_stats.append({
                "discord_id": member["discord_id"],
                "discord_name": member["discord_name"],
                "member_id": member["id"],
                "post_count": count,
                "posts": posts
            })

            if count == 0:
                already_exists = await db.is_penalty_exists(member["id"], week_start)
                if not already_exists:
                    await db.add_penalty(member["id"], week_start, week_end, penalty_amount)
                    logger.info("벌금 부과: %s (%s원)", member["discord_name"], penalty_amount)

        embed = weekly_report_embed(week_start, week_end, member_stats, penalty_amount)
        await channel.send(embed=embed)
        logger.info("주간 리포트 발송 완료 [Guild: %s]", guild_id)

    @weekly_report_task.before_loop
    async def before_weekly_report(self):
        await self.bot.wait_until_ready()

    @tasks.loop(time=REMIND_TIME)
    async def remind_task(self):
        """매주 일요일 10:00에 모든 서버에 미작성자 리마인드"""
        if get_kst_now().weekday() != 6:
            logger.debug("오늘은 일요일이 아니므로 마감 리마인드를 건너뜁니다.")
            return

        logger.info("마감 리마인드 시작")
        guild_ids = await db.get_all_guild_ids()

        for guild_id in guild_ids:
            try:
                await self._send_remind(guild_id)
            except Exception as e:
                logger.error("리마인드 실패 [Guild: %s]: %s", guild_id, e)

    async def _send_remind(self, guild_id: str):
        channel_id = await db.get_setting("notification_channel_id", guild_id=guild_id)
        if not channel_id:
            return

        channel = self.bot.get_channel(int(channel_id))
        if not channel:
            return

        week_start, week_end = get_week_range()
        members = await db.get_all_members(guild_id)

        members_without_posts = []
        for member in members:
            count = await db.get_post_count_in_range(member["id"], week_start, week_end)
            if count == 0:
                members_without_posts.append(member)

        if not members_without_posts:
            return

        mentions = " ".join([f"<@{m['discord_id']}>" for m in members_without_posts])
        embed = remind_embed(members_without_posts)
        await channel.send(content=f"{mentions}", embed=embed)
        logger.info("리마인드 발송 완료 [Guild: %s] (대상: %d명)", guild_id, len(members_without_posts))

    @remind_task.before_loop
    async def before_remind(self):
        await self.bot.wait_until_ready()

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
        channel_id = await db.get_setting("notification_channel_id", guild_id=guild_id)
        if not channel_id:
            return

        channel = self.bot.get_channel(int(channel_id))
        if not channel:
            return

        members = await db.get_all_members(guild_id)
        for member in members:
            blog_url = member["blog_url"].rstrip("/")
            sitemap_url = f"{blog_url}/sitemap.xml"

            try:
                async with session.get(sitemap_url, timeout=10) as resp:
                    if resp.status != 200:
                        continue
                    xml_data = await resp.text()
                    root = ET.fromstring(xml_data)
                    
                    urls = [elem.text for elem in root.findall('.//{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
                    ignore_suffixes = ('/category', '/tag', '/guestbook', '/manage')
                    post_urls = [u for u in urls if u and not u.endswith(ignore_suffixes) and u != blog_url]
                    
                    for link in post_urls:
                        # 이미 DB에 있는 글이면 패스
                        if await db.is_post_exists(link):
                            continue
                        
                        # 누락된 글을 찾았으므로 페이지에서 메타 태그로 제목 추출
                        title = "누락된 블로그 포스팅"
                        try:
                            async with session.get(link, timeout=5) as post_resp:
                                if post_resp.status == 200:
                                    html = await post_resp.text()
                                    match = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html, re.IGNORECASE)
                                    if match:
                                        title = match.group(1).replace('&#39;', "'").replace('&quot;', '"')
                        except Exception:
                            pass # 제목을 못 가져와도 진행

                        published_str = get_kst_now().strftime("%Y-%m-%d")
                        
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
                            await channel.send(content=f"{mention}님! 누락될 뻔한 글을 주워왔어요!", embed=embed)
                            logger.info("누락 글 감지 및 추가: [%s] %s", member["discord_name"], title)
            except Exception as e:
                logger.error("사이트맵 파싱 오류 [%s]: %s", member["discord_name"], e)

    @daily_sitemap_scan.before_loop
    async def before_daily_sitemap_scan(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Scheduler(bot))
