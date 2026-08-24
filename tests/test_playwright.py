"""项目专用 Chrome 可执行文件检查。"""

from __future__ import annotations

from obs_match_recorder.browser import BrowserController


def test_project_chrome_executable_exists() -> None:
    chrome = BrowserController._chrome_executable()

    assert chrome is not None
    assert chrome.name.lower() == "chrome.exe"
