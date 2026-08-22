"""启动 OBS 比赛录制助手。

项目采用 src 布局（见 README「项目结构」），源码位于 src/ 目录。
这里把 src 加入模块搜索路径，保证直接运行 python run.py 即可启动，
无需 pip install -e 安装。
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from obs_match_recorder.main import main  # noqa: E402


if __name__ == "__main__":
    main()
