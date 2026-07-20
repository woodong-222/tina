import logging
import discord

import database as db
from utils.blog_utils import (
    normalize_tistory_url, normalize_velog_url,
    check_url_accessible, scan_and_save_existing_posts,
)
from utils.time_utils import get_week_range, get_month_range
from utils.embed_builder import (
    register_success_embed, already_registered_embed,
    invalid_tistory_url_embed, invalid_velog_url_embed, invalid_blog_url_embed,
    connection_error_embed, error_embed,
)

logger = logging.getLogger(__name__)


async def register_and_report(
    interaction: discord.Interaction,
    raw_url: str,
    platform: str | None = None,
    *,
    display_user: discord.Member,
    is_admin: bool,
):
    """블로그 등록 공통 플로우. 호출 측에서 interaction.response는 이미 defer된 상태여야 함.
    platform이 None이면 URL로 티스토리/벨로그를 자동 인지한다."""
    if interaction.guild_id is None:
        await interaction.followup.send(embed=error_embed("이 명령은 서버 안에서만 사용할 수 있어요."))
        return

    if platform is None:
        # 결과 문자열 substring이 아니라 어느 normalizer가 매칭됐는지로 판별
        # (예: velog.io/@tistory.com 유저명 오분류 방지)
        tistory_url = normalize_tistory_url(raw_url)
        if tistory_url:
            platform, blog_url = "tistory", tistory_url
        else:
            velog_url = normalize_velog_url(raw_url)
            if not velog_url:
                await interaction.followup.send(embed=invalid_blog_url_embed())
                return
            platform, blog_url = "velog", velog_url
    elif platform == "tistory":
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
    success = await db.add_member(
        guild_id, str(display_user.id), display_user.display_name, blog_url, platform
    )
    if not success:
        name = display_user.display_name if is_admin else None
        await interaction.followup.send(embed=already_registered_embed(name))
        return

    member = await db.get_member_by_discord_id(guild_id, str(display_user.id), platform)
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
            logger.error("기존 글 스캔 실패 [%s]: %s", display_user.display_name, e)

    await interaction.followup.send(
        content=display_user.mention,
        embed=register_success_embed(
            display_user.display_name, blog_url, existing_count, week_count, month_count, is_admin=is_admin
        )
    )
    logger.info(
        "등록(%s, admin=%s): %s (기존 %d, 주 %d, 월 %d)",
        platform, is_admin, display_user.display_name, existing_count, week_count, month_count,
    )
