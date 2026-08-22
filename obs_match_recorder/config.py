"""应用配置的读取、校验与保存。"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


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
