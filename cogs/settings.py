import logging
import discord
from discord import app_commands
from discord.ext import commands

from utils.components import SettingsPanelView
from utils.embed_builder import info_embed, admin_only_embed, COLOR_ADMIN

logger = logging.getLogger(__name__)


class Settings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="설정", description="[관리자] 채널·초기화·벌금 설정을 한 곳에서 관리합니다")
    @app_commands.default_permissions(administrator=True)
    async def settings_panel(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(embed=admin_only_embed(), ephemeral=True)
            return

        guild_id = str(interaction.guild_id)
        embed = info_embed(
            "⚙️ 티나 설정",
            "아래 메뉴에서 설정할 항목을 선택해주세요.\n(이 메시지는 나에게만 보이며 잠시 후 만료됩니다)",
            color=COLOR_ADMIN,
        )
        view = SettingsPanelView(guild_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()
        logger.info("설정 패널 열림 (Guild: %s, User: %s)", guild_id, interaction.user.display_name)


async def setup(bot: commands.Bot):
    await bot.add_cog(Settings(bot))
