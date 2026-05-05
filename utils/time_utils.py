from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

def get_kst_now() -> datetime:
    """한국 시간(KST) 기준 현재 시간을 반환합니다."""
    return datetime.now(KST)
def get_week_range(dt: datetime = None) -> tuple[str, str]:
    """해당 날짜가 속한 주의 월요일~일요일 범위를 반환"""
    if dt is None:
        dt = get_kst_now()

    monday = dt - timedelta(days=dt.weekday())
    sunday = monday + timedelta(days=6)

    return monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")


def get_last_week_range() -> tuple[str, str]:
    """지난 주 월요일~일요일 범위를 반환"""
    today = get_kst_now()
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)

    return last_monday.strftime("%Y-%m-%d"), last_sunday.strftime("%Y-%m-%d")


def get_month_range(dt: datetime = None) -> tuple[str, str]:
    """해당 날짜가 속한 달의 1일~말일 범위를 반환"""
    if dt is None:
        dt = get_kst_now()

    first_day = dt.replace(day=1)

    if dt.month == 12:
        last_day = dt.replace(year=dt.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        last_day = dt.replace(month=dt.month + 1, day=1) - timedelta(days=1)

    return first_day.strftime("%Y-%m-%d"), last_day.strftime("%Y-%m-%d")


def format_date_range(start: str, end: str) -> str:
    """날짜 범위를 보기 좋게 포맷 (05/05 ~ 05/11)"""
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    return f"{s.strftime('%m/%d')} ~ {e.strftime('%m/%d')}"


def is_sunday() -> bool:
    return get_kst_now().weekday() == 6


def is_monday() -> bool:
    return get_kst_now().weekday() == 0
