import logging
import discord
from discord import app_commands
from discord.ext import commands

import database as db
from utils.blog_utils import normalize_tistory_url, check_url_accessible, scan_and_save_existing_posts
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
        if member:
            try:
                existing_count = await scan_and_save_existing_posts(member, blog_url)
            except Exception as e:
                logger.error("기존 글 스캔 실패 [%s]: %s", 유저.display_name, e)

        await interaction.followup.send(embed=register_success_embed(유저.mention, blog_url, existing_count, is_admin=True))
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
