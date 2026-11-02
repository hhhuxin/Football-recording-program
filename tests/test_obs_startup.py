"""OBS 自动启动行为测试。"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from obs_match_recorder.obs_controller import (
    OBSConnection,
    OBSController,
    OBSControllerError,
)
from obs_match_recorder.main import OBSMatchRecorderApp


CONNECTION = OBSConnection(host="localhost", port=4455, password="secret")


class FakeOBSClient:
    def __init__(self) -> None:
        self.updated_inputs: list[tuple[str, dict, bool]] = []

    def get_input_list(self):
        return SimpleNamespace(
            inputs=[
                {
                    "inputName": "显示器采集",
                    "inputKind": "monitor_capture",
                },
                {
                    "inputName": "桌面音频",
                    "inputKind": "wasapi_output_capture",
                },
            ]
        )

    def set_input_settings(self, name, settings, overlay) -> None:
        self.updated_inputs.append((name, settings, overlay))

    def get_current_program_scene(self):
        return SimpleNamespace(current_program_scene_name="场景")

    def get_source_screenshot(self, name, img_format, width, height, quality):
        assert (name, img_format, width, height, quality) == (
            "场景",
            "png",
            320,
            180,
            -1,
        )
        image_data = base64.b64encode(b"program-frame").decode("ascii")
        return SimpleNamespace(image_data=f"data:image/png;base64,{image_data}")


class FakeRecordingOBSClient:
    def __init__(self, active: bool) -> None:
        self.active = active
        self.start_requests = 0

    def get_input_list(self):
        return SimpleNamespace(inputs=[])

    def get_record_status(self):
        return SimpleNamespace(output_active=self.active)

    def start_record(self) -> None:
        self.start_requests += 1


def test_ensure_available_does_not_start_an_already_connected_obs(monkeypatch) -> None:
    launches: list[tuple[list[str], str | None]] = []
    monkeypatch.setattr(OBSController, "test_connection", lambda self: "31.1.0")

    result = OBSController.ensure_available(
        CONNECTION,
        executable_path=Path(r"E:\Program Files\obs-studio\bin\64bit\obs64.exe"),
        launch_process=lambda command, **kwargs: launches.append(
            (command, kwargs.get("cwd"))
        ),
        is_process_running=lambda: False,
    )

    assert result.version == "31.1.0"
    assert result.started_by_app is False
    assert launches == []


def test_start_recording_accepts_an_already_active_recording(monkeypatch) -> None:
    client = FakeRecordingOBSClient(active=True)
    monkeypatch.setattr(OBSController, "_client", lambda self: client)

    started_now = OBSController(CONNECTION).start_recording()

    assert started_now is False
    assert client.start_requests == 0


def test_ensure_available_starts_obs_and_waits_for_websocket(monkeypatch) -> None:
    attempts = iter([OBSControllerError("port not ready"), "31.1.0"])
    launches: list[tuple[list[str], str | None]] = []
    executable = Path(r"E:\Program Files\obs-studio\bin\64bit\obs64.exe")

    def test_connection(self) -> str:
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(OBSController, "test_connection", test_connection)
    monkeypatch.setattr(Path, "is_file", lambda self: self == executable)

    result = OBSController.ensure_available(
        CONNECTION,
        executable_path=executable,
        launch_process=lambda command, **kwargs: launches.append(
            (command, kwargs.get("cwd"))
        ),
        is_process_running=lambda: False,
        poll_interval_seconds=0,
    )

    assert result.version == "31.1.0"
    assert result.started_by_app is True
    assert launches == [([str(executable)], str(executable.parent))]


def test_ensure_available_does_not_relaunch_running_obs_without_websocket(monkeypatch) -> None:
    launches: list[tuple[list[str], str | None]] = []
    monkeypatch.setattr(
        OBSController,
        "test_connection",
        lambda self: (_ for _ in ()).throw(OBSControllerError("port not ready")),
    )

    with pytest.raises(OBSControllerError, match="WebSocket"):
        OBSController.ensure_available(
            CONNECTION,
            launch_process=lambda command, **kwargs: launches.append(
                (command, kwargs.get("cwd"))
            ),
            is_process_running=lambda: True,
            startup_timeout_seconds=0,
        )

    assert launches == []


def test_prefer_windows_graphics_capture_updates_monitor_capture_only(monkeypatch) -> None:
    client = FakeOBSClient()
    monkeypatch.setattr(OBSController, "_client", lambda self: client)

    updated = OBSController(CONNECTION).prefer_windows_graphics_capture()

    assert updated == ["显示器采集"]
    assert client.updated_inputs == [("显示器采集", {"method": 1}, True)]


def test_program_frame_fingerprint_hashes_program_scene_screenshot(monkeypatch) -> None:
    client = FakeOBSClient()
    monkeypatch.setattr(OBSController, "_client", lambda self: client)

    fingerprint = OBSController(CONNECTION).program_frame_fingerprint()

    assert fingerprint == hashlib.sha256(b"program-frame").hexdigest()


def test_output_stall_requires_two_unchanged_health_checks() -> None:
    app = object.__new__(OBSMatchRecorderApp)
    app.last_obs_frame_fingerprint = "initial"
    app.unchanged_obs_frame_checks = 0

    assert app._observe_obs_output("initial") is False
    assert app.unchanged_obs_frame_checks == 1
    assert app._observe_obs_output("initial") is True

    assert app._observe_obs_output("updated") is False
    assert app.unchanged_obs_frame_checks == 0
