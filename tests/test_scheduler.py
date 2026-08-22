"""scheduled_start_time 时间计算单元测试。"""

from __future__ import annotations

from datetime import datetime

import pytest

from obs_match_recorder.scheduler import TIME_FORMAT, scheduled_start_time


def test_subtracts_advance_minutes() -> None:
    result = scheduled_start_time("2026-08-20 20:00", "15")
    assert result == datetime(2026, 8, 20, 19, 45)


def test_zero_advance_keeps_start_time() -> None:
    result = scheduled_start_time("2026-08-20 02:00", "0")
    assert result == datetime(2026, 8, 20, 2, 0)


def test_large_advance_crosses_midnight() -> None:
    result = scheduled_start_time("2026-08-20 00:30", "120")
    assert result == datetime(2026, 8, 19, 22, 30)


def test_advance_must_be_integer_minutes() -> None:
    with pytest.raises(ValueError):
        scheduled_start_time("2026-08-20 20:00", "abc")


def test_invalid_start_time_raises() -> None:
    with pytest.raises(ValueError):
        scheduled_start_time("not-a-time", "5")


def test_time_format_constant() -> None:
    # 与 GUI 输入提示保持一致
    assert TIME_FORMAT == "%Y-%m-%d %H:%M"
