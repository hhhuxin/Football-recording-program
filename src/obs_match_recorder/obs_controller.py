"""OBS WebSocket 录制控制。"""

from __future__ import annotations

import socket
from dataclasses import dataclass


class OBSControllerError(RuntimeError):
    """无法执行 OBS 控制请求。"""


@dataclass(frozen=True)
class OBSConnection:
    host: str
    port: int
    password: str


class OBSController:
    def __init__(self, connection: OBSConnection) -> None:
        self.connection = connection

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
    ) -> None:
        """
        请求 OBS 开始录制。

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
                raise OBSControllerError(
                    "OBS 当前已经在录制，无需重复开始。"
                )

            client.start_record()

        except OBSControllerError:
            raise

        except Exception as error:
            raise OBSControllerError(
                f"无法开始 OBS 录制：{error}"
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
