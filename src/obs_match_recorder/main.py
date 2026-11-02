"""OBS 比赛录制助手：GUI 主程序。

业务逻辑（直播源管理、监控循环）在 source_manager.py，
配置校验在 config.py，本文件只负责界面、任务编排与日志。
"""

from __future__ import annotations

import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .browser import BrowserController, BrowserControllerError
from .config import (
    AppConfig,
    connection_from_config,
    load_config,
    save_config,
    validate_config,
    validate_obs_connection,
    validate_sites,
)
from .obs_controller import OBSController, OBSControllerError
from .scheduler import TIME_FORMAT, scheduled_start_time
from .source_manager import SourceManager


class OBSMatchRecorderApp:
    APP_VERSION = "V2.0"
    RECORDING_HEALTH_INTERVAL_MS = 60 * 1000
    MAX_UNCHANGED_OUTPUT_CHECKS = 2
    # Playwright Sync API 不能跨线程使用；所有后台浏览器/OBS任务共用一个线程。
    BACKGROUND_WORKER_COUNT = 1

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"OBS 比赛录制助手 {self.APP_VERSION}")
        self.root.minsize(820, 700)

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        self.variables = {
            "match_name": tk.StringVar(),
            "match_start_time": tk.StringVar(),
            "advance_start_minutes": tk.StringVar(value="5"),
            "max_recording_minutes": tk.StringVar(value="150"),
            "site_a_url": tk.StringVar(),
            "site_b_url": tk.StringVar(value="https://jlyuntianly.top/live/6"),
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
        self.recording_health_after_id = None
        self.recording_health_active = False
        self.recording_recovery_active = False
        self.last_obs_frame_fingerprint: str | None = None
        self.unchanged_obs_frame_checks = 0

        self.browser = BrowserController()

        # Playwright Sync API 的 Browser/Page 绑定创建它们的线程。
        # 启动、监控、健康检查和恢复必须复用同一个工作线程，否则会触发
        # "Cannot switch to a different thread"，并使 Sync API 无法重连。
        self.executor = ThreadPoolExecutor(
            max_workers=self.BACKGROUND_WORKER_COUNT,
            thread_name_prefix="obs-match-recorder",
        )

        # 直播源管理：页面操作 + 监控循环 + 源切换
        self.sources = SourceManager(
            browser=self.browser,
            root=self.root,
            executor=self.executor,
            log=self.log,
            safe_log=self._safe_log,
            url_for_site=self._url_for_site,
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
            ("咪咕体育 URL", "site_a_url"),
            ("海星体育 URL", "site_b_url"),
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
        if not config.site_b_url.strip():
            self.variables["site_b_url"].set("https://jlyuntianly.top/live/6")

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

        errors = validate_config(config)

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

        errors = validate_obs_connection(config)

        if errors:
            messagebox.showwarning(
                "无法测试 OBS",
                "\n".join(errors),
            )
            return

        self.log(
            "正在确认 OBS 已启动并测试 WebSocket 连接……"
        )

        self._run_in_background(
            lambda: OBSController.ensure_available(
                connection_from_config(config)
            ),
            self._show_obs_test_success,
        )

    def _show_obs_test_success(
        self,
        availability,
    ) -> None:
        startup_message = (
            "已自动启动 OBS，"
            if availability.started_by_app
            else "OBS 已在运行，"
        )
        self.log(
            f"{startup_message}连接测试通过：OBS Studio {availability.version}。"
        )

        messagebox.showinfo(
            "OBS 连接成功",
            f"{startup_message}已连接到 OBS Studio {availability.version}。",
        )

    # =========================================================
    # 浏览器测试
    # =========================================================

    def test_browser(self) -> None:
        config = self._collect_config()

        errors = validate_sites(config)

        if not config.site_a_url and not config.site_b_url:
            errors.append("请至少填写一个直播 URL。")

        if errors:
            messagebox.showwarning(
                "无法测试浏览器",
                "\n".join(errors),
            )
            return

        self.log(
            "正在启动项目专用 Chrome 浏览器……"
        )

        def work():
            self.browser.start()

            page_a, page_b = self.sources.open_sites(
                config.site_a_url,
                config.site_b_url,
            )
            player_state = self.sources.prepare_haixing_test_playback()

            return (
                page_a,
                page_b,
                page_a.title() if page_a is not None else "未配置",
                page_b.title() if page_b is not None else "未配置",
                page_a.url if page_a is not None else "",
                page_b.url if page_b is not None else "",
                player_state,
            )

        def success(result) -> None:
            (
                page_a,
                page_b,
                title_a,
                title_b,
                url_a,
                url_b,
                player_state,
            ) = result

            self.log(
                f"咪咕体育：{title_a}"
            )

            self.log(
                f"咪咕体育地址：{url_a}"
            )

            self.log(
                f"海星体育：{title_b if page_b is not None else '未配置，未打开'}"
            )

            self.log(
                f"海星体育地址：{url_b}"
            )

            if player_state is not None:
                self.log(
                    f"海星体育：播放={player_state.playing}，"
                    f"静音={player_state.muted}，画质={player_state.quality}，"
                    f"全屏={player_state.fullscreen}。此测试未启动录制。"
                )
                # 不弹出成功对话框，保持 Chrome 全屏播放在前台。
                return

            messagebox.showinfo(
                "浏览器测试成功",
                "页面已打开，请手动确认播放画面。此测试不会启动 OBS 录制。",
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
            validate_config(config)
            + validate_obs_connection(config)
        )

        if errors:
            messagebox.showwarning(
                "无法启动任务",
                "\n".join(errors),
            )
            return

        save_config(config)

        # 如果之前存在旧监控循环，先彻底作废
        self.sources.reset()

        self.task_active = True
        self.sources.task_active = True
        self.recording_started_by_app = False
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
        self.log("正在确认 OBS 已启动……")

        def work():
            connection = connection_from_config(config)
            availability = OBSController.ensure_available(connection)
            obs = OBSController(connection)

            # 到达计划时间后先开始 OBS 录制，再准备直播页面。这样即使
            # 页面加载较慢，OBS 也已经进入录制状态。
            obs.start_recording(config.obs_recording_directory)

            # 采集方式优化不能阻断录制；某些 OBS 场景没有显示器采集源时
            # 该操作会失败，但不应因此停止刚刚启动的录制。
            try:
                display_capture_names = obs.prefer_windows_graphics_capture()
            except OBSControllerError:
                display_capture_names = []

            # 打开页面、探测、等待可用源、激活首选源
            result = self.sources.open_and_select(
                config.site_a_url,
                config.site_b_url,
            )

            if result is None:
                return None

            (
                page_a,
                page_b,
                monitor,
                selected,
                status_a,
                status_b,
                player_state,
            ) = result

            return (
                page_a,
                page_b,
                monitor,
                selected,
                status_a,
                status_b,
                player_state,
                availability,
                display_capture_names,
                None,
            )

        self._run_in_background(
            work,
            self._recording_workflow_started,
            error_title="启动录制失败",
            on_error=self._recording_workflow_failed,
        )

    def _recording_workflow_failed(self, error) -> None:
        """启动阶段失败时保留任务并自动重试，避免错过比赛。"""

        if not self.task_active:
            return

        self.log(
            f"启动录制失败：{error}。"
            "30 秒后将重新确认 OBS 并打开直播页面。"
        )
        self.root.after(
            30000,
            self._start_recording_workflow,
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
            availability,
            display_capture_names,
            frame_fingerprint,
        ) = result

        self.sources.page_a = page_a
        self.sources.page_b = page_b
        self.sources.source_monitor = monitor
        self.sources.current_site = selected.name

        self.recording_started_by_app = True
        self.last_obs_frame_fingerprint = frame_fingerprint
        self.unchanged_obs_frame_checks = 0

        self.log(
            "OBS 已自动启动。"
            if availability.started_by_app
            else "OBS 已在运行。"
        )
        if display_capture_names:
            self.log(
                "已将 OBS 显示器采集切换为 Windows Graphics Capture："
                + "、".join(display_capture_names)
            )

        self.log(
            f"咪咕视频：{status_a.result.status} | "
            f"评分：{status_a.result.score} | "
            f"播放：{status_a.result.video_playing}"
        )

        self.log(
            f"海星体育：{status_b.result.status} | "
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
        self.log(
            "录音设置：已自动静音 OBS 麦克风输入，"
            "保留桌面音频用于录制直播声音。"
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
        self.sources.start_monitor_loop()
        self._schedule_recording_health_check()

    # 停止任务
    # =========================================================

    def stop_task(self) -> None:
        if not self.task_active:
            return

        self.task_active = False
        self.scheduled_time = None
        self._cancel_recording_timer()
        self.recording_deadline = None

        # 停止直播源管理：作废所有旧监控回调并停止监控器
        self.sources.stop()
        self._cancel_recording_health_check()
        self.recording_recovery_active = False
        self.last_obs_frame_fingerprint = None
        self.unchanged_obs_frame_checks = 0

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

        def stop_work():
            self.sources.restore_active_cursor()
            return OBSController(
                connection_from_config(config)
            ).stop_recording()

        self._run_in_background(
            stop_work,
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

    def _schedule_recording_health_check(self) -> None:
        if self.recording_health_after_id is not None:
            try:
                self.root.after_cancel(self.recording_health_after_id)
            except tk.TclError:
                pass

        if not self.task_active or not self.recording_started_by_app:
            self.recording_health_after_id = None
            return

        self.recording_health_after_id = self.root.after(
            self.RECORDING_HEALTH_INTERVAL_MS,
            self._run_recording_health_check,
        )

    def _cancel_recording_health_check(self) -> None:
        if self.recording_health_after_id is None:
            return
        try:
            self.root.after_cancel(self.recording_health_after_id)
        except tk.TclError:
            pass
        self.recording_health_after_id = None

    def _run_recording_health_check(self) -> None:
        self.recording_health_after_id = None
        if (
            not self.task_active
            or not self.recording_started_by_app
            or self.recording_health_active
            or self.recording_recovery_active
        ):
            return

        self.recording_health_active = True
        config = self._collect_config()

        def work():
            obs = OBSController(connection_from_config(config))
            obs_recording = obs.is_recording()
            player_state, playback_ok = self.sources.check_active_playback()
            frame_fingerprint = obs.program_frame_fingerprint()
            return obs_recording, playback_ok, player_state, frame_fingerprint

        self._run_in_background(
            work,
            self._recording_health_checked,
            error_title="录制健康检查失败",
            on_error=self._recording_health_check_failed,
        )

    def _recording_health_check_failed(self, error) -> None:
        self.recording_health_active = False
        if not self.task_active or not self.recording_started_by_app:
            return
        self.log(f"录制健康检查异常：{error}。准备重启 Chrome 和录制流程。")
        self._start_recording_recovery("健康检查无法完成")

    def _recording_health_checked(self, result) -> None:
        self.recording_health_active = False
        if not self.task_active or not self.recording_started_by_app:
            return

        obs_recording, playback_ok, player_state, frame_fingerprint = result
        output_stalled = self._observe_obs_output(frame_fingerprint)
        if obs_recording and playback_ok and not output_stalled:
            self.log(
                "录制健康检查通过：OBS 正在录制，"
                "当前直播画面仍在播放。"
            )
            self._schedule_recording_health_check()
            return

        if obs_recording and playback_ok and output_stalled:
            self.log(
                "录制健康检查发现 OBS 输出画面连续两次未变化，"
                "正在重建 Chrome 播放和录制流程。"
            )
            self._start_recording_recovery("OBS 输出画面疑似冻结")
            return

        self.log(
            "录制健康检查失败："
            f"OBS录制={obs_recording}，"
            f"播放条件={playback_ok}，"
            f"播放器状态={player_state}。"
        )
        self._start_recording_recovery("OBS 或直播播放状态异常")

    def _observe_obs_output(self, frame_fingerprint: str) -> bool:
        """记录 OBS 节目画面指纹，连续不变时判定为采集画面冻结。"""

        if self.last_obs_frame_fingerprint != frame_fingerprint:
            self.last_obs_frame_fingerprint = frame_fingerprint
            self.unchanged_obs_frame_checks = 0
            return False

        self.unchanged_obs_frame_checks += 1
        return (
            self.unchanged_obs_frame_checks
            >= self.MAX_UNCHANGED_OUTPUT_CHECKS
        )

    def _start_recording_recovery(self, reason: str) -> None:
        if self.recording_recovery_active or not self.task_active:
            return

        self.recording_recovery_active = True
        self.last_obs_frame_fingerprint = None
        self.unchanged_obs_frame_checks = 0
        self.sources.invalidate_monitor_loop()
        config = self._collect_config()
        self.log(
            f"{reason}，正在停止当前录制并退出 Chrome，"
            "随后重新打开页面和 OBS……"
        )

        def work():
            connection = connection_from_config(config)
            OBSController.ensure_available(connection)
            obs = OBSController(connection)
            display_capture_names = obs.prefer_windows_graphics_capture()
            try:
                obs.stop_recording()
            except OBSControllerError:
                # OBS 已经停止时允许继续重建播放会话。
                pass

            if not self.task_active:
                return None

            result = self.sources.restart_session(
                config.site_a_url,
                config.site_b_url,
            )
            if not self.task_active:
                return None
            obs.start_recording(config.obs_recording_directory)
            frame_fingerprint = obs.program_frame_fingerprint()
            return result, display_capture_names, frame_fingerprint

        self._run_in_background(
            work,
            self._recording_recovery_finished,
            error_title="自动恢复录制失败",
            on_error=self._recording_recovery_failed,
        )

    def _recording_recovery_finished(self, result) -> None:
        self.recording_recovery_active = False
        if not self.task_active or result is None:
            return

        workflow_result, display_capture_names, frame_fingerprint = result
        (
            page_a,
            page_b,
            monitor,
            selected,
            status_a,
            status_b,
            player_state,
        ) = workflow_result
        self.sources.page_a = page_a
        self.sources.page_b = page_b
        self.sources.source_monitor = monitor
        self.sources.current_site = selected.name
        self.recording_started_by_app = True
        self.last_obs_frame_fingerprint = frame_fingerprint
        self.unchanged_obs_frame_checks = 0

        self.log(
            f"自动恢复完成：当前直播源为网站 {selected.name}，"
            f"播放={player_state.playing}，静音={player_state.muted}，"
            f"全屏={player_state.fullscreen}。"
        )
        if display_capture_names:
            self.log(
                "已确认 OBS 显示器采集使用 Windows Graphics Capture："
                + "、".join(display_capture_names)
            )
        self.sources.start_monitor_loop()
        self._schedule_recording_health_check()

    def _recording_recovery_failed(self, error) -> None:
        self.recording_recovery_active = False
        if not self.task_active:
            return
        self.log(f"自动恢复录制失败：{error}。3 分钟后再次检查并重试。")
        self._schedule_recording_health_check()

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
    # 配置辅助
    # =========================================================

    def _url_for_site(self, site: str) -> str:
        config = self._collect_config()
        return config.site_a_url if site == "A" else config.site_b_url

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
        self._cancel_recording_health_check()
        self.recording_recovery_active = False

        self.sources.close()

        # BrowserController 也必须由创建 Playwright Sync API 的工作线程关闭。
        # 不取消队列中的清理任务，确保 close 不会被 shutdown 丢弃。
        try:
            self.executor.submit(self.browser.close)
        except RuntimeError:
            pass

        self.executor.shutdown(wait=False, cancel_futures=False)

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
