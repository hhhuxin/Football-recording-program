"""咪咕和飞速直播播放器的页面控制。"""

from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import Page


class PlayerControlError(RuntimeError):
    """播放器不存在或无法执行必要控制。"""


@dataclass(frozen=True)
class PlayerState:
    site: str
    video_exists: bool
    playing: bool
    muted: bool
    fullscreen: bool
    player_width: int
    player_height: int
    quality: str = ""
    danmaku_disabled: bool = False

    @property
    def ready_for_recording(self) -> bool:
        return (
            self.video_exists
            and self.playing
            and not self.muted
            and self.fullscreen
            and self.player_width > 0
            and self.player_height > 0
        )


class PlayerAdapter:
    site = "?"
    PLAY_SELECTORS: list[str] = [
        "[aria-label='播放']",
        "[aria-label='Play']",
        "[title='播放']",
        "[title='Play']",
        ".play-btn",
        ".play-button",
    ]

    def __init__(self, page: Page) -> None:
        self.page = page

    def prepare(self, activate: bool = False) -> PlayerState:
        raise NotImplementedError

    def inspect(self) -> PlayerState:
        raise NotImplementedError

    def _evaluate_video(self, activate: bool, pause: bool = False) -> bool:
        selector = getattr(self, "VIDEO_SELECTOR", "video")
        return bool(self.page.locator(selector).first.evaluate(
            """
            async (video, args) => {
                const {activate, pause} = args;
                video.volume = activate ? 1 : 0;
                video.muted = !activate;
                if (pause) {
                    video.pause();
                    return true;
                }
                if (activate && (video.paused || video.ended)) {
                    try {
                        await video.play();
                    } catch (_) {
                        return false;
                    }
                }
                return !activate || (!video.paused && !video.ended);
            }
            """,
            {"activate": activate, "pause": pause},
        ))

    def _wait_for_playing(self, timeout: int = 5000) -> bool:
        selector = getattr(self, "VIDEO_SELECTOR", "video")
        try:
            self.page.wait_for_function(
                """
                selector => {
                    const video = document.querySelector(selector);
                    return Boolean(
                        video &&
                        !video.paused &&
                        !video.ended &&
                        video.readyState >= 2
                    );
                }
                """,
                selector,
                timeout=timeout,
            )
            return True
        except Exception:
            return False

    def _activate_video(self, play_selectors: list[str] | None = None) -> bool:
        if self._evaluate_video(True):
            return self._wait_for_playing()

        # 未静音的 video 可能被浏览器自动播放策略拦截；可见播放按钮的
        # Playwright 点击带有真实用户输入语义，再重试脚本播放。
        selectors = play_selectors or self.PLAY_SELECTORS
        if self._click_first(selectors):
            self._evaluate_video(True)

        return self._wait_for_playing()

    def deactivate(self) -> None:
        selector = getattr(self, "VIDEO_SELECTOR", "video")
        if not self.page.locator(selector).count():
            return
        self._evaluate_video(False, pause=True)

    def _wait_for_video(self, selector: str, timeout: int = 15000) -> bool:
        try:
            self.page.wait_for_selector(
                selector,
                state="attached",
                timeout=timeout,
            )
            return True
        except Exception:
            return bool(self.page.locator(selector).count())

    def _click_first(self, selectors: list[str], timeout: int = 1200) -> bool:
        for selector in selectors:
            try:
                locator = self.page.locator(selector)
                for index in range(locator.count()):
                    candidate = locator.nth(index)
                    if candidate.is_visible(timeout=timeout):
                        candidate.click(timeout=timeout, force=True)
                        return True
            except Exception:
                continue
        return False

    def _request_fullscreen(self, selector: str) -> None:
        self.page.evaluate(
            """
            selector => {
                const target = document.querySelector(selector);
                if (!target) return false;
                if (document.fullscreenElement) return true;
                const request = target.requestFullscreen || target.webkitRequestFullscreen;
                if (!request) return false;
                return Promise.resolve(request.call(target)).then(() => true).catch(() => false);
            }
            """,
            selector,
        )

    def _state(self, selector: str, quality: str = "", danmaku_disabled: bool = False) -> PlayerState:
        video_selector = getattr(self, "VIDEO_SELECTOR", "video")
        data = self.page.locator(video_selector).first.evaluate(
            """
            (video, selector) => {
                const player = document.querySelector(selector) || video.parentElement;
                const rect = player.getBoundingClientRect();
                const viewport = {width: window.innerWidth, height: window.innerHeight};
                const fullscreen = Boolean(document.fullscreenElement) ||
                    (rect.width >= viewport.width * 0.95 && rect.height >= viewport.height * 0.90);
                return {
                    exists: true,
                    playing: !video.paused && !video.ended && video.readyState >= 2,
                    muted: Boolean(video.muted) || video.volume === 0,
                    fullscreen,
                    width: Math.round(rect.width),
                    height: Math.round(rect.height)
                };
            }
            """,
            selector,
        )
        return PlayerState(
            site=self.site,
            video_exists=True,
            playing=bool(data.get("playing")),
            muted=bool(data.get("muted")),
            fullscreen=bool(data.get("fullscreen")),
            player_width=int(data.get("width", 0)),
            player_height=int(data.get("height", 0)),
            quality=quality,
            danmaku_disabled=danmaku_disabled,
        )


class MiguPlayerAdapter(PlayerAdapter):
    site = "A"

    VIDEO_SELECTOR = "#m-player, video.m-player, video"
    PLAYER_SELECTOR = "#mod-player, .video-wrapper, #sport-live"
    PLAY_SELECTORS = [
        "#mod-player .play-btn",
        ".m-player .play-btn",
        *PlayerAdapter.PLAY_SELECTORS,
    ]

    def prepare(self, activate: bool = False) -> PlayerState:
        self._wait_for_video(self.VIDEO_SELECTOR)
        video = self.page.locator(self.VIDEO_SELECTOR).first
        if not video.count():
            raise PlayerControlError("咪咕页面没有找到 #m-player 视频。")

        self._evaluate_video(activate, pause=not activate)

        # 参考页面的控制结构：画质菜单中第一个可见项是当前账号可选择的最高档。
        quality = ""
        if self._click_first([".ratetype"]):
            quality_items = self.page.locator(".ratetype-content .item-rt")
            for index in range(quality_items.count()):
                item = quality_items.nth(index)
                try:
                    if item.is_visible(timeout=500):
                        quality = item.inner_text().strip()
                        item.click(timeout=1200, force=True)
                        break
                except Exception:
                    continue

        if activate:
            fullscreen_requested = self._click_first(
                [".zoom-btn", ".zoom-btn .btn-bg", "[title='全屏']"]
            )
            if not fullscreen_requested:
                self._request_fullscreen(self.PLAYER_SELECTOR)

        if activate:
            self._activate_video(self.PLAY_SELECTORS)

        return self._state(self.PLAYER_SELECTOR, quality=quality)

    def inspect(self) -> PlayerState:
        if not self.page.locator(self.VIDEO_SELECTOR).count():
            return PlayerState(self.site, False, False, True, False, 0, 0)
        return self._state(self.PLAYER_SELECTOR)


class FeisuPlayerAdapter(PlayerAdapter):
    site = "B"

    VIDEO_SELECTOR = "#video video, video.dplayer-video-current, video"
    PLAYER_SELECTOR = "#video, .dplayer"
    PLAY_SELECTORS = [
        ".dplayer-play-icon",
        ".dplayer-toggle",
        *PlayerAdapter.PLAY_SELECTORS,
    ]

    def _disable_danmaku(self) -> bool:
        checkbox = self.page.locator(".dplayer-showdan-setting-input").first
        try:
            if checkbox.count() and checkbox.is_checked():
                checkbox.click(force=True)
        except Exception:
            pass

        # 参考页使用 dplayer-no-danmaku 控制弹幕层；补上该状态，避免
        # 页面刷新后仅复位了复选框而弹幕画布仍继续显示。
        try:
            self.page.evaluate(
                """
                () => {
                    const player = document.querySelector('#video, .dplayer');
                    if (!player) return false;
                    player.classList.add('dplayer-no-danmaku');
                    player.querySelectorAll('.dplayer-danmaku, .dplayer-comment')
                        .forEach(node => node.style.display = 'none');
                    return true;
                }
                """
            )
        except Exception:
            pass

        return bool(
            self.page.locator("#video.dplayer-no-danmaku, .dplayer-no-danmaku").count()
        )

    def _dismiss_mute_overlay(self) -> None:
        self._click_first(
            [
                "text=取消静音",
                "text=点击取消静音",
                ".novouceFlag",
                ".dplayer-volume-icon",
            ],
            timeout=700,
        )

    def prepare(self, activate: bool = False) -> PlayerState:
        self._wait_for_video(self.VIDEO_SELECTOR)
        video = self.page.locator(self.VIDEO_SELECTOR).first
        if not video.count():
            raise PlayerControlError("飞速页面没有找到 DPlayer 视频。")

        danmaku_disabled = self._disable_danmaku()
        if activate:
            self._dismiss_mute_overlay()
        self._evaluate_video(activate, pause=not activate)

        if activate:
            fullscreen_requested = self._click_first(
                ["#video .dplayer-full-icon", ".dplayer-full-icon"]
            )
            if not fullscreen_requested:
                self._request_fullscreen(self.PLAYER_SELECTOR)

        if activate:
            self._dismiss_mute_overlay()
            self._activate_video(self.PLAY_SELECTORS)

        return self._state(
            self.PLAYER_SELECTOR,
            danmaku_disabled=danmaku_disabled,
        )

    def inspect(self) -> PlayerState:
        if not self.page.locator(self.VIDEO_SELECTOR).count():
            return PlayerState(self.site, False, False, True, False, 0, 0)
        return self._state(
            self.PLAYER_SELECTOR,
            danmaku_disabled=bool(
                self.page.locator("#video.dplayer-no-danmaku, .dplayer-no-danmaku").count()
            ),
        )


def adapter_for(site: str, page: Page) -> PlayerAdapter:
    if site.upper() == "A":
        return MiguPlayerAdapter(page)
    if site.upper() == "B":
        return FeisuPlayerAdapter(page)
    raise ValueError(f"不支持的网站：{site}")
