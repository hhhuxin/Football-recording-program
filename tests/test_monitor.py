"""SourceMonitor 对真实直播页面的监控（集成测试）。

需要调试 Chrome 已启动（start_chrome_debug.bat）。

运行方式：
    pytest tests/test_monitor.py --run-integration
"""

from __future__ import annotations

import pytest

from obs_match_recorder.browser import BrowserController
from obs_match_recorder.monitor import SourceMonitor

SITE_A = "https://www.miguvideo.com/p/live/120000586222"
SITE_B = "https://www.fszb336.com/broadcast/details?room_id=910427680"


@pytest.mark.integration
def test_monitor_detect_once(browser: BrowserController) -> None:
    page_a = browser.open_page(SITE_A)
    page_b = browser.open_page(SITE_B)

    monitor = SourceMonitor(
        page_a=page_a,
        page_b=page_b,
        site_a_url=SITE_A,
        site_b_url=SITE_B,
        interval=5,
    )

    status_a, status_b = monitor.detect_once()

    assert status_a.name == "A"
    assert status_b.name == "B"
    assert status_a.result.score >= 0
    assert status_b.result.score >= 0


@pytest.mark.integration
def test_monitor_choose_site_returns_playable_or_none(
    browser: BrowserController,
) -> None:
    page_a = browser.open_page(SITE_A)
    page_b = browser.open_page(SITE_B)

    monitor = SourceMonitor(
        page_a=page_a,
        page_b=page_b,
        site_a_url=SITE_A,
        site_b_url=SITE_B,
        interval=5,
    )

    status_a, status_b = monitor.detect_once()
    selected = monitor.choose_site(status_a, status_b)

    # 当前没开播时允许返回 None；有结果时必须落到 A 或 B
    if selected is not None:
        assert selected.name in {"A", "B"}
