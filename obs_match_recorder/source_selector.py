"""直播源选择与切换判定。"""

from __future__ import annotations

from dataclasses import dataclass

from .monitor import SiteStatus


@dataclass
class SourceSelection:
    selected_name: str | None
    selected_url: str | None
    changed: bool


class SourceSelector:
    """根据 A/B 网站状态选择当前直播源。"""

    def __init__(self) -> None:
        self.current_name: str | None = None
        self.current_url: str | None = None

    def select(
        self,
        status_a: SiteStatus,
        status_b: SiteStatus,
    ) -> SourceSelection:

        candidates = [
            status_a,
            status_b,
        ]

        # 只选择明确正在播放的网站
        playing = [
            status
            for status in candidates
            if status.result.status == "正在播放"
            and status.result.video_playing
        ]

        if not playing:
            return SourceSelection(
                selected_name=self.current_name,
                selected_url=self.current_url,
                changed=False,
            )

        # 如果当前网站仍然正常播放，继续使用当前网站
        if self.current_name is not None:
            for status in playing:
                if status.name == self.current_name:
                    return SourceSelection(
                        selected_name=status.name,
                        selected_url=status.url,
                        changed=False,
                    )

        # 当前网站失效，选择评分最高的网站
        selected = max(
            playing,
            key=lambda status: status.result.score,
        )

        changed = (
            selected.name != self.current_name
            or selected.url != self.current_url
        )

        self.current_name = selected.name
        self.current_url = selected.url

        return SourceSelection(
            selected_name=selected.name,
            selected_url=selected.url,
            changed=changed,
        )

    def reset(self) -> None:
        """重置当前直播源。"""

        self.current_name = None
        self.current_url = None