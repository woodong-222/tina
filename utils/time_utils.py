import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

def get_kst_now() -> datetime:
    """한국 시간(KST) 기준 현재 시간을 반환합니다."""
    return datetime.now(KST)


def get_week_range(dt: datetime = None, reset_weekday: int = 0, reset_hour: int = 9, reset_minute: int = 0) -> tuple[str, str]:
    """해당 날짜가 속한 주의 설정된 요일/시간 ~ 다음 주 요일/시간-1초 범위를 반환"""
    if dt is None:
        dt = get_kst_now()

    days_since_reset = (dt.weekday() - reset_weekday) % 7

    if days_since_reset == 0:
        if dt.hour < reset_hour or (dt.hour == reset_hour and dt.minute < reset_minute):
            days_since_reset = 7

    start_date = dt - timedelta(days=days_since_reset)
    start_dt = start_date.replace(hour=reset_hour, minute=reset_minute, second=0, microsecond=0)
    end_dt = start_dt + timedelta(days=7) - timedelta(seconds=1)

    return start_dt.strftime("%Y-%m-%d %H:%M:%S"), end_dt.strftime("%Y-%m-%d %H:%M:%S")


def get_last_week_range(reset_weekday: int = 0, reset_hour: int = 9, reset_minute: int = 0) -> tuple[str, str]:
    """지난 주의 설정된 요일/시간 ~ 이번 주 요일/시간-1초 범위를 반환"""
    start_str, end_str = get_week_range(reset_weekday=reset_weekday, reset_hour=reset_hour, reset_minute=reset_minute)
    
    start_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S") - timedelta(days=7)
    end_dt = datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S") - timedelta(days=7)
    
    return start_dt.strftime("%Y-%m-%d %H:%M:%S"), end_dt.strftime("%Y-%m-%d %H:%M:%S")


def get_month_range(dt: datetime = None) -> tuple[str, str]:
    """해당 날짜가 속한 달의 1일 00:00:00 ~ 말일 23:59:59 범위를 반환"""
    if dt is None:
        dt = get_kst_now()

    first_day = dt.replace(day=1)

    if dt.month == 12:
        last_day = dt.replace(year=dt.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        last_day = dt.replace(month=dt.month + 1, day=1) - timedelta(days=1)

    return first_day.strftime("%Y-%m-%d 00:00:00"), last_day.strftime("%Y-%m-%d 23:59:59")


def get_last_month_range() -> tuple[str, str]:
    """지난 달의 1일 00:00:00 ~ 말일 23:59:59 범위를 반환"""
    first_of_this_month = get_kst_now().replace(day=1)
    last_day_of_prev_month = first_of_this_month - timedelta(days=1)
    return get_month_range(last_day_of_prev_month)


def parse_pause_until(date_str: str) -> datetime | None:
    """벌금 정지 해제 일시 파싱. '5월 11일 09:00' / '5/11 09:00' / '2026-05-11 09:00' 지원.
    형식 전체 일치(fullmatch)만 허용하고, 과거 일시는 무효(None)로 처리."""
    now = get_kst_now()
    year = now.year
    s = date_str.strip()
    dt = None

    m = re.fullmatch(r'(\d{1,2})월\s*(\d{1,2})일(?:\s+(\d{1,2}):(\d{2}))?', s)
    if m:
        try:
            dt = datetime(year, int(m.group(1)), int(m.group(2)),
                          int(m.group(3) or 0), int(m.group(4) or 0), tzinfo=KST)
        except ValueError:
            return None

    if dt is None:
        m = re.fullmatch(r'(\d{1,2})/(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?', s)
        if m:
            try:
                dt = datetime(year, int(m.group(1)), int(m.group(2)),
                              int(m.group(3) or 0), int(m.group(4) or 0), tzinfo=KST)
            except ValueError:
                return None

    if dt is None:
        m = re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{1,2}):(\d{2}))?', s)
        if m:
            try:
                dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                              int(m.group(4) or 0), int(m.group(5) or 0), tzinfo=KST)
            except ValueError:
                return None

    if dt is None or dt <= now:
        return None
    return dt


def format_date_range(start: str, end: str) -> str:
    """날짜 범위를 보기 좋게 포맷 (05/05 ~ 05/11)"""
    s_date = start.split(" ")[0]
    e_date = end.split(" ")[0]
    
    s = datetime.strptime(s_date, "%Y-%m-%d")
    e = datetime.strptime(e_date, "%Y-%m-%d")
    return f"{s.strftime('%m/%d')} ~ {e.strftime('%m/%d')}"
