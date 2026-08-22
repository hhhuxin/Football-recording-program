"""直播网站通用检测适配器。

V0.3：
- 检查页面是否正常
- 检查 HTML5 Video
- 检查直播相关文字
- 检查视频是否正在播放
- 获取 currentTime / 分辨率等信息

本模块暂时不负责：
- 点击播放
- 修改画质
- 全屏
- OBS 控制
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Page


@dataclass
class VideoStatus:
    """HTML5 Video 当前状态。"""

    exists: bool = False
    paused: bool = True
    ready_state: int = 0
    current_time: float = 0.0
    duration: float = 0.0
    video_width: int = 0
    video_height: int = 0

    @property
    def playing(self) -> bool:
        """判断视频是否处于基本播放状态。"""

        return (
            self.exists
            and not self.paused
            and self.ready_state >= 2
        )


@dataclass
class LiveCheckResult:
    """一次直播检测的结果。"""

    page_loaded: bool = False
    player_found: bool = False
    live_text_found: bool = False
    error_detected: bool = False

    video_exists: bool = False
    video_playing: bool = False

    current_time: float = 0.0
    video_width: int = 0
    video_height: int = 0

    score: int = 0
    status: str = "未知"

    detail: str = ""


class SiteAdapter:
    """通用直播网站适配器。

    V0.3 不针对任何特定网站写死结构。
    后续可以继承本类，为网站 A/B 分别实现专用规则。
    """

    LIVE_KEYWORDS = [
        "直播中",
        "正在直播",
        "直播",
        "live",
        "live now",
        "on air",
        "streaming",
    ]

    ERROR_KEYWORDS = [
        "播放失败",
        "无法播放",
        "视频加载失败",
        "加载失败",
        "播放错误",
        "网络错误",
        "network error",
        "playback error",
        "video unavailable",
        "stream unavailable",
    ]

    def __init__(self, page: Page, site_name: str = "网站") -> None:
        self.page = page
        self.site_name = site_name

    def check(self) -> LiveCheckResult:
        """执行一次综合直播状态检测。"""

        result = LiveCheckResult()

        # --------------------------------------------------
        # 1. 页面状态
        # --------------------------------------------------
        try:
            if self.page.is_closed():
                result.detail = "浏览器页面已经关闭。"
                result.status = "页面关闭"
                return result

            result.page_loaded = True

        except Exception as error:
            result.detail = f"页面检测失败：{error}"
            result.status = "页面异常"
            return result

        # 页面正常加载 +1
        result.score += 1

        # --------------------------------------------------
        # 2. 获取页面文字
        # --------------------------------------------------
        try:
            body_text = self.page.locator("body").inner_text(timeout=3000)
            body_text_lower = body_text.lower()
        except Exception:
            body_text_lower = ""

        # --------------------------------------------------
        # 3. 检查直播关键词
        # --------------------------------------------------
        for keyword in self.LIVE_KEYWORDS:
            if keyword.lower() in body_text_lower:
                result.live_text_found = True
                result.score += 2
                break

        # --------------------------------------------------
        # 4. 检查错误关键词
        # --------------------------------------------------
        for keyword in self.ERROR_KEYWORDS:
            if keyword.lower() in body_text_lower:
                result.error_detected = True
                break

        # --------------------------------------------------
        # 5. 查找 HTML5 Video
        # --------------------------------------------------
        video_status = self.get_video_status()

        result.video_exists = video_status.exists

        if video_status.exists:
            result.player_found = True
            result.score += 2

            result.current_time = video_status.current_time
            result.video_width = video_status.video_width
            result.video_height = video_status.video_height

            if video_status.playing:
                result.video_playing = True
                result.score += 3

        # --------------------------------------------------
        # 6. 正常分辨率
        # --------------------------------------------------
        if result.video_width > 0 and result.video_height > 0:
            result.score += 1

        # --------------------------------------------------
        # 7. 最终状态
        # --------------------------------------------------
        if result.error_detected:
            result.status = "播放错误"

        elif result.video_playing:
            result.status = "正在播放"

        elif result.live_text_found and result.video_exists:
            result.status = "检测到直播"

        elif result.video_exists:
            result.status = "发现播放器"

        elif result.live_text_found:
            result.status = "疑似直播"

        else:
            result.status = "未检测到直播"

        result.detail = self._build_detail(result)

        return result

    def get_video_status(self) -> VideoStatus:
        """读取页面第一个 HTML5 Video 的状态。"""

        if self.page.is_closed():
            return VideoStatus()

        try:
            data: dict[str, Any] | None = self.page.locator("video").first.evaluate(
                """
                video => ({
                    exists: true,
                    paused: video.paused,
                    readyState: video.readyState,
                    currentTime: Number(video.currentTime || 0),
                    duration: Number(video.duration || 0),
                    videoWidth: video.videoWidth || 0,
                    videoHeight: video.videoHeight || 0
                })
                """
            )

        except Exception:
            return VideoStatus()

        if not data:
            return VideoStatus()

        return VideoStatus(
            exists=True,
            paused=bool(data.get("paused", True)),
            ready_state=int(data.get("readyState", 0)),
            current_time=float(data.get("currentTime", 0)),
            duration=float(data.get("duration", 0)),
            video_width=int(data.get("videoWidth", 0)),
            video_height=int(data.get("videoHeight", 0)),
        )

    def _build_detail(self, result: LiveCheckResult) -> str:
        parts: list[str] = []

        if result.page_loaded:
            parts.append("页面正常")

        if result.player_found:
            parts.append("发现播放器")

        if result.live_text_found:
            parts.append("发现直播文字")

        if result.video_playing:
            parts.append("Video 正在播放")

        if result.video_width and result.video_height:
            parts.append(
                f"分辨率 {result.video_width}x{result.video_height}"
            )

        if result.error_detected:
            parts.append("检测到错误提示")

        if not parts:
            parts.append("没有发现有效直播信号")

        return "；".join(parts)


class GenericSiteAdapter(SiteAdapter):
    """通用网站适配器。

    当前网站 A/B 都先使用这个版本。
    后续如果某个网站结构特殊，再单独继承 SiteAdapter。
    """

    pass