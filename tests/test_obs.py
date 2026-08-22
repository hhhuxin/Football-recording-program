"""OBS WebSocket 控制测试。

连接测试是集成测试（需要 OBS 已启动并启用 WebSocket 服务器）：
    pytest tests/test_obs.py --run-integration

注意：录制启停测试会真的开始/停止录制，请在测试环境中谨慎使用。
"""

from __future__ import annotations

import pytest

from obs_match_recorder.obs_controller import OBSController


@pytest.mark.integration
def test_obs_connection(obs: OBSController) -> None:
    version = obs.test_connection()
    assert version, "应能拿到 OBS 版本号"


@pytest.mark.integration
def test_obs_record_cycle(obs: OBSController) -> None:
    """完整的开始→停止录制循环，验证 WebSocket 控制链路。"""

    # 先停止任何残留录制，保证测试从干净状态开始
    try:
        obs.stop_recording()
    except Exception:
        pass

    obs.start_recording()
    # 请求后 OBS 应处于录制中（通过再次 stop 的成功返回值间接验证）

    stopped = obs.stop_recording()
    assert stopped, "停止录制应返回 True，说明此前确实在录制"
