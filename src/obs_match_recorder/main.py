"""OBS 比赛录制助手 V0.2。"""

from __future__ import annotations

import tkinter as tk
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from urllib.parse import urlparse

from .browser import BrowserController, BrowserControllerError
from .config import AppConfig, load_config, save_config
from .detector import DetectionResult, LiveDetector
from .monitor import SiteStatus, SourceMonitor
from .obs_controller import (
    OBSConnection,
    OBSController,
    OBSControllerError,
)
from .player_adapter import (
    PlayerControlError,
    PlayerState,
    adapter_for,
)
from .scheduler import TIME_FORMAT, scheduled_start_time


class OBSMatchRecorderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("OBS 比赛录制助手 V0.2")
        self.root.minsize(820, 700)

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        self.variables = {
            "match_name": tk.StringVar(),
            "match_start_time": tk.StringVar(),
            "advance_start_minutes": tk.StringVar(value="5"),
            "max_recording_minutes": tk.StringVar(value="150"),
            "site_a_url": tk.StringVar(),
            "site_b_url": tk.StringVar(),
            "obs_recording_directory": tk.StringVar(),
            "obs_host": tk.StringVar(value="localhost"),
            "obs_port": tk.StringVar(value="4455"),
            "obs_password": tk.StringVar(),
        }

        self.task_active = False
        self.recording_started_by_app = False
        self.scheduled_time: datetime | None = None
        self.recording_deadline: datetime | None = None
        self.recording_stop_after_id = None

        self.browser = BrowserController()

        self.page_a = None
        self.page_b = None

        self.source_monitor: SourceMonitor | None = None

        self.current_source: str | None = None
        self.source_failure_counts = {"A": 0, "B": 0}

        # 防止出现两个监控循环同时运行
        self.monitor_loop_active = False

        # 每次启动新的监控循环都会生成新的 ID。
        # 旧循环即使还有 after 回调，也会自动失效。
        self.monitor_loop_id = 0

        self.executor = ThreadPoolExecutor(
            max_workers=3,
            thread_name_prefix="obs-match-recorder",
        )

        self._build_interface()
        self._apply_config(load_config())

        self.root.protocol(
            "WM_DELETE_WINDOW",
            self.on_close,
        )

        self.log(
            "程序已启动。请填写配置后保存、测试或启动定时任务。"
        )

    # =========================================================
    # 界面
    # =========================================================

    def _build_interface(self) -> None:
        form = ttk.LabelFrame(
            self.root,
            text="比赛配置",
            padding=14,
        )

        form.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=12,
            pady=(12, 8),
        )

        form.columnconfigure(1, weight=1)

        fields = [
            ("比赛名称", "match_name"),
            ("比赛开始时间", "match_start_time"),
            ("提前启动（分钟）", "advance_start_minutes"),
            ("最大录制时长（分钟）", "max_recording_minutes"),
            ("网站 A URL", "site_a_url"),
            ("网站 B URL", "site_b_url"),
        ]

        for row, (label, key) in enumerate(fields):
            ttk.Label(
                form,
                text=label,
            ).grid(
                row=row,
                column=0,
                sticky="w",
                padx=(0, 12),
                pady=5,
            )

            entry = ttk.Entry(
                form,
                textvariable=self.variables[key],
            )

            entry.grid(
                row=row,
                column=1,
                columnspan=2,
                sticky="ew",
                pady=5,
            )

            if key == "match_start_time":
                if not self.variables[key].get():
                    self.variables[key].set(
                        "例如：2026-08-20 20:00"
                    )

                entry.bind(
                    "<FocusIn>",
                    self._clear_time_hint,
                )

        directory_row = len(fields)

        ttk.Label(
            form,
            text="OBS 录制目录",
        ).grid(
            row=directory_row,
            column=0,
            sticky="w",
            padx=(0, 12),
            pady=5,
        )

        ttk.Entry(
            form,
            textvariable=self.variables[
                "obs_recording_directory"
            ],
        ).grid(
            row=directory_row,
            column=1,
            sticky="ew",
            pady=5,
        )

        ttk.Button(
            form,
            text="选择目录",
            command=self.choose_recording_directory,
        ).grid(
            row=directory_row,
            column=2,
            padx=(8, 0),
            pady=5,
        )

        obs_row = directory_row + 1

        ttk.Label(
            form,
            text="OBS WebSocket 地址",
        ).grid(
            row=obs_row,
            column=0,
            sticky="w",
            padx=(0, 12),
            pady=5,
        )

        obs_connection = ttk.Frame(form)

        obs_connection.grid(
            row=obs_row,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=5,
        )

        obs_connection.columnconfigure(0, weight=1)

        ttk.Entry(
            obs_connection,
            textvariable=self.variables["obs_host"],
        ).grid(
            row=0,
            column=0,
            sticky="ew",
        )

        ttk.Label(
            obs_connection,
            text="端口",
        ).grid(
            row=0,
            column=1,
            padx=(12, 4),
        )

        ttk.Entry(
            obs_connection,
            textvariable=self.variables["obs_port"],
            width=8,
        ).grid(
            row=0,
            column=2,
        )

        password_row = obs_row + 1

        ttk.Label(
            form,
            text="OBS WebSocket 密码",
        ).grid(
            row=password_row,
            column=0,
            sticky="w",
            padx=(0, 12),
            pady=5,
        )

        ttk.Entry(
            form,
            textvariable=self.variables["obs_password"],
            show="*",
        ).grid(
            row=password_row,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=5,
        )

        button_bar = ttk.Frame(form)

        button_bar.grid(
            row=password_row + 1,
            column=0,
            columnspan=3,
            sticky="e",
            pady=(10, 0),
        )

        ttk.Button(
            button_bar,
            text="重新读取配置",
            command=self.reload_config,
        ).grid(row=0, column=0, padx=4)

        ttk.Button(
            button_bar,
            text="保存配置",
            command=self.save_current_config,
        ).grid(row=0, column=1, padx=4)

        ttk.Button(
            button_bar,
            text="测试配置",
            command=self.test_configuration,
        ).grid(row=0, column=2, padx=4)

        ttk.Button(
            button_bar,
            text="测试 OBS 连接",
            command=self.test_obs_connection,
        ).grid(row=0, column=3, padx=4)

        ttk.Button(
            button_bar,
            text="测试浏览器",
            command=self.test_browser,
        ).grid(row=0, column=4, padx=4)

        self.start_task_button = ttk.Button(
            button_bar,
            text="开始任务",
            command=self.start_task,
        )

        self.start_task_button.grid(
            row=0,
            column=5,
            padx=4,
        )

        self.stop_task_button = ttk.Button(
            button_bar,
            text="停止任务",
            command=self.stop_task,
            state="disabled",
        )

        self.stop_task_button.grid(
            row=0,
            column=6,
            padx=4,
        )

        log_frame = ttk.LabelFrame(
            self.root,
            text="运行日志",
            padding=8,
        )

        log_frame.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=12,
            pady=(0, 12),
        )

        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=15,
            state="disabled",
            wrap="word",
        )

        self.log_text.grid(
            row=0,
            column=0,
            sticky="nsew",
        )

    # =========================================================
    # 配置
    # =========================================================

    def _clear_time_hint(self, event: tk.Event) -> None:
        if (
            self.variables["match_start_time"].get()
            == "例如：2026-08-20 20:00"
        ):
            self.variables["match_start_time"].set("")

    def choose_recording_directory(self) -> None:
        selected_directory = filedialog.askdirectory(
            title="选择 OBS 录制目录"
        )

        if selected_directory:
            self.variables[
                "obs_recording_directory"
            ].set(selected_directory)

            self.log(
                f"已选择录制目录：{selected_directory}"
            )

    def _collect_config(self) -> AppConfig:
        values = {
            name: variable.get().strip()
            for name, variable in self.variables.items()
        }

        if (
            values["match_start_time"]
            == "例如：2026-08-20 20:00"
        ):
            values["match_start_time"] = ""

        return AppConfig(**values)

    def _apply_config(self, config: AppConfig) -> None:
        for name, variable in self.variables.items():
            variable.set(getattr(config, name))

    def reload_config(self) -> None:
        self._apply_config(load_config())
        self.log("已从磁盘重新读取配置。")

    def save_current_config(self) -> None:
        config_path = save_config(
            self._collect_config()
        )

        self.log(
            f"配置已保存：{config_path}"
        )

        messagebox.showinfo(
            "保存成功",
            "配置已保存。",
        )

    def test_configuration(self) -> None:
        config = self._collect_config()

        errors = self._validate_config(config)

        if errors:
            message = "\n".join(errors)

            self.log(
                "配置测试失败："
                + message.replace("\n", "；")
            )

            messagebox.showwarning(
                "配置测试未通过",
                message,
            )
            return

        save_config(config)

        self.log(
            "配置测试通过：格式有效，配置已自动保存。"
        )

        messagebox.showinfo(
            "配置测试通过",
            "基础配置有效，已保存。",
        )

    # =========================================================
    # OBS
    # =========================================================

    def test_obs_connection(self) -> None:
        config = self._collect_config()

        errors = self._validate_obs_connection(config)

        if errors:
            messagebox.showwarning(
                "无法测试 OBS",
                "\n".join(errors),
            )
            return

        self.log(
            "正在测试 OBS WebSocket 连接……"
        )

        self._run_in_background(
            lambda: OBSController(
                self._connection_from_config(config)
            ).test_connection(),
            self._show_obs_test_success,
        )

    def _show_obs_test_success(
        self,
        version: str,
    ) -> None:
        self.log(
            f"OBS 连接测试通过：OBS Studio {version}。"
        )

        messagebox.showinfo(
            "OBS 连接成功",
            f"已连接到 OBS Studio {version}。",
        )

    # =========================================================
    # 浏览器测试
    # =========================================================

    def test_browser(self) -> None:
        config = self._collect_config()

        errors = self._validate_sites(config)

        if errors:
            messagebox.showwarning(
                "无法测试浏览器",
                "\n".join(errors),
            )
            return

        self.log(
            "正在启动 Playwright 浏览器……"
        )

        def work():
            self.browser.start()

            page_a, page_b = self.browser.open_sites(
                config.site_a_url,
                config.site_b_url,
            )

            self._prepare_backup_page("A", page_a)
            self._prepare_backup_page("B", page_b)

            return (
                page_a,
                page_b,
                page_a.title(),
                page_b.title() if page_b is not None else "未配置",
                page_a.url,
                page_b.url if page_b is not None else "",
            )

        def success(result) -> None:
            (
                page_a,
                page_b,
                title_a,
                title_b,
                url_a,
                url_b,
            ) = result

            self.page_a = page_a
            self.page_b = page_b

            self.log(
                f"网站 A 打开成功：{title_a}"
            )

            self.log(
                f"网站 A 地址：{url_a}"
            )

            self.log(
                f"网站 B：{title_b if page_b is not None else '未配置，未打开'}"
            )

            self.log(
                f"网站 B 地址：{url_b}"
            )

            self.log(
                "Playwright 浏览器测试通过。"
            )

            messagebox.showinfo(
                "浏览器测试成功",
                "网站 A 已打开。"
                + ("网站 B 也已打开。" if page_b is not None else "网站 B 未配置。"),
            )

        self._run_in_background(
            work,
            success,
            error_title="浏览器操作失败",
        )

    # =========================================================
    # 定时任务
    # =========================================================

    def start_task(self) -> None:
        config = self._collect_config()

        errors = (
            self._validate_config(config)
            + self._validate_obs_connection(config)
        )

        if errors:
            messagebox.showwarning(
                "无法启动任务",
                "\n".join(errors),
            )
            return

        save_config(config)

        # 如果之前存在旧监控循环，先彻底作废
        self._invalidate_monitor_loop()

        self.task_active = True
        self.recording_started_by_app = False
        self.current_source = None
        self.source_failure_counts = {"A": 0, "B": 0}
        self._cancel_recording_timer()
        self.recording_deadline = None

        self.start_task_button.configure(
            state="disabled"
        )

        self.stop_task_button.configure(
            state="normal"
        )

        self.scheduled_time = scheduled_start_time(
            config.match_start_time,
            config.advance_start_minutes,
        )

        self.log(
            "定时任务已启动，"
            f"计划录制开始时间："
            f"{self.scheduled_time.strftime(TIME_FORMAT)}。"
        )

        self._check_schedule()

    def _check_schedule(self) -> None:
        if (
            not self.task_active
            or self.scheduled_time is None
        ):
            return

        remaining_seconds = (
            self.scheduled_time
            - datetime.now()
        ).total_seconds()

        if remaining_seconds <= 0:
            self.log(
                "已到录制时间，"
                "正在准备直播源和 OBS……"
            )

            self.scheduled_time = None

            self._start_recording_workflow()
            return

        if int(remaining_seconds) % 60 == 0:
            self.log(
                "定时任务等待中，"
                f"距离录制开始还有 "
                f"{int(remaining_seconds // 60)} 分钟。"
            )

        self.root.after(
            1000,
            self._check_schedule,
        )

    # =========================================================
    # 开始录制
    # =========================================================

    def _start_recording_workflow(self) -> None:
        if not self.task_active:
            return

        config = self._collect_config()

        def work():
            self.browser.start()

            page_a, page_b = self.browser.open_sites(
                config.site_a_url,
                config.site_b_url,
            )

            # 两个页面都先关闭声音并关闭弹幕；真正使用的页面稍后再激活。
            self._prepare_backup_page("A", page_a)
            self._prepare_backup_page("B", page_b)

            status_a = self._probe_source(
                "A",
                page_a,
                config.site_a_url,
            )
            status_b = (
                self._probe_source("B", page_b, config.site_b_url)
                if page_b is not None
                else SiteStatus(
                    name="B",
                    url="",
                    result=self._unconfigured_result(),
                )
            )

            monitor = SourceMonitor(
                page_a=page_a,
                page_b=page_b,
                site_a_url=config.site_a_url,
                site_b_url=config.site_b_url,
                interval=5,
            )

            selected = monitor.choose_site(
                status_a,
                status_b,
            )

            # A/B 都没有可用直播源时：等待重试，而不是直接失败退出。
            # 直播源经常在开赛前几分钟才拉流，需要轮询等待。
            waiting_logged = False
            while selected is None and self.task_active:
                if not waiting_logged:
                    self._safe_log(
                        "A、B 两个网站当前都没有可用直播源，"
                        "进入等待模式，每 5 秒检测一次……"
                    )
                    waiting_logged = True

                time.sleep(5)

                if not self.task_active:
                    break

                status_a, status_b = monitor.detect_once()
                selected = monitor.choose_site(
                    status_a,
                    status_b,
                )

                if selected is not None:
                    self._safe_log(
                        "检测到可用直播源，继续启动录制流程。"
                    )

            if not self.task_active:
                return None

            if selected is None:
                raise RuntimeError(
                    "A、B 两个网站当前都没有可用直播源。"
                )

            selected, player_state = self._activate_first_ready_source(
                selected.name,
                page_a,
                page_b,
                status_a,
                status_b,
            )

            self._close_source_page(
                "B" if selected.name == "A" else "A",
                page_a,
                page_b,
            )

            monitor.current_site = selected.name

            obs = OBSController(
                self._connection_from_config(config)
            )

            obs.test_connection()

            obs.start_recording(
                config.obs_recording_directory
            )

            return (
                page_a,
                page_b,
                monitor,
                selected,
                status_a,
                status_b,
                player_state,
            )

        self._run_in_background(
            work,
            self._recording_workflow_started,
            error_title="启动录制失败",
        )

    def _recording_workflow_started(
        self,
        result,
    ) -> None:
        if not self.task_active:
            return

        if result is None:
            # work 返回 None 表示等待期间任务被取消（用户点了停止）。
            self.log("任务已取消，录制流程未启动。")
            return

        (
            page_a,
            page_b,
            monitor,
            selected,
            status_a,
            status_b,
            player_state,
        ) = result

        self.page_a = page_a
        self.page_b = page_b

        self.source_monitor = monitor

        self.recording_started_by_app = True
        self.current_source = selected.name

        self.log(
            f"网站 A：{status_a.result.status} | "
            f"评分：{status_a.result.score} | "
            f"播放：{status_a.result.video_playing}"
        )

        self.log(
            f"网站 B：{status_b.result.status} | "
            f"评分：{status_b.result.score} | "
            f"播放：{status_b.result.video_playing}"
        )

        self.log(
            f"当前选择：网站 {selected.name}"
        )

        self.log(
            f"当前地址：{selected.url}"
        )

        self.log(
            "播放器状态："
            f"全屏={player_state.fullscreen}，"
            f"静音={player_state.muted}，"
            f"播放={player_state.playing}，"
            f"画质={player_state.quality or '页面未返回名称'}"
        )

        self.log(
            "OBS 录制已开始。"
            "正在持续监控直播源。"
        )

        config = self._collect_config()
        duration_minutes = int(config.max_recording_minutes)
        self.recording_deadline = datetime.now().replace(microsecond=0) + timedelta(
            minutes=duration_minutes
        )
        self.recording_stop_after_id = self.root.after(
            duration_minutes * 60 * 1000,
            self._auto_stop_recording,
        )
        self.log(
            f"最大录制时长：{duration_minutes} 分钟，"
            f"预计停止时间：{self.recording_deadline.strftime('%Y-%m-%d %H:%M:%S')}。"
        )

        # 确保只启动一个监控循环
        self._start_monitor_loop()

    # =========================================================
    # 直播源监控
    # =========================================================

    def _invalidate_monitor_loop(self) -> None:
        """
        让之前所有已经排队的监控回调失效。
        """

        self.monitor_loop_id += 1
        self.monitor_loop_active = False

    def _start_monitor_loop(self) -> None:
        if not self.task_active:
            return

        if self.source_monitor is None:
            return

        # 已经有一个循环在运行时，禁止再次启动
        if self.monitor_loop_active:
            return

        self.monitor_loop_active = True

        # 当前这一轮监控的唯一 ID
        loop_id = self.monitor_loop_id + 1
        self.monitor_loop_id = loop_id

        self._run_monitor_detection(loop_id)

    def _run_monitor_detection(
        self,
        loop_id: int,
    ) -> None:
        if not self.task_active:
            self.monitor_loop_active = False
            return

        if loop_id != self.monitor_loop_id:
            return

        if self.source_monitor is None:
            self.monitor_loop_active = False
            return

        monitor = self.source_monitor

        def work():
            return monitor.detect_once()

        self._run_in_background(
            work,
            lambda result: self._process_monitor_result(
                result,
                loop_id,
            ),
            error_title="直播源检测失败",
            on_error=lambda error: self._monitor_detection_failed(
                error,
                loop_id,
            ),
        )

    def _monitor_detection_failed(self, error, loop_id: int) -> None:
        if not self.task_active or loop_id != self.monitor_loop_id:
            return

        self.log(f"直播源检测异常：{error}。准备刷新当前源并继续监控。")

        if self.current_source is not None:
            site = self.current_source
            self.monitor_loop_active = False
            self._run_in_background(
                lambda: self._refresh_source(site),
                lambda _: self._schedule_next_monitor(loop_id),
                error_title="刷新直播源失败",
                on_error=lambda err: self._schedule_next_monitor(loop_id),
            )
            return

        self._schedule_next_monitor(loop_id)

    def _process_monitor_result(
        self,
        result,
        loop_id: int,
    ) -> None:
        # 旧监控循环的结果直接丢弃
        if loop_id != self.monitor_loop_id:
            return

        if not self.task_active:
            self.monitor_loop_active = False
            return

        if self.source_monitor is None:
            self.monitor_loop_active = False
            return

        status_a, status_b = result

        statuses = {"A": status_a, "B": status_b}
        current_status = statuses.get(self.current_source)

        if self.current_source is not None:
            if current_status and SourceMonitor._is_playing(current_status):
                self.source_failure_counts[self.current_source] = 0
            else:
                self.source_failure_counts[self.current_source] += 1

                # 先刷新当前页几次，给网络抖动和播放器重连留出时间。
                if self.source_failure_counts[self.current_source] < 3:
                    self.log(
                        f"网站 {self.current_source} 播放异常，"
                        f"第 {self.source_failure_counts[self.current_source]} 次重试并刷新页面。"
                    )
                    site = self.current_source
                    self.monitor_loop_active = False
                    self._run_in_background(
                        lambda: self._refresh_source(site),
                        lambda _: self._schedule_next_monitor(loop_id),
                        error_title="刷新直播源失败",
                        on_error=lambda err: self._schedule_next_monitor(loop_id),
                    )
                    return

                alternate = "B" if self.current_source == "A" else "A"
                self.log(
                    f"网站 {self.current_source} 连续恢复失败，"
                    f"重新打开备用网站 {alternate}。"
                )
                # URL 在主线程取好再传入后台线程，
                # 避免后台线程读取 Tk 变量。
                alternate_url = self._url_for_site(alternate)
                self.monitor_loop_active = False
                self._run_in_background(
                    lambda: self._reopen_and_detect(
                        alternate,
                        alternate_url,
                    ),
                    lambda fresh: self._choose_and_maybe_switch(
                        fresh,
                        loop_id,
                    ),
                    error_title="切换备用源失败",
                    on_error=lambda err: self._schedule_next_monitor(loop_id),
                )
                return

        self._choose_and_maybe_switch(
            (status_a, status_b),
            loop_id,
        )

    def _reopen_and_detect(self, alternate: str, url: str):
        """后台线程：重新打开备用源并重新检测两个源。"""

        self._reopen_source(alternate, url)
        return self.source_monitor.detect_once()

    def _choose_and_maybe_switch(
        self,
        fresh_result,
        loop_id: int,
    ) -> None:
        """主线程：选择最佳源；需要切换时把切换操作放到后台线程。"""

        if not self.task_active or loop_id != self.monitor_loop_id:
            return

        status_a, status_b = fresh_result

        selected = self.source_monitor.choose_site(
            status_a,
            status_b,
        )

        self.log(
            f"网站 A：{status_a.result.status} | "
            f"评分：{status_a.result.score} | "
            f"播放：{status_a.result.video_playing}"
        )

        self.log(
            f"网站 B：{status_b.result.status} | "
            f"评分：{status_b.result.score} | "
            f"播放：{status_b.result.video_playing}"
        )

        if selected is None:
            self.log(
                "当前 A/B 均无可用直播源，"
                "保持 OBS 录制。"
            )
            self._schedule_next_monitor(loop_id)
            return

        if selected.name == self.current_source:
            self.log(
                f"当前直播源：网站 {selected.name}"
            )
            self._schedule_next_monitor(loop_id)
            return

        # 需要切换直播源：切换操作全部放到后台线程执行，
        # 避免页面加载/播放器控制阻塞 GUI。
        old_source = self.current_source
        self.monitor_loop_active = False
        self._run_in_background(
            lambda: self._switch_source_work(
                selected.name,
                old_source,
            ),
            lambda player_state: self._switch_finished(
                player_state,
                selected,
                old_source,
                loop_id,
            ),
            error_title="直播源切换失败",
            on_error=lambda err: self._monitor_switch_failed(
                err,
                loop_id,
            ),
        )

    def _switch_source_work(self, site: str, old_source: str | None) -> PlayerState:
        """后台线程：停用旧源、激活新源、关闭旧源页面。返回新源状态。"""

        if old_source is not None:
            self._deactivate_source(
                old_source,
                self.page_a,
                self.page_b,
            )

        player_state = self._activate_source(
            site,
            self.page_a,
            self.page_b,
        )

        if old_source is not None:
            self._close_source_page(
                old_source,
                self.page_a,
                self.page_b,
            )

        return player_state

    def _switch_finished(
        self,
        player_state: PlayerState,
        selected,
        old_source: str | None,
        loop_id: int,
    ) -> None:
        if not self.task_active or loop_id != self.monitor_loop_id:
            self.monitor_loop_active = False
            return

        # 关键：切换后同步 SourceMonitor 内部的当前源记录，
        # 否则 choose_site 会一直以为旧源还是当前源，导致 A/B 来回切换。
        self.current_source = selected.name
        if self.source_monitor is not None:
            self.source_monitor.current_site = selected.name

        if old_source is None:
            self.log(
                f"当前直播源：网站 {selected.name}"
            )
        else:
            self.log(
                f"直播源发生切换："
                f"{old_source} → {selected.name}"
            )

            self.log(
                f"当前地址：{selected.url}"
            )

            self.log(
                "备用源已激活："
                f"全屏={player_state.fullscreen}，"
                f"静音={player_state.muted}"
            )

        self._schedule_next_monitor(loop_id)

    def _monitor_switch_failed(self, error, loop_id: int) -> None:
        if not self.task_active or loop_id != self.monitor_loop_id:
            self.monitor_loop_active = False
            return

        self.log(
            f"直播源切换失败：{error}。继续监控当前源。"
        )
        self._schedule_next_monitor(loop_id)

    def _schedule_next_monitor(self, loop_id: int) -> None:
        """统一安排下一轮监控检测（只能在主线程调用）。"""

        self.monitor_loop_active = False

        if not self.task_active:
            return

        if loop_id != self.monitor_loop_id:
            return

        self.root.after(
            5000,
            lambda: self._start_monitor_loop_if_valid(
                loop_id
            ),
        )

    def _start_monitor_loop_if_valid(
        self,
        loop_id: int,
    ) -> None:
        if not self.task_active:
            return

        if loop_id != self.monitor_loop_id:
            return

        if self.monitor_loop_active:
            return

        self._start_monitor_loop()

    # =========================================================
    # 停止任务
    # =========================================================

    def stop_task(self) -> None:
        if not self.task_active:
            return

        self.task_active = False
        self.scheduled_time = None
        self._cancel_recording_timer()
        self.recording_deadline = None

        # 立即让所有旧监控回调失效
        self._invalidate_monitor_loop()

        if self.source_monitor is not None:
            try:
                self.source_monitor.stop()
            except Exception:
                pass

        self.start_task_button.configure(
            state="normal"
        )

        self.stop_task_button.configure(
            state="disabled"
        )

        if not self.recording_started_by_app:
            self.log(
                "定时任务已取消。"
            )
            return

        self.log(
            "正在请求 OBS 停止录制……"
        )

        config = self._collect_config()

        self._run_in_background(
            lambda: OBSController(
                self._connection_from_config(config)
            ).stop_recording(),
            self._recording_stopped,
        )

    def _recording_stopped(
        self,
        stopped: bool,
    ) -> None:
        self.recording_started_by_app = False
        self._cancel_recording_timer()
        self.recording_deadline = None

        self.log(
            "OBS 录制已停止。"
            if stopped
            else
            "OBS 当前未在录制，无需停止。"
        )

    # =========================================================
    # 后台任务
    # =========================================================

    def _cancel_recording_timer(self) -> None:
        if self.recording_stop_after_id is None:
            return

        try:
            self.root.after_cancel(self.recording_stop_after_id)
        except tk.TclError:
            pass

        self.recording_stop_after_id = None

    def _auto_stop_recording(self) -> None:
        self.recording_stop_after_id = None

        if not self.task_active or not self.recording_started_by_app:
            return

        self.log("已达到最大录制时长，正在自动停止 OBS 录制……")
        self.stop_task()

    def _run_in_background(
        self,
        work,
        on_success,
        error_title: str = "操作失败",
        on_error=None,
    ) -> None:
        future = self.executor.submit(work)

        def process_result() -> None:
            try:
                result = future.result()

            except BrowserControllerError as error:
                self.log(str(error))

                if on_error is not None:
                    on_error(error)
                    return

                messagebox.showerror(
                    error_title,
                    str(error),
                )

            except OBSControllerError as error:
                self.log(str(error))

                if on_error is not None:
                    on_error(error)
                    return

                messagebox.showerror(
                    "OBS 操作失败",
                    str(error),
                )

            except Exception as error:
                self.log(
                    f"发生未预期的错误：{error}"
                )

                if on_error is not None:
                    on_error(error)
                    return

                messagebox.showerror(
                    error_title,
                    str(error),
                )

            else:
                on_success(result)

        future.add_done_callback(
            lambda _: self.root.after(
                0,
                process_result,
            )
        )

    # =========================================================
    # 配置验证
    # =========================================================

    @staticmethod
    def _prepare_backup_page(site: str, page) -> None:
        """准备备用页，但不抢占当前浏览器焦点，也不打开声音。"""

        if page is None:
            return

        try:
            adapter_for(site, page).prepare(activate=False)
        except PlayerControlError:
            # 页面可能还在广告/登录跳转中，后续检测和切换会再次尝试。
            pass

    def _probe_source(self, site: str, page, url: str) -> SiteStatus:
        """串行唤醒并检测单个来源，确保另一个来源不会同时出声。"""

        page.bring_to_front()
        try:
            adapter_for(site, page).prepare(activate=True)
        except PlayerControlError:
            pass

        result = LiveDetector(page).detect()

        try:
            adapter_for(site, page).deactivate()
        except Exception:
            pass

        return SiteStatus(name=site, url=url, result=result)

    @staticmethod
    def _unconfigured_result():
        return DetectionResult(
            status="未配置",
            score=0,
            page_ok=False,
            player_found=False,
            live_text_found=False,
            error_found=False,
            video_exists=False,
            video_playing=False,
            current_time=0.0,
            video_width=0,
            video_height=0,
            details="网站 B 未配置",
        )

    def _refresh_source(self, site: str) -> None:
        page = self._page_for_site(site, self.page_a, self.page_b)
        if page is None or page.is_closed():
            return

        try:
            page.reload(wait_until="domcontentloaded", timeout=30000)
            adapter_for(site, page).prepare(activate=True)
            if self.source_monitor is not None:
                self.source_monitor.reset_progress(site)
        except Exception as error:
            self._safe_log(f"网站 {site} 刷新失败：{error}")

    def _reopen_source(self, site: str, url: str) -> None:
        if not url:
            self._safe_log(f"网站 {site} 未配置备用 URL，继续等待当前源恢复。")
            return

        page = self.browser.open_page(url)
        self._prepare_backup_page(site, page)

        if site == "A":
            self.page_a = page
        else:
            self.page_b = page

        if self.source_monitor is not None:
            self.source_monitor.replace_page(site, page)

    def _url_for_site(self, site: str) -> str:
        config = self._collect_config()
        return config.site_a_url if site == "A" else config.site_b_url

    def _close_source_page(self, site: str, page_a, page_b) -> None:
        page = self._page_for_site(site, page_a, page_b)
        if page is None or page.is_closed():
            return
        self.browser.close_page(page)

    @staticmethod
    def _deactivate_source(site: str, page_a, page_b) -> None:
        page = OBSMatchRecorderApp._page_for_site(site, page_a, page_b)
        if page is None or page.is_closed():
            return
        adapter_for(site, page).deactivate()

    @staticmethod
    def _page_for_site(site: str, page_a, page_b):
        return page_a if site == "A" else page_b

    def _activate_source(self, site: str, page_a, page_b) -> PlayerState:
        page = self._page_for_site(site, page_a, page_b)

        for other_site in ("A", "B"):
            if other_site == site:
                continue
            other_page = self._page_for_site(other_site, page_a, page_b)
            if other_page is not None and not other_page.is_closed():
                adapter_for(other_site, other_page).deactivate()

        page.bring_to_front()

        last_state: PlayerState | None = None
        for _ in range(3):
            state = adapter_for(site, page).prepare(activate=True)
            last_state = state
            if state.ready_for_recording:
                if not self.browser.ensure_foreground_and_fullscreen(page):
                    raise PlayerControlError(
                        "Chrome 窗口未能置前并进入浏览器全屏。"
                    )
                return adapter_for(site, page).inspect()

        raise PlayerControlError(
            f"网站 {site} 未达到录制条件："
            f"播放={last_state.playing if last_state else False}，"
            f"静音={last_state.muted if last_state else True}，"
            f"全屏={last_state.fullscreen if last_state else False}。"
        )

    def _activate_first_ready_source(
        self,
        preferred_site: str,
        page_a,
        page_b,
        status_a,
        status_b,
    ):
        order = [preferred_site, "B" if preferred_site == "A" else "A"]
        statuses = {"A": status_a, "B": status_b}
        errors: list[str] = []

        for site in order:
            if site != preferred_site and not SourceMonitor._is_playing(statuses[site]):
                continue
            try:
                state = self._activate_source(site, page_a, page_b)
                return statuses[site], state
            except PlayerControlError as error:
                errors.append(f"网站 {site}：{error}")

        raise PlayerControlError(
            "首选源和备用源都未达到录制条件。\n" + "\n".join(errors)
        )

    @staticmethod
    def _validate_sites(
        config: AppConfig,
    ) -> list[str]:
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

    @staticmethod
    def _validate_config(
        config: AppConfig,
    ) -> list[str]:
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

        errors.extend(
            OBSMatchRecorderApp._validate_sites(
                config
            )
        )

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

    @staticmethod
    def _validate_obs_connection(
        config: AppConfig,
    ) -> list[str]:
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

    @staticmethod
    def _connection_from_config(
        config: AppConfig,
    ) -> OBSConnection:
        return OBSConnection(
            host=config.obs_host,
            port=int(config.obs_port),
            password=config.obs_password,
        )

    # =========================================================
    # 日志
    # =========================================================

    def _safe_log(self, message: str) -> None:
        """线程安全的日志：把日志投递到主线程再写入界面。

        后台工作线程中不允许直接操作 Tk 控件，
        必须通过 root.after 回到主线程执行。
        """

        try:
            self.root.after(0, lambda: self.log(message))
        except tk.TclError:
            # 窗口可能已关闭
            pass

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        self.log_text.configure(
            state="normal"
        )

        self.log_text.insert(
            "end",
            f"[{timestamp}] {message}\n",
        )

        self.log_text.see("end")

        self.log_text.configure(
            state="disabled"
        )

    # =========================================================
    # 关闭
    # =========================================================

    def on_close(self) -> None:
        if (
            self.task_active
            and not messagebox.askyesno(
                "退出程序",
                "当前任务仍在运行。"
                "退出将取消定时任务，"
                "但不会停止 OBS 录制。"
                "确定退出吗？",
            )
        ):
            return

        self.task_active = False

        self._cancel_recording_timer()

        self._invalidate_monitor_loop()

        if self.source_monitor is not None:
            try:
                self.source_monitor.stop()
            except Exception:
                pass

        try:
            self.browser.close()
        except Exception:
            pass

        self.executor.shutdown(
            wait=False,
            cancel_futures=True,
        )

        self.root.destroy()


def main() -> None:
    root = tk.Tk()

    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass

    OBSMatchRecorderApp(root)

    root.mainloop()


if __name__ == "__main__":
    main()
