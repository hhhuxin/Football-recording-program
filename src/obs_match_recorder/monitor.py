"""比赛直播监控。"""

from __future__ import annotations

import time
from dataclasses import dataclass

from playwright.sync_api import Page

from .detector import DetectionResult, LiveDetector


@dataclass
class SiteStatus:
    name: str
    url: str
    result: DetectionResult


class SourceMonitor:
    """持续监控 A/B 两个直播源，并选择当前最佳直播源。"""

    def __init__(
        self,
        page_a: Page,
        page_b: Page | None,
        site_a_url: str,
        site_b_url: str,
        interval: int = 5,
    ) -> None:
        self.page_a = page_a
        self.page_b = page_b

        self.site_a_url = site_a_url
        self.site_b_url = site_b_url

        self.interval = interval

        self.detector_a = LiveDetector(page_a)
        self.detector_b = LiveDetector(page_b) if page_b is not None else None

        self.running = False
        self.current_site: str | None = None
        self.last_current_times: dict[str, float] = {}
        self.stalled_counts: dict[str, int] = {"A": 0, "B": 0}
        self.stalled_limit = max(2, (20 + interval - 1) // interval)

    def detect_once(self) -> tuple[SiteStatus, SiteStatus]:
        result_a = self.detector_a.detect()
        result_b = (
            self.detector_b.detect()
            if self.detector_b is not None
            else DetectionResult(
                status="未配置",
                score=0,
                page_ok=False,
                player_found=False,
                live_text_found=False,
                error_found=False,
                video_exists=False,
                video_playing=False,
                current_time=0.0,
                video_width=0,
                video_height=0,
                details="海星体育未配置",
            )
        )

        status_a = SiteStatus(
            name="A",
            url=self.site_a_url,
            result=result_a,
        )

        status_b = SiteStatus(
            name="B",
            url=self.site_b_url,
            result=result_b,
        )

        self._update_playback_progress(status_a)
        self._update_playback_progress(status_b)

        return status_a, status_b

    def _update_playback_progress(self, status: SiteStatus) -> None:
        site = status.name
        result = status.result

        if not result.video_playing:
            self.stalled_counts[site] += 1
            return

        previous = self.last_current_times.get(site)
        current = result.current_time
        progressed = previous is None or current > previous + 0.1
        self.last_current_times[site] = current

        if progressed:
            self.stalled_counts[site] = 0
            return

        self.stalled_counts[site] += 1
        if self.stalled_counts[site] < self.stalled_limit:
            return

        result.video_playing = False
        result.status = "播放卡顿"
        result.details += (
            f"；currentTime 已连续 {self.stalled_limit * self.interval} 秒未变化"
        )

    @staticmethod
    def _is_playing(status: SiteStatus) -> bool:
        return (
            status.result.status == "正在播放"
            and status.result.video_playing
        )

    def choose_site(
        self,
        status_a: SiteStatus,
        status_b: SiteStatus,
    ) -> SiteStatus | None:
        """
        选择最佳直播源。

        规则：
        1. 当前源仍在播放，优先继续使用当前源。
        2. 当前源断播后，切换到另一个正在播放的源。
        3. 两个都在播放时，不主动切换。
        4. 两个都不在播放时，选择评分最高的源。
        """

        statuses = {
            "A": status_a,
            "B": status_b,
        }

        # 当前源仍正常播放：保持当前源，不切换。
        if self.current_site in statuses:
            current = statuses[self.current_site]

            if self._is_playing(current):
                return current

        # 当前源不可用，寻找正在播放的源。
        playing = [
            status
            for status in (status_a, status_b)
            if self._is_playing(status)
        ]

        if playing:
            return max(
                playing,
                key=lambda status: (
                    status.result.score,
                    1 if status.name == "A" else 0,
                ),
            )

        # 没有真正播放的源时，退回评分最高的源。
        candidates = [
            status
            for status in (status_a, status_b)
            if status.result.score >= 3
        ]

        if not candidates:
            return None

        return max(
            candidates,
            key=lambda status: (
                status.result.score,
                1 if status.name == "A" else 0,
            ),
        )

    def replace_page(self, site: str, page: Page) -> None:
        """在备用标签页被关闭后，替换为重新打开的页面。"""

        if site == "A":
            self.page_a = page
            self.detector_a = LiveDetector(page)
        else:
            self.page_b = page
            self.detector_b = LiveDetector(page)

        self.last_current_times.pop(site, None)
        self.stalled_counts[site] = 0

    def reset_progress(self, site: str) -> None:
        self.last_current_times.pop(site, None)
        self.stalled_counts[site] = 0

    def run_once(self) -> SiteStatus | None:
        status_a, status_b = self.detect_once()

        selected = self.choose_site(
            status_a,
            status_b,
        )

        if selected is not None:
            self.current_site = selected.name

        return selected

    def run(self, callback=None) -> None:
        self.running = True

        while self.running:
            try:
                status_a, status_b = self.detect_once()

                selected = self.choose_site(
                    status_a,
                    status_b,
                )

                if selected is not None:
                    self.current_site = selected.name

                if callback is not None:
                    callback(
                        selected,
                        status_a,
                        status_b,
                    )

            except Exception as error:
                if callback is not None:
                    callback(
                        None,
                        None,
                        None,
                        error,
                    )

            for _ in range(self.interval):
                if not self.running:
                    break

                time.sleep(1)

    def stop(self) -> None:
        self.running = False


# 保留旧名称，避免已有测试代码报错。
LiveMonitor = SourceMonitor
