import discord
from discord import app_commands
from discord.ext import commands

import aiohttp
import feedparser
import xml.etree.ElementTree as ET
import logging
import re
from datetime import datetime

import database as db
from utils.time_utils import get_week_range, get_month_range, format_date_range, get_kst_now
from utils.embed_builder import (
    stats_embed, status_embed, help_embed, penalty_embed, 
    server_stats_embed, server_penalty_embed, member_list_embed, 
    refresh_embed, info_embed, error_embed,
    register_success_embed, unregister_success_embed, 
    already_registered_embed, not_registered_embed, 
    invalid_tistory_url_embed, no_members_embed, 
    system_error_embed, connection_error_embed
)

logger = logging.getLogger(__name__)


class Commands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="통계", description="이번 주/이번 달 포스팅 통계를 확인합니다")
    @app_commands.describe(유저="통계를 확인할 유저 (미입력 시 전체)")
    async def stats(self, interaction: discord.Interaction, 유저: discord.Member = None):
        await interaction.response.defer()
        guild_id = str(interaction.guild_id)
        r_day, r_hour, r_min = await db.get_reset_time(guild_id)
        week_start, week_end = get_week_range(reset_weekday=r_day, reset_hour=r_hour, reset_minute=r_min)
        month_start, month_end = get_month_range()

        if 유저:
            member = await db.get_member_by_discord_id(guild_id, str(유저.id))
            if not member:
                await interaction.followup.send(embed=not_registered_embed(유저.display_name), ephemeral=True)
                return

            week_count = await db.get_post_count_in_range(member["id"], week_start, week_end)
            month_count = await db.get_post_count_in_range(member["id"], month_start, month_end)
            total_penalty = await db.get_total_penalty(member["id"])

            embed = stats_embed(
                target_name=유저.display_name,
                week_count=week_count,
                month_count=month_count,
                week_range=format_date_range(week_start, week_end),
                month_range=format_date_range(month_start, month_end),
                total_penalty=total_penalty
            )
            await interaction.followup.send(embed=embed)
        else:
            members = await db.get_all_members(guild_id)
            if not members:
                await interaction.followup.send(embed=no_members_embed(), ephemeral=True)
                return

            member_stats = []
            for member in members:
                week_count = await db.get_post_count_in_range(member["id"], week_start, week_end)
                month_count = await db.get_post_count_in_range(member["id"], month_start, month_end)
                total_penalty = await db.get_total_penalty(member["id"])
                member_stats.append({
                    "discord_id": member["discord_id"],
                    "discord_name": member["discord_name"],
                    "week_count": week_count,
                    "month_count": month_count,
                    "total_penalty": total_penalty
                })

            embed = server_stats_embed(
                member_stats,
                format_date_range(week_start, week_end),
                format_date_range(month_start, month_end)
            )
            await interaction.followup.send(embed=embed)

    @app_commands.command(name="현황", description="이번 주 작성 현황을 확인합니다")
    @app_commands.describe(유저="확인할 유저 (미입력 시 전체)")
    async def status(self, interaction: discord.Interaction, 유저: discord.Member = None):
        await interaction.response.defer()
        guild_id = str(interaction.guild_id)
        r_day, r_hour, r_min = await db.get_reset_time(guild_id)
        week_start, week_end = get_week_range(reset_weekday=r_day, reset_hour=r_hour, reset_minute=r_min)
        members = await db.get_all_members(guild_id)

        if not members:
            await interaction.followup.send(embed=no_members_embed(is_status=True), ephemeral=True)
            return

        if 유저:
            members = [m for m in members if m["discord_id"] == str(유저.id)]
            if not members:
                await interaction.followup.send(embed=not_registered_embed(유저.display_name), ephemeral=True)
                return

        member_stats = []
        for member in members:
            count = await db.get_post_count_in_range(member["id"], week_start, week_end)
            member_stats.append({
                "discord_id": member["discord_id"],
                "discord_name": member["discord_name"],
                "post_count": count
            })

        embed = status_embed(week_start, week_end, member_stats)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="벌금", description="벌금 현황을 조회합니다")
    @app_commands.describe(유저="조회할 유저 (미입력 시 전체)")
    async def penalty(self, interaction: discord.Interaction, 유저: discord.Member = None):
        await interaction.response.defer()
        guild_id = str(interaction.guild_id)

        if 유저:
            member = await db.get_member_by_discord_id(guild_id, str(유저.id))
            if not member:
                await interaction.followup.send(embed=not_registered_embed(유저.display_name), ephemeral=True)
                return

            penalties = await db.get_penalties_for_member(member["id"])
            total = await db.get_total_penalty(member["id"])

            embed = penalty_embed(유저.display_name, penalties, total)
            await interaction.followup.send(embed=embed)
        else:
            members = await db.get_all_members(guild_id)
            if not members:
                await interaction.followup.send(embed=no_members_embed(), ephemeral=True)
                return

            penalties_by_member = []
            total_guild_penalty = 0
            for member in members:
                total_p = await db.get_total_penalty(member["id"])
                if total_p > 0:
                    penalties_by_member.append({
                        "discord_id": member["discord_id"],
                        "discord_name": member["discord_name"],
                        "total_penalty": total_p
                    })
                    total_guild_penalty += total_p

            embed = server_penalty_embed(penalties_by_member, total_guild_penalty)
            await interaction.followup.send(embed=embed)

    @app_commands.command(name="새로고침", description="블로그 최신 글을 즉시 확인합니다")
    @app_commands.describe(유저="확인할 유저 (미입력 시 전체)")
    async def refresh(self, interaction: discord.Interaction, 유저: discord.Member = None):
        await interaction.response.defer()

        rss_cog = self.bot.get_cog("RSSMonitor")
        if not rss_cog:
            await interaction.followup.send(embed=system_error_embed())
            return

        guild_id = str(interaction.guild_id)
        target_discord_id = str(유저.id) if 유저 else None
        new_count = await rss_cog.manual_poll(guild_id, target_discord_id=target_discord_id)
        
        target_str = f"**{유저.display_name}**님의 " if 유저 else "모든 멤버의 "
        embed = refresh_embed(target_str, new_count)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="도움말", description="티나 사용법을 안내합니다")
    async def help_command(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        r_day, r_hour, r_min = await db.get_reset_time(guild_id)
        
        days = ["월", "화", "수", "목", "금", "토", "일"]
        reset_day_str = f"{days[r_day]}요일"
        reset_time_str = f"{r_hour:02d}:{r_min:02d}"
        
        embed = help_embed(reset_day_str, reset_time_str)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="신규등록", description="내 블로그를 봇에 등록합니다")
    @app_commands.describe(블로그="티스토리 블로그 주소 (예: https://example.tistory.com)")
    async def register_self(self, interaction: discord.Interaction, 블로그: str):
        유저 = interaction.user
        await interaction.response.defer()

        # 1. 정규표현식으로 티스토리 아이디 추출 ([아이디].tistory.com)
        # www.woododo.tistory.com 혹은 woododo.tistory.com) 등 다양한 입력 대응
        match = re.search(r'(?:www\.)?([a-z0-9-]+)\.tistory\.com', 블로그.lower())
        
        if not match or match.group(1) == "www":
            await interaction.followup.send(embed=invalid_tistory_url_embed())
            return

        blog_id = match.group(1)
        블로그 = f"https://{blog_id}.tistory.com"

        # 2. URL 접속 가능 여부 확인
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(블로그, timeout=10) as resp:
                    if resp.status != 200:
                        await interaction.followup.send(embed=connection_error_embed(블로그, resp.status))
                        return
            except Exception as e:
                logger.error(f"URL 검증 실패: {블로그} - {e}")
                await interaction.followup.send(embed=connection_error_embed(블로그))
                return

        guild_id = str(interaction.guild_id)
        success = await db.add_member(
            guild_id=guild_id,
            discord_id=str(유저.id),
            discord_name=유저.display_name,
            blog_url=블로그
        )

        if not success:
            await interaction.followup.send(embed=already_registered_embed())
            return

        rss_url = 블로그.rstrip("/") + "/rss"
        sitemap_url = 블로그.rstrip("/") + "/sitemap.xml"
        existing_count = 0
        added_links = set()

        try:
            member = await db.get_member_by_discord_id(guild_id, str(유저.id))
            if not member:
                return

            feed = feedparser.parse(rss_url)
            if feed.entries:
                for entry in feed.entries:
                    link = entry.get("link", "").strip()
                    if not link or link in added_links:
                        continue

                    title = entry.get("title", "제목 없음").strip()
                    try:
                        published_dt = datetime(*entry.published_parsed[:6]) if entry.get("published_parsed") else get_kst_now()
                        published_str = published_dt.strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        published_str = get_kst_now().strftime("%Y-%m-%d %H:%M:%S")

                    await db.add_post(
                        member_id=member["id"],
                        title=title,
                        link=link,
                        published_at=published_str,
                        is_initial=True
                    )
                    added_links.add(link)
                    existing_count += 1

            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get(sitemap_url, timeout=10) as resp:
                        if resp.status == 200:
                            xml_data = await resp.text()
                            root = ET.fromstring(xml_data)
                            
                            urls = [elem.text for elem in root.findall('.//{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
                            ignore_patterns = ('/category', '/tag', '/guestbook', '/manage')
                            post_urls = [
                                u for u in urls 
                                if u and not any(p in u for p in ignore_patterns) 
                                and "/m/" not in u 
                                and u != 블로그.rstrip("/")
                            ]
                            
                            for link in post_urls:
                                if link in added_links:
                                    continue
                                
                                await db.add_post(
                                    member_id=member["id"],
                                    title="이전 글",
                                    link=link,
                                    published_at=get_kst_now().strftime("%Y-%m-%d %H:%M:%S"),
                                    is_initial=True
                                )
                                added_links.add(link)
                                existing_count += 1
                except Exception as e:
                    logger.error("사이트맵 스캔 실패 [%s]: %s", 유저.display_name, e)

        except Exception as e:
            logger.error("기존 글 스캔 실패 [%s]: %s", 유저.display_name, e)

        embed = register_success_embed(유저.mention, 블로그, existing_count)
        await interaction.followup.send(embed=embed)
        logger.info("본인 등록: %s (기존 글 %d편)", 유저.display_name, existing_count)

    @app_commands.command(name="삭제", description="내 블로그 등록을 해제합니다")
    async def unregister_self(self, interaction: discord.Interaction):
        유저 = interaction.user
        await interaction.response.defer()
        guild_id = str(interaction.guild_id)
        success = await db.remove_member(guild_id, str(유저.id))

        if success:
            await interaction.followup.send(embed=unregister_success_embed(유저.display_name))
        else:
            await interaction.followup.send(embed=not_registered_embed(), ephemeral=True)

    @app_commands.command(name="멤버목록", description="등록된 멤버 목록을 조회합니다")
    async def list_members(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild_id = str(interaction.guild_id)
        members = await db.get_all_members(guild_id)
        embed = member_list_embed(members)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="초기화설정", description="[관리자] 주간 통계 및 벌금 초기화 요일/시간을 설정합니다")
    @app_commands.describe(요일="초기화 요일 (예: 월요일, 수)", 시간="초기화 시간 (예: 09:00, 15:30)")
    @app_commands.default_permissions(administrator=True)
    async def set_reset_time(self, interaction: discord.Interaction, 요일: str, 시간: str):
        await interaction.response.defer()
        guild_id = str(interaction.guild_id)
        
        # 1. 요일 파싱
        day_map = {
            "월": 0, "월요일": 0, "화": 1, "화요일": 1, "수": 2, "수요일": 2,
            "목": 3, "목요일": 3, "금": 4, "금요일": 4, "토": 5, "토요일": 5,
            "일": 6, "일요일": 6
        }
        
        parsed_day = day_map.get(요일.strip())
        if parsed_day is None:
            await interaction.followup.send(embed=error_embed("올바른 요일을 입력해주세요! (예: 월요일, 수)"))
            return
            
        # 2. 시간 파싱
        try:
            hour_str, min_str = 시간.split(":")
            hour = int(hour_str)
            minute = int(min_str)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except ValueError:
            await interaction.followup.send(embed=error_embed("올바른 시간 형식을 입력해주세요! (예: 09:00, 15:30)"))
            return
            
        # 3. DB 저장
        await db.set_setting(guild_id, "reset_weekday", str(parsed_day))
        await db.set_setting(guild_id, "reset_time", f"{hour:02d}:{minute:02d}")
        
        days = ["월", "화", "수", "목", "금", "토", "일"]
        
        embed = info_embed(
            "초기화 시간 설정 완료",
            f"이 서버의 주간 초기화 및 벌금 정산 시간이 **{days[parsed_day]}요일 {hour:02d}:{minute:02d}** (으)로 변경되었어요!\n"
            f"마감 리마인드는 정확히 24시간 전인 **{days[(parsed_day-1)%7]}요일 {hour:02d}:{minute:02d}** 에 발송됩니다.",
            color=0xF1C40F
        )
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Commands(bot))
