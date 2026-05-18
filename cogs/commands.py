import discord
from discord import app_commands
from discord.ext import commands
import logging

import database as db
from utils.time_utils import get_week_range, get_month_range, format_date_range
from utils.blog_utils import (
    normalize_tistory_url, normalize_velog_url,
    check_url_accessible, scan_and_save_existing_posts
)
from utils.embed_builder import (
    stats_embed, status_embed, help_embed, penalty_embed,
    server_stats_embed, server_penalty_embed, member_list_embed,
    refresh_embed, info_embed, error_embed,
    register_success_embed, unregister_success_embed,
    already_registered_embed, not_registered_embed, not_registered_platform_embed,
    invalid_tistory_url_embed, invalid_velog_url_embed, no_members_embed,
    system_error_embed, connection_error_embed, post_list_embed,
    COLOR_ADMIN
)

logger = logging.getLogger(__name__)


class Commands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ===== 등록 =====

    @app_commands.command(name="티스토리등록", description="내 티스토리 블로그를 봇에 등록합니다")
    @app_commands.describe(블로그="티스토리 블로그 주소 (예: https://아이디.tistory.com)")
    async def register_tistory(self, interaction: discord.Interaction, 블로그: str):
        await self._register_blog(interaction, 블로그, "tistory")

    @app_commands.command(name="벨로그등록", description="내 벨로그를 봇에 등록합니다")
    @app_commands.describe(블로그="벨로그 주소 (예: https://velog.io/@아이디)")
    async def register_velog(self, interaction: discord.Interaction, 블로그: str):
        await self._register_blog(interaction, 블로그, "velog")

    async def _register_blog(self, interaction: discord.Interaction, raw_url: str, platform: str):
        유저 = interaction.user
        await interaction.response.defer()

        if platform == "tistory":
            blog_url = normalize_tistory_url(raw_url)
            if not blog_url:
                await interaction.followup.send(embed=invalid_tistory_url_embed())
                return
        else:
            blog_url = normalize_velog_url(raw_url)
            if not blog_url:
                await interaction.followup.send(embed=invalid_velog_url_embed())
                return

        ok, status_code = await check_url_accessible(blog_url)
        if not ok:
            await interaction.followup.send(embed=connection_error_embed(blog_url, status_code))
            return

        guild_id = str(interaction.guild_id)
        success = await db.add_member(guild_id, str(유저.id), 유저.display_name, blog_url, platform)
        if not success:
            await interaction.followup.send(embed=already_registered_embed())
            return

        member = await db.get_member_by_discord_id(guild_id, str(유저.id), platform)
        existing_count = week_count = month_count = 0
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

        await interaction.followup.send(embed=register_success_embed(유저.mention, blog_url, existing_count, week_count, month_count))
        logger.info("본인 등록(%s): %s (기존 글 %d편, 이번주 %d편, 이번달 %d편)", platform, 유저.display_name, existing_count, week_count, month_count)

    # ===== 삭제 =====

    @app_commands.command(name="티스토리삭제", description="티스토리 블로그 등록을 해제합니다")
    async def unregister_tistory(self, interaction: discord.Interaction):
        await self._unregister_blog(interaction, "tistory")

    @app_commands.command(name="벨로그삭제", description="벨로그 등록을 해제합니다")
    async def unregister_velog(self, interaction: discord.Interaction):
        await self._unregister_blog(interaction, "velog")

    async def _unregister_blog(self, interaction: discord.Interaction, platform: str):
        유저 = interaction.user
        await interaction.response.defer()
        guild_id = str(interaction.guild_id)
        success = await db.remove_member(guild_id, str(유저.id), platform)
        platform_name = "티스토리" if platform == "tistory" else "벨로그"

        if success:
            await interaction.followup.send(embed=unregister_success_embed(유저.display_name, platform_name))
            logger.info("본인 삭제(%s): %s (Guild: %s)", platform, 유저.display_name, guild_id)
        else:
            await interaction.followup.send(embed=not_registered_platform_embed(platform_name), ephemeral=True)

    # ===== 통계 =====

    @app_commands.command(name="통계", description="이번 주/이번 달 포스팅 통계를 확인합니다")
    @app_commands.describe(유저="통계를 확인할 유저 (미입력 시 전체)")
    async def stats(self, interaction: discord.Interaction, 유저: discord.Member = None):
        await interaction.response.defer()
        guild_id = str(interaction.guild_id)
        r_day, r_hour, r_min = await db.get_reset_time(guild_id)
        week_start, week_end = get_week_range(reset_weekday=r_day, reset_hour=r_hour, reset_minute=r_min)
        month_start, month_end = get_month_range()

        if 유저:
            members = await db.get_members_by_discord_id(guild_id, str(유저.id))
            if not members:
                await interaction.followup.send(embed=not_registered_embed(유저.display_name), ephemeral=True)
                return

            week_count = month_count = total_penalty = 0
            for member in members:
                week_count += await db.get_post_count_in_range(member["id"], week_start, week_end)
                month_count += await db.get_post_count_in_range(member["id"], month_start, month_end)
                total_penalty += await db.get_total_penalty(member["id"])

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
            all_members = await db.get_all_members(guild_id)
            if not all_members:
                await interaction.followup.send(embed=no_members_embed(), ephemeral=True)
                return

            aggregated = {}
            for member in all_members:
                discord_id = member["discord_id"]
                guild_member = interaction.guild.get_member(int(discord_id))
                display_name = member["discord_name"]
                if guild_member:
                    display_name = guild_member.display_name
                    if display_name != member["discord_name"]:
                        await db.update_discord_name(member["id"], display_name)

                if discord_id not in aggregated:
                    aggregated[discord_id] = {"discord_id": discord_id, "discord_name": display_name, "week_count": 0, "month_count": 0, "total_penalty": 0}

                aggregated[discord_id]["week_count"] += await db.get_post_count_in_range(member["id"], week_start, week_end)
                aggregated[discord_id]["month_count"] += await db.get_post_count_in_range(member["id"], month_start, month_end)
                aggregated[discord_id]["total_penalty"] += await db.get_total_penalty(member["id"])

            embed = server_stats_embed(
                list(aggregated.values()),
                format_date_range(week_start, week_end),
                format_date_range(month_start, month_end)
            )
            await interaction.followup.send(embed=embed)

    # ===== 벌금 =====

    @app_commands.command(name="벌금", description="벌금 현황을 조회합니다")
    @app_commands.describe(유저="조회할 유저 (미입력 시 전체)")
    async def penalty(self, interaction: discord.Interaction, 유저: discord.Member = None):
        await interaction.response.defer()
        guild_id = str(interaction.guild_id)

        if 유저:
            members = await db.get_members_by_discord_id(guild_id, str(유저.id))
            if not members:
                await interaction.followup.send(embed=not_registered_embed(유저.display_name), ephemeral=True)
                return

            all_penalties = []
            total = 0
            for member in members:
                all_penalties.extend(await db.get_penalties_for_member(member["id"]))
                total += await db.get_total_penalty(member["id"])
            all_penalties.sort(key=lambda p: p["week_start"], reverse=True)

            embed = penalty_embed(유저.display_name, all_penalties, total)
            await interaction.followup.send(embed=embed)
        else:
            all_members = await db.get_all_members(guild_id)
            if not all_members:
                await interaction.followup.send(embed=no_members_embed(), ephemeral=True)
                return

            aggregated = {}
            for member in all_members:
                discord_id = member["discord_id"]
                guild_member = interaction.guild.get_member(int(discord_id))
                display_name = member["discord_name"]
                if guild_member:
                    display_name = guild_member.display_name
                    if display_name != member["discord_name"]:
                        await db.update_discord_name(member["id"], display_name)

                if discord_id not in aggregated:
                    aggregated[discord_id] = {"discord_id": discord_id, "discord_name": display_name, "total_penalty": 0}

                aggregated[discord_id]["total_penalty"] += await db.get_total_penalty(member["id"])

            penalties_by_member = [v for v in aggregated.values() if v["total_penalty"] > 0]
            total_guild_penalty = sum(v["total_penalty"] for v in aggregated.values())

            embed = server_penalty_embed(penalties_by_member, total_guild_penalty)
            await interaction.followup.send(embed=embed)

    # ===== 새로고침 =====

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
        logger.info("새로고침: %s→ 새 글 %d건 감지 (Guild: %s)", target_str, new_count, guild_id)

    # ===== 도움말 =====

    @app_commands.command(name="도움말", description="티나 사용법을 안내합니다")
    async def help_command(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        r_day, r_hour, r_min = await db.get_reset_time(guild_id)

        days = ["월", "화", "수", "목", "금", "토", "일"]
        reset_day_str = f"{days[r_day]}요일"
        reset_time_str = f"{r_hour:02d}:{r_min:02d}"
        remind_day_str = f"{days[(r_day - 1) % 7]}요일"

        embed = help_embed(reset_day_str, reset_time_str, remind_day_str, reset_time_str)
        await interaction.response.send_message(embed=embed)

    # ===== 조회 =====

    @app_commands.command(name="조회", description="이번 주 현황 및 포스팅 목록을 조회합니다")
    @app_commands.describe(유저="조회할 유저 (미입력 시 전체 현황)")
    async def post_list(self, interaction: discord.Interaction, 유저: discord.Member = None):
        await interaction.response.defer()
        guild_id = str(interaction.guild_id)
        r_day, r_hour, r_min = await db.get_reset_time(guild_id)
        week_start, week_end = get_week_range(reset_weekday=r_day, reset_hour=r_hour, reset_minute=r_min)

        if 유저 is None:
            all_members = await db.get_all_members(guild_id)
            if not all_members:
                await interaction.followup.send(embed=no_members_embed(is_status=True), ephemeral=True)
                return

            aggregated = {}
            for member in all_members:
                discord_id = member["discord_id"]
                guild_member = interaction.guild.get_member(int(discord_id))
                display_name = member["discord_name"]
                if guild_member:
                    display_name = guild_member.display_name
                    if display_name != member["discord_name"]:
                        await db.update_discord_name(member["id"], display_name)

                if discord_id not in aggregated:
                    aggregated[discord_id] = {"discord_id": discord_id, "discord_name": display_name, "post_count": 0}

                aggregated[discord_id]["post_count"] += await db.get_post_count_in_range(member["id"], week_start, week_end)

            embed = status_embed(week_start, week_end, list(aggregated.values()))
            await interaction.followup.send(embed=embed)
        else:
            members = await db.get_members_by_discord_id(guild_id, str(유저.id))
            if not members:
                await interaction.followup.send(embed=not_registered_embed(유저.display_name), ephemeral=True)
                return

            all_posts = []
            for member in members:
                posts = await db.get_posts_in_range(member["id"], week_start, week_end)
                all_posts.extend(posts)
            all_posts.sort(key=lambda p: p["published_at"] or "", reverse=True)

            week_range = format_date_range(week_start, week_end)
            logger.debug("조회: [%s] 이번 주 %d편 (Guild: %s)", 유저.display_name, len(all_posts), guild_id)
            embed = post_list_embed(유저.display_name, week_range, all_posts, len(all_posts))
            await interaction.followup.send(embed=embed)

    # ===== 멤버목록 =====

    @app_commands.command(name="멤버목록", description="등록된 멤버 목록을 조회합니다")
    async def list_members(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild_id = str(interaction.guild_id)
        members = await db.get_all_members(guild_id)

        for member in members:
            guild_member = interaction.guild.get_member(int(member["discord_id"]))
            if guild_member:
                display_name = guild_member.display_name
                if display_name != member["discord_name"]:
                    await db.update_discord_name(member["id"], display_name)
                    member["discord_name"] = display_name

        embed = member_list_embed(members)
        await interaction.followup.send(embed=embed)

    # ===== 초기화설정 =====

    @app_commands.command(name="초기화설정", description="[관리자] 주간 통계 및 벌금 초기화 요일/시간을 설정합니다")
    @app_commands.describe(요일="초기화 요일 (예: 월요일, 수)", 시간="초기화 시간 (예: 09:00, 15:30)")
    @app_commands.default_permissions(administrator=True)
    async def set_reset_time(self, interaction: discord.Interaction, 요일: str, 시간: str):
        await interaction.response.defer()
        guild_id = str(interaction.guild_id)

        day_map = {
            "월": 0, "월요일": 0, "화": 1, "화요일": 1, "수": 2, "수요일": 2,
            "목": 3, "목요일": 3, "금": 4, "금요일": 4, "토": 5, "토요일": 5,
            "일": 6, "일요일": 6
        }

        parsed_day = day_map.get(요일.strip())
        if parsed_day is None:
            await interaction.followup.send(embed=error_embed("올바른 요일을 입력해주세요! (예: 월요일, 수)"))
            return

        try:
            hour_str, min_str = 시간.split(":")
            hour = int(hour_str)
            minute = int(min_str)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except ValueError:
            await interaction.followup.send(embed=error_embed("올바른 시간 형식을 입력해주세요! (예: 09:00, 15:30)"))
            return

        await db.set_setting(guild_id, "reset_weekday", str(parsed_day))
        await db.set_setting(guild_id, "reset_time", f"{hour:02d}:{minute:02d}")

        days = ["월", "화", "수", "목", "금", "토", "일"]
        embed = info_embed(
            "초기화 시간 설정 완료",
            f"이 서버의 주간 초기화 및 벌금 정산 시간이 **{days[parsed_day]}요일 {hour:02d}:{minute:02d}** (으)로 변경되었어요!\n"
            f"마감 리마인드는 정확히 24시간 전인 **{days[(parsed_day-1)%7]}요일 {hour:02d}:{minute:02d}** 에 발송됩니다.",
            color=COLOR_ADMIN
        )
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Commands(bot))
