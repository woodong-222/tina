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

    # 현재 시간이 지정된 요일/시간 이전이면, 개념상 지난주에 속함
    # 요일 비교 시, 현재 요일이 리셋 요일보다 작으면 무조건 지난 주
    # 현재 요일과 리셋 요일이 같고, 현재 시간이 리셋 시간보다 작으면 지난 주
    is_previous_week = False
    if dt.weekday() < reset_weekday:
        is_previous_week = True
    elif dt.weekday() == reset_weekday:
        if dt.hour < reset_hour or (dt.hour == reset_hour and dt.minute < reset_minute):
            is_previous_week = True

    if is_previous_week:
        base_dt = dt - timedelta(days=7)
    else:
        base_dt = dt

    # 해당 주의 시작 요일로 이동
    days_to_subtract = (base_dt.weekday() - reset_weekday) % 7
    start_date = base_dt - timedelta(days=days_to_subtract)
    
    # 시간 설정
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


def format_date_range(start: str, end: str) -> str:
    """날짜 범위를 보기 좋게 포맷 (05/05 ~ 05/11)"""
    s_date = start.split(" ")[0]
    e_date = end.split(" ")[0]
    
    s = datetime.strptime(s_date, "%Y-%m-%d")
    e = datetime.strptime(e_date, "%Y-%m-%d")
    return f"{s.strftime('%m/%d')} ~ {e.strftime('%m/%d')}"


def is_sunday() -> bool:
    return get_kst_now().weekday() == 6


def is_monday() -> bool:
    return get_kst_now().weekday() == 0
