"""录制启动阶段的直播源选择单元测试。"""

from __future__ import annotations

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

    assert "网站 A" in message
    assert "播放=False" in message
    assert "currentTime=12.5" in message
    assert "分辨率=1280x720" in message
