"""pytest 共享配置。

- src 布局：把 src 加入模块搜索路径，测试才能 import obs_match_recorder。
- 集成测试默认跳过，加 --run-integration 才运行（需要真实 Chrome / OBS 环境）。
- 提供浏览器与 OBS 的 fixture。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parent.parent / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from obs_match_recorder.browser import BrowserController  # noqa: E402
from obs_match_recorder.obs_controller import OBSConnection, OBSController  # noqa: E402


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: 需要真实 Chrome / OBS 环境的集成测试，加 --run-integration 运行",
    )


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="运行需要真实 Chrome / OBS 环境的集成测试",
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    if config.getoption("--run-integration"):
        return

    skip = pytest.mark.skip(
        reason="需要真实环境；加 --run-integration 运行"
    )

    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="module")
def browser() -> BrowserController:
    """连接已启动的调试 Chrome（需先运行 start_chrome_debug.bat）。"""

    controller = BrowserController()
    controller.start()
    yield controller
    controller.close()


@pytest.fixture(scope="module")
def obs() -> OBSController:
    """连接本机 OBS WebSocket（需先在 OBS 中启用 WebSocket 服务器）。"""

    connection = OBSConnection(
        host="localhost",
        port=4455,
        password="123456",
    )
    return OBSController(connection)
