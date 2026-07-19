import discord
from utils.time_utils import format_date_range, get_kst_now

_MAX_FIELD = 1024


def _truncate_field(value: str) -> str:
    if len(value) <= _MAX_FIELD:
        return value
    cutoff = value[: _MAX_FIELD - 5].rfind('\n')
    if cutoff == -1:
        cutoff = _MAX_FIELD - 5
    return value[:cutoff] + "\n..."


# 색상 상수 정의
COLOR_ERROR = 0xE74C3C    # 빨간색 (에러)
COLOR_SUCCESS = 0x2ECC71  # 초록색 (일반 명령어 성공)
COLOR_ADMIN = 0xF1C40F    # 노란색 (관리자 명령어 성공)
COLOR_INFO = 0x3498DB     # 파란색 (주기적 알림/정보)
COLOR_HELP = 0xFFB6C1     # 핑크색 (도움말)

# 아이콘 체계
_MEDALS = ("🥇", "🥈", "🥉")
_ICON_WROTE = "🔹"   # 4위 이하 작성자(편수>0)
_ICON_NONE = "🔴"    # 미작성(0편)
_EMPTY_MSG = "아무도 없네요 저 티나랑 놀아주세요"


def _rank_lines(member_stats: list[dict], *, with_penalty: bool) -> str:
    """편수 내림차순 메달 순위 라인 생성. with_penalty=True면 0편에 '(벌금)' 접미."""
    if not member_stats:
        return _EMPTY_MSG

    # 편수 내림차순, 동점은 discord_id로 결정적 2차 정렬
    ranked = sorted(member_stats, key=lambda s: (-s["post_count"], str(s["discord_id"])))

    lines = []
    rank = 0          # 실제 등수(동점 공유, 1부터)
    prev_count = None
    seen = 0          # 지금까지 본 작성자 수 (동점 등수 계산용)
    for stat in ranked:
        count = stat["post_count"]
        mention = f"<@{stat['discord_id']}>"
        if count > 0:
            seen += 1
            if count != prev_count:
                rank = seen  # 동점이 아니면 등수 = 순번
            prev_count = count
            icon = _MEDALS[rank - 1] if rank - 1 < len(_MEDALS) else _ICON_WROTE
            lines.append(f"{icon} {mention} — **{count}편**")
        else:
            suffix = " (벌금)" if with_penalty else ""
            lines.append(f"{_ICON_NONE} {mention} — **0편**{suffix}")
    return "\n".join(lines)


def bot_welcome_embed() -> discord.Embed:
    """봇이 서버에 처음 입장했을 때 전송하는 환영 메시지"""
    embed = discord.Embed(
        title="안녕하세요! 저는 티나예요!",
        description=(
            "블로그 포스팅을 자동으로 감지하고 알려주는 봇이에요.\n\n"
            "**시작하려면 아래 명령어를 사용해주세요:**\n"
            "1. `/채널설정 #채널` — 알림을 받을 채널 설정\n"
            "2. `/멤버티스토리등록` / `/멤버벨로그등록 @유저 블로그URL` — 멤버 등록 (관리자 전용)\n"
            "3. `/도움말` — 전체 사용법 보기"
        ),
        color=COLOR_SUCCESS,
        timestamp=get_kst_now()
    )
    embed.set_footer(text="티나 • 블로그 포스팅 알림 봇")
    return embed


def new_post_embed(
    author_name: str,
    title: str,
    link: str,
    published_at: str,
    summary: str | None = None,
    summary_failed: bool = False,
) -> discord.Embed:
    """새 블로그 글 알림 Embed"""
    embed = discord.Embed(
        title="새 블로그 글이 올라왔어요!",
        description="읽고 나서 댓글 하나씩 꼭 달아주세요!\n글쓴이에게 큰 힘이 된답니다 💬",
        color=COLOR_INFO,
        timestamp=get_kst_now()
    )
    embed.add_field(name="제목", value=_truncate_field(f"[{title}]({link})"), inline=False)
    embed.add_field(name="작성자", value=author_name, inline=True)
    embed.add_field(name="발행일", value=published_at, inline=True)

    if summary:
        embed.add_field(name="요약", value=_truncate_field(summary), inline=False)
    elif summary_failed:
        embed.add_field(name="요약", value="요약을 생성하지 못했어요.", inline=False)

    embed.set_footer(text="티나 • 블로그 포스팅 알림")

    return embed


def missed_post_embed(author_name: str, title: str, link: str, published_at: str) -> discord.Embed:
    """누락 감지 알림 Embed"""
    embed = discord.Embed(
        title="티나가 누락된 블로그 글을 찾아왔어요!",
        description="다행히 통계에 정상 반영되었으니 걱정마세요.",
        color=COLOR_INFO,
        timestamp=get_kst_now()
    )
    embed.add_field(name="제목", value=_truncate_field(f"[{title}]({link})"), inline=False)
    embed.add_field(name="작성자", value=author_name, inline=True)
    embed.add_field(name="발행일", value=published_at, inline=True)
    embed.set_footer(text="티나 • 누락 감지")

    return embed


def weekly_report_embed(
    week_start: str,
    week_end: str,
    member_stats: list[dict],
    penalty_amount: int,
    is_paused: bool = False,
) -> discord.Embed:
    """주간 리포트 Embed"""
    embed = discord.Embed(
        title=f"🏆 주간 블로그 리포트 ({format_date_range(week_start, week_end)})",
        description="지난 한 주 동안 다들 고생 많으셨어요! 이번 주 최종 순위를 확인해볼게요.",
        color=COLOR_INFO,
        timestamp=get_kst_now()
    )

    embed.add_field(
        name="작성 순위",
        value=_truncate_field(_rank_lines(member_stats, with_penalty=not is_paused)),
        inline=False,
    )

    penalty_members = [
        f"<@{stat['discord_id']}>"
        for stat in member_stats
        if stat["post_count"] == 0 and not is_paused
    ]

    if is_paused:
        embed.add_field(name="벌금 정지", value="이번 주는 벌금이 정지되었어요.", inline=False)
    elif penalty_members:
        embed.add_field(
            name=f"💰 벌금 대상 ({penalty_amount:,}원)",
            value=_truncate_field(", ".join(penalty_members)),
            inline=False
        )

    embed.set_footer(text="티나 • 주간 리포트")

    return embed


def post_list_embed(
    target_name: str,
    week_range: str,
    posts: list[dict],
    week_count: int
) -> discord.Embed:
    """이번 주 포스팅 목록 Embed"""
    embed = discord.Embed(
        title=f"{target_name}님의 이번 주 포스팅 목록",
        description=f"기간: {week_range}",
        color=COLOR_SUCCESS,
        timestamp=get_kst_now()
    )

    if not posts:
        embed.add_field(name="포스팅 목록", value="이번 주 작성한 글이 없어요", inline=False)
    else:
        lines = []
        for p in posts:
            published_date = p["published_at"].split(" ")[0]
            lines.append(f"[{p['title']}]({p['link']}) — {published_date}")
        embed.add_field(name="포스팅 목록", value=_truncate_field("\n".join(lines)), inline=False)

    embed.set_footer(text=f"티나 • 총 {week_count}편")

    return embed


def remind_embed(members_without_posts: list[dict]) -> discord.Embed:
    """마감 리마인드 Embed"""
    mentions = [f"<@{m['discord_id']}>" for m in members_without_posts]

    embed = discord.Embed(
        title="블로그 마감 리마인드!",
        description="째깍⏰째깍⏰ 이번 주 마감이 얼마 남지 않았어요. 오늘 안에 꼭 올려주세요!",
        color=COLOR_INFO,
        timestamp=get_kst_now()
    )
    embed.add_field(
        name="아직 글을 안 쓰신 분",
        value="\n".join(mentions) if mentions else "이번 주는 모두 작성 완료하셨네요! 최고예요!",
        inline=False
    )
    embed.set_footer(text="티나 • 마감 리마인드")

    return embed


def server_stats_embed(
    member_stats: list[dict],
    week_range: str,
    month_range: str
) -> discord.Embed:
    """전체 서버 멤버 통계 Embed"""
    embed = discord.Embed(
        title="📊 전체 멤버 블로그 통계",
        description=f"우리 멤버들이 함께 작성한 포스팅 통계예요.\n\n이번 주: {week_range}\n이번 달: {month_range}",
        color=COLOR_ADMIN,
        timestamp=get_kst_now()
    )

    ranked = sorted(member_stats, key=lambda s: s["week_count"], reverse=True)
    lines = []
    for stat in ranked:
        name = stat.get("discord_name", "알 수 없음")
        week_c = stat["week_count"]
        month_c = stat["month_count"]
        pen = stat["total_penalty"]
        lines.append(f"**{name}** — 주 **{week_c}편** · 월 **{month_c}편** · 벌금 **{pen:,}원**")

    embed.add_field(
        name="멤버별 요약",
        value=_truncate_field("\n".join(lines)) if lines else _EMPTY_MSG,
        inline=False
    )
    embed.set_footer(text="티나 • 전체 통계")

    return embed


def stats_embed(
    target_name: str,
    week_count: int,
    month_count: int,
    week_range: str,
    month_range: str,
    total_penalty: int
) -> discord.Embed:
    """통계 Embed"""
    embed = discord.Embed(
        title=f"{target_name}님의 블로그 통계",
        description="지금까지 작성해주신 포스팅 통계예요. 꾸준한 기록이 모여서 큰 성장이 될 거예요!",
        color=COLOR_SUCCESS,
        timestamp=get_kst_now()
    )
    embed.add_field(name=f"이번 주 ({week_range})", value=f"**{week_count}편** 작성", inline=True)
    embed.add_field(name=f"이번 달 ({month_range})", value=f"**{month_count}편** 작성", inline=True)
    embed.add_field(name="누적 벌금", value=f"**{total_penalty:,}원**", inline=True)
    embed.set_footer(text="티나 • 통계")

    return embed


def status_embed(week_start: str, week_end: str, member_stats: list[dict]) -> discord.Embed:
    """이번 주 현황 Embed"""
    embed = discord.Embed(
        title=f"📊 이번 주 현황 ({format_date_range(week_start, week_end)})",
        description="이번 주 블로그 작성 현황이에요.",
        color=COLOR_SUCCESS,
        timestamp=get_kst_now()
    )

    ranked = sorted(member_stats, key=lambda s: s["post_count"], reverse=True)
    lines = []
    for stat in ranked:
        count = stat["post_count"]
        name = stat.get("discord_name", "알 수 없음")
        icon = "🟢" if count > 0 else "🔴"
        lines.append(f"{icon} **{name}** — **{count}편**")

    embed.add_field(
        name="작성 현황",
        value=_truncate_field("\n".join(lines)) if lines else _EMPTY_MSG,
        inline=False
    )
    embed.set_footer(text="티나 • 현황")

    return embed


def _schedule_field_value(reset_day: str, reset_time: str, remind_day: str) -> str:
    return (
        f"• 주간 리포트: 매주 **{reset_day} {reset_time}**\n"
        f"• 마감 리마인드: 매주 **{remind_day} {reset_time}**\n"
        f"• 월간 리포트: 매달 **1일 {reset_time}**"
    )


def help_embed(reset_day: str = "월요일", reset_time: str = "09:00", remind_day: str = "일요일") -> discord.Embed:
    """일반 도움말 Embed"""
    embed = discord.Embed(
        title="📖 티나 도움말",
        description=f"티나는 여러분들의 블로그 활동을 응원해요! 화이팅! 💖\n\n이 서버는 매주 **{reset_day} {reset_time}**에 주간 정산이 진행됩니다.\n",
        color=COLOR_HELP,
        timestamp=get_kst_now()
    )

    embed.add_field(
        name="📊 일반 명령어",
        value=(
            "*(명령어 뒤에 `@유저`를 지정하지 않으면 전체를 보여줍니다)*\n"
            "`/등록` — 내 블로그 등록 (티스토리/벨로그 선택 후 주소 입력)\n"
            "`/삭제` — 내 블로그 등록 해제\n"
            "`/멤버목록` — 등록된 멤버 목록 확인\n"
            "`/통계 [@유저]` — 이번 주/달 포스팅 통계\n"
            "`/조회 [@유저]` — 이번 주 현황 / 포스팅 목록\n"
            "`/벌금 [@유저]` — 벌금 현황 조회\n"
            "`/새로고침 [@유저]` — 최신 글 즉시 확인\n"
            "`/도움말` — 이 도움말 표시\n"
        ),
        inline=False
    )

    embed.add_field(
        name="⏰ 티나가 알려드려요!",
        value=_schedule_field_value(reset_day, reset_time, remind_day),
        inline=False
    )

    embed.add_field(
        name="🔧 관리자 전용",
        value="관리 명령어는 `/관리자도움말`을 이용해주세요!",
        inline=False
    )

    embed.set_footer(text="티나 • made by woo")
    return embed


def admin_help_embed(reset_day: str = "월요일", reset_time: str = "09:00", remind_day: str = "일요일") -> discord.Embed:
    """관리자 도움말 Embed"""
    embed = discord.Embed(
        title="🔧 티나 관리자 도움말",
        description="관리자 전용 명령어 목록이에요.",
        color=COLOR_ADMIN,
        timestamp=get_kst_now()
    )

    embed.add_field(
        name="관리 명령어",
        value=(
            "`/설정` — 통합 설정 패널 (알림 채널·초기화 요일/시간·벌금 금액·벌금 정지/재개)\n"
            "`/멤버등록 [@유저]` — 멤버 블로그 대리 등록 (플랫폼 선택)\n"
            "`/멤버삭제 [@유저]` — 멤버 블로그 대리 삭제\n"
            "`/벌금변경 [@유저] [금액]` — 벌금 수동 조정 (양수: 추가, 음수: 차감)\n"
            "`/벌금정리 [작업]` — 벌금 정산(누적 유지) 또는 초기화(완전 삭제)\n"
        ),
        inline=False
    )

    embed.add_field(
        name="⏰ 티나가 알려드려요!",
        value=_schedule_field_value(reset_day, reset_time, remind_day),
        inline=False
    )

    embed.set_footer(text="티나 • made by woo")
    return embed


def member_list_embed(members: list[dict]) -> discord.Embed:
    """멤버 목록 Embed"""
    embed = discord.Embed(
        title="등록된 멤버 목록",
        color=COLOR_SUCCESS,
        timestamp=get_kst_now()
    )

    if not members:
        embed.description = "아무도 없네요 저 티나랑 놀아주세요\n`/티스토리등록` 또는 `/벨로그등록`으로 블로그를 추가해주세요!"
    else:
        embed.description = "티나와 함께 꾸준히 기록을 남기고 있는 멤버들이에요. 다들 앞으로도 잘 부탁드려요!\n"
        for i, member in enumerate(members, 1):
            platform_label = "티스토리" if member.get("platform") == "tistory" else "벨로그"
            embed.add_field(
                name=f"{i}. {member['discord_name']} ({platform_label})",
                value=f"{member['blog_url']}",
                inline=False
            )

    embed.set_footer(text=f"티나 • 총 {len(members)}명")

    return embed


def penalty_embed(target_name: str, penalties: list[dict], total: int) -> discord.Embed:
    """벌금 현황 Embed"""
    embed = discord.Embed(
        title=f"{target_name}님의 벌금 현황",
        color=COLOR_SUCCESS,
        timestamp=get_kst_now()
    )

    if not penalties:
        embed.description = "벌금 기록이 없습니다!"
    else:
        embed.description = "지금까지 쌓인 벌금 내역이에요. 다음 주부터는 꼭 제때 작성해서 벌금을 내지 않도록 해요!\n"
        recent = penalties[:10]
        lines = []
        for p in recent:
            lines.append(
                f"{format_date_range(p['week_start'], p['week_end'])} — **{p['amount']:,}원**"
            )
        embed.add_field(name="최근 벌금 내역", value="\n".join(lines), inline=False)

    embed.add_field(name="누적 벌금 합계", value=f"**{total:,}원**", inline=False)
    embed.set_footer(text="티나 • 벌금 현황")

    return embed


def server_penalty_embed(penalties_by_member: list[dict], total_guild_penalty: int) -> discord.Embed:
    """전체 서버 멤버 벌금 현황 Embed"""
    embed = discord.Embed(
        title="전체 멤버 벌금 현황",
        description="서버의 전체 벌금 현황이에요.\n벌금이 너무 많이 쌓이지 않도록 다 함께 파이팅해요!\n",
        color=COLOR_ADMIN,
        timestamp=get_kst_now()
    )

    lines = []
    for stat in penalties_by_member:
        name = stat.get("discord_name", "알 수 없음")
        total_p = stat["total_penalty"]
        if total_p > 0:
            lines.append(f"**{name}** - **{total_p:,}원**")

    embed.add_field(
        name="멤버별 누적 벌금",
        value="\n".join(lines) if lines else "벌금이 부과된 멤버가 없습니다!",
        inline=False
    )

    embed.add_field(name="서버 총 누적 벌금액", value=f"**{total_guild_penalty:,}원**", inline=False)
    embed.set_footer(text="티나 • 전체 벌금 현황")

    return embed


def penalty_settle_embed(member_count: int) -> discord.Embed:
    """벌금 정산 완료 Embed"""
    embed = discord.Embed(
        title="벌금 정산 완료",
        description=(
            "현재까지의 벌금이 정산 처리되었어요.\n"
            "누적 기록은 그대로 유지되며, 현재 미납 벌금은 **0원**으로 초기화되었어요."
        ),
        color=COLOR_ADMIN,
        timestamp=get_kst_now()
    )
    embed.add_field(name="정산 대상 멤버 수", value=f"**{member_count}명**", inline=True)
    embed.set_footer(text="티나 • 벌금 정산")
    return embed


def penalty_reset_embed(member_count: int) -> discord.Embed:
    """벌금 완전 초기화 완료 Embed"""
    embed = discord.Embed(
        title="벌금 완전 초기화 완료",
        description=(
            "모든 벌금 기록이 완전히 삭제되었어요.\n"
            "누적 기록을 포함한 모든 벌금이 **0원**으로 초기화되었어요."
        ),
        color=COLOR_ADMIN,
        timestamp=get_kst_now()
    )
    embed.add_field(name="초기화 대상 멤버 수", value=f"**{member_count}명**", inline=True)
    embed.set_footer(text="티나 • 벌금 초기화")
    return embed


def penalty_change_embed(member_mention: str, amount: int, new_total: int) -> discord.Embed:
    """벌금 수동 조정 결과 Embed"""
    action = "추가" if amount > 0 else "차감"
    embed = discord.Embed(
        title=f"벌금 {action} 완료",
        description=f"{member_mention}님의 벌금이 수동으로 조정되었어요.",
        color=COLOR_ADMIN,
        timestamp=get_kst_now()
    )
    embed.add_field(name="조정 금액", value=f"**{amount:+,}원**", inline=True)
    embed.add_field(name="변경 후 총 벌금", value=f"**{new_total:,}원**", inline=True)
    embed.set_footer(text="티나 • 벌금 관리")
    return embed


def refresh_embed(target_str: str, new_count: int) -> discord.Embed:
    """새로고침 결과 Embed"""
    embed = discord.Embed(
        title="최신 글 확인 완료!",
        description=f"티나가 방금 {target_str}블로그를 꼼꼼히 확인하고 왔어요!\n새로 감지된 글은 총 **{new_count}편**이에요!",
        color=COLOR_INFO,
        timestamp=get_kst_now()
    )
    embed.set_footer(text="티나 • 새로고침")
    
    return embed


def info_embed(title: str, description: str, color: int = COLOR_INFO) -> discord.Embed:
    """일반 정보 알림 Embed"""
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=get_kst_now()
    )
    embed.set_footer(text="티나 • 알림")
    return embed


def error_embed(description: str) -> discord.Embed:
    """오류 알림 Embed"""
    embed = discord.Embed(
        title="앗! 문제가 생겼어요",
        description=description,
        color=COLOR_ERROR,
        timestamp=get_kst_now()
    )
    embed.set_footer(text="티나 • 오류")
    return embed


def register_success_embed(
    user_mention: str,
    blog_url: str,
    existing_count: int,
    week_count: int = 0,
    month_count: int = 0,
    is_admin: bool = False,
) -> discord.Embed:
    """등록 성공 알림 Embed"""
    embed = discord.Embed(
        title="등록 환영해요!" if not is_admin else "멤버 등록 완료!",
        description=f"{user_mention}님의 블로그가 등록되었어요!\n지금부터 새 글이 올라오면 가장 먼저 달려와서 알려드릴게요!",
        color=COLOR_ADMIN if is_admin else COLOR_SUCCESS,
        timestamp=get_kst_now()
    )
    embed.add_field(name="블로그", value=blog_url, inline=False)
    embed.add_field(name="기존 포스팅", value=f"{existing_count}편", inline=True)
    embed.add_field(name="이번 주", value=f"{week_count}편", inline=True)
    embed.add_field(name="이번 달", value=f"{month_count}편", inline=True)
    embed.set_footer(text="티나 • 멤버 등록")
    return embed


def monthly_report_embed(year: int, month: int, member_stats: list[dict]) -> discord.Embed:
    """월간 리포트 Embed"""
    embed = discord.Embed(
        title=f"🏆 {year}년 {month}월 월간 블로그 리포트",
        description="지난 한 달 동안 모두 고생 많으셨어요! 이번 달 최종 순위를 확인해볼게요.",
        color=COLOR_INFO,
        timestamp=get_kst_now()
    )

    embed.add_field(
        name="작성 순위",
        value=_truncate_field(_rank_lines(member_stats, with_penalty=False)),
        inline=False,
    )
    embed.set_footer(text="티나 • 월간 리포트")
    return embed


def unregister_success_embed(user_name: str, platform_name: str = "") -> discord.Embed:
    """등록 해제 성공 알림 Embed"""
    platform_str = f" {platform_name}" if platform_name else ""
    return info_embed(
        "삭제 완료",
        f"**{user_name}**님의{platform_str} 블로그 등록이 해제되었어요.\n\n이제 못 보게 되어 슬프지만...\n마음이 바뀌면 언제든 다시 돌아와 주실 거죠? 기다리고 있을게요!",
        color=COLOR_SUCCESS
    )


def already_registered_embed(user_name: str = None) -> discord.Embed:
    """이미 등록된 경우 알림 Embed"""
    msg = f"**{user_name}**님은 이미 등록된 멤버예요!" if user_name else "이미 등록되어 있는 것 같아요!"
    return info_embed("등록 안내", msg, color=COLOR_SUCCESS)


def admin_only_embed() -> discord.Embed:
    """관리자 권한 부족 알림 Embed"""
    return error_embed("이 명령어는 **서버 관리자** 권한이 필요해요.")


def command_error_embed(error_msg: str) -> discord.Embed:
    """명령어 오류 알림 Embed"""
    return error_embed(f"오류가 발생했어요: {error_msg}")


def not_registered_embed(user_name: str = None) -> discord.Embed:
    """등록되지 않은 경우 알림 Embed"""
    msg = f"**{user_name}**님은 아직 등록되지 않은 상태예요." if user_name else "아직 등록되지 않은 상태예요. 티나와 함께하고 싶다면 먼저 등록해 주세요!"
    return error_embed(msg)


def not_registered_platform_embed(platform_name: str) -> discord.Embed:
    """특정 플랫폼에 등록되지 않은 경우 알림 Embed"""
    return error_embed(f"{platform_name} 블로그가 등록되어 있지 않아요.")


def invalid_tistory_url_embed() -> discord.Embed:
    """잘못된 티스토리 URL 알림 Embed"""
    return error_embed(
        "유효한 티스토리 주소를 입력해주세요!\n"
        "예: `https://아이디.tistory.com` 혹은 `아이디.tistory.com`"
    )


def invalid_velog_url_embed() -> discord.Embed:
    """잘못된 벨로그 URL 알림 Embed"""
    return error_embed(
        "유효한 벨로그 주소를 입력해주세요!\n"
        "예: `https://velog.io/@아이디` 혹은 `velog.io/@아이디`"
    )


def no_members_embed(is_status: bool = False) -> discord.Embed:
    """멤버가 없을 때 알림 Embed"""
    title = "현황 안내" if is_status else "알림"
    desc = "아무도 없네요 저 티나랑 놀아주세요"
    if is_status:
        desc += "\n`/티스토리등록` 또는 `/벨로그등록`으로 블로그를 추가해주세요!"
    return info_embed(title, desc)


def system_error_embed() -> discord.Embed:
    """시스템 오류 알림 Embed"""
    return error_embed("블로그 감지 시스템에 일시적인 오류가 있어요.\n잠시 후 다시 시도해 주세요!")


def connection_error_embed(blog_url: str, status_code: int = None) -> discord.Embed:
    """블로그 접속 실패 알림 Embed"""
    if status_code:
        desc = f"입력하신 블로그 주소에 접속할 수 없어요. (HTTP {status_code})\n{blog_url}"
    else:
        desc = f"블로그에 연결할 수 없어요. 오타가 없는지 확인해주세요!\n{blog_url}"
    return error_embed(desc)


def welcome_embed(user_mention: str, reset_day: str = "월요일", reset_time: str = "09:00") -> discord.Embed:
    """새 멤버 환영 Embed"""
    embed = discord.Embed(
        title="반가워요",
        description=(
            f"안녕하세요! 저는 티나라고 해요.\n"
            f"티나는 {user_mention}님의 블로그 활동을 도와드려요.\n\n"
            f"이 서버는 매주 **{reset_day} {reset_time}**에 주간 정산이 진행됩니다.\n"
            f"앞으로 같이 열심히 블로그 포스팅을 해봐요!"
        ),
        color=COLOR_SUCCESS,
        timestamp=get_kst_now()
    )
    embed.add_field(
        name="시작하기",
        value="`/티스토리등록` 또는 `/벨로그등록` 명령어로 본인의 블로그를 등록해 보세요!\n티나가 꼼꼼하게 새 글을 감시해 드릴게요.",
        inline=False
    )
    embed.add_field(
        name="도움말", 
        value="다른 기능이 궁금하다면 언제든 `/도움말`을 입력해 주세요!", 
        inline=False
    )
    embed.set_footer(text="티나 • 인사")
    return embed
