"""OBS WebSocket 录制控制。"""

from __future__ import annotations

import base64
import hashlib
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class OBSControllerError(RuntimeError):
    """无法执行 OBS 控制请求。"""


@dataclass(frozen=True)
class OBSConnection:
    host: str
    port: int
    password: str


@dataclass(frozen=True)
class OBSAvailability:
    """OBS WebSocket 就绪状态。"""

    version: str
    started_by_app: bool


class OBSController:
    DEFAULT_EXECUTABLE_PATH = Path(
        r"D:\Program Files\obs-studio\bin\64bit\obs64.exe"
    )
    STARTUP_TIMEOUT_SECONDS = 30.0
    STARTUP_POLL_INTERVAL_SECONDS = 1.0
    DISPLAY_CAPTURE_INPUT_KIND = "monitor_capture"
    WINDOWS_GRAPHICS_CAPTURE_METHOD = 1
    SCREENSHOT_WIDTH = 320
    SCREENSHOT_HEIGHT = 180

    def __init__(self, connection: OBSConnection) -> None:
        self.connection = connection

    @classmethod
    def ensure_available(
        cls,
        connection: OBSConnection,
        *,
        executable_path: Path | None = None,
        launch_process: Callable[..., object] | None = None,
        is_process_running: Callable[[], bool] | None = None,
        startup_timeout_seconds: float | None = None,
        poll_interval_seconds: float | None = None,
    ) -> OBSAvailability:
        """确认 OBS 已可通过 WebSocket 控制，必要时启动它。"""

        controller = cls(connection)
        initial_error: OBSControllerError | None = None
        try:
            return OBSAvailability(
                version=controller.test_connection(),
                started_by_app=False,
            )
        except OBSControllerError as error:
            initial_error = error

        started_by_app = False
        if not (is_process_running or cls._is_process_running)():
            executable = executable_path or cls.DEFAULT_EXECUTABLE_PATH
            if not executable.is_file():
                raise OBSControllerError(
                    f"OBS 未运行，且未找到启动程序：{executable}。\n"
                    "请确认 OBS 安装位置，或手动启动 OBS Studio。"
                ) from initial_error

            try:
                (launch_process or subprocess.Popen)(
                    [str(executable)],
                    cwd=str(executable.parent),
                )
                started_by_app = True
            except OSError as error:
                raise OBSControllerError(
                    f"无法启动 OBS：{executable}。\n原始错误：{error}"
                ) from error

        timeout = (
            cls.STARTUP_TIMEOUT_SECONDS
            if startup_timeout_seconds is None
            else startup_timeout_seconds
        )
        poll_interval = (
            cls.STARTUP_POLL_INTERVAL_SECONDS
            if poll_interval_seconds is None
            else poll_interval_seconds
        )
        deadline = time.monotonic() + max(0.0, timeout)
        last_error = initial_error

        while True:
            try:
                return OBSAvailability(
                    version=controller.test_connection(),
                    started_by_app=started_by_app,
                )
            except OBSControllerError as error:
                last_error = error

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(max(0.0, poll_interval), remaining))

        raise OBSControllerError(
            f"OBS 已启动，但 {int(timeout)} 秒内未能连接 WebSocket "
            f"{connection.host}:{connection.port}。\n"
            "请在 OBS 的“工具 → WebSocket 服务器设置”中启用服务，"
            "并确认端口和密码与本程序一致。\n"
            f"最后一次错误：{last_error}"
        ) from last_error

    @staticmethod
    def _is_process_running() -> bool:
        """检查 Windows 上是否已有 OBS 进程，避免重复启动。"""

        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq obs64.exe", "/FO", "CSV", "/NH"],
                capture_output=True,
                check=False,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            return False
        return "obs64.exe" in result.stdout.lower()

    def _client(self):
        try:
            import obsws_python as obs
        except ImportError as error:
            raise OBSControllerError(
                "缺少 obsws-python。请执行："
                "python -m pip install -r requirements.txt"
            ) from error

        try:
            with socket.create_connection(
                (self.connection.host, self.connection.port),
                timeout=1.5,
            ):
                pass
        except OSError as error:
            raise OBSControllerError(
                f"OBS WebSocket 端口未监听："
                f"{self.connection.host}:{self.connection.port}。\n"
                "请启动 OBS Studio，并在“工具 → WebSocket 服务器设置”中启用 WebSocket，"
                "确认端口与密码和本程序一致。"
            ) from error

        try:
            return obs.ReqClient(
                host=self.connection.host,
                port=self.connection.port,
                password=self.connection.password,
                timeout=5,
            )
        except Exception as error:
            raise OBSControllerError(
                "无法连接 OBS WebSocket。请检查 OBS WebSocket 版本、端口和密码。\n"
                f"原始错误：{error}"
            ) from error

    def test_connection(self) -> str:
        """测试 OBS WebSocket 连接，并返回 OBS 版本。"""

        client = self._client()

        try:
            return client.get_version().obs_version

        except Exception as error:
            raise OBSControllerError(
                f"OBS 连接测试失败：{error}"
            ) from error

    def start_recording(
        self,
        recording_directory: str | None = None,
    ) -> bool:
        """
        请求 OBS 开始录制，并返回本次是否实际发出了开始请求。

        注意：
        程序不会修改 OBS 的录制目录。
        OBS 将直接使用 OBS Studio 当前设置的录制目录。

        recording_directory 参数保留是为了兼容 V0.2
        其他模块的调用，不再实际使用。
        """

        client = self._client()

        try:
            self._mute_microphone_inputs(client)
            status = client.get_record_status()

            if status.output_active:
                # 已在录制是预期的幂等状态，不应让启动流程误判失败、
                # 进而在 30 秒后重开浏览器页面。
                return False

            client.start_record()
            return True

        except OBSControllerError:
            raise

        except Exception as error:
            raise OBSControllerError(
                f"无法开始 OBS 录制：{error}"
            ) from error

    def prefer_windows_graphics_capture(self) -> list[str]:
        """将现有显示器采集切换为 Windows Graphics Capture。

        OBS 的 ``monitor_capture`` 源以整数保存采集方式：0 是 DXGI，1 是
        Windows Graphics Capture。后者对 Chrome 的硬件视频画面更稳定。
        """

        client = self._client()
        try:
            inputs = client.get_input_list().inputs
            updated_names: list[str] = []
            for item in inputs:
                if item.get("inputKind") != self.DISPLAY_CAPTURE_INPUT_KIND:
                    continue

                name = item.get("inputName")
                if not name:
                    continue

                client.set_input_settings(
                    name,
                    {"method": self.WINDOWS_GRAPHICS_CAPTURE_METHOD},
                    True,
                )
                updated_names.append(name)
            return updated_names
        except Exception as error:
            raise OBSControllerError(
                f"无法切换 OBS 显示器采集方式：{error}"
            ) from error

    def program_frame_fingerprint(self) -> str:
        """返回节目场景缩略图的像素指纹，用于判断 OBS 输出画面是否冻结。"""

        client = self._client()
        try:
            scene_name = client.get_current_program_scene().current_program_scene_name
            screenshot = client.get_source_screenshot(
                scene_name,
                "png",
                self.SCREENSHOT_WIDTH,
                self.SCREENSHOT_HEIGHT,
                -1,
            )
            image_data = screenshot.image_data
            if "," in image_data:
                image_data = image_data.split(",", 1)[1]
            image_bytes = base64.b64decode(image_data, validate=True)
            if not image_bytes:
                raise ValueError("OBS 返回了空截图")
            return hashlib.sha256(image_bytes).hexdigest()
        except Exception as error:
            raise OBSControllerError(
                f"无法获取 OBS 输出画面：{error}"
            ) from error

    @staticmethod
    def _mute_microphone_inputs(client) -> list[str]:
        """录制比赛时关闭 OBS 中的麦克风输入，避免混入环境底噪。"""

        muted_names: list[str] = []
        try:
            inputs = client.get_input_list().inputs
        except Exception:
            return muted_names

        for item in inputs:
            if item.get("inputKind") != "wasapi_input_capture":
                continue

            name = item.get("inputName")
            if not name:
                continue

            try:
                client.set_input_mute(name, True)
                muted_names.append(name)
            except Exception:
                continue

        return muted_names

    def stop_recording(self) -> bool:
        """停止 OBS 当前录制。"""

        client = self._client()

        try:
            status = client.get_record_status()

            if not status.output_active:
                return False

            client.stop_record()
            return True

        except Exception as error:
            raise OBSControllerError(
                f"无法停止 OBS 录制：{error}"
            ) from error

    def is_recording(self) -> bool:
        """读取 OBS 当前是否仍在录制。"""

        client = self._client()
        try:
            return bool(client.get_record_status().output_active)
        except Exception as error:
            raise OBSControllerError(
                f"无法读取 OBS 录制状态：{error}"
            ) from error
