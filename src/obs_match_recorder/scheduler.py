"""比赛录制任务的时间计算。"""

from __future__ import annotations

from datetime import datetime, timedelta


TIME_FORMAT = "%Y-%m-%d %H:%M"


def scheduled_start_time(match_start_time: str, advance_start_minutes: str) -> datetime:
    match_start = datetime.strptime(match_start_time, TIME_FORMAT)
    return match_start - timedelta(minutes=int(advance_start_minutes))
