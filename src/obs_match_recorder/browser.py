"""Playwright 浏览器管理。"""

from __future__ import annotations

import os
import socket
import re
import subprocess
import time
import ctypes
from ctypes import WINFUNCTYPE, byref, c_bool, windll
from ctypes import wintypes
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import (
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)


class BrowserControllerError(RuntimeError):
    """浏览器控制错误。"""


class BrowserController:
    def __init__(self, cdp_url: str = "http://127.0.0.1:9222") -> None:
        self.cdp_url = cdp_url.rstrip("/")
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._managed_pages: list[Page] = []

    @staticmethod
    def _chrome_executable() -> Path | None:
        candidates = [
            Path(os.environ.get("ProgramFiles", ""))
            / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("ProgramFiles(x86)", ""))
            / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Google/Chrome/Application/chrome.exe",
        ]
        return next((path for path in candidates if path.is_file()), None)

    @staticmethod
    def _profile_directory() -> Path:
        # 项目根目录（src 布局下：browser.py 位于 <根>/src/obs_match_recorder/）
        project_root = Path(__file__).resolve().parent.parent.parent
        return project_root / "ChromeRecorderProfile"

    def _check_cdp_port(self) -> None:
        """给出比 Playwright 原始 ECONNREFUSED 更可执行的提示。"""

        parsed = urlparse(self.cdp_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 9222

        try:
            with socket.create_connection((host, port), timeout=1.5):
                return
        except OSError as error:
            chrome = BrowserController._chrome_executable()
            chrome_command = (
                f'"{chrome}"'
                if chrome is not None
                else "chrome.exe"
            )
            profile = BrowserController._profile_directory()
            raise BrowserControllerError(
                f"Chrome 远程调试端口未开启：{host}:{port}。\n"
                "当前普通 Chrome 不能被 Playwright 接管。请完全退出 Chrome，"
                "双击项目目录中的 start_chrome_debug.bat，"
                "然后在新窗口登录咪咕和飞速。\n"
                "等新 Chrome 打开后再点击“测试浏览器”。\n"
                "对应命令：\n"
                f'{chrome_command} --remote-debugging-port={port} '
                f'--user-data-dir="{profile}"'
            ) from error

    def _is_cdp_ready(self) -> bool:
        """检查 CDP 的 HTTP 接口，避免只检查 TCP 后连接到失效实例。"""

        parsed = urlparse(self.cdp_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 9222
        try:
            with socket.create_connection((host, port), timeout=1.5) as connection:
                connection.sendall(
                    f"GET /json/version HTTP/1.1\r\nHost: {host}:{port}\r\n"
                    "Connection: close\r\n\r\n".encode("ascii")
                )
                response = connection.recv(4096).decode("utf-8", "ignore")
            return "200" in response.splitlines()[0] and "webSocketDebuggerUrl" in response
        except (OSError, IndexError):
            return False

    def _start_debug_chrome(self) -> None:
        """没有 CDP 时自动启动项目专用 Chrome。"""

        chrome = self._chrome_executable()
        if chrome is None:
            self._check_cdp_port()
            return

        parsed = urlparse(self.cdp_url)
        port = parsed.port or 9222
        profile = self._profile_directory()
        profile.mkdir(parents=True, exist_ok=True)

        try:
            subprocess.Popen(
                [
                    str(chrome),
                    f"--remote-debugging-port={port}",
                    f"--user-data-dir={profile}",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
                cwd=str(profile),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    if os.name == "nt"
                    else 0
                ),
            )
        except OSError as error:
            raise BrowserControllerError(f"无法自动启动 Chrome：{error}") from error

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self._is_cdp_ready():
                return
            time.sleep(0.25)

        raise BrowserControllerError(
            "Chrome 已尝试启动，但远程调试接口仍未就绪。"
            "请确认项目专用 Chrome 窗口已打开。"
        )

    def _ensure_cdp_ready(self) -> None:
        if not self._is_cdp_ready():
            self._start_debug_chrome()

    def _debug_browser_pid(self) -> int | None:
        parsed = urlparse(self.cdp_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 9222

        try:
            output = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
        except OSError:
            return None

        pattern = re.compile(
            rf"^\s*TCP\s+{re.escape(host)}:{port}\s+\S+\s+LISTENING\s+(\d+)\s*$",
            re.IGNORECASE | re.MULTILINE,
        )
        match = pattern.search(output)
        return int(match.group(1)) if match else None

    def _focus_debug_window(self) -> bool:
        if os.name != "nt":
            return False

        browser_pid = self._debug_browser_pid()
        if browser_pid is None:
            return False

        user32 = windll.user32
        kernel32 = windll.kernel32
        hwnd_found = wintypes.HWND()

        @WINFUNCTYPE(c_bool, wintypes.HWND, wintypes.LPARAM)
        def find_chrome_window(hwnd, _):
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, byref(process_id))
            class_name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_name, len(class_name))

            if (
                process_id.value == browser_pid
                and user32.IsWindowVisible(hwnd)
                and class_name.value.startswith("Chrome_WidgetWin")
            ):
                hwnd_found.value = hwnd
                return False
            return True

        user32.EnumWindows(find_chrome_window, 0)
        hwnd = hwnd_found.value
        if not hwnd:
            return False

        user32.AllowSetForegroundWindow(-1)
        foreground = user32.GetForegroundWindow()
        current_thread = kernel32.GetCurrentThreadId()
        foreground_thread = user32.GetWindowThreadProcessId(
            foreground,
            None,
        )
        target_thread = user32.GetWindowThreadProcessId(hwnd, None)

        for thread_id in {foreground_thread, target_thread}:
            if thread_id and thread_id != current_thread:
                user32.AttachThreadInput(current_thread, thread_id, True)

        try:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            for thread_id in {foreground_thread, target_thread}:
                if thread_id and thread_id != current_thread:
                    user32.AttachThreadInput(current_thread, thread_id, False)

        return user32.GetForegroundWindow() == hwnd

    @staticmethod
    def _is_browser_fullscreen(page: Page) -> bool:
        try:
            return bool(
                page.evaluate(
                    """
                    () => window.innerHeight >= window.screen.height - 2
                    """
                )
            )
        except Exception:
            return False

    def ensure_foreground_and_fullscreen(self, page: Page) -> bool:
        """让当前播放标签所在 Chrome 置前，并进入浏览器级 F11 全屏。"""

        page.bring_to_front()
        focused = self._focus_debug_window()

        if not self._is_browser_fullscreen(page) and os.name == "nt":
            windll.user32.keybd_event(0x7A, 0, 0, 0)  # VK_F11
            windll.user32.keybd_event(0x7A, 0, 0x0002, 0)
            time.sleep(0.8)

        return focused and self._is_browser_fullscreen(page)

    def start(self) -> None:
        if self._context is not None:
            try:
                if not self._context.pages:
                    self.close()
                else:
                    return
            except Exception:
                self.close()

        try:
            self._ensure_cdp_ready()
            self._playwright = sync_playwright().start()

            browser = self._playwright.chromium.connect_over_cdp(
                self.cdp_url
            )

            if not browser.contexts:
                raise RuntimeError(
                    "没有找到 Chrome 页面。"
                )

            self._context = browser.contexts[0]

        except Exception as error:
            self.close()

            raise BrowserControllerError(
                f"无法连接 Chrome：{error}"
            ) from error

    def open_page(self, url: str) -> Page:
        if self._context is None:
            raise BrowserControllerError(
                "Chrome 尚未连接。"
            )

        for attempt in range(2):
            try:
                page = self._context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                self._managed_pages.append(page)
                return page
            except Exception as error:
                target_closed = "target page, context or browser has been closed" in str(error).lower()
                if attempt == 0 and target_closed:
                    self.close()
                    try:
                        self.start()
                    except Exception:
                        raise BrowserControllerError(
                            f"无法打开网页：{url}\n{error}"
                        ) from error
                    continue
                raise BrowserControllerError(f"无法打开网页：{url}\n{error}") from error

        raise BrowserControllerError(f"无法打开网页：{url}")

    def open_sites(
        self,
        site_a_url: str,
        site_b_url: str,
    ) -> tuple[Page, Page | None]:

        self.close_managed_pages()

        page_a = self.open_page(site_a_url)
        page_b = self.open_page(site_b_url) if site_b_url.strip() else None

        return page_a, page_b

    def close_managed_pages(self) -> None:
        for page in self._managed_pages:
            try:
                if not page.is_closed():
                    page.close()
            except Exception:
                pass

        self._managed_pages.clear()

    def close_page(self, page: Page) -> None:
        try:
            if not page.is_closed():
                page.close()
        finally:
            self._managed_pages = [
                managed for managed in self._managed_pages
                if managed is not page
            ]

    @staticmethod
    def hide_cursor(page: Page) -> None:
        """隐藏网页中的鼠标光标，并把系统鼠标移到左上角一次。"""

        try:
            page.evaluate(
                """
                () => {
                    let style = document.getElementById(
                        'obs-match-recorder-cursor-style'
                    );
                    if (!style) {
                        style = document.createElement('style');
                        style.id = 'obs-match-recorder-cursor-style';
                        document.head.appendChild(style);
                    }
                    style.textContent = `
                        *, *::before, *::after {
                            cursor: none !important;
                        }
                    `;
                    document.documentElement.style.setProperty(
                        'cursor', 'none', 'important'
                    );
                    document.body.style.setProperty(
                        'cursor', 'none', 'important'
                    );
                }
                """
            )
        except Exception:
            pass

        if os.name == "nt":
            try:
                windll.user32.SetCursorPos(1, 1)
            except Exception:
                pass

    @staticmethod
    def restore_cursor(page: Page) -> None:
        """任务结束后恢复页面的正常鼠标样式。"""

        try:
            page.evaluate(
                """
                () => {
                    document.getElementById(
                        'obs-match-recorder-cursor-style'
                    )?.remove();
                    document.documentElement.style.removeProperty('cursor');
                    document.body.style.removeProperty('cursor');
                }
                """
            )
        except Exception:
            pass

    def silence_other_pages(self, active_page: Page) -> None:
        """暂停并静音其它标签页中的媒体，避免录制时出现重音。"""

        if self._context is None:
            return
        for page in self._context.pages:
            if page is active_page or page.is_closed():
                continue
            try:
                page.evaluate(
                    """
                    () => document.querySelectorAll('video, audio').forEach(media => {
                        media.pause();
                        media.muted = true;
                        media.volume = 0;
                    })
                    """
                )
            except Exception:
                pass

    def close_unexpected_pages(
        self,
        keep_pages: list[Page | None],
        allowed_hosts: set[str],
    ) -> None:
        """关闭重复直播页和广告弹出的外部标签页。"""

        if self._context is None:
            return
        keep_pages = [page for page in keep_pages if page is not None]
        keep_ids = {id(page) for page in keep_pages}
        keep_urls = {
            page.url for page in keep_pages if not page.is_closed()
        }
        for page in self._context.pages:
            if page.is_closed() or id(page) in keep_ids:
                continue
            try:
                parsed = urlparse(page.url)
                if parsed.scheme in {"chrome", "devtools"}:
                    continue
                if page.url in keep_urls or (
                    parsed.hostname and parsed.hostname not in allowed_hosts
                ):
                    page.close()
            except Exception:
                pass

    def close(self) -> None:
        self.close_managed_pages()
        self._context = None

        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass

        self._playwright = None
