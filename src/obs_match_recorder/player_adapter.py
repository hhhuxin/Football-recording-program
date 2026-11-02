"""咪咕视频和海星体育直播播放器的页面控制。"""

from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import Frame, Page


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

    def _surface(self):
        """返回播放器所在 DOM 表面；普通页面默认就是主页面。"""

        return self.page

    def prepare(
        self,
        activate: bool = False,
        quality_fallback_steps: int = 0,
    ) -> PlayerState:
        raise NotImplementedError

    def inspect(self) -> PlayerState:
        raise NotImplementedError

    def has_active_advertisement(self) -> bool:
        """返回播放器是否处于需要等待的片前广告阶段。"""

        return False

    def expand_advertisement_display(self) -> bool:
        """将片前广告显示区域铺满浏览器视口。"""

        return False

    def _evaluate_video(self, activate: bool, pause: bool = False) -> bool:
        selector = getattr(self, "VIDEO_SELECTOR", "video")
        try:
            surface = self._surface()
            return bool(surface.locator(selector).first.evaluate(
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
            self._surface().wait_for_function(
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
                arg=selector,
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
        surface = self._surface()
        if not surface.locator(selector).count():
            return
        surface.locator(selector).evaluate_all(
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
            self._surface().wait_for_selector(
                selector,
                state="attached",
                timeout=timeout,
            )
            return True
        except Exception:
            return bool(self._surface().locator(selector).count())

    def _click_first(self, selectors: list[str], timeout: int = 1200) -> bool:
        surface = self._surface()
        for selector in selectors:
            try:
                locator = surface.locator(selector)
                for index in range(locator.count()):
                    candidate = locator.nth(index)
                    if candidate.is_visible(timeout=timeout):
                        candidate.click(timeout=timeout, force=True)
                        return True
            except Exception:
                continue
        return False

    def _request_fullscreen(self, selector: str) -> None:
        self._surface().evaluate(
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
            self._surface().evaluate(
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

    def _is_native_fullscreen(self) -> bool:
        """仅检查页面是否已进入浏览器原生全屏。"""

        return bool(self._surface().evaluate("() => Boolean(document.fullscreenElement)"))

    def _normalize_fullscreen_layout(self) -> None:
        """同步播放器容器尺寸，避免全屏后视频只占窗口上半部分。"""

        try:
            self._surface().evaluate(
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
        surface = self._surface()
        data = surface.locator(video_selector).first.evaluate(
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
    FULLSCREEN_SELECTORS = [
        ".zoom-btn .btn-bg[title='全屏']",
        "#mod-player [title='全屏']",
        "#mod-player [aria-label='全屏']",
        "#mod-player [class*='fullscreen']",
        "#mod-player [class*='full-screen']",
        "#mod-player [class*='fullScreen']",
    ]
    # 咪咕的“原画/下载 App”可能弹出二维码，固定使用菜单文字为“蓝光”的选项。
    # data-val 只作为旧页面兼容回退，不能单独作为画质判断依据。
    BLUE_QUALITY_FALLBACK_SELECTOR = ".ratetype-content .item-rt[data-val='4']"
    PLAY_SELECTORS = [
        "#mod-player .play-btn",
        ".m-player .play-btn",
        *PlayerAdapter.PLAY_SELECTORS,
    ]

    def _is_migu_fullscreen_display(self) -> bool:
        """确认咪咕画面实际盖住页面，不把带导航的伪全屏当作成功。"""

        try:
            return bool(
                self.page.evaluate(
                    """
                    selector => {
                        if (document.fullscreenElement) return true;
                        const player = document.querySelector(selector);
                        if (!player) return false;
                        const rect = player.getBoundingClientRect();
                        return rect.left <= 2 && rect.top <= 2 &&
                            rect.right >= innerWidth - 2 &&
                            rect.bottom >= innerHeight - 2;
                    }
                    """,
                    self.PLAYER_SELECTOR,
                )
            )
        except Exception:
            return False

    def _enter_migu_fullscreen(self) -> bool:
        """点击咪咕右下角全屏图标，选择器失效时使用播放器内坐标兜底。"""

        if self._is_migu_fullscreen_display():
            return True

        clicked = self._click_first(self.FULLSCREEN_SELECTORS)
        if clicked:
            self.page.wait_for_timeout(300)
        if self._is_migu_fullscreen_display():
            return True

        try:
            player = self.page.locator(self.PLAYER_SELECTOR).first
            box = player.bounding_box()
            if box and box["width"] >= 100 and box["height"] >= 100:
                # 咪咕全屏图标固定在控制栏最右侧。先移动以唤出控制栏，
                # 再点击距播放器右、下边缘约 36、28 像素的图标中心。
                x = box["x"] + box["width"] - 36
                y = box["y"] + box["height"] - 28
                self.page.mouse.move(x, y)
                self.page.wait_for_timeout(150)
                self.page.mouse.click(x, y)
                self.page.wait_for_timeout(self.FULLSCREEN_SETTLE_MS)
        except Exception:
            pass

        if self._is_migu_fullscreen_display():
            return True

        self._request_fullscreen(self.PLAYER_SELECTOR)
        self.page.wait_for_timeout(self.FULLSCREEN_SETTLE_MS)
        return self._is_migu_fullscreen_display()

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
        if self.has_active_advertisement():
            # 广告播放期间不能隐藏广告层、切画质或触发主视频播放。
            # SourceManager 会负责等待广告结束后重新进入准备流程。
            return self._state(self.PLAYER_SELECTOR)
        self._dismiss_qrcode()
        self._dismiss_ad_overlay()
        self._claim_match_pass()
        self._dismiss_membership_popup()

        if activate:
            # 先进入播放器全屏，再操作画质和播放，避免网页仍在普通布局时
            # 同时触发多个播放器重排。
            # 页面内播放器即使几乎占满视口，上方导航和下方控件仍会被录入。
            # 因此咪咕必须以原生全屏状态为准，确保会点击右下角全屏图标。
            if not self._is_migu_fullscreen_display():
                self._enter_migu_fullscreen()
            self._normalize_fullscreen_layout()
            self._dismiss_qrcode()
            self._dismiss_ad_overlay()
            self._claim_match_pass()
            self._dismiss_membership_popup()
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
            self._claim_match_pass()
            self._dismiss_membership_popup()
            self._normalize_fullscreen_layout()

        return self._state(self.PLAYER_SELECTOR, quality=quality)

    def _select_blue_quality(self, fallback_steps: int = 0) -> str:
        """依次选择原画、蓝光和高清 720P。"""

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

        def is_selectable_original(quality: str) -> bool:
            upper_quality = quality.upper()
            return "原画" in quality and not any(
                marker in upper_quality
                for marker in ("下载", "APP", "智能")
            )

        # 原画为用户确认的首选；仍只按用户可见的菜单文字识别，避免
        # 把同名的下载 App/二维码入口误认为播放画质。
        original_index = next(
            (
                index
                for index, (_, quality) in enumerate(visible_items)
                if is_selectable_original(quality)
            ),
            None,
        )

        # 原画不可用时才选择蓝光。
        blue_index = next(
            (
                index
                for index, (_, quality) in enumerate(visible_items)
                if "蓝光" in quality and is_safe_fallback(quality)
            ),
            None,
        )
        original = (
            visible_items[original_index]
            if original_index is not None
            else None
        )
        blue = (
            visible_items[blue_index]
            if blue_index is not None
            else None
        )
        # 启动流程以三轮明确执行“原画 -> 蓝光 -> 高清 720P”。第一轮
        # 若原画不存在才直接尝试蓝光；第二轮不会再次选择原画，避免原画
        # 一次拉流不稳定时跳过蓝光而直接降到 720P。
        item_and_quality = (
            (original or blue) if fallback_steps <= 0
            else blue if fallback_steps == 1
            else None
        )

        # 免费场次通常不显示原画或蓝光。此时直接选择 720P，不把缺少
        # 高画质当作播放器失败，也不因此触发页面刷新重试。
        hd_720 = next(
            (
                pair for pair in visible_items
                if "高清" in pair[1] and "720" in pair[1]
                and is_safe_fallback(pair[1])
            ),
            None,
        )
        if item_and_quality is None and hd_720 is not None:
            item_and_quality = hd_720

        # 原画或蓝光未能播放时，优先选择明确标有“高清 720P”的安全画质。
        # 不同页面的菜单顺序可能变化，因此不能只假定蓝光下方第一项就是 720P。
        if fallback_steps >= 2:
            lower_start = (
                blue_index if blue_index is not None
                else original_index if original_index is not None
                else -1
            )
            lower_items = visible_items[lower_start + 1:]
            safe_lower = [
                pair for pair in lower_items if is_safe_fallback(pair[1])
            ]
            lower_hd_720 = next(
                (
                    pair for pair in safe_lower
                    if "高清" in pair[1] and "720" in pair[1]
                ),
                None,
            )
            if hd_720 is not None:
                item_and_quality = hd_720
            elif lower_hd_720 is not None:
                item_and_quality = lower_hd_720
            elif item_and_quality is None and safe_lower:
                item_and_quality = safe_lower[
                    min(fallback_steps - 1, len(safe_lower) - 1)
                ]

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
        self._claim_match_pass()
        self._dismiss_membership_popup()
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
        """保留咪咕片前广告，避免广告尚未播完就误判直播不可用。"""

        return False

    @staticmethod
    def _advertisement_visibility_script() -> str:
        """返回检测咪咕可见片前广告容器的页面脚本。"""

        return r"""
            () => {
                const candidates = document.querySelectorAll(
                    '#mod-player #adver-box, #mod-player .adver-box, #adver-box, .adver-box'
                );
                return [...candidates].some(element => {
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0 &&
                        style.display !== 'none' && style.visibility !== 'hidden';
                });
            }
        """

    def has_active_advertisement(self) -> bool:
        """识别咪咕正在显示的片前广告容器。"""

        try:
            return bool(
                self.page.evaluate(self._advertisement_visibility_script())
            )
        except Exception:
            return False

    def expand_advertisement_display(self) -> bool:
        """用咪咕全屏按钮扩展广告显示，不触发播放、画质或音量操作。"""

        try:
            if not self._is_migu_fullscreen_display():
                self._enter_migu_fullscreen()
            self._normalize_fullscreen_layout()
            return self._is_migu_fullscreen_display()
        except Exception:
            return False

    @staticmethod
    def _membership_popup_dismissal_script() -> str:
        """返回仅关闭普通咪咕会员续费弹窗的页面脚本。"""

        return r"""
            () => {
                const popupSelectors = [
                    '.member-order-container',
                    '[role="dialog"]', '.modal', '.dialog', '.popup',
                    '[class*="modal"]', '[class*="dialog"]', '[class*="popup"]'
                ];
                const closeSelector = [
                    '[aria-label="关闭"]', '[title="关闭"]', '.close',
                    '.icon-close', '[class*="close"]'
                ].join(', ');
                const membershipMarkers = [
                    '足球通', '会员', '续费', '连续包月', '赛季包'
                ];
                const candidates = [
                    ...new Set(popupSelectors.flatMap(selector =>
                        [...document.querySelectorAll(selector)]
                    ))
                ];

                for (const popup of candidates) {
                    const style = getComputedStyle(popup);
                    const rect = popup.getBoundingClientRect();
                    const text = (popup.innerText || '').replace(/\s+/g, '');
                    if (
                        rect.width < 200 || rect.height < 120 ||
                        style.display === 'none' || style.visibility === 'hidden' ||
                        text.includes('通看全场') || text.includes('使用5张券') ||
                        !membershipMarkers.some(marker => text.includes(marker))
                    ) {
                        continue;
                    }

                    const closeButton = popup.querySelector(closeSelector);
                    if (closeButton instanceof HTMLElement) {
                        closeButton.click();
                    }
                    // 咪咕的 .member-order-container 由 Vue 控制；即使关闭
                    // 回调没有及时完成，也要立即移除遮挡层，避免录到会员页。
                    popup.style.setProperty('display', 'none', 'important');
                    return true;
                }
                return false;
            }
        """

    def _dismiss_membership_popup(self) -> bool:
        """关闭咪咕页面中的普通足球会员续费模态框。"""

        try:
            return bool(
                self.page.evaluate(self._membership_popup_dismissal_script())
            )
        except Exception:
            return False

    @staticmethod
    def _match_pass_claim_script() -> str:
        """返回仅点击咪咕“使用 5 张券”通看按钮的页面脚本。"""

        return r"""
            () => {
                const normalize = value => (value || '').replace(/\s+/g, '');
                const popupSelectors = [
                    '.member-order-container',
                    '[role="dialog"]', '.modal', '.dialog', '.popup',
                    '[class*="modal"]', '[class*="dialog"]', '[class*="popup"]'
                ];
                const visible = element => {
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0 &&
                        style.display !== 'none' && style.visibility !== 'hidden';
                };
                const candidates = [
                    ...new Set(popupSelectors.flatMap(selector =>
                        [...document.querySelectorAll(selector)]
                    ))
                ];

                for (const popup of candidates) {
                    const popupText = normalize(popup.innerText);
                    if (
                        !visible(popup) ||
                        !popupText.includes('通看全场') ||
                        !popupText.includes('使用5张券')
                    ) {
                        continue;
                    }

                    const actions = popup.querySelectorAll(
                        'button, [role="button"], a, input[type="button"], input[type="submit"], div, span'
                    );
                    for (const action of actions) {
                        const actionText = normalize(
                            action.innerText || action.value || action.getAttribute('aria-label')
                        );
                        if (
                            actionText === '使用5张券' &&
                            visible(action) &&
                            !action.hasAttribute('disabled') &&
                            action.getAttribute('aria-disabled') !== 'true'
                        ) {
                            action.click();
                            return true;
                        }
                    }
                }
                return false;
            }
        """

    def _claim_match_pass(self) -> bool:
        """确认咪咕通看券弹窗后使用用户授权的 5 张券。"""

        try:
            clicked = bool(self.page.evaluate(self._match_pass_claim_script()))
            if clicked:
                self.page.wait_for_timeout(1000)
            return clicked
        except Exception:
            return False


class HaixingPlayerAdapter(PlayerAdapter):
    """海星体育页面内跨域 iframe 中的 DPlayer 控制器。"""

    site = "B"
    VIDEO_SELECTOR = "video.dplayer-video-current, video"
    PLAYER_SELECTOR = "#dplayer, .dplayer"
    PLAY_SELECTORS = [
        ".dplayer-play-icon",
        ".dplayer-toggle",
        *PlayerAdapter.PLAY_SELECTORS,
    ]
    QUALITY_SELECTORS = [
        ".dplayer-quality-item",
        ".dplayer-quality-list [data-quality]",
    ]

    @staticmethod
    def frame_for_page(page: Page):
        """动态找到海星播放器 iframe，兼容 iframe 刷新后引用变化。"""

        for frame in page.frames:
            if frame is page.main_frame:
                continue
            if "video.club7275.top" in frame.url:
                return frame
            try:
                if frame.locator("#dplayer, video").count():
                    return frame
            except Exception:
                continue
        return page

    def _surface(self):
        return self.frame_for_page(self.page)

    def _disable_danmaku(self) -> bool:
        """关闭实际弹幕开关，并持续隐藏播放器重建的弹幕层。"""
        try:
            return bool(self._surface().evaluate(
                """
                () => {
                    const player = document.querySelector('#dplayer, .dplayer');
                    if (!player) return false;
                    const button = player.querySelector('.dplayer-danmaku-controller-icon');
                    if (button?.classList.contains('active')) button.click();
                    const checkbox = player.querySelector('.dplayer-showdan-setting-input');
                    if (checkbox?.checked) checkbox.click();
                    let style = document.getElementById('recorder-hide-danmaku');
                    if (!style) {
                        style = document.createElement('style');
                        style.id = 'recorder-hide-danmaku';
                        document.head.appendChild(style);
                    }
                    style.textContent = `
                        .dplayer-danmaku, .dplayer-danmaku-item, .dplayer-comment {
                            display: none !important;
                            visibility: hidden !important;
                        }
                    `;
                    player.classList.add('dplayer-no-danmaku');
                    return !button?.classList.contains('active') && !checkbox?.checked &&
                        [...player.querySelectorAll('.dplayer-danmaku')].every(
                            node => getComputedStyle(node).display === 'none'
                        );
                }
                """
            ))
        except Exception:
            return False

    def _dismiss_mute_overlay(self) -> bool:
        if not self._surface().locator(self.VIDEO_SELECTOR).first.evaluate(
            "video => video.muted || video.volume === 0"
        ):
            return True
        return self._click_first(
            [
                ".dplayer-videomuted-warp",
                ".dplayer-videomuted-horn",
                "text=取消静音",
                "text=点击取消静音",
                ".dplayer-volume-icon",
            ],
            timeout=900,
        )

    def _select_quality(self, fallback_steps: int = 0) -> str:
        if not self._click_first([".dplayer-quality-icon"], timeout=1500):
            return ""

        items = []
        for selector in self.QUALITY_SELECTORS:
            locator = self._surface().locator(selector)
            for index in range(locator.count()):
                item = locator.nth(index)
                try:
                    if item.is_visible(timeout=500):
                        text = item.inner_text().strip()
                        if text and all(existing[0] != item for existing in items):
                            items.append((item, text))
                except Exception:
                    continue

        quality_tiers = (
            ("原画质", "原画"),
            ("蓝光",),
            ("高清",),
            ("标清",),
            ("流畅",),
        )
        selected = None
        for tier in quality_tiers[min(fallback_steps, len(quality_tiers) - 1):]:
            selected = next(
                (
                    (item, text)
                    for item, text in items
                    if any(wanted in text for wanted in tier)
                ),
                None,
            )
            if selected:
                break
        if selected is None:
            return ""

        item, text = selected
        try:
            item.click(force=True, timeout=1500)
            self._surface().wait_for_function(
                "wanted => document.querySelector('.dplayer-quality-icon')?.textContent.trim() === wanted",
                arg=text,
                timeout=5000,
            )
            return self._surface().locator('.dplayer-quality-icon').first.inner_text().strip()
        except Exception:
            # 未登录时网站会拒绝切换；必须返回实际画质，不能把点击项当作结果。
            return self._surface().locator('.dplayer-quality-icon').first.inner_text().strip()

    def _recover_after_quality_switch(self, quality_fallback_steps: int) -> bool:
        """画质切换造成暂停时，先用真实播放操作恢复，不立即刷新。"""

        for attempt in range(2):
            self._dismiss_mute_overlay()
            self._activate_video(self.PLAY_SELECTORS)
            if self._wait_for_playing(timeout=3500):
                return True
            if attempt == 0:
                # DPlayer 切码率后偶尔只更新了菜单状态，重复点当前画质
                # 让播放器重新挂载媒体，再次点击播放。
                self._select_quality(quality_fallback_steps)
        return False

    def prepare(
        self,
        activate: bool = False,
        quality_fallback_steps: int = 0,
    ) -> PlayerState:
        self._wait_for_video(self.VIDEO_SELECTOR, timeout=15000)
        if not self._surface().locator(self.VIDEO_SELECTOR).count():
            raise PlayerControlError("海星体育页面没有找到 DPlayer 视频。")

        danmaku_disabled = self._disable_danmaku()
        if activate:
            self._dismiss_mute_overlay()
        self._evaluate_video(activate, pause=not activate)

        if activate:
            quality = self._select_quality(quality_fallback_steps)
            if not quality:
                raise PlayerControlError("海星体育画质菜单中未找到可选画质。")

            self._recover_after_quality_switch(quality_fallback_steps)
            # 切换画质可能重建播放器，进入全屏前再次关闭弹幕并验证。
            danmaku_disabled = self._disable_danmaku()
            if not danmaku_disabled:
                raise PlayerControlError("海星体育弹幕未能关闭，暂不进入全屏。")
            if not self._is_native_fullscreen():
                self._click_first(
                    ["#fullSpan", ".dplayer-full-icon[aria-label='全屏']"]
                )
                self._surface().wait_for_timeout(500)
                if not self._is_native_fullscreen():
                    self._request_fullscreen(self.PLAYER_SELECTOR)
                self._surface().wait_for_timeout(500)
            if not self._is_native_fullscreen():
                raise PlayerControlError("海星体育未能进入全屏，请检查浏览器全屏权限。")
            self._normalize_fullscreen_layout()
            self._dismiss_mute_overlay()
            self._evaluate_video(True)
            self._normalize_fullscreen_layout()
        else:
            quality = ""

        return self._state(
            self.PLAYER_SELECTOR,
            quality=quality,
            danmaku_disabled=danmaku_disabled,
        )

    def inspect(self) -> PlayerState:
        surface = self._surface()
        if not surface.locator(self.VIDEO_SELECTOR).count():
            return PlayerState(self.site, False, False, True, False, 0, 0)
        return self._state(
            self.PLAYER_SELECTOR,
            quality=surface.locator('.dplayer-quality-icon').first.inner_text().strip()
            if surface.locator('.dplayer-quality-icon').count() else "",
            danmaku_disabled=bool(
                surface.locator("#dplayer.dplayer-no-danmaku, .dplayer-no-danmaku").count()
            ),
        )


# 保留旧名称，避免外部脚本导入时破坏兼容性。
FeisuPlayerAdapter = HaixingPlayerAdapter


def adapter_for(site: str, page: Page) -> PlayerAdapter:
    if site.upper() == "A":
        return MiguPlayerAdapter(page)
    if site.upper() == "B":
        return HaixingPlayerAdapter(page)
    raise ValueError(f"不支持的网站：{site}")
