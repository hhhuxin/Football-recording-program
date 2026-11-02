"""录制启动阶段的直播源选择单元测试。"""

from __future__ import annotations

import inspect

from obs_match_recorder.detector import DetectionResult
from obs_match_recorder.monitor import SiteStatus
from obs_match_recorder.source_manager import SourceManager


def _status(
    site: str,
    *,
    playing: bool,
    score: int = 8,
) -> SiteStatus:
    return SiteStatus(
        name=site,
        url=f"https://example.test/{site}",
        result=DetectionResult(
            status="正在播放" if playing else "疑似直播",
            score=score,
            page_ok=True,
            player_found=True,
            live_text_found=True,
            error_found=False,
            video_exists=True,
            video_playing=playing,
            current_time=12.5,
            video_width=1280,
            video_height=720,
            details="页面正常；找到 Video",
        ),
    )


def test_startup_does_not_select_a_paused_candidate() -> None:
    paused = _status("A", playing=False)

    assert SourceManager._playing_site_or_none(paused) is None


def test_startup_accepts_a_source_that_is_really_playing() -> None:
    playing = _status("A", playing=True)

    assert SourceManager._playing_site_or_none(playing) is playing


def test_waiting_status_contains_playback_diagnostics() -> None:
    status_a = _status("A", playing=False)
    status_b = _status("B", playing=False, score=0)

    message = SourceManager._format_waiting_status(status_a, status_b)

    assert "咪咕视频" in message
    assert "播放=False" in message
    assert "currentTime=12.5" in message
    assert "分辨率=1280x720" in message


def test_advertisement_wait_covers_ninety_second_migu_preroll() -> None:
    assert SourceManager.ADVERTISEMENT_WAIT_TIMEOUT_SECONDS >= 90
    assert SourceManager.ADVERTISEMENT_POLL_INTERVAL_MS == 1000


def test_probe_does_not_pause_a_page_after_detection() -> None:
    source = inspect.getsource(SourceManager.probe_source)

    assert ".deactivate()" not in source


def test_advertisement_completion_skips_backup_page_pause() -> None:
    source = inspect.getsource(SourceManager.prepare_backup_page)

    assert "advertisement_waited = self._wait_for_advertisement(site, page)" in source
    assert "if advertisement_waited:" in source
    assert "return" in source


def test_active_playback_check_retries_current_page_before_full_recovery() -> None:
    source = inspect.getsource(SourceManager.check_active_playback)

    assert "self.refresh_source(self.current_site)" in source
    assert "先刷新并重新准备播放器一次" in source


def test_cursor_is_hidden_after_playback_progress_is_confirmed() -> None:
    source = inspect.getsource(SourceManager.activate_source)

    progress_check = source.index(
        "if self._has_playback_progress(page, progress_wait_ms, site):"
    )
    hide_cursor = source.index("self.browser.hide_cursor(page)")
    successful_return = source.index("return stabilized")
    assert progress_check < hide_cursor < successful_return


def test_high_quality_is_given_time_to_stabilize_before_720p_fallback() -> None:
    source = inspect.getsource(SourceManager.activate_source)

    assert "HIGH_QUALITY_SETTLE_MS" in source
    assert "HIGH_QUALITY_PROGRESS_WAIT_MS" in source
    assert "marker in state.quality for marker in (\"原画\", \"蓝光\")" in source
