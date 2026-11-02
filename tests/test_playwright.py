"""项目专用 Chrome 可执行文件检查。"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

from obs_match_recorder.browser import BrowserController


def test_project_chrome_executable_exists() -> None:
    chrome = BrowserController._chrome_executable()

    assert chrome is not None
    assert chrome.name.lower() == "chrome.exe"


def test_debug_chrome_launch_hides_crash_restore_prompt() -> None:
    command = BrowserController._debug_chrome_command(
        Path(r"C:\Chrome\chrome.exe"),
        9222,
        Path(r"C:\ChromeRecorderProfile"),
    )

    assert "--hide-crash-restore-bubble" in command


def test_browser_system_commands_are_started_without_a_console_window() -> None:
    assert BrowserController.HIDDEN_SUBPROCESS_FLAGS != 0

    source = inspect.getsource(BrowserController._debug_browser_pid)
    assert "creationflags=self.HIDDEN_SUBPROCESS_FLAGS" in source

    source = inspect.getsource(BrowserController.restart)
    assert "creationflags=self.HIDDEN_SUBPROCESS_FLAGS" in source


def test_hidden_cursor_is_parked_at_the_bottom_right() -> None:
    source = inspect.getsource(BrowserController.hide_cursor)

    assert "GetSystemMetrics(0)" in source
    assert "GetSystemMetrics(1)" in source
    assert "screen_width - 1" in source
    assert "screen_height - 1" in source


def test_frozen_browser_uses_profile_next_to_project_root(monkeypatch) -> None:
    executable = Path(r"D:\\AI\\Football recording program\\dist\\recorder.exe")
    project_profile = executable.parent.parent / "ChromeRecorderProfile"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))

    assert BrowserController._profile_directory() == project_profile


def test_haixing_anti_debug_compatibility_is_scoped_to_player_host() -> None:
    script = BrowserController.HAIXING_COMPATIBILITY_SCRIPT

    assert "video.club7275.top" in script
    assert "localStorage.setItem('debug', '1')" in script


def test_haixing_compatibility_is_injected_before_navigation() -> None:
    source = inspect.getsource(BrowserController.open_page)

    assert "page.add_init_script" in source
    assert source.index("page.add_init_script") < source.index("page.goto")
