import logging
import discord
from discord.ext import commands
import database as db
from utils.embed_builder import welcome_embed

logger = logging.getLogger(__name__)

class Events(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """새 멤버가 서버에 입장했을 때 실행되는 이벤트"""
        if member.bot:
            return

        guild_id = str(member.guild.id)
        logger.info("새 멤버 입장: %s (서버: %s)", member.display_name, member.guild.name)

        # 1. 설정된 알림 채널 가져오기
        channel_id_str = await db.get_setting(guild_id, "notification_channel_id")
        
        if not channel_id_str:
            logger.debug("알림 채널이 설정되지 않아 환영 메시지를 보낼 수 없습니다. (서버: %s)", guild_id)
            return

        channel = member.guild.get_channel(int(channel_id_str))
        if not channel:
            logger.warning("알림 채널(ID: %s)을 찾을 수 없습니다. (서버: %s)", channel_id_str, guild_id)
            return

        # 2. 환영 메시지 전송
        try:
            embed = welcome_embed(member.mention)
            await channel.send(content=f"{member.mention}", embed=embed)
        except Exception as e:
            logger.error("환영 메시지 전송 실패: %s", e)

async def setup(bot: commands.Bot):
    await bot.add_cog(Events(bot))
