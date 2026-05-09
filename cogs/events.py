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
    async def on_ready(self):
        """봇 시작 시 오프라인 중 퇴장된 서버 데이터 정리"""
        bot_guild_ids = {str(g.id) for g in self.bot.guilds}
        db_guild_ids = await db.get_all_guild_ids()

        for guild_id in db_guild_ids:
            if guild_id not in bot_guild_ids:
                await db.delete_all_guild_data(guild_id)
                logger.info("오프라인 중 퇴장된 서버 데이터 삭제 [Guild: %s]", guild_id)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        """봇이 서버에서 퇴장하거나 서버가 삭제될 때 DB 데이터 전체 삭제"""
        guild_id = str(guild.id)
        await db.delete_all_guild_data(guild_id)
        logger.info("서버 퇴장 - 데이터 전체 삭제: %s (ID: %s)", guild.name, guild_id)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """새 멤버가 서버에 입장했을 때 실행되는 이벤트"""
        if member.bot:
            return

        guild_id = str(member.guild.id)
        logger.info("새 멤버 입장: %s (서버: %s)", member.display_name, member.guild.name)

        # 1. 설정된 알림 채널 가져오기
        channel_id_str = await db.get_setting("notification_channel_id", guild_id=guild_id)
        
        if not channel_id_str:
            logger.debug("알림 채널이 설정되지 않아 환영 메시지를 보낼 수 없습니다. (서버: %s)", guild_id)
            return

        channel = member.guild.get_channel(int(channel_id_str))
        if not channel:
            logger.warning("알림 채널(ID: %s)을 찾을 수 없습니다. (서버: %s)", channel_id_str, guild_id)
            return

        # 2. 환영 메시지 전송
        try:
            r_day, r_hour, r_min = await db.get_reset_time(guild_id)
            days = ["월", "화", "수", "목", "금", "토", "일"]
            reset_day_str = f"{days[r_day]}요일"
            reset_time_str = f"{r_hour:02d}:{r_min:02d}"

            embed = welcome_embed(member.mention, reset_day=reset_day_str, reset_time=reset_time_str)
            await channel.send(content=f"{member.mention}", embed=embed)
        except Exception as e:
            logger.error("환영 메시지 전송 실패: %s", e)

async def setup(bot: commands.Bot):
    await bot.add_cog(Events(bot))
