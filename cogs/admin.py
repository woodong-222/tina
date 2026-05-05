import logging
import re
import feedparser
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
import aiohttp
import xml.etree.ElementTree as ET
from utils.time_utils import get_kst_now

import database as db
from utils.embed_builder import (
    status_embed, help_embed, member_list_embed, 
    info_embed, error_embed, register_success_embed,
    already_registered_embed, not_registered_embed,
    invalid_tistory_url_embed, connection_error_embed,
    COLOR_ADMIN
)

logger = logging.getLogger(__name__)


def is_admin():
    """디스코드 서버 관리자 권한 체크 데코레이터"""
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "이 명령어는 **서버 관리자** 권한이 필요해요.",
                ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="멤버신규등록", description="[관리자] 새 멤버를 등록합니다")
    @app_commands.describe(
        유저="등록할 디스코드 유저",
        블로그="티스토리 블로그 주소 (예: https://example.tistory.com)"
    )
    @is_admin()
    async def register_member(self, interaction: discord.Interaction, 유저: discord.Member, 블로그: str):
        await interaction.response.defer()

        # 1. 정규표현식으로 티스토리 아이디 추출
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
            await interaction.followup.send(embed=already_registered_embed(유저.display_name))
            return

        rss_url = 블로그.rstrip("/") + "/rss"
        sitemap_url = 블로그.rstrip("/") + "/sitemap.xml"
        existing_count = 0
        added_links = set()

        try:
            member = await db.get_member_by_discord_id(guild_id, str(유저.id))
            if not member:
                return

            # 1. RSS 파싱 (최근 글 위주, 제목/날짜 정확함)
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

            # 2. 사이트맵 파싱 (과거 글 전체 스캔)
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get(sitemap_url, timeout=10) as resp:
                        if resp.status == 200:
                            xml_data = await resp.text()
                            root = ET.fromstring(xml_data)
                            
                            urls = [elem.text for elem in root.findall('.//{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
                            # 카테고리, 태그, 방명록, 블로그 홈, 모바일 버전(/m/) 등 제외
                            ignore_suffixes = ('/category', '/tag', '/guestbook', '/manage')
                            post_urls = [u for u in urls if u and not u.endswith(ignore_suffixes) and "/m/" not in u and u != 블로그.rstrip("/")]
                            
                            for link in post_urls:
                                if link in added_links:
                                    continue
                                
                                # 사이트맵에는 제목/날짜 정보가 없으므로 더미 데이터 삽입 (is_initial=1 이라 표시되지 않음)
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

        embed = register_success_embed(유저.mention, 블로그, existing_count, is_admin=True)
        await interaction.followup.send(embed=embed)
        logger.info("멤버 등록: %s (기존 글 %d편)", 유저.display_name, existing_count)

    @app_commands.command(name="멤버삭제", description="[관리자] 멤버를 삭제합니다")
    @app_commands.describe(유저="삭제할 디스코드 유저")
    @is_admin()
    async def remove_member(self, interaction: discord.Interaction, 유저: discord.Member):
        await interaction.response.defer()
        guild_id = str(interaction.guild_id)
        success = await db.remove_member(guild_id, str(유저.id))

        if success:
            embed = info_embed("멤버 삭제 완료", f"**{유저.display_name}**님이 목록에서 삭제되었어요.", color=COLOR_ADMIN)
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(embed=not_registered_embed(유저.display_name), ephemeral=True)


    @app_commands.command(name="채널설정", description="[관리자] 알림 채널을 설정합니다")
    @app_commands.describe(채널="알림을 보낼 채널")
    @is_admin()
    async def set_channel(self, interaction: discord.Interaction, 채널: discord.TextChannel):
        guild_id = str(interaction.guild_id)
        await db.set_setting(guild_id, "notification_channel_id", str(채널.id))
        embed = info_embed("설정 완료", f"알림 채널을 {채널.mention}으로 설정했어요! 이제 이곳에서 소식을 전해드릴게요.", color=COLOR_ADMIN)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="벌금설정", description="[관리자] 벌금 금액을 변경합니다")
    @app_commands.describe(금액="벌금 금액 (원)")
    @is_admin()
    async def set_penalty(self, interaction: discord.Interaction, 금액: int):
        if 금액 < 0:
            await interaction.response.send_message("벌금 금액은 0 이상이어야 해요.", ephemeral=True)
            return

        guild_id = str(interaction.guild_id)
        await db.set_setting(guild_id, "penalty_amount", str(금액))
        embed = info_embed("설정 완료", f"벌금 금액을 **{금액:,}원**으로 변경했어요! 다들 긴장해야겠는걸요?", color=COLOR_ADMIN)
        await interaction.response.send_message(embed=embed)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            pass
        else:
            await interaction.response.send_message(f"오류가 발생했어요: {error}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
