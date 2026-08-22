# thirdparty — 第三方依赖说明

本项目不包含第三方库的源码或二进制，运行时依赖以下外部软件，请单独安装与配置。

## OBS Studio（必需）

录制功能依赖 OBS 通过 WebSocket 接收控制指令。

1. 从 <https://obsproject.com/> 下载并安装 OBS Studio。
2. 打开 OBS，进入「工具 → WebSocket 服务器设置」。
3. 勾选「启用 WebSocket 服务器」，端口默认 `4455`。
4. 设置密码（程序内需填入相同密码）。
5. 在 OBS 中配置好录制场景（捕获 Chrome 窗口或显示器）和输出设置（录制目录、编码）。

程序通过 `obsws-python` 库与 OBS 通信，相关代码见 `src/obs_match_recorder/obs_controller.py`。

## Chrome 浏览器（必需）

程序通过 Chrome DevTools Protocol（CDP）接管一个**独立配置**的 Chrome 调试实例，
因此不需要 Playwright 自带浏览器，但要求本机安装 Chrome。

启动调试 Chrome 见根目录 `start_chrome_debug.bat`。
