# OBS 比赛录制助手 V2.0

## V2.0 版本说明（2026-11-02）

本版主要解决海星体育播放器在 Playwright/CDP 控制下显示 404、无法继续播放的问题，并已在 Windows 笔记本上完成比赛录制测试。

- 修复播放器初始化脚本未执行及播放器域名不匹配的问题，使用播放器提供的本地调试开关，避免反调试检测误触发 404。
- 海星播放准备流程：取消静音 → 选择账号可用的原画质 → 关闭弹幕 → 全屏；画质切换需要先登录海星账号。
- 固定 URL 顺序为咪咕体育、海星体育，移除浏览器模式选择，复用项目专用 Chrome 的登录配置。
- 适配本机 OBS 启动路径；本机连接未填写密码时，可从 OBS 本地设置读取对应端口的认证密码。
- 已验证网页播放、刷新、声音开启、全屏、弹幕关闭及 OBS 短时录制；43 项单元测试通过，8 项外部环境测试默认跳过。

此版本上传源码，旧版 EXE 不代表 V2.0。使用 `start.bat` 启动，首次运行先安装 Python 与 `requirements.txt` 中的依赖，并配置 OBS 场景、桌面音频和录制目录。浏览器登录数据、OBS 密码及本机 OBS 配置不随源码上传。

无人值守录制期间需保持电脑唤醒；本版尚不支持从睡眠或休眠中自动唤醒。笔记本熄屏、合盖后的显示器采集效果需另行验证。

V2.0 首轮验证：第一个 URL 为咪咕体育，第二个为海星体育；移除浏览器选择项，统一使用项目专用 Chrome。修复海星播放器初始化脚本与域名匹配，避免反调试检测显示 404。

最小测试：运行 `start.bat`，第二个 URL 填入 `https://migo123.top/live/1536`，第一个可暂时留空，点击「测试浏览器」。程序打开海星后依次取消静音、尝试选择原画质、进入全屏，不启动 OBS。网站要求登录才能切换画质；未登录时日志会显示实际画质并提示登录后重试。

Windows 桌面程序：无人值守自动录制足球比赛直播。

- 通过 Chrome 远程调试端口（CDP）接管**已登录**的 Chrome
- 同时打开咪咕视频（网站 A）和海星体育（网站 B）两个直播页
- 自动检测直播状态，优先使用咪咕视频，异常时切换到海星体育（一用一备）
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
```

## 快速开始

1. **启动调试 Chrome**：双击 `start_chrome_debug.bat`，在新窗口登录咪咕视频和海星体育（程序只能接管这个调试窗口，不是普通 Chrome）。
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
│       ├── browser.py         # 启动并通过 CDP 连接项目专用 Chrome
│       ├── detector.py        # 直播状态检测（多信号评分）
│       ├── monitor.py         # 持续监控与卡顿判断、选源
│       ├── player_adapter.py  # 咪咕视频 / 海星体育播放器控制
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
│   └── reference/             # 咪咕视频 / 海星体育参考网页（分析页面结构用）
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
