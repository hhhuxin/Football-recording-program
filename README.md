# OBS 比赛录制助手

Windows 桌面程序：无人值守自动录制足球比赛直播。

- 通过 Chrome 远程调试端口（CDP）接管**已登录**的 Chrome
- 同时打开咪咕（网站 A）和飞速直播（网站 B）两个直播页
- 自动检测直播状态，优先使用咪咕，异常时切换到飞速（一用一备）
- 自动完成：点击播放 → 选择当前账号可用的最高画质 → 关闭弹幕 → 取消静音 → 全屏
- 通过 OBS WebSocket 自动开始 / 停止录制
- 录制过程中持续监控：卡顿自动刷新，连续失败自动切换备用源
- 达到最大录制时长自动停止

> 注意：程序只会选择当前账号实际能看到的最高画质，不会绕过咪咕的会员权限或付费限制。

## 运行环境

- Windows 10 / 11
- Python 3.10 或更高版本（Windows 官方安装包自带 Tkinter）
- Chrome 浏览器
- OBS Studio（需启用 WebSocket 服务器，默认端口 4455）

安装依赖：

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## 快速开始

1. **启动调试 Chrome**：双击 `start_chrome_debug.bat`，在新窗口登录咪咕和飞速（程序只能接管这个调试窗口，不是普通 Chrome）。
2. **启动 OBS**，在「工具 → WebSocket 服务器设置」中启用 WebSocket，端口 `4455`，密码填入程序。
3. **启动程序**：双击 `start.bat`，或运行 `python run.py`。
4. 填写比赛名称、开始时间（`YYYY-MM-DD HH:MM`）、提前分钟数、两个直播 URL，选择录制目录。
5. 先点「测试浏览器」和「测试 OBS 连接」，都通过后再点「开始任务」。

配置保存在 `%APPDATA%\OBSMatchRecorder\config.json`。

## 项目结构

```
Football-recording-program/
├── run.py                     # 主入口（直接 python run.py 启动）
├── start.bat                  # 双击启动程序
├── start_chrome_debug.bat     # 双击启动调试 Chrome（登录用）
├── requirements.txt           # Python 依赖
├── pytest.ini                 # pytest 配置
├── OBSMatchRecorder.spec      # PyInstaller 打包配置（新版）
├── OBSMatchRecorder_V1_0.spec # PyInstaller 打包配置（V1.0 单文件版）
│
├── src/                       # 源码（src 布局）
│   └── obs_match_recorder/
│       ├── main.py            # Tkinter GUI 主程序
│       ├── browser.py         # Playwright 连接调试 Chrome
│       ├── detector.py        # 直播状态检测（多信号评分）
│       ├── monitor.py         # 持续监控与卡顿判断、选源
│       ├── player_adapter.py  # 咪咕 / 飞速播放器控制
│       ├── obs_controller.py  # OBS WebSocket 录制控制
│       ├── config.py          # 配置读取与保存
│       └── scheduler.py       # 比赛时间计算
│
├── tests/                     # 测试
│   ├── conftest.py            # pytest 配置：src 路径 + 浏览器/OBS fixture
│   ├── test_scheduler.py      # 单元测试：时间计算
│   ├── test_source_selector.py# 单元测试：选源逻辑
│   └── test_*.py              # 集成测试（需真实 Chrome/OBS，默认跳过）
│
├── docs/                      # 文档与参考资料
│   ├── 需求文档v1.0.txt
│   ├── 需求文档v1.1.txt
│   └── reference/             # 咪咕 / 飞速参考网页（分析页面结构用）
│
└── thirdparty/                # 第三方依赖说明（OBS 等）
```

## 测试

单元测试（无需外部环境）：

```powershell
python -m pytest
```

集成测试（需要调试 Chrome / OBS 已启动，默认跳过）：

```powershell
python -m pytest --run-integration
```

## 打包成 EXE

```powershell
python -m pip install pyinstaller
pyinstaller OBSMatchRecorder.spec
```

产物在 `dist/OBSMatchRecorder/` 下。

## 常见问题

- **「Chrome 远程调试端口未开启」**：先完全退出 Chrome，再双击 `start_chrome_debug.bat`，等新 Chrome 打开后再测试。
- **OBS 无法连接**：确认 OBS 已启动，且「工具 → WebSocket 服务器设置」已启用，端口和密码与程序一致。
- **录不到画面**：程序用 OBS 当前场景录制；请让场景捕获 Chrome 窗口或显示器，并确保 Chrome 播放器铺满窗口。
