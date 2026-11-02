"""直播页面检测器。"""

from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import Page


@dataclass
class DetectionResult:
    status: str
    score: int
    page_ok: bool
    player_found: bool
    live_text_found: bool
    error_found: bool
    video_exists: bool
    video_playing: bool
    current_time: float
    video_width: int
    video_height: int
    details: str


class LiveDetector:
    """检测网页是否正在播放直播。"""

    def __init__(self, page: Page) -> None:
        self.page = page

    def _player_surface(self):
        """海星体育的实际 video 位于跨域 iframe，动态取最新 frame。"""

        for frame in self.page.frames:
            if frame is self.page.main_frame:
                continue
            if "video.club7275.top" in frame.url:
                return frame
            try:
                if frame.locator("#dplayer, video").count():
                    return frame
            except Exception:
                continue
        return self.page

    def detect(self) -> DetectionResult:
        page_ok = False
        player_found = False
        live_text_found = False
        error_found = False
        video_exists = False
        video_playing = False
        current_time = 0.0
        video_width = 0
        video_height = 0

        details: list[str] = []

        # ---------------------------------------------------------
        # 1. 页面是否正常
        # ---------------------------------------------------------
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=5000)
            page_ok = True
            details.append("页面正常")
        except Exception:
            try:
                if self.page.url:
                    page_ok = True
                    details.append("页面已打开")
            except Exception:
                pass

        # ---------------------------------------------------------
        # 2. 页面文字
        # ---------------------------------------------------------
        try:
            text = self.page.locator("body").inner_text(timeout=3000).lower()

            live_keywords = [
                "直播",
                "live",
                "正在直播",
                "直播中",
                "观看直播",
                "live now",
                "playing",
            ]

            if any(keyword.lower() in text for keyword in live_keywords):
                live_text_found = True
                details.append("发现直播文字")

            error_keywords = [
                "出错啦",
                "页面不存在",
                "直播已结束",
                "直播间不存在",
                "network error",
                "error loading",
                "播放失败",
                "加载失败",
            ]

            if any(keyword.lower() in text for keyword in error_keywords):
                error_found = True
                details.append("检测到错误")

        except Exception:
            pass

        # ---------------------------------------------------------
        # 3. 查找 HTML5 Video
        # ---------------------------------------------------------
        try:
            surface = self._player_surface()
            video_count = surface.locator("video").count()

            if video_count > 0:
                video_exists = True
                details.append("找到 Video")

                video = surface.locator("video").first

                data = video.evaluate(
                    """
                    video => ({
                        paused: video.paused,
                        ended: video.ended,
                        currentTime: video.currentTime || 0,
                        width: video.videoWidth || 0,
                        height: video.videoHeight || 0
                    })
                    """
                )

                current_time = float(data.get("currentTime", 0))
                video_width = int(data.get("width", 0))
                video_height = int(data.get("height", 0))

                paused = bool(data.get("paused", True))
                ended = bool(data.get("ended", False))

                if not paused and not ended:
                    video_playing = True
                    details.append("Video 正在播放")

                if video_width > 0 and video_height > 0:
                    details.append(
                        f"分辨率 {video_width}x{video_height}"
                    )

        except Exception:
            pass

        # ---------------------------------------------------------
        # 4. 查找常见播放器元素
        # ---------------------------------------------------------
        try:
            surface = self._player_surface()
            selectors = [
                "video",
                "iframe",
                "[class*='player']",
                "[class*='Player']",
                "[class*='live-player']",
                "[class*='livePlayer']",
                "[id*='player']",
                "[id*='Player']",
                "[class*='video']",
                "[class*='Video']",
            ]

            for selector in selectors:
                try:
                    if surface.locator(selector).count() > 0:
                        player_found = True
                        break
                except Exception:
                    continue

            if player_found:
                details.append("发现播放器")

        except Exception:
            pass

        # ---------------------------------------------------------
        # 5. 计算评分
        # ---------------------------------------------------------
        score = 0

        if page_ok:
            score += 1

        if player_found:
            score += 2

        if live_text_found:
            score += 2

        if video_exists:
            score += 2

        if video_playing:
            score += 2

        if video_width > 0 and video_height > 0:
            score += 1

        if error_found:
            score -= 3

        # 防止出现负数
        score = max(0, score)

        # ---------------------------------------------------------
        # 6. 判断状态
        # ---------------------------------------------------------
        if video_playing and video_width > 0 and video_height > 0:
            status = "正在播放"

        elif video_exists and video_playing:
            status = "正在播放"

        elif score >= 3 and live_text_found:
            status = "疑似直播"

        elif error_found:
            status = "页面异常"

        elif page_ok:
            status = "未确认直播"

        else:
            status = "页面无法访问"

        return DetectionResult(
            status=status,
            score=score,
            page_ok=page_ok,
            player_found=player_found,
            live_text_found=live_text_found,
            error_found=error_found,
            video_exists=video_exists,
            video_playing=video_playing,
            current_time=current_time,
            video_width=video_width,
            video_height=video_height,
            details="；".join(details),
        )


def detect_page(page: Page) -> DetectionResult:
    """直接检测一个 Playwright Page。"""

    detector = LiveDetector(page)
    return detector.detect()
