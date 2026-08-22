"""应用配置的读取、校验与保存。"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .obs_controller import OBSConnection
from .scheduler import TIME_FORMAT


@dataclass
class AppConfig:
    match_name: str = ""
    match_start_time: str = ""
    advance_start_minutes: str = "5"
    max_recording_minutes: str = "150"
    site_a_url: str = ""
    site_b_url: str = ""
    obs_recording_directory: str = ""
    obs_host: str = "localhost"
    obs_port: str = "4455"
    obs_password: str = ""


def get_config_path() -> Path:
    app_data = os.environ.get("APPDATA")
    base_directory = Path(app_data) if app_data else Path.home() / ".obs_match_recorder"
    return base_directory / "OBSMatchRecorder" / "config.json"


def load_config() -> AppConfig:
    config_path = get_config_path()
    if not config_path.exists():
        return AppConfig()

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppConfig()

    valid_fields = {field_name: data[field_name] for field_name in AppConfig.__dataclass_fields__ if field_name in data}
    return AppConfig(**valid_fields)


def save_config(config: AppConfig) -> Path:
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
    return config_path


# =========================================================
# 配置校验
# =========================================================

def validate_sites(config: AppConfig) -> list[str]:
    """校验 A/B 网站 URL 格式。"""

    errors: list[str] = []

    for label, address in (
        ("网站 A", config.site_a_url),
        ("网站 B", config.site_b_url),
    ):
        if label == "网站 B" and not address:
            continue

        parsed = urlparse(address)

        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
        ):
            errors.append(
                f"{label} URL 应以 "
                "http:// 或 https:// 开头。"
            )

    return errors


def validate_config(config: AppConfig) -> list[str]:
    """校验比赛与录制相关的配置。"""

    errors: list[str] = []

    if not config.match_name:
        errors.append(
            "请填写比赛名称。"
        )

    try:
        datetime.strptime(
            config.match_start_time,
            TIME_FORMAT,
        )
    except ValueError:
        errors.append(
            "比赛开始时间格式应为 "
            "YYYY-MM-DD HH:MM。"
        )

    try:
        if int(
            config.advance_start_minutes
        ) < 0:
            raise ValueError

    except ValueError:
        errors.append(
            "提前启动时间应为不小于 "
            "0 的整数分钟。"
        )

    try:
        if int(config.max_recording_minutes) <= 0:
            raise ValueError

    except ValueError:
        errors.append(
            "最大录制时长应为大于 0 的整数分钟。"
        )

    errors.extend(validate_sites(config))

    if not config.obs_recording_directory:
        errors.append(
            "请选择 OBS 录制目录。"
        )

    elif not Path(
        config.obs_recording_directory
    ).is_dir():
        errors.append(
            "OBS 录制目录不存在或不可访问。"
        )

    return errors


def validate_obs_connection(config: AppConfig) -> list[str]:
    """校验 OBS WebSocket 连接参数。"""

    errors: list[str] = []

    if not config.obs_host:
        errors.append(
            "请填写 OBS WebSocket 地址。"
        )

    try:
        port = int(config.obs_port)

        if not 1 <= port <= 65535:
            raise ValueError

    except ValueError:
        errors.append(
            "OBS WebSocket 端口应为 "
            "1 到 65535 的整数。"
        )

    return errors


def connection_from_config(config: AppConfig) -> OBSConnection:
    """从配置构造 OBS 连接参数。"""

    return OBSConnection(
        host=config.obs_host,
        port=int(config.obs_port),
        password=config.obs_password,
    )
