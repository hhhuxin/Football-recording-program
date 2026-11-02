"""浏览器自动化线程约束测试。"""

from __future__ import annotations

from obs_match_recorder.main import OBSMatchRecorderApp


def test_browser_work_is_serialized_on_one_worker_thread() -> None:
    """Playwright Sync API 创建和使用必须留在同一个后台线程。"""

    assert OBSMatchRecorderApp.BACKGROUND_WORKER_COUNT == 1
