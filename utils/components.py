import logging
import discord

import database as db
from utils.registration import register_and_report
from utils.time_utils import parse_pause_until
from utils.embed_builder import (
    info_embed, error_embed, unregister_success_embed, not_registered_embed, COLOR_ADMIN,
)

logger = logging.getLogger(__name__)

_TIMEOUT = 120  # 초
_DAYS = ["월", "화", "수", "목", "금", "토", "일"]


# ===== 본인 블로그 등록 모달 (공개) =====

class BlogRegisterModal(discord.ui.Modal):
    def __init__(self, platform: str, *, display_user=None, is_admin: bool = False):
        self.platform = platform
        self._display_user = display_user
        self._is_admin = is_admin
        title = "티스토리 등록" if platform == "tistory" else "벨로그 등록"
        super().__init__(title=title, timeout=_TIMEOUT)
        placeholder = (
            "https://아이디.tistory.com" if platform == "tistory"
            else "https://velog.io/@아이디"
        )
        self.blog_url = discord.ui.TextInput(
            label="블로그 주소", placeholder=placeholder, required=True, max_length=200
        )
        self.add_item(self.blog_url)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        user = self._display_user or interaction.user
        await register_and_report(
            interaction, self.blog_url.value, self.platform,
            display_user=user, is_admin=self._is_admin,
        )


class RegisterPlatformView(discord.ui.View):
    """플랫폼(티스토리/벨로그) 선택 → URL 모달. 본인/관리자 공용."""

    def __init__(self, *, display_user=None, is_admin: bool = False):
        super().__init__(timeout=_TIMEOUT)
        self.display_user = display_user
        self.is_admin = is_admin
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.is_admin and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                embed=error_embed("이 명령은 서버 관리자만 사용할 수 있어요."), ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    @discord.ui.select(
        placeholder="플랫폼을 선택하세요",
        options=[
            discord.SelectOption(label="티스토리", value="tistory", emoji="📕"),
            discord.SelectOption(label="벨로그", value="velog", emoji="📗"),
        ],
    )
    async def pick(self, interaction: discord.Interaction, select: discord.ui.Select):
        platform = select.values[0]
        await interaction.response.send_modal(
            BlogRegisterModal(platform, display_user=self.display_user, is_admin=self.is_admin)
        )
        self.stop()


class _UnregisterSelect(discord.ui.Select):
    def __init__(self, parent: "UnregisterView", members: list[dict]):
        self.parent_view = parent
        options = [
            discord.SelectOption(
                label="티스토리" if m["platform"] == "tistory" else "벨로그",
                description=m["blog_url"][:100],
                value=m["platform"],
            )
            for m in members
        ]
        super().__init__(placeholder="삭제할 블로그를 선택하세요", options=options)

    async def callback(self, interaction: discord.Interaction):
        p = self.parent_view
        platform = self.values[0]
        await db.remove_member(p.guild_id, p.discord_id, platform)
        platform_name = "티스토리" if platform == "tistory" else "벨로그"
        name = p.target_name or interaction.user.display_name
        await interaction.response.edit_message(
            embed=unregister_success_embed(name, platform_name), view=None
        )
        p.stop()


class UnregisterView(discord.ui.View):
    """등록된 블로그 목록 드롭다운 → 선택 삭제. 본인/관리자 공용."""

    def __init__(self, guild_id: str, discord_id: str, members: list[dict], *, is_admin: bool = False, target_name: str = ""):
        super().__init__(timeout=_TIMEOUT)
        self.guild_id = guild_id
        self.discord_id = discord_id
        self.is_admin = is_admin
        self.target_name = target_name
        self.message = None
        self.add_item(_UnregisterSelect(self, members))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.is_admin and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                embed=error_embed("이 명령은 서버 관리자만 사용할 수 있어요."), ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


# ===== 관리자 View 공통 (권한 체크 + timeout 비활성화) =====

class _AdminView(discord.ui.View):
    def __init__(self, timeout: int = _TIMEOUT):
        super().__init__(timeout=timeout)
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                embed=error_embed("이 설정은 서버 관리자만 사용할 수 있어요."), ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


# ===== 초기화 요일/시간 =====

class _WeekdaySelect(discord.ui.Select):
    def __init__(self, parent: "ResetTimeView"):
        self.parent_view = parent
        options = [
            discord.SelectOption(label=f"{d}요일", value=str(i), default=(i == parent.weekday))
            for i, d in enumerate(_DAYS)
        ]
        super().__init__(placeholder="초기화 요일", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.weekday = int(self.values[0])
        await interaction.response.defer()


class _HourSelect(discord.ui.Select):
    def __init__(self, parent: "ResetTimeView"):
        self.parent_view = parent
        options = [
            discord.SelectOption(label=f"{h:02d}시", value=str(h), default=(h == parent.hour))
            for h in range(24)
        ]
        super().__init__(placeholder="시", options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.hour = int(self.values[0])
        await interaction.response.defer()


class _MinuteSelect(discord.ui.Select):
    def __init__(self, parent: "ResetTimeView"):
        self.parent_view = parent
        options = [
            discord.SelectOption(label=f"{m:02d}분", value=str(m), default=(m == parent.minute))
            for m in range(0, 60, 5)
        ]
        super().__init__(placeholder="분", options=options, row=2)

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.minute = int(self.values[0])
        await interaction.response.defer()


class ResetTimeView(_AdminView):
    def __init__(self, guild_id: str, weekday: int = 0, hour: int = 9, minute: int = 0):
        super().__init__()
        self.guild_id = guild_id
        self.weekday = weekday
        self.hour = hour
        self.minute = minute - (minute % 5)  # 5분 단위로 스냅
        self.add_item(_WeekdaySelect(self))
        self.add_item(_HourSelect(self))
        self.add_item(_MinuteSelect(self))

    @discord.ui.button(label="저장", style=discord.ButtonStyle.success, row=3)
    async def save(self, interaction: discord.Interaction, button: discord.ui.Button):
        await db.set_setting(self.guild_id, "reset_weekday", str(self.weekday))
        await db.set_setting(self.guild_id, "reset_time", f"{self.hour:02d}:{self.minute:02d}")
        for child in self.children:
            child.disabled = True
        remind = _DAYS[(self.weekday - 1) % 7]
        await interaction.response.edit_message(
            embed=info_embed(
                "초기화 시간 설정 완료",
                f"주간 초기화를 **{_DAYS[self.weekday]}요일 {self.hour:02d}:{self.minute:02d}**로 설정했어요!\n"
                f"마감 리마인드는 **{remind}요일 {self.hour:02d}:{self.minute:02d}**에 발송됩니다.",
                color=COLOR_ADMIN,
            ),
            view=self,
        )
        self.stop()
        logger.info("초기화 시간 설정(패널): %s %02d:%02d (Guild: %s)",
                    _DAYS[self.weekday], self.hour, self.minute, self.guild_id)


# ===== 벌금 금액 모달 =====

class PenaltyAmountModal(discord.ui.Modal, title="벌금 금액 설정"):
    amount = discord.ui.TextInput(label="벌금 금액(원)", placeholder="예: 5000", required=True, max_length=12)

    def __init__(self, guild_id: str):
        super().__init__(timeout=_TIMEOUT)
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            val = int(self.amount.value.replace(",", "").strip())
            if not (0 <= val <= 2_147_483_647):
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("0 이상 2,147,483,647 이하의 숫자를 입력해주세요."), ephemeral=True
            )
            return
        await db.set_setting(self.guild_id, "penalty_amount", str(val))
        await interaction.response.send_message(
            embed=info_embed("설정 완료", f"벌금 금액을 **{val:,}원**으로 변경했어요!", color=COLOR_ADMIN),
            ephemeral=True,
        )
        logger.info("벌금 금액 설정(패널): %d원 (Guild: %s)", val, self.guild_id)


# ===== 벌금 기간 정지 모달 =====

class PenaltyPauseModal(discord.ui.Modal, title="벌금 기간 정지"):
    date = discord.ui.TextInput(
        label="정지 해제 일시",
        placeholder="예: 5월 11일 09:00 / 5/11 09:00 / 2026-05-11 09:00",
        required=True, max_length=30,
    )

    def __init__(self, guild_id: str):
        super().__init__(timeout=_TIMEOUT)
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        dt = parse_pause_until(self.date.value)
        if dt is None:
            await interaction.response.send_message(
                embed=error_embed("날짜 형식이 올바르지 않아요.\n예: `5월 11일 09:00` / `5/11 09:00` / `2026-05-11 09:00`"),
                ephemeral=True,
            )
            return
        paused_until = dt.strftime("%Y-%m-%d %H:%M:%S")
        await db.set_setting(self.guild_id, "penalty_paused", "0")
        await db.set_setting(self.guild_id, "penalty_paused_until", paused_until)
        await interaction.response.send_message(
            embed=info_embed(
                "벌금 일시정지",
                f"**{dt.strftime('%Y년 %m월 %d일 %H:%M')}**까지 벌금 부과를 정지했어요.\n"
                f"해당 시점 이후 주간 리포트부터 자동 재개됩니다.",
                color=COLOR_ADMIN,
            ),
            ephemeral=True,
        )
        logger.info("벌금 기간 정지(패널): %s까지 (Guild: %s)", paused_until, self.guild_id)


# ===== 벌금 정지/재개 버튼 =====

class PenaltyControlView(_AdminView):
    def __init__(self, guild_id: str):
        super().__init__()
        self.guild_id = guild_id

    @discord.ui.button(label="재개", style=discord.ButtonStyle.success)
    async def resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        await db.set_setting(self.guild_id, "penalty_paused", "0")
        await db.set_setting(self.guild_id, "penalty_paused_until", "")
        await interaction.response.send_message(
            embed=info_embed("벌금 재개", "벌금 부과가 재개되었어요. 다들 이번 주도 파이팅!", color=COLOR_ADMIN),
            ephemeral=True,
        )
        logger.info("벌금 재개(패널) (Guild: %s)", self.guild_id)

    @discord.ui.button(label="무기한 정지", style=discord.ButtonStyle.danger)
    async def pause_forever(self, interaction: discord.Interaction, button: discord.ui.Button):
        await db.set_setting(self.guild_id, "penalty_paused", "1")
        await db.set_setting(self.guild_id, "penalty_paused_until", "")
        await interaction.response.send_message(
            embed=info_embed("벌금 정지", "벌금 부과를 무기한 정지했어요. 재개 버튼으로 해제할 수 있어요.", color=COLOR_ADMIN),
            ephemeral=True,
        )
        logger.info("벌금 무기한 정지(패널) (Guild: %s)", self.guild_id)

    @discord.ui.button(label="기간 정지", style=discord.ButtonStyle.secondary)
    async def pause_until(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PenaltyPauseModal(self.guild_id))


# ===== 알림 채널 선택 =====

class ChannelSelectView(_AdminView):
    def __init__(self, guild_id: str):
        super().__init__()
        self.guild_id = guild_id

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="알림 채널을 선택하세요",
    )
    async def pick(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        picked = select.values[0]
        channel = interaction.guild.get_channel(picked.id)
        perms = channel.permissions_for(interaction.guild.me) if channel else None
        if not perms or not (perms.view_channel and perms.send_messages and perms.embed_links):
            await interaction.response.send_message(
                embed=error_embed(
                    f"<#{picked.id}> 채널에 알림을 보낼 권한이 없어요.\n"
                    "봇에게 **채널 보기 · 메시지 보내기 · 링크 첨부** 권한을 준 채널을 선택해주세요."
                ),
                ephemeral=True,
            )
            return
        await db.set_setting(self.guild_id, "notification_channel_id", str(picked.id))
        await interaction.response.edit_message(
            embed=info_embed("설정 완료", f"알림 채널을 <#{picked.id}>으로 설정했어요!", color=COLOR_ADMIN),
            view=None,
        )
        self.stop()
        logger.info("알림 채널 설정(패널): %s (Guild: %s)", picked.id, self.guild_id)


# ===== 통합 설정 패널 =====

class SettingsPanelView(_AdminView):
    def __init__(self, guild_id: str):
        super().__init__()
        self.guild_id = guild_id

    @discord.ui.select(
        placeholder="무엇을 설정할까요?",
        options=[
            discord.SelectOption(label="알림 채널", value="channel", emoji="📢"),
            discord.SelectOption(label="초기화 요일/시간", value="reset", emoji="⏰"),
            discord.SelectOption(label="벌금 금액", value="penalty_amount", emoji="💰"),
            discord.SelectOption(label="벌금 정지/재개", value="penalty_pause", emoji="⏸️"),
        ],
    )
    async def choose(self, interaction: discord.Interaction, select: discord.ui.Select):
        choice = select.values[0]
        if choice == "channel":
            view = ChannelSelectView(self.guild_id)
            await interaction.response.edit_message(
                embed=info_embed("알림 채널 설정", "알림을 보낼 채널을 골라주세요.", color=COLOR_ADMIN),
                view=view,
            )
            view.message = interaction.message
            self.stop()
        elif choice == "reset":
            r_day, r_hour, r_min = await db.get_reset_time(self.guild_id)
            view = ResetTimeView(self.guild_id, r_day, r_hour, r_min)
            await interaction.response.edit_message(
                embed=info_embed("초기화 시간 설정", "요일과 시간을 고른 뒤 **저장**을 눌러주세요.", color=COLOR_ADMIN),
                view=view,
            )
            view.message = interaction.message
            self.stop()
        elif choice == "penalty_amount":
            await interaction.response.send_modal(PenaltyAmountModal(self.guild_id))
        elif choice == "penalty_pause":
            view = PenaltyControlView(self.guild_id)
            await interaction.response.edit_message(
                embed=info_embed("벌금 정지/재개", "아래 버튼으로 제어하세요.", color=COLOR_ADMIN),
                view=view,
            )
            view.message = interaction.message
            self.stop()
