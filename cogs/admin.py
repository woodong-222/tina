import logging
import re
import discord
from datetime import datetime
from discord import app_commands
from discord.ext import commands

import database as db
from utils.blog_utils import normalize_tistory_url, check_url_accessible, scan_and_save_existing_posts
from utils.time_utils import get_week_range, get_month_range, get_kst_now, KST
from utils.embed_builder import (
    status_embed, help_embed, member_list_embed,
    info_embed, error_embed, register_success_embed,
    already_registered_embed, not_registered_embed,
    invalid_tistory_url_embed, connection_error_embed,
    admin_only_embed, command_error_embed,
    penalty_change_embed,
    COLOR_ADMIN
)

logger = logging.getLogger(__name__)


def _parse_pause_until(date_str: str) -> datetime | None:
    year = get_kst_now().year
    s = date_str.strip()

    m = re.match(r'(\d{1,2})월\s*(\d{1,2})일(?:\s+(\d{1,2}):(\d{2}))?', s)
    if m:
        try:
            return datetime(year, int(m.group(1)), int(m.group(2)),
                            int(m.group(3) or 0), int(m.group(4) or 0), tzinfo=KST)
        except ValueError:
            return None

    m = re.match(r'(\d{1,2})/(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?', s)
    if m:
        try:
            return datetime(year, int(m.group(1)), int(m.group(2)),
                            int(m.group(3) or 0), int(m.group(4) or 0), tzinfo=KST)
        except ValueError:
            return None

    m = re.match(r'(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{1,2}):(\d{2}))?', s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            int(m.group(4) or 0), int(m.group(5) or 0), tzinfo=KST)
        except ValueError:
            return None

    return None


def is_admin():
    """디스코드 서버 관리자 권한 체크 데코레이터"""
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                embed=admin_only_embed(),
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

        blog_url = normalize_tistory_url(블로그)
        if not blog_url:
            await interaction.followup.send(embed=invalid_tistory_url_embed())
            return

        ok, status_code = await check_url_accessible(blog_url)
        if not ok:
            await interaction.followup.send(embed=connection_error_embed(blog_url, status_code))
            return

        guild_id = str(interaction.guild_id)
        success = await db.add_member(guild_id, str(유저.id), 유저.display_name, blog_url)
        if not success:
            await interaction.followup.send(embed=already_registered_embed(유저.display_name))
            return

        member = await db.get_member_by_discord_id(guild_id, str(유저.id))
        existing_count = 0
        week_count = 0
        month_count = 0
        if member:
            try:
                existing_count = await scan_and_save_existing_posts(member, blog_url)
                r_day, r_hour, r_min = await db.get_reset_time(guild_id)
                week_start, week_end = get_week_range(reset_weekday=r_day, reset_hour=r_hour, reset_minute=r_min)
                month_start, month_end = get_month_range()
                week_count = await db.get_post_count_in_range(member["id"], week_start, week_end)
                month_count = await db.get_post_count_in_range(member["id"], month_start, month_end)
            except Exception as e:
                logger.error("기존 글 스캔 실패 [%s]: %s", 유저.display_name, e)

        await interaction.followup.send(embed=register_success_embed(유저.mention, blog_url, existing_count, week_count, month_count, is_admin=True))
        logger.info("멤버 등록: %s (기존 글 %d편, 이번주 %d편, 이번달 %d편)", 유저.display_name, existing_count, week_count, month_count)

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
            logger.info("멤버 삭제: %s (Guild: %s)", 유저.display_name, guild_id)
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
        logger.info("알림 채널 설정: #%s (Guild: %s)", 채널.name, guild_id)

    @app_commands.command(name="벌금설정", description="[관리자] 벌금 금액을 변경합니다")
    @app_commands.describe(금액="벌금 금액 (원)")
    @is_admin()
    async def set_penalty(self, interaction: discord.Interaction, 금액: int):
        if 금액 < 0:
            await interaction.response.send_message(
                embed=error_embed("벌금 금액은 0 이상이어야 해요."), 
                ephemeral=True
            )
            return

        guild_id = str(interaction.guild_id)
        await db.set_setting(guild_id, "penalty_amount", str(금액))
        embed = info_embed("설정 완료", f"벌금 금액을 **{금액:,}원**으로 변경했어요! 다들 긴장해야겠는걸요?", color=COLOR_ADMIN)
        await interaction.response.send_message(embed=embed)
        logger.info("벌금 금액 설정: %d원 (Guild: %s)", 금액, guild_id)

    @app_commands.command(name="벌금정지", description="[관리자] 벌금 부과를 일시정지합니다")
    @app_commands.describe(날짜시간="정지 해제 일시 (예: 5월 11일 09:00 / 5/11 09:00 / 2026-05-11 09:00). 미입력 시 수동 재개까지 정지")
    @is_admin()
    async def pause_penalty(self, interaction: discord.Interaction, 날짜시간: str = None):
        guild_id = str(interaction.guild_id)

        if 날짜시간 is None:
            await db.set_setting(guild_id, "penalty_paused", "1")
            await db.set_setting(guild_id, "penalty_paused_until", "")
            embed = info_embed("벌금 정지", "벌금 부과가 일시정지되었어요. `/벌금재개`로 해제할 수 있어요.", color=COLOR_ADMIN)
            await interaction.response.send_message(embed=embed)
            logger.info("벌금 무기한 정지 (Guild: %s)", guild_id)
        else:
            dt = _parse_pause_until(날짜시간)
            if dt is None:
                await interaction.response.send_message(
                    embed=error_embed("날짜 형식이 올바르지 않아요.\n예시: `5월 11일 09:00` / `5/11 09:00` / `2026-05-11 09:00`"),
                    ephemeral=True
                )
                return

            paused_until_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            await db.set_setting(guild_id, "penalty_paused", "0")
            await db.set_setting(guild_id, "penalty_paused_until", paused_until_str)

            display = dt.strftime("%Y년 %m월 %d일 %H:%M")
            embed = info_embed(
                "벌금 일시정지",
                f"**{display}**까지 벌금 부과가 일시정지되었어요.\n해당 시점 이후 주간 리포트부터 자동으로 재개됩니다.",
                color=COLOR_ADMIN
            )
            await interaction.response.send_message(embed=embed)
            logger.info("벌금 기간 정지: %s까지 (Guild: %s)", paused_until_str, guild_id)

    @app_commands.command(name="벌금재개", description="[관리자] 일시정지된 벌금 부과를 재개합니다")
    @is_admin()
    async def resume_penalty(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        await db.set_setting(guild_id, "penalty_paused", "0")
        await db.set_setting(guild_id, "penalty_paused_until", "")

        embed = info_embed("벌금 재개", "벌금 부과가 재개되었어요. 다들 이번 주도 파이팅!", color=COLOR_ADMIN)
        await interaction.response.send_message(embed=embed)
        logger.info("벌금 재개 (Guild: %s)", guild_id)

    @app_commands.command(name="벌금변경", description="[관리자] 멤버 벌금을 수동으로 조정합니다")
    @app_commands.describe(
        유저="대상 유저",
        금액="조정할 금액 (양수: 추가, 음수: 차감)"
    )
    @is_admin()
    async def adjust_penalty(self, interaction: discord.Interaction, 유저: discord.Member, 금액: int):
        await interaction.response.defer()
        guild_id = str(interaction.guild_id)

        member = await db.get_member_by_discord_id(guild_id, str(유저.id))
        if not member:
            await interaction.followup.send(embed=not_registered_embed(유저.display_name), ephemeral=True)
            return

        if 금액 == 0:
            await interaction.followup.send(embed=error_embed("0원은 조정할 수 없어요."), ephemeral=True)
            return

        current_total = await db.get_total_penalty(member["id"])
        if 금액 < 0 and current_total + 금액 < 0:
            await interaction.followup.send(
                embed=error_embed(f"차감 후 총 벌금이 음수가 됩니다. (현재: {current_total:,}원, 차감: {abs(금액):,}원)"),
                ephemeral=True
            )
            return

        r_day, r_hour, r_min = await db.get_reset_time(guild_id)
        week_start, week_end = get_week_range(reset_weekday=r_day, reset_hour=r_hour, reset_minute=r_min)
        await db.add_penalty(member["id"], week_start, week_end, 금액)

        new_total = await db.get_total_penalty(member["id"])
        await interaction.followup.send(embed=penalty_change_embed(유저.mention, 금액, new_total))
        logger.info("벌금 수동 조정: %s (%+d원 → 총 %d원)", 유저.display_name, 금액, new_total)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            pass
        else:
            await interaction.response.send_message(
                embed=command_error_embed(str(error)), 
                ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
