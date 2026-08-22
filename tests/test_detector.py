"""LiveDetector 对真实直播页面的检测（集成测试）。

需要先运行 start_chrome_debug.bat 启动调试 Chrome，
否则测试会因无法连接 CDP 而失败。

运行方式：
    pytest tests/test_detector.py --run-integration
"""

from __future__ import annotations

import pytest

from obs_match_recorder.browser import BrowserController
from obs_match_recorder.detector import LiveDetector

# 参考网页保存时的真实地址
SITE_A = "https://www.miguvideo.com/p/live/120000586222"
SITE_B = "https://www.fszb336.com/broadcast/details?room_id=910427680"


@pytest.mark.integration
def test_detect_real_site_a(browser: BrowserController) -> None:
    page = browser.open_page(SITE_A)
    result = LiveDetector(page).detect()

    assert result.page_ok, "咪咕页面应能正常加载"
    assert result.details, "检测结果应包含详细信息"


@pytest.mark.integration
def test_detect_real_site_b(browser: BrowserController) -> None:
    page = browser.open_page(SITE_B)
    result = LiveDetector(page).detect()

    assert result.page_ok, "飞速页面应能正常加载"
    assert result.details, "检测结果应包含详细信息"


@pytest.mark.integration
def test_detect_both_sites_in_one_pass(browser: BrowserController) -> None:
    page_a = browser.open_page(SITE_A)
    page_b = browser.open_page(SITE_B)

    result_a = LiveDetector(page_a).detect()
    result_b = LiveDetector(page_b).detect()

    # 至少能给出状态与评分，而不是抛异常
    assert result_a.status
    assert result_b.status
    assert result_a.score >= 0
    assert result_b.score >= 0
