"""咪咕会员弹窗和通看券处理规则测试。"""

from __future__ import annotations

import inspect

from obs_match_recorder.player_adapter import HaixingPlayerAdapter, MiguPlayerAdapter
from obs_match_recorder.source_manager import SourceManager


def test_membership_popup_script_skips_match_pass_popup() -> None:
    script = MiguPlayerAdapter._membership_popup_dismissal_script()

    assert "足球通" in script
    assert ".member-order-container" in script
    assert "续费" in script
    assert "会员" in script
    assert ".close" in script
    assert "通看全场" in script
    assert "使用5张券" in script


def test_match_pass_script_requires_exact_authorized_button_text() -> None:
    script = MiguPlayerAdapter._match_pass_claim_script()

    assert "通看全场" in script
    assert "使用5张券" in script
    assert "actionText === '使用5张券'" in script
    assert "action.click()" in script
    assert "popupText.includes('通看全场')" in script


def test_advertisement_script_detects_current_migu_ad_container() -> None:
    script = MiguPlayerAdapter._advertisement_visibility_script()

    assert "#adver-box" in script
    assert ".adver-box" in script
    assert "getBoundingClientRect" in script


def test_advertisement_display_uses_migu_fullscreen_control() -> None:
    source = inspect.getsource(MiguPlayerAdapter.expand_advertisement_display)

    assert "_enter_migu_fullscreen" in source
    assert "_is_migu_fullscreen_display" in source
    assert "_normalize_fullscreen_layout" in source


def test_migu_playback_uses_native_fullscreen_before_quality_and_playback() -> None:
    source = inspect.getsource(MiguPlayerAdapter.prepare)

    assert "if not self._is_migu_fullscreen_display():" in source
    assert "_enter_migu_fullscreen" in source


def test_migu_fullscreen_selectors_cover_title_and_icon_controls() -> None:
    selectors = MiguPlayerAdapter.FULLSCREEN_SELECTORS

    assert ".zoom-btn .btn-bg[title='全屏']" in selectors
    assert "#mod-player [class*='fullscreen']" in selectors


def test_migu_fullscreen_click_has_bottom_right_coordinate_fallback() -> None:
    source = inspect.getsource(MiguPlayerAdapter._enter_migu_fullscreen)

    assert "self.page.mouse.click(x, y)" in source
    assert 'box["width"] - 36' in source
    assert 'box["height"] - 28' in source


def test_free_match_without_premium_quality_selects_720p_without_reload() -> None:
    source = inspect.getsource(MiguPlayerAdapter._select_blue_quality)

    assert "if item_and_quality is None and hd_720 is not None:" in source
    assert "免费场次通常不显示原画或蓝光" in source


def test_original_quality_is_preferred_before_blue_quality() -> None:
    source = inspect.getsource(MiguPlayerAdapter._select_blue_quality)

    assert "def is_selectable_original" in source
    assert "(original or blue) if fallback_steps <= 0" in source
    assert "else blue if fallback_steps == 1" in source


def test_quality_fallback_order_is_original_then_blue_then_720p() -> None:
    source = inspect.getsource(SourceManager.activate_source)

    assert "quality_fallback_steps = attempt" in source
    assert "原画未稳定" in source
    assert "改用蓝光重试" in source
    assert "原画和蓝光均未稳定" in source
    assert "改用高清 720P 重试" in source


def test_advertisement_attempts_player_fullscreen_when_browser_fullscreen_fails() -> None:
    source = inspect.getsource(SourceManager._wait_for_advertisement)

    assert "if not self.browser.ensure_foreground_and_fullscreen(page):" in source
    assert "if not adapter.expand_advertisement_display():" in source
    assert "elif not adapter.expand_advertisement_display():" not in source


def test_haixing_uses_the_embedded_dplayer_controls() -> None:
    assert HaixingPlayerAdapter.VIDEO_SELECTOR == "video.dplayer-video-current, video"
    assert ".dplayer-showdan-setting-input" in inspect.getsource(
        HaixingPlayerAdapter._disable_danmaku
    )
    assert ".dplayer-videomuted-warp" in inspect.getsource(
        HaixingPlayerAdapter._dismiss_mute_overlay
    )
    assert ".dplayer-quality-icon" in inspect.getsource(
        HaixingPlayerAdapter._select_quality
    )
    assert "原画质" in inspect.getsource(HaixingPlayerAdapter._select_quality)
    assert ".dplayer-full-icon" in inspect.getsource(
        HaixingPlayerAdapter.prepare
    )


def test_haixing_quality_switch_has_playback_recovery() -> None:
    source = inspect.getsource(HaixingPlayerAdapter._recover_after_quality_switch)

    assert "_activate_video" in source
    assert "_wait_for_playing" in source
    assert "_select_quality(quality_fallback_steps)" in source
