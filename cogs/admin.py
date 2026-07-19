import logging
import discord
from discord import app_commands
from discord.ext import commands

import database as db
from utils.components import RegisterPlatformView, UnregisterView, _AdminView
from utils.time_utils import get_week_range
from utils.embed_builder import (
    info_embed, error_embed,
    not_registered_embed,
    admin_only_embed, command_error_embed,
    penalty_change_embed, penalty_settle_embed, penalty_reset_embed,
    COLOR_ADMIN
)

logger = logging.getLogger(__name__)


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


class _PenaltyResetConfirmView(_AdminView):
    def __init__(self, guild_id: str, member_count: int):
        super().__init__(timeout=30)  # _AdminView: 관리자 interaction_check + on_timeout 비활성
        self.guild_id = guild_id
        self.member_count = member_count

    @discord.ui.button(label="확인 (삭제)", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await db.reset_penalties_for_guild(self.guild_id)
        self.stop()
        await interaction.response.edit_message(embed=penalty_reset_embed(self.member_count), view=None)
        logger.info("벌금 완전 초기화 완료 (Guild: %s, 멤버 수: %d)", self.guild_id, self.member_count)

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(
            embed=info_embed("취소됨", "벌금 초기화를 취소했어요.", color=COLOR_ADMIN),
            view=None
        )


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ===== 멤버 등록/삭제 =====

    @app_commands.command(name="멤버등록", description="[관리자] 멤버의 블로그를 등록합니다 (플랫폼 선택)")
    @app_commands.describe(유저="등록할 디스코드 유저")
    @app_commands.guild_only()
    @is_admin()
    async def register_member(self, interaction: discord.Interaction, 유저: discord.Member):
        view = RegisterPlatformView(display_user=유저, is_admin=True)
        embed = info_embed(
            "멤버 블로그 등록",
            f"**{유저.display_name}**님의 등록할 플랫폼을 선택하면 주소 입력창이 나와요.",
            color=COLOR_ADMIN,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()

    @app_commands.command(name="멤버삭제", description="[관리자] 멤버의 블로그 등록을 해제합니다")
    @app_commands.describe(유저="삭제할 디스코드 유저")
    @app_commands.guild_only()
    @is_admin()
    async def remove_member(self, interaction: discord.Interaction, 유저: discord.Member):
        guild_id = str(interaction.guild_id)
        members = await db.get_members_by_discord_id(guild_id, str(유저.id))
        if not members:
            await interaction.response.send_message(embed=not_registered_embed(유저.display_name), ephemeral=True)
            return
        view = UnregisterView(guild_id, str(유저.id), members, is_admin=True, target_name=유저.display_name)
        embed = info_embed(
            "멤버 블로그 삭제",
            f"**{유저.display_name}**님의 삭제할 블로그를 선택해주세요.",
            color=COLOR_ADMIN,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()

    # ===== 벌금 관리 =====

    @app_commands.command(name="벌금변경", description="[관리자] 멤버 벌금을 수동으로 조정합니다")
    @app_commands.describe(유저="대상 유저", 금액="조정할 금액 (양수: 추가, 음수: 차감)")
    @is_admin()
    async def adjust_penalty(self, interaction: discord.Interaction, 유저: discord.Member, 금액: int):
        await interaction.response.defer()
        guild_id = str(interaction.guild_id)

        members = await db.get_members_by_discord_id(guild_id, str(유저.id))
        if not members:
            await interaction.followup.send(embed=not_registered_embed(유저.display_name), ephemeral=True)
            return

        if 금액 == 0:
            await interaction.followup.send(embed=error_embed("0원은 조정할 수 없어요."), ephemeral=True)
            return

        current_total = 0
        for m in members:
            current_total += await db.get_total_penalty(m["id"])

        if 금액 < 0 and current_total + 금액 < 0:
            await interaction.followup.send(
                embed=error_embed(f"차감 후 총 벌금이 음수가 됩니다. (현재: {current_total:,}원, 차감: {abs(금액):,}원)"),
                ephemeral=True
            )
            return

        r_day, r_hour, r_min = await db.get_reset_time(guild_id)
        week_start, week_end = get_week_range(reset_weekday=r_day, reset_hour=r_hour, reset_minute=r_min)
        await db.add_penalty(members[0]["id"], week_start, week_end, 금액)

        new_total = 0
        for m in members:
            new_total += await db.get_total_penalty(m["id"])

        await interaction.followup.send(embed=penalty_change_embed(유저.mention, 금액, new_total))
        logger.info("벌금 수동 조정: %s (%+d원 → 총 %d원)", 유저.display_name, 금액, new_total)

    # ===== 벌금 정리 (정산/초기화 통합) =====

    @app_commands.command(name="벌금정리", description="[관리자] 벌금을 정산하거나 완전 초기화합니다")
    @app_commands.describe(작업="정산: 미납 0원 처리(누적 유지) / 초기화: 모든 기록 완전 삭제")
    @app_commands.choices(작업=[
        app_commands.Choice(name="정산 (미납 0원 처리, 누적 기록 유지)", value="settle"),
        app_commands.Choice(name="초기화 (모든 기록 완전 삭제, 되돌릴 수 없음)", value="reset"),
    ])
    @app_commands.guild_only()
    @is_admin()
    async def manage_penalty(self, interaction: discord.Interaction, 작업: app_commands.Choice[str]):
        guild_id = str(interaction.guild_id)
        members = await db.get_all_members(guild_id)
        if not members:
            await interaction.response.send_message(embed=error_embed("등록된 멤버가 없어요."), ephemeral=True)
            return

        member_count = len(set(m["discord_id"] for m in members))

        if 작업.value == "settle":
            await db.settle_penalties_for_guild(guild_id)
            await interaction.response.send_message(embed=penalty_settle_embed(member_count))
            logger.info("벌금 정산 완료 (Guild: %s, 멤버 수: %d)", guild_id, member_count)
        else:  # reset — 파괴적이라 확인 버튼
            view = _PenaltyResetConfirmView(guild_id, member_count)
            await interaction.response.send_message(
                embed=error_embed(
                    "⚠️ **정말로 모든 벌금 기록을 삭제할까요?**\n"
                    "이 작업은 누적 기록을 포함한 **모든 벌금 데이터**를 삭제하며, 되돌릴 수 없어요."
                ),
                view=view,
                ephemeral=True
            )
            view.message = await interaction.original_response()

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
