"""直播源管理：页面操作、监控循环、源切换。

从 main.py 中拆分出的业务逻辑层：
- 负责 A/B 两个直播页的打开、探测、激活、刷新、重开、关闭
- 负责持续监控循环（检测 → 选源 → 刷新/切换 → 下一轮）
- 不直接操作 Tk 控件；日志通过回调交给 GUI 层
"""

from __future__ import annotations

import time
from typing import Callable
from urllib.parse import urlparse

from playwright.sync_api import Page

from .detector import DetectionResult, LiveDetector
from .monitor import SiteStatus, SourceMonitor
from .player_adapter import (
    HaixingPlayerAdapter,
    PlayerControlError,
    PlayerState,
    adapter_for,
)


class SourceManager:
    """管理 A/B 两个直播源及其监控循环。"""

    SITE_LABELS = {"A": "咪咕视频", "B": "海星体育"}

    # 开赛前页面可能已经打开但尚未拉流。等待半小时后才退出，避免
    # 短暂网络异常导致错过比赛，同时也避免后台线程无限等待。
    SOURCE_WAIT_TIMEOUT_SECONDS = 30 * 60
    PLAYBACK_PROGRESS_WAIT_MS = 6000
    HIGH_QUALITY_SETTLE_MS = 10_000
    HIGH_QUALITY_PROGRESS_WAIT_MS = 15_000
    ADVERTISEMENT_WAIT_TIMEOUT_SECONDS = 100
    ADVERTISEMENT_POLL_INTERVAL_MS = 1000
    STARTUP_RESTART_ATTEMPTS = 2
    ACTIVATION_ATTEMPTS = 3

    def __init__(
        self,
        browser,
        root,
        executor,
        log: Callable[[str], None],
        safe_log: Callable[[str], None],
        url_for_site: Callable[[str], str],
    ) -> None:
        self.browser = browser
        self.root = root
        self.executor = executor
        self._log = log
        self._safe_log = safe_log
        self._url_for_site = url_for_site

        self.page_a: Page | None = None
        self.page_b: Page | None = None

        self.source_monitor: SourceMonitor | None = None

        self.current_site: str | None = None
        self.source_failure_counts = {"A": 0, "B": 0}

        # 防止出现两个监控循环同时运行
        self.monitor_loop_active = False

        # 每次启动新的监控循环都会生成新的 ID。
        # 旧循环即使还有 after 回调，也会自动失效。
        self.monitor_loop_id = 0

        # 任务是否仍然活跃（由 GUI 层控制）
        self.task_active = False

    @classmethod
    def site_label(cls, site: str | None) -> str:
        return cls.SITE_LABELS.get(site or "", site or "未知源")

    # =========================================================
    # 生命周期
    # =========================================================

    def reset(self) -> None:
        """启动新任务前重置内部状态。"""

        self.current_site = None
        self.source_failure_counts = {"A": 0, "B": 0}
        self.invalidate_monitor_loop()

    def stop(self) -> None:
        """停止任务：作废所有监控回调并停止监控器。"""

        self.task_active = False
        self.invalidate_monitor_loop()

        if self.source_monitor is not None:
            try:
                self.source_monitor.stop()
            except Exception:
                pass

    def close(self) -> None:
        """关闭所有页面与监控（程序退出时调用）。"""

        self.task_active = False
        self.invalidate_monitor_loop()

        if self.source_monitor is not None:
            try:
                self.source_monitor.stop()
            except Exception:
                pass

    def restore_active_cursor(self) -> None:
        """恢复当前页面鼠标样式；调用方必须在浏览器工作线程执行。"""

        if self.current_site is None:
            return
        page = self._page_for_site(self.current_site)
        if page is not None and not page.is_closed():
            self.browser.restore_cursor(page)

    # =========================================================
    # 页面操作
    # =========================================================

    def open_sites(
        self,
        site_a_url: str,
        site_b_url: str,
    ) -> tuple[Page | None, Page | None]:
        """打开（或重新打开）A/B 两个页面，并保存引用。"""

        self.browser.close_managed_pages()
        self.page_a = self.browser.open_page(site_a_url) if site_a_url.strip() else None
        if self.page_a is not None:
            self._safe_log("咪咕视频页面已打开，等待播放器和二维码加载 3 秒……")
            self.page_a.wait_for_timeout(3000)
        self.page_b = (
            self.browser.open_page(site_b_url)
            if site_b_url.strip()
            else None
        )
        if self.page_b is not None:
            self._safe_log("海星体育页面已打开，等待播放器初始化 3 秒……")
            self.page_b.wait_for_timeout(3000)
        self.browser.close_unexpected_pages(
            [self.page_a, self.page_b],
            self._allowed_hosts(site_a_url, site_b_url),
        )
        return self.page_a, self.page_b

    def prepare_haixing_test_playback(self) -> PlayerState | None:
        """仅准备海星试听画面，不启动录制或源监控。"""
        if self.page_b is None:
            return None
        self.page_b.bring_to_front()
        if self.page_a is not None:
            adapter_for("A", self.page_a).deactivate()
        self._safe_log("海星体育：正在取消静音、选择原画质、进入全屏……")
        adapter = HaixingPlayerAdapter(self.page_b)
        state = adapter.prepare(activate=True)
        if not state.ready_for_recording:
            raise PlayerControlError(f"海星播放设置未完成：{state}")
        if "原画" not in state.quality:
            self._safe_log(
                f"海星当前实际画质为「{state.quality}」，未切换到原画。"
                "网站要求登录后才能切换画质，请在此浏览器登录后重新测试。"
            )
        return state

    @staticmethod
    def _allowed_hosts(*urls: str) -> set[str]:
        return {
            parsed.hostname
            for parsed in (urlparse(url) for url in urls)
            if parsed.hostname
        }

    def _stabilize_active_source(
        self,
        site: str,
        page: Page,
    ) -> PlayerState:
        """保证当前页独占媒体、没有外部弹窗并保持前台播放。"""

        # activate_source 已经完成了带延时的播放、画质和全屏操作。这里先
        # 检查现状，避免稳定化阶段再次无条件调用 ensure_active 导致播放器
        # 在短时间内重复切换状态而卡住。
        adapter = adapter_for(site, page)
        state = adapter.inspect()
        if not state.ready_for_recording:
            state = adapter.ensure_active()
        self.browser.silence_other_pages(page)
        self.browser.close_unexpected_pages(
            [self.page_a, self.page_b, page],
            self._allowed_hosts(
                self._url_for_site("A"),
                self._url_for_site("B"),
            ),
        )
        if not self.browser.ensure_foreground_and_fullscreen(page):
            raise PlayerControlError(
                "Chrome 窗口未能置前并进入浏览器全屏。"
            )
        self.browser.hide_cursor(page)
        return adapter_for(site, page).inspect()

    def prepare_backup_page(self, site: str, page: Page | None) -> None:
        """准备备用页，但不抢占当前浏览器焦点，也不打开声音。"""

        if page is None:
            return

        advertisement_waited = self._wait_for_advertisement(site, page)
        if advertisement_waited:
            # prepare(activate=False) 会明确调用 video.pause()。广告刚结束时
            # 不能再走这个备用页静音分支，后续 probe/activate 会立即恢复直播。
            return
        try:
            adapter_for(site, page).prepare(activate=False)
        except PlayerControlError:
            # 页面可能还在广告/登录跳转中，后续检测和切换会再次尝试。
            pass

    def probe_source(self, site: str, page: Page | None, url: str) -> SiteStatus:
        if page is None:
            return SiteStatus(name=site, url=url, result=self._unconfigured_result())
        """串行唤醒并检测单个来源，确保另一个来源不会同时出声。"""

        self._wait_for_advertisement(site, page)
        page.bring_to_front()
        self.browser.silence_other_pages(page)
        try:
            adapter_for(site, page).prepare(activate=True)
        except PlayerControlError:
            pass

        result = LiveDetector(page).detect()

        return SiteStatus(name=site, url=url, result=result)

    @staticmethod
    def _unconfigured_result() -> DetectionResult:
        return DetectionResult(
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

    def refresh_source(self, site: str) -> None:
        """刷新当前源页面并重新准备播放器（后台线程调用）。"""

        page = self._page_for_site(site)
        if page is None or page.is_closed():
            return

        try:
            # 广告页不能刷新，否则 90 秒广告会从头开始。广告结束后直接
            # 恢复播放器准备流程，只有不存在广告时才执行页面刷新。
            if self._wait_for_advertisement(site, page):
                if adapter_for(site, page).has_active_advertisement():
                    return
                adapter_for(site, page).prepare(activate=True)
                if self.source_monitor is not None:
                    self.source_monitor.reset_progress(site)
                return

            page.reload(wait_until="domcontentloaded", timeout=30000)
            self._wait_for_advertisement(site, page)
            adapter_for(site, page).prepare(activate=True)
            if self.source_monitor is not None:
                self.source_monitor.reset_progress(site)
        except Exception as error:
            self._safe_log(f"{self.site_label(site)}刷新失败：{error}")

    def reopen_source(self, site: str, url: str) -> None:
        """重新打开备用源页面并替换监控引用（后台线程调用）。"""

        if not url:
            self._safe_log(f"{self.site_label(site)}未配置备用 URL，继续等待当前源恢复。")
            return

        page = self.browser.open_page(url)
        self.prepare_backup_page(site, page)

        if site == "A":
            self.page_a = page
        else:
            self.page_b = page

        if self.source_monitor is not None:
            self.source_monitor.replace_page(site, page)

    def close_source_page(self, site: str) -> None:
        page = self._page_for_site(site)
        if page is None or page.is_closed():
            return
        self.browser.close_page(page)

    def deactivate_source(self, site: str) -> None:
        page = self._page_for_site(site)
        if page is None or page.is_closed():
            return
        adapter_for(site, page).deactivate()

    def _page_for_site(self, site: str) -> Page | None:
        return self.page_a if site == "A" else self.page_b

    def _wait_for_advertisement(self, site: str, page: Page) -> bool:
        """等待片前广告结束；整个等待过程不刷新、不点击播放器。"""

        adapter = adapter_for(site, page)
        if not adapter.has_active_advertisement():
            return False

        # 先尝试 Chrome 全屏；即使该步骤未确认成功，仍必须继续点击咪咕
        # 播放器右下角的全屏控件。后者可独立进入播放器全屏。
        if not self.browser.ensure_foreground_and_fullscreen(page):
            self._safe_log(
                f"{self.site_label(site)}广告页未能确认浏览器全屏，"
                "继续尝试咪咕播放器全屏。"
            )
        if not adapter.expand_advertisement_display():
            self._safe_log(
                f"{self.site_label(site)}广告页未能确认播放器铺满窗口，"
                "仍将等待广告结束后继续播放准备。"
            )

        self._safe_log(
            f"{self.site_label(site)}检测到片前广告，"
            f"最多等待 {self.ADVERTISEMENT_WAIT_TIMEOUT_SECONDS} 秒，"
            "广告结束前不刷新页面也不操作播放器。"
        )
        deadline = time.monotonic() + self.ADVERTISEMENT_WAIT_TIMEOUT_SECONDS
        while self.task_active and time.monotonic() < deadline:
            page.wait_for_timeout(self.ADVERTISEMENT_POLL_INTERVAL_MS)
            if not adapter.has_active_advertisement():
                self._safe_log(
                    f"{self.site_label(site)}片前广告已结束，继续准备直播播放器。"
                )
                return True

        if self.task_active:
            self._safe_log(
                f"{self.site_label(site)}片前广告等待超过 "
                f"{self.ADVERTISEMENT_WAIT_TIMEOUT_SECONDS} 秒，"
                "保留当前页面并继续常规直播检测，不刷新页面。"
            )
        return True

    def activate_source(self, site: str) -> PlayerState:
        """激活指定源：依次尝试原画、蓝光、高清 720P。"""

        page = self._page_for_site(site)
        if page is None or page.is_closed():
            raise PlayerControlError(f"{self.site_label(site)}页面不存在或已经关闭。")

        for other_site in ("A", "B"):
            if other_site == site:
                continue
            other_page = self._page_for_site(other_site)
            if other_page is not None and not other_page.is_closed():
                adapter_for(other_site, other_page).deactivate()

        page.bring_to_front()
        self.browser.silence_other_pages(page)
        self.browser.close_unexpected_pages(
            [self.page_a, self.page_b, page],
            self._allowed_hosts(
                self._url_for_site("A"),
                self._url_for_site("B"),
            ),
        )
        self._wait_for_advertisement(site, page)

        last_state: PlayerState | None = None
        last_error: PlayerControlError | None = None
        for attempt in range(self.ACTIVATION_ATTEMPTS):
            if attempt:
                page.reload(wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2500)
            # 三次准备严格对应原画、蓝光、高清 720P，避免原画一次
            # 拉流较慢就越过蓝光直接降级。
            quality_fallback_steps = attempt
            try:
                state = adapter_for(site, page).prepare(
                    activate=True,
                    quality_fallback_steps=quality_fallback_steps,
                )
            except PlayerControlError as error:
                last_error = error
                self._safe_log(
                    f"{self.site_label(site)}第 {attempt + 1} 次播放器准备失败：{error}"
                )
                continue
            last_state = state
            high_quality = any(
                marker in state.quality for marker in ("原画", "蓝光")
            )
            if high_quality and not state.ready_for_recording:
                # 咪咕切到原画/蓝光时会重新拉流，首秒可能短暂显示暂停。
                # 不应立刻误判失败并降到 720P。
                page.wait_for_timeout(self.HIGH_QUALITY_SETTLE_MS)
                state = adapter_for(site, page).inspect()
                last_state = state
            if quality_fallback_steps == 1:
                self._safe_log(
                    f"{self.site_label(site)}原画未稳定，"
                    f"改用蓝光重试：{state.quality or '未返回名称'}。"
                )
            elif quality_fallback_steps >= 2:
                self._safe_log(
                    f"{self.site_label(site)}原画和蓝光均未稳定，"
                    f"改用高清 720P 重试：{state.quality or '未返回名称'}。"
                )
            if state.ready_for_recording:
                stabilized = self._stabilize_active_source(site, page)
                progress_wait_ms = (
                    self.HIGH_QUALITY_PROGRESS_WAIT_MS
                    if high_quality
                    else self.PLAYBACK_PROGRESS_WAIT_MS
                )
                if self._has_playback_progress(page, progress_wait_ms, site):
                    # 画面、声音和进度均确认成功后再藏鼠标。此前的全屏、
                    # 画质和播放点击可能会重新让鼠标显示在画面中央。
                    self.browser.hide_cursor(page)
                    return stabilized
                last_state = stabilized
                self._safe_log(
                    f"{self.site_label(site)}播放器状态看似就绪，但 currentTime 未推进，"
                    "等待后重试，暂不启动 OBS。"
                )

        raise PlayerControlError(
            f"{self.site_label(site)}未达到录制条件："
            f"播放={last_state.playing if last_state else False}，"
            f"静音={last_state.muted if last_state else True}，"
            f"全屏={last_state.fullscreen if last_state else False}，"
            f"画质={last_state.quality if last_state else ''}，"
            f"弹幕关闭={last_state.danmaku_disabled if last_state else False}，"
            "currentTime 未连续推进。"
            + (f"\n最后一次播放器错误：{last_error}" if last_error else "")
        )

    def _has_playback_progress(
        self,
        page: Page,
        wait_ms: int | None = None,
        site: str | None = None,
    ) -> bool:
        """确认播放器不是只停在一帧画面后就启动 OBS。"""

        surface = (
            HaixingPlayerAdapter.frame_for_page(page)
            if site == "B"
            else page
        )
        video = surface.locator("video").first
        last_time: float | None = None
        sample_count = max(
            2,
            (wait_ms or self.PLAYBACK_PROGRESS_WAIT_MS) // 1000,
        )
        try:
            for index in range(sample_count):
                snapshot = video.evaluate(
                    "video => ({paused: video.paused, currentTime: video.currentTime})",
                    timeout=5000,
                )
                current_time = float(snapshot.get("currentTime", 0))
                if not bool(snapshot.get("paused", True)):
                    # 播放器重建 video 节点时 currentTime 可能归零；重建后
                    # 从新时间点继续增长，仍然说明直播已经恢复出帧。
                    if last_time is not None and current_time > last_time + 0.1:
                        return True
                    last_time = current_time
                else:
                    last_time = None

                if index < sample_count - 1:
                    surface.wait_for_timeout(1000)
        except Exception:
            return False

        return False

    def check_active_playback(self) -> tuple[PlayerState | None, bool]:
        """检查当前源；异常时先在原页面执行一次刷新恢复。"""

        if self.current_site is None:
            return None, False
        page = self._page_for_site(self.current_site)
        if page is None or page.is_closed():
            return None, False

        adapter = adapter_for(self.current_site, page)
        state = adapter.inspect()
        if state.ready_for_recording and self._has_playback_progress(
            page, site=self.current_site
        ):
            return state, True

        # 先在原标签页恢复一次，避免网页瞬时暂停就关闭 Chrome、打断录制。
        # refresh_source 会识别片前广告，广告期间只等待而不会刷新。
        self._safe_log(
            f"{self.site_label(self.current_site)}播放暂停或画面未推进，"
            "先刷新并重新准备播放器一次。"
        )
        self.refresh_source(self.current_site)

        if page.is_closed():
            return None, False
        state = adapter_for(self.current_site, page).inspect()
        return state, bool(
            state.ready_for_recording
            and self._has_playback_progress(page, site=self.current_site)
        )

    def restart_session(self, site_a_url: str, site_b_url: str):
        """重启项目专用 Chrome，并重新建立 A/B 页面和播放器会话。"""

        self._safe_log("正在关闭卡死的项目专用 Chrome……")
        self.browser.restart()
        self._safe_log("Chrome 已重启，重新打开直播页面……")
        return self.open_and_select(site_a_url, site_b_url)

    def activate_first_ready_source(
        self,
        preferred_site: str,
        status_a: SiteStatus,
        status_b: SiteStatus,
    ):
        """按优先级尝试激活第一个达到录制条件的源。"""

        order = [preferred_site, "B" if preferred_site == "A" else "A"]
        statuses = {"A": status_a, "B": status_b}
        errors: list[str] = []

        for site in order:
            if site != preferred_site and not SourceMonitor._is_playing(statuses[site]):
                continue
            try:
                state = self.activate_source(site)
                return statuses[site], state
            except PlayerControlError as error:
                errors.append(f"{self.site_label(site)}：{error}")

        raise PlayerControlError(
            "首选源和备用源都未达到录制条件。\n" + "\n".join(errors)
        )

    # =========================================================
    # 录制流程（后台线程调用）
    # =========================================================

    def open_and_select(
        self,
        site_a_url: str,
        site_b_url: str,
    ):
        """打开页面、探测、等待可用源、激活首选源（后台线程）。

        返回 (page_a, page_b, monitor, selected, status_a, status_b, player_state)。
        任务被取消时返回 None。
        """

        self._safe_log("正在连接 Chrome 调试浏览器……")
        self.browser.start()

        self._safe_log("正在打开咪咕视频直播页……")
        page_a, page_b = self.open_sites(site_a_url, site_b_url)
        if page_b is not None:
            self._safe_log("正在打开海星体育备用直播页……")

        # 两个页面都先关闭声音并关闭弹幕；真正使用的页面稍后再激活。
        self._safe_log("正在准备播放器并关闭弹窗……")
        self.prepare_backup_page("A", page_a)
        self.prepare_backup_page("B", page_b)

        self._safe_log("正在检测咪咕视频/海星体育的直播状态……")
        status_a = self.probe_source("A", page_a, site_a_url)
        status_b = (
            self.probe_source("B", page_b, site_b_url)
            if page_b is not None
            else SiteStatus(
                name="B",
                url="",
                result=self._unconfigured_result(),
            )
        )

        monitor = SourceMonitor(
            page_a=page_a,
            page_b=page_b,
            site_a_url=site_a_url,
            site_b_url=site_b_url,
            interval=5,
        )

        selected = self._playing_site_or_none(
            monitor.choose_site(status_a, status_b)
        )
        self._log_waiting_status(status_a, status_b, initial=True)

        # A/B 都没有可用直播源时：等待重试，而不是直接失败退出。
        # 直播源经常在开赛前几分钟才拉流，需要轮询等待。
        waiting_logged = False
        wait_started = time.monotonic()
        last_status_log = wait_started
        while selected is None and self.task_active:
            elapsed = time.monotonic() - wait_started
            if elapsed >= self.SOURCE_WAIT_TIMEOUT_SECONDS:
                raise PlayerControlError(
                    "直播源等待超时（30 分钟）。\n"
                    + self._format_waiting_status(status_a, status_b)
                    + "\n请检查直播是否已开始、账号登录状态和咪咕弹窗。"
                )

            if not waiting_logged:
                self._safe_log(
                    "A、B 两个网站当前都没有可用直播源，"
                    "进入等待模式，每 5 秒检测一次；"
                    "只有视频真正播放后才会启动 OBS。"
                )
                waiting_logged = True

            time.sleep(5)

            if not self.task_active:
                break

            status_a, status_b = monitor.detect_once()
            selected = self._playing_site_or_none(
                monitor.choose_site(status_a, status_b)
            )

            now = time.monotonic()
            if now - last_status_log >= 30 and selected is None:
                self._log_waiting_status(status_a, status_b)
                last_status_log = now

            if selected is not None:
                self._safe_log(
                    "检测到可用直播源，继续启动录制流程。"
                )

        if not self.task_active:
            return None

        if selected is None:
            raise RuntimeError(
                "A、B 两个网站当前都没有可用直播源。"
            )

        player_state: PlayerState | None = None
        for restart_attempt in range(self.STARTUP_RESTART_ATTEMPTS + 1):
            try:
                selected, player_state = self.activate_first_ready_source(
                    selected.name,
                    status_a,
                    status_b,
                )
                break
            except PlayerControlError as error:
                if restart_attempt >= self.STARTUP_RESTART_ATTEMPTS:
                    raise PlayerControlError(
                        "首选源和备用源都未达到录制条件，已完成自动重试。\n"
                        f"{error}"
                    ) from error

                self._safe_log(
                    f"播放器启动失败（第 {restart_attempt + 1} 次）：{error}"
                )
                self._safe_log(
                    "将退出并重启项目专用 Chrome，然后重新执行播放器操作……"
                )
                self.browser.restart()
                page_a, page_b = self.open_sites(site_a_url, site_b_url)
                self.prepare_backup_page("A", page_a)
                self.prepare_backup_page("B", page_b)
                status_a = self.probe_source("A", page_a, site_a_url)
                status_b = (
                    self.probe_source("B", page_b, site_b_url)
                    if page_b is not None
                    else SiteStatus(
                        name="B",
                        url="",
                        result=self._unconfigured_result(),
                    )
                )
                monitor = SourceMonitor(
                    page_a=page_a,
                    page_b=page_b,
                    site_a_url=site_a_url,
                    site_b_url=site_b_url,
                    interval=5,
                )
                selected = self._playing_site_or_none(
                    monitor.choose_site(status_a, status_b)
                )
                if selected is None:
                    raise PlayerControlError(
                        "Chrome 重启后仍未检测到真正推进画面的直播源。"
                    ) from error

        if player_state is None:
            raise PlayerControlError(
                "播放器启动失败，未能取得有效播放器状态。"
            )

        # 保留备用页供 SourceMonitor 继续检测；它已被静音/暂停，不能产生第二路声音。
        self.deactivate_source("B" if selected.name == "A" else "A")

        monitor.current_site = selected.name

        return (
            page_a,
            page_b,
            monitor,
            selected,
            status_a,
            status_b,
            player_state,
        )

    @staticmethod
    def _playing_site_or_none(selected: SiteStatus | None) -> SiteStatus | None:
        """启动阶段只接受真正播放中的源，暂停页面继续等待。"""

        if selected is None or not SourceMonitor._is_playing(selected):
            return None
        return selected

    @staticmethod
    def _format_waiting_status(
        status_a: SiteStatus,
        status_b: SiteStatus,
    ) -> str:
        def describe(status: SiteStatus) -> str:
            result = status.result
            resolution = f"{result.video_width}x{result.video_height}"
            details = result.details or "无详细信息"
            return (
                f"{SourceManager.SITE_LABELS.get(status.name, status.name)}："
                f"状态={result.status}，"
                f"播放={result.video_playing}，"
                f"currentTime={result.current_time:.1f}，"
                f"分辨率={resolution}，详情={details}"
            )

        return "\n".join((describe(status_a), describe(status_b)))

    def _log_waiting_status(
        self,
        status_a: SiteStatus,
        status_b: SiteStatus,
        initial: bool = False,
    ) -> None:
        prefix = "初次检测结果：" if initial else "直播源仍未就绪："
        self._safe_log(prefix + "\n" + self._format_waiting_status(status_a, status_b))

    # =========================================================
    # 监控循环
    # =========================================================

    def invalidate_monitor_loop(self) -> None:
        """让之前所有已经排队的监控回调失效。"""

        self.monitor_loop_id += 1
        self.monitor_loop_active = False

    def start_monitor_loop(self) -> None:
        if not self.task_active:
            return

        if self.source_monitor is None:
            return

        # 已经有一个循环在运行时，禁止再次启动
        if self.monitor_loop_active:
            return

        self.monitor_loop_active = True

        # 当前这一轮监控的唯一 ID
        loop_id = self.monitor_loop_id + 1
        self.monitor_loop_id = loop_id

        self._run_monitor_detection(loop_id)

    def _run_monitor_detection(self, loop_id: int) -> None:
        if not self.task_active:
            self.monitor_loop_active = False
            return

        if loop_id != self.monitor_loop_id:
            return

        if self.source_monitor is None:
            self.monitor_loop_active = False
            return

        monitor = self.source_monitor

        def work():
            if self.current_site is not None:
                page = self._page_for_site(self.current_site)
                if page is not None and not page.is_closed():
                    self._stabilize_active_source(self.current_site, page)
            return monitor.detect_once()

        self._run_in_background(
            work,
            lambda result: self._process_monitor_result(
                result,
                loop_id,
            ),
            on_error=lambda error: self._monitor_detection_failed(
                error,
                loop_id,
            ),
        )

    def _monitor_detection_failed(self, error, loop_id: int) -> None:
        if not self.task_active or loop_id != self.monitor_loop_id:
            return

        self._log(f"直播源检测异常：{error}。准备刷新当前源并继续监控。")

        if self.current_site is not None:
            site = self.current_site
            self.monitor_loop_active = False
            self._run_in_background(
                lambda: self.refresh_source(site),
                lambda _: self._schedule_next_monitor(loop_id),
                on_error=lambda err: self._schedule_next_monitor(loop_id),
            )
            return

        self._schedule_next_monitor(loop_id)

    def _process_monitor_result(
        self,
        result,
        loop_id: int,
    ) -> None:
        # 旧监控循环的结果直接丢弃
        if loop_id != self.monitor_loop_id:
            return

        if not self.task_active:
            self.monitor_loop_active = False
            return

        if self.source_monitor is None:
            self.monitor_loop_active = False
            return

        status_a, status_b = result

        statuses = {"A": status_a, "B": status_b}
        current_status = statuses.get(self.current_site)

        if self.current_site is not None:
            if current_status and SourceMonitor._is_playing(current_status):
                self.source_failure_counts[self.current_site] = 0
            else:
                self.source_failure_counts[self.current_site] += 1

                # 先刷新当前页几次，给网络抖动和播放器重连留出时间。
                if self.source_failure_counts[self.current_site] < 3:
                    self._log(
                        f"{self.site_label(self.current_site)}播放异常，"
                        f"第 {self.source_failure_counts[self.current_site]} 次重试并刷新页面。"
                    )
                    site = self.current_site
                    self.monitor_loop_active = False
                    self._run_in_background(
                        lambda: self.refresh_source(site),
                        lambda _: self._schedule_next_monitor(loop_id),
                        on_error=lambda err: self._schedule_next_monitor(loop_id),
                    )
                    return

                alternate = "B" if self.current_site == "A" else "A"
                self._log(
                    f"{self.site_label(self.current_site)}连续恢复失败，"
                    f"重新打开备用{self.site_label(alternate)}。"
                )
                # URL 在主线程取好再传入后台线程，
                # 避免后台线程读取 Tk 变量。
                alternate_url = self._url_for_site(alternate)
                self.monitor_loop_active = False
                self._run_in_background(
                    lambda: self._reopen_and_detect(
                        alternate,
                        alternate_url,
                    ),
                    lambda fresh: self._choose_and_maybe_switch(
                        fresh,
                        loop_id,
                    ),
                    on_error=lambda err: self._schedule_next_monitor(loop_id),
                )
                return

        self._choose_and_maybe_switch(
            (status_a, status_b),
            loop_id,
        )

    def _reopen_and_detect(self, alternate: str, url: str):
        """后台线程：重新打开备用源并重新检测两个源。"""

        self.reopen_source(alternate, url)
        return self.source_monitor.detect_once()

    def _choose_and_maybe_switch(
        self,
        fresh_result,
        loop_id: int,
    ) -> None:
        """主线程：选择最佳源；需要切换时把切换操作放到后台线程。"""

        if not self.task_active or loop_id != self.monitor_loop_id:
            return

        status_a, status_b = fresh_result

        selected = self.source_monitor.choose_site(
            status_a,
            status_b,
        )

        self._log(
            f"咪咕视频：{status_a.result.status} | "
            f"评分：{status_a.result.score} | "
            f"播放：{status_a.result.video_playing}"
        )

        self._log(
            f"海星体育：{status_b.result.status} | "
            f"评分：{status_b.result.score} | "
            f"播放：{status_b.result.video_playing}"
        )

        if selected is None:
            self._log(
                "当前 A/B 均无可用直播源，"
                "保持 OBS 录制。"
            )
            self._schedule_next_monitor(loop_id)
            return

        if selected.name == self.current_site:
            self._log(
                f"当前直播源：{self.site_label(selected.name)}"
            )
            self._schedule_next_monitor(loop_id)
            return

        # 需要切换直播源：切换操作全部放到后台线程执行，
        # 避免页面加载/播放器控制阻塞 GUI。
        old_source = self.current_site
        self.monitor_loop_active = False
        self._run_in_background(
            lambda: self._switch_source_work(
                selected.name,
                old_source,
            ),
            lambda player_state: self._switch_finished(
                player_state,
                selected,
                old_source,
                loop_id,
            ),
            on_error=lambda err: self._monitor_switch_failed(
                err,
                loop_id,
            ),
        )

    def _switch_source_work(self, site: str, old_source: str | None) -> PlayerState:
        """后台线程：停用旧源、激活新源、关闭旧源页面。返回新源状态。"""

        if old_source is not None:
            self.deactivate_source(old_source)

        player_state = self.activate_source(site)

        if old_source is not None:
            # 旧页保留给 SourceMonitor 探测，保持暂停静音即可，不能关闭后继续用失效引用检测。
            self.deactivate_source(old_source)

        return player_state

    def _switch_finished(
        self,
        player_state: PlayerState,
        selected,
        old_source: str | None,
        loop_id: int,
    ) -> None:
        if not self.task_active or loop_id != self.monitor_loop_id:
            self.monitor_loop_active = False
            return

        # 关键：切换后同步 SourceMonitor 内部的当前源记录，
        # 否则 choose_site 会一直以为旧源还是当前源，导致 A/B 来回切换。
        self.current_site = selected.name
        if self.source_monitor is not None:
            self.source_monitor.current_site = selected.name

        if old_source is None:
            self._log(
                f"当前直播源：{self.site_label(selected.name)}"
            )
        else:
            self._log(
                f"直播源发生切换："
                f"{old_source} → {selected.name}"
            )

            self._log(
                f"当前地址：{selected.url}"
            )

            self._log(
                "备用源已激活："
                f"全屏={player_state.fullscreen}，"
                f"静音={player_state.muted}"
            )

        self._schedule_next_monitor(loop_id)

    def _monitor_switch_failed(self, error, loop_id: int) -> None:
        if not self.task_active or loop_id != self.monitor_loop_id:
            self.monitor_loop_active = False
            return

        self._log(
            f"直播源切换失败：{error}。继续监控当前源。"
        )
        self._schedule_next_monitor(loop_id)

    def _schedule_next_monitor(self, loop_id: int) -> None:
        """统一安排下一轮监控检测（只能在主线程调用）。"""

        self.monitor_loop_active = False

        if not self.task_active:
            return

        if loop_id != self.monitor_loop_id:
            return

        self.root.after(
            5000,
            lambda: self._start_monitor_loop_if_valid(
                loop_id
            ),
        )

    def _start_monitor_loop_if_valid(self, loop_id: int) -> None:
        if not self.task_active:
            return

        if loop_id != self.monitor_loop_id:
            return

        if self.monitor_loop_active:
            return

        self.start_monitor_loop()

    # =========================================================
    # 后台任务
    # =========================================================

    def _run_in_background(
        self,
        work,
        on_success,
        on_error=None,
    ) -> None:
        """提交后台任务；结果回主线程处理（监控循环专用，不弹窗）。"""

        future = self.executor.submit(work)

        def process_result() -> None:
            try:
                result = future.result()
            except Exception as error:
                self._safe_log(f"后台任务失败：{error}")

                if on_error is not None:
                    on_error(error)
                return

            on_success(result)

        future.add_done_callback(
            lambda _: self.root.after(
                0,
                process_result,
            )
        )
