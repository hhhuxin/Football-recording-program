# OBS 比赛录制助手 V1.1

这是 Windows 桌面程序的 V1.1 半成品，当前包含：

- 比赛与网站参数配置界面
- 最大录制时长设置，达到时间后自动停止 OBS
- OBS 录制目录选择
- 配置的保存和重新读取
- 配置测试与运行日志窗口
- Playwright 连接已登录的 Chrome
- 咪咕优先、飞速备用的直播检测
- 咪咕画质、静音、全屏控制
- 飞速弹幕、静音、全屏控制
- 当前源异常时刷新，连续失败后切换备用源
- OBS WebSocket 录制控制

## 运行

系统需要安装 Python 3.10 或更高版本（Windows 官方安装包通常自带 Tkinter）。在项目目录中双击 `start.bat`，或在 PowerShell 中运行：

```powershell
python run.py
```

安装依赖：

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
```

程序连接的是已经登录的 Chrome 调试实例，不是 Playwright 自己新建的临时浏览器。
首次使用时请完全退出 Chrome，然后双击 `start_chrome_debug.bat`，在打开的 Chrome 中登录咪咕和飞速；以后都用这个 Chrome 保持登录状态。

OBS 端需要在“工具 → WebSocket 服务器设置”中启用 WebSocket，默认端口为 `4455`，并把密码填入程序。

注意：程序只会选择当前账号实际能看到的最高画质，不会绕过咪咕的会员权限或付费限制。

## 使用

1. 用 `start_chrome_debug.bat` 启动并登录 Chrome。
2. 启动 OBS，启用 OBS WebSocket。
3. 启动程序，填入比赛名称、`YYYY-MM-DD HH:MM` 格式的开始时间、提前分钟数，以及两个 `http(s)` URL。
4. 先点击“测试浏览器”和“测试 OBS 连接”，两项都通过后再启动定时任务。
5. 录制画面使用 OBS 当前场景设置；请让场景捕获 Chrome 窗口或显示器，并确保 Chrome 播放器本身铺满窗口。

配置保存在 `%APPDATA%\OBSMatchRecorder\config.json`。
