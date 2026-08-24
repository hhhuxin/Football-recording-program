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
    PAGE_SETTLE_MS = 1200
    FULLSCREEN_SETTLE_MS = 1500
    PLAYBACK_SETTLE_MS = 1000
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

    def prepare(
        self,
        activate: bool = False,
        quality_fallback_steps: int = 0,
    ) -> PlayerState:
        raise NotImplementedError

    def inspect(self) -> PlayerState:
        raise NotImplementedError

    def _evaluate_video(self, activate: bool, pause: bool = False) -> bool:
        selector = getattr(self, "VIDEO_SELECTOR", "video")
        try:
            return bool(self.page.locator(selector).first.evaluate(
                """
                async (video, args) => {
                    const {activate, pause} = args;
                    const videos = [...document.querySelectorAll('video')];
                    videos.forEach(item => {
                        if (item !== video) {
                            item.pause();
                            item.muted = true;
                            item.volume = 0;
                        }
                    });
                    document.querySelectorAll('audio').forEach(item => {
                        item.pause();
                        item.muted = true;
                        item.volume = 0;
                    });
                    video.volume = activate ? Math.max(video.volume, 0.5) : 0;
                    video.muted = !activate;
                    if (pause) {
                        video.pause();
                        return true;
                    }
                    if (activate && (video.paused || video.ended)) {
                        // 部分咪咕页面的 play() Promise 可能一直 pending。
                        // 这里只发起播放请求，不等待网页 Promise；下面的
                        // _wait_for_playing() 会用明确超时检查真实播放状态。
                        try {
                            const pending = video.play();
                            if (pending && typeof pending.catch === 'function') {
                                pending.catch(() => {});
                            }
                        } catch (_) {
                            return false;
                        }
                    }
                    return !activate || (!video.paused && !video.ended);
                }
                """,
                {"activate": activate, "pause": pause},
                timeout=5000,
            ))
        except Exception:
            # 页面仍在切换广告/播放器实例时，交给点击播放和状态轮询重试。
            return False

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
        self.page.locator(selector).evaluate_all(
            """
            videos => videos.forEach(video => {
                video.pause();
                video.muted = true;
                video.volume = 0;
            })
            """
        )

    def ensure_active(self) -> PlayerState:
        """修复当前源被网页重新静音或暂停后的状态。"""

        return self.prepare(activate=True)

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

    def _is_player_fullscreen(self) -> bool:
        return bool(
            self.page.evaluate(
                """
                selector => {
                    if (document.fullscreenElement) return true;
                    const player = document.querySelector(selector);
                    if (!player) return false;
                    const rect = player.getBoundingClientRect();
                    return rect.width >= innerWidth * .95 &&
                        rect.height >= innerHeight * .90;
                }
                """,
                self.PLAYER_SELECTOR,
            )
        )

    def _normalize_fullscreen_layout(self) -> None:
        """同步播放器容器尺寸，避免全屏后视频只占窗口上半部分。"""

        try:
            self.page.evaluate(
                """
                selector => {
                    const player = document.querySelector(selector);
                    const fullscreen = document.fullscreenElement;
                    if (!player && !fullscreen) return false;

                    const root = fullscreen || player;
                    const important = (element, property, value) =>
                        element.style.setProperty(property, value, 'important');

                    let style = document.getElementById(
                        'obs-match-recorder-fullscreen-layout'
                    );
                    if (!style) {
                        style = document.createElement('style');
                        style.id = 'obs-match-recorder-fullscreen-layout';
                        document.head.appendChild(style);
                    }
                    style.textContent = `
                        html, body {
                            width: 100% !important;
                            height: 100% !important;
                            margin: 0 !important;
                            overflow: hidden !important;
                            background: #000 !important;
                        }
                        #sport-live, #mod-player, #video, #video.dplayer, .dplayer {
                            position: fixed !important;
                            inset: 0 !important;
                            width: 100vw !important;
                            height: 100vh !important;
                            min-width: 100vw !important;
                            min-height: 100vh !important;
                            max-width: none !important;
                            max-height: none !important;
                            margin: 0 !important;
                            padding: 0 !important;
                            overflow: hidden !important;
                        }
                        #mod-player .video-wrapper,
                        #mod-player .mg_player_wrapper,
                        #video .dplayer-video-wrap,
                        #m-player,
                        #video video,
                        .dplayer video {
                            position: absolute !important;
                            inset: 0 !important;
                            width: 100% !important;
                            height: 100% !important;
                            min-width: 100% !important;
                            min-height: 100% !important;
                            max-width: none !important;
                            max-height: none !important;
                        }
                        #m-player, #video video, .dplayer video {
                            object-fit: contain !important;
                            background: #000 !important;
                        }
                        #mod-player .adver-box,
                        #mod-player .video-vr,
                        #mod-player .player-tips,
                        #video .dplayer-controller,
                        #video .dplayer-mask {
                            position: absolute !important;
                            left: 0 !important;
                            right: 0 !important;
                            width: 100% !important;
                        }
                        #mod-player .control-wrapper {
                            bottom: 0 !important;
                        }
                    `;

                    important(root, 'position', 'fixed');
                    important(root, 'inset', '0');
                    important(root, 'width', '100vw');
                    important(root, 'height', '100vh');
                    important(root, 'max-width', 'none');
                    important(root, 'max-height', 'none');
                    important(root, 'overflow', 'hidden');

                    const wrapper = root.querySelector(
                        '.video-wrapper, .video-content, #video'
                    );
                    if (wrapper) {
                        important(wrapper, 'width', '100%');
                        important(wrapper, 'height', '100%');
                    }

                    const video = root.querySelector('video');
                    if (video) {
                        important(video, 'width', '100%');
                        important(video, 'height', '100%');
                        important(video, 'object-fit', 'contain');
                    }

                    important(document.documentElement, 'overflow', 'hidden');
                    important(document.body, 'overflow', 'hidden');
                    window.dispatchEvent(new Event('resize'));
                    return true;
                }
                """,
                self.PLAYER_SELECTOR,
            )
        except Exception:
            pass

    def _close_quality_menu(self) -> None:
        """画质切换完成后关闭菜单，避免遮挡录制画面。"""

        menu = self.page.locator(".ratetype-content")
        if not menu.count():
            return
        try:
            if not menu.first.is_visible(timeout=300):
                return
        except Exception:
            return

        self._click_first([".ratetype"], timeout=800)
        try:
            if menu.first.is_visible(timeout=300):
                self.page.evaluate(
                    """
                    () => document.querySelectorAll('.ratetype-content')
                        .forEach(menu => menu.classList.add('none'))
                    """
                )
        except Exception:
            pass

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
    # 咪咕的“原画/下载 App”可能弹出二维码，固定使用菜单文字为“蓝光”的选项。
    # data-val 只作为旧页面兼容回退，不能单独作为画质判断依据。
    BLUE_QUALITY_FALLBACK_SELECTOR = ".ratetype-content .item-rt[data-val='4']"
    PLAY_SELECTORS = [
        "#mod-player .play-btn",
        ".m-player .play-btn",
        *PlayerAdapter.PLAY_SELECTORS,
    ]

    def prepare(
        self,
        activate: bool = False,
        quality_fallback_steps: int = 0,
    ) -> PlayerState:
        self._wait_for_video(self.VIDEO_SELECTOR)
        video = self.page.locator(self.VIDEO_SELECTOR).first
        if not video.count():
            raise PlayerControlError("咪咕页面没有找到 #m-player 视频。")

        # 咪咕播放器的 video 节点通常先于广告、控制栏和二维码弹窗出现。
        # 给这些异步组件留出时间，避免后续全屏/播放操作连续撞在初始化阶段。
        self.page.wait_for_timeout(self.PAGE_SETTLE_MS)
        self._dismiss_qrcode()
        self._dismiss_ad_overlay()

        if activate:
            # 先进入播放器全屏，再操作画质和播放，避免网页仍在普通布局时
            # 同时触发多个播放器重排。
            if not self._is_player_fullscreen():
                fullscreen_requested = self._click_first(
                    [".zoom-btn .btn-bg[title='全屏']", "[title='全屏']"]
                )
                if not fullscreen_requested:
                    self._request_fullscreen(self.PLAYER_SELECTOR)
                self.page.wait_for_timeout(self.FULLSCREEN_SETTLE_MS)
            self._normalize_fullscreen_layout()
            self._dismiss_qrcode()
            self._dismiss_ad_overlay()
        else:
            self._evaluate_video(False, pause=True)

        quality = self._select_blue_quality(quality_fallback_steps)
        if activate and not quality:
            self._close_quality_menu()
            raise PlayerControlError(
                "咪咕画质菜单中未找到可确认的“蓝光”选项，"
                "为避免误选原画/下载 App，暂不开始录制。"
            )

        self._close_quality_menu()
        self.page.wait_for_timeout(self.PAGE_SETTLE_MS // 2)

        if activate:
            self._activate_video(self.PLAY_SELECTORS)
            self.page.wait_for_timeout(self.PLAYBACK_SETTLE_MS)
            self._dismiss_qrcode()
            self._dismiss_ad_overlay()
            self._normalize_fullscreen_layout()

        return self._state(self.PLAYER_SELECTOR, quality=quality)

    def _select_blue_quality(self, fallback_steps: int = 0) -> str:
        """选择蓝光；多次播放失败后按菜单顺序降一档。"""

        if not self._click_first([".ratetype"]):
            return ""

        items = self.page.locator(".ratetype-content .item-rt")
        visible_items = []
        for index in range(items.count()):
            item = items.nth(index)
            try:
                if item.is_visible(timeout=500):
                    visible_items.append((item, item.inner_text().strip()))
            except Exception:
                continue

        def is_safe_fallback(quality: str) -> bool:
            upper_quality = quality.upper()
            return not any(
                marker in upper_quality
                for marker in ("原画", "4K", "下载", "APP", "智能")
            )

        # 画质内部值可能随页面模板变化，因此先按用户可见的“蓝光”文字匹配。
        blue_index = next(
            (
                index
                for index, (_, quality) in enumerate(visible_items)
                if "蓝光" in quality and is_safe_fallback(quality)
            ),
            None,
        )
        item_and_quality = (
            visible_items[blue_index]
            if blue_index is not None and fallback_steps <= 0
            else None
        )

        # 发生多次播放失败后，从蓝光下方按页面实际排列选择下一档安全画质。
        # 通常会得到“高清 720P”，但不假定所有播放页的 data-val 都相同。
        if blue_index is not None and fallback_steps > 0:
            lower_items = visible_items[blue_index + 1:]
            safe_lower = [
                pair for pair in lower_items if is_safe_fallback(pair[1])
            ]
            if safe_lower:
                item_and_quality = safe_lower[min(fallback_steps - 1, len(safe_lower) - 1)]

        # 兼容已观察到的旧页面，但仍要求其显示文字确实是蓝光，防止
        # data-val="4" 在新页面中被重新定义后误选。
        if item_and_quality is None and fallback_steps <= 0:
            fallback = self.page.locator(self.BLUE_QUALITY_FALLBACK_SELECTOR).first
            for item, quality in visible_items:
                if item.get_attribute("data-val") == "4":
                    fallback = item
                    break
            try:
                if fallback.is_visible(timeout=500):
                    fallback_quality = fallback.inner_text().strip()
                    if "蓝光" in fallback_quality:
                        item_and_quality = (fallback, fallback_quality)
            except Exception:
                pass

        if item_and_quality is None:
            return ""

        item, quality = item_and_quality
        try:
            item.click(timeout=1200, force=True)
            # 给播放器切换码率和更新当前选中标记留出时间。
            self.page.wait_for_timeout(400)
            return quality
        except Exception:
            return ""

    def inspect(self) -> PlayerState:
        if not self.page.locator(self.VIDEO_SELECTOR).count():
            return PlayerState(self.site, False, False, True, False, 0, 0)
        self._dismiss_qrcode()
        self._dismiss_ad_overlay()
        return self._state(self.PLAYER_SELECTOR)

    def _dismiss_qrcode(self) -> bool:
        """关闭咪咕播放器中央的手机客户端下载二维码。"""

        # 当前页面实际使用 .qrcode，旧页面则把它包在
        # .qrcode-container 中。只检查后者会漏掉截图中的弹窗。
        overlays = self.page.locator(".qrcode-container, .qrcode")
        dismissed = False

        for index in range(overlays.count()):
            overlay = overlays.nth(index)
            try:
                if not overlay.is_visible(timeout=300):
                    continue
            except Exception:
                continue

            close_button = overlay.locator(
                ".qr-close, .close, [aria-label='关闭'], [title='关闭']"
            )
            try:
                if close_button.count() and close_button.first.is_visible(timeout=300):
                    close_button.first.click(force=True, timeout=1000)
            except Exception:
                pass

            try:
                if overlay.is_visible(timeout=300):
                    overlay.evaluate(
                        "element => { element.style.display = 'none'; }"
                    )
                dismissed = True
            except Exception:
                continue

        return dismissed

    def _dismiss_ad_overlay(self) -> bool:
        """关闭咪咕播放器首屏广告层，避免它挡住播放按钮。"""

        # adver-box 是咪咕播放器的首屏广告容器；部分页面会额外渲染
        # 一个带 close 类名的广告节点。不要隐藏 player-tips，它包含播放控制。
        selectors = [
            "#mod-player .adver-box .close",
            "#mod-player .adver-box [aria-label='关闭']",
            "#mod-player .adver-box [title='关闭']",
            "#mod-player .ad-close",
            "#mod-player .advert-close",
            "#mod-player [class*='adver'] [class*='close']",
            "#mod-player [class*='advert'] [class*='close']",
        ]
        clicked = self._click_first(selectors, timeout=700)

        try:
            hidden = self.page.evaluate(
                """
                () => {
                    const player = document.querySelector('#mod-player');
                    if (!player) return false;
                    const candidates = [
                        ...player.querySelectorAll(
                            '#adver-box, .adver-box, .ad-close, .advert-close'
                        )
                    ];
                    let changed = false;
                    for (const element of candidates) {
                        const rect = element.getBoundingClientRect();
                        const style = getComputedStyle(element);
                        if (
                            rect.width > 0 &&
                            rect.height > 0 &&
                            style.display !== 'none' &&
                            style.visibility !== 'hidden'
                        ) {
                            element.style.setProperty('display', 'none', 'important');
                            changed = true;
                        }
                    }
                    return changed;
                }
                """
            )
        except Exception:
            hidden = False

        return bool(clicked or hidden)


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

    def prepare(
        self,
        activate: bool = False,
        quality_fallback_steps: int = 0,
    ) -> PlayerState:
        self._wait_for_video(self.VIDEO_SELECTOR)
        video = self.page.locator(self.VIDEO_SELECTOR).first
        if not video.count():
            raise PlayerControlError("飞速页面没有找到 DPlayer 视频。")

        danmaku_disabled = self._disable_danmaku()
        if activate:
            self._dismiss_mute_overlay()
        self._evaluate_video(activate, pause=not activate)

        if activate:
            if not self._is_player_fullscreen():
                fullscreen_requested = self._click_first(
                    ["#video .dplayer-full-icon", ".dplayer-full-icon"]
                )
                if not fullscreen_requested:
                    self._request_fullscreen(self.PLAYER_SELECTOR)
                self.page.wait_for_timeout(250)
            self._normalize_fullscreen_layout()

        if activate:
            self._dismiss_mute_overlay()
            self._activate_video(self.PLAY_SELECTORS)
            self._normalize_fullscreen_layout()

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
