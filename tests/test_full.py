"""全流程集成测试：浏览器 → 检测 → 选源 → OBS 连接。

不做真实录制（避免产生无用的录像文件），
仅验证链路各环节能串联执行。

运行方式：
    pytest tests/test_full.py --run-integration
"""

from __future__ import annotations

import pytest

from obs_match_recorder.browser import BrowserController
from obs_match_recorder.monitor import SourceMonitor
from obs_match_recorder.obs_controller import OBSController

SITE_A = "https://www.miguvideo.com/p/live/120000586222"
SITE_B = "https://www.fszb336.com/broadcast/details?room_id=910427680"


@pytest.mark.integration
def test_full_flow(
    browser: BrowserController,
    obs: OBSController,
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

    # 浏览器与检测链路必须工作
    assert status_a.result.score >= 0
    assert status_b.result.score >= 0

    # OBS 连接链路必须工作
    version = obs.test_connection()
    assert version

    # 有源时记录一下选择结果，方便人工观察
    if selected is not None:
        print(f"当前选择：网站 {selected.name}（{selected.result.status}）")
