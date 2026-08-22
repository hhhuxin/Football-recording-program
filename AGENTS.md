# AGENTS.md — 给编码代理（Codex / Claude Code 等）的开发指南

本文件是**面向 AI 编码代理的说明书**，每次修改代码前请先通读。
它的存在是为了弥补"一次性读完全局"的困难：本项目的关键约束和陷阱全部写在下面，遵守它们可以避免引入低级 bug。

---

## 1. 项目是什么

**OBS 比赛录制助手**：Windows 桌面程序（Tkinter），无人值守自动录制足球比赛直播。

核心链路：

```
定时到点 → 接管已登录的 Chrome（CDP :9222）→ 同时打开咪咕(A)/飞速(B) 两页
→ 探测哪个真的在播 → 优先 A，A 不行用 B（一用一备）
→ 自动：点播放 / 选最高画质 / 关弹幕 / 去静音 / 全屏
→ OBS WebSocket 开始录制 → 每 5 秒监控（卡顿刷新，连续 3 次失败切备用源）
→ 达到最大录制时长自动停止
```

需求文档：`docs/需求文档v1.0.txt`（详细）、`docs/需求文档v1.1.txt`（最新补充，以此为准）。
参考网页（咪咕/飞速的真实 HTML 快照）：`docs/reference/`，页面结构变了先来这里对照。

---

## 2. 项目结构（src 布局）

```
run.py                     # 入口，只需 python run.py 即可启动
src/obs_match_recorder/
├── main.py                # 【GUI 层】Tkinter 界面 + 任务编排 + 日志（~1000 行）
├── source_manager.py      # 【业务层】直播源管理：页面操作 + 监控循环 + 源切换（~690 行）
├── browser.py             # Playwright 连接已登录 Chrome（CDP 9222）
├── player_adapter.py      # 咪咕/飞速播放器控制（画质/弹幕/静音/全屏）
├── detector.py            # 页面直播状态检测（多信号评分）
├── monitor.py             # SourceMonitor：持续检测 + 卡顿判定 + 选源
├── obs_controller.py      # OBS WebSocket 录制控制
├── config.py              # 配置读写 + 全部校验函数
└── scheduler.py           # 比赛时间计算
tests/                     # pytest 测试
docs/                      # 需求文档 + 参考网页
thirdparty/                # OBS / Chrome 外部依赖说明
```

### 分层铁律

- **GUI 层（main.py）只做界面、编排、日志**，不直接操作浏览器页面。
- **业务层（source_manager.py）不碰 Tk 控件**，日志通过回调 `log`/`safe_log` 交给 GUI。
- 新增"页面怎么操作"的逻辑 → 放 `player_adapter.py` 或 `source_manager.py`。
- 新增"直播状态怎么判断"的逻辑 → 放 `detector.py` / `monitor.py`。
- 新增界面元素 → 放 `main.py`。
- **不要在 main.py 里塞业务逻辑，也不要在 source_manager.py 里 import tkinter。**

---

## 3. 最容易踩的坑（每条都出过 bug，务必遵守）

### 3.1 线程模型（最重要）

程序有两条线程上下文：
- **主线程（Tk）**：GUI 事件、`root.after` 回调、日志写入。
- **后台线程（ThreadPoolExecutor，max_workers=3）**：所有 Playwright / OBS 网络操作。

**规则：**
1. **Playwright 的重操作（打开页面、reload、detect、激活播放器、点击）必须放后台线程**，绝不能直接在 Tk 主线程同步执行——否则页面一慢，整个窗口卡死几十秒。
2. **后台线程绝不能直接操作 Tk 控件**（`log_text`、`StringVar` 等）。日志用 `_safe_log()`（内部 `root.after` 投递回主线程）；需要读配置 URL 时，在主线程取好再传进后台任务。
3. **监控循环的调度**：检测结果回到主线程后，用 `_run_in_background(work, on_success, on_error)` 提交下一步重操作，结果再回主线程。不要自己 `time.sleep` + 同步调用。
4. 每个后台任务完成后必须走 `_schedule_next_monitor(loop_id)` 安排下一轮，且要检查 `loop_id != self.monitor_loop_id`（旧循环回调直接丢弃）。

### 3.2 源切换（乒乓 bug 的血泪教训）

- `SourceManager.current_site` 是"程序认为的当前源"，`SourceMonitor.current_site` 是"选源器内部的当前源"。
- **切换源成功后必须同时更新两个**：
  ```python
  self.current_site = selected.name
  self.source_monitor.current_site = selected.name
  ```
  只更新一个会导致 `choose_site` 误以为旧源还是当前源 → A/B 来回切换。

### 3.3 无直播源时

- A/B 都没开播时**不能抛错退出**，必须等待重试（直播源常在开赛前几分钟才拉流）。
- `open_and_select()` 里已有等待循环（`while selected is None and self.task_active`），改这里时保持"可被 `task_active=False` 中断"。

### 3.4 浏览器接管方式

- 程序**不自己启动浏览器**，而是通过 CDP 连接用户已用 `start_chrome_debug.bat` 启动的调试 Chrome（端口 9222，独立 profile `ChromeRecorderProfile` 在项目根目录）。
- Chrome profile 路径由 `browser.py` 的 `_profile_directory()` 计算：`项目根 / ChromeRecorderProfile`（src 布局下是三级 `parent.parent.parent`）。**不要改成指向 src 里面。**

### 3.5 咪咕 / 飞速播放器要点（player_adapter.py）

- 咪咕：视频 `#m-player`；画质菜单 `.ratetype` → `.ratetype-content .item-rt`，**第一个可见项是当前账号可选的最高档**（不绕过付费）；全屏 `.zoom-btn`。
- 飞速：DPlayer；弹幕开关 `.dplayer-showdan-setting-input`；取消静音浮层 `.novouceFlag`（刷新后默认静音）；全屏 `.dplayer-full-icon`。
- 页面结构若对不上，先对照 `docs/reference/` 里的 HTML 快照，不要凭空改选择器。

### 3.6 其他

- 版本号：README / 窗口标题 / 代码注释中的版本号应统一（当前主线 V1.1）。
- 不要新增与现有模块功能重复的文件（曾经有过 `site_adapter.py`、`source_selector.py` 重复实现，已删除）。
- 所有关键操作遵循"尝试 → 操作 → 等待 → 验证结果 → 失败重试 → 多次失败记日志"的链路，**不要"点了就当作成功"**。

---

## 4. 测试

```powershell
python -m pytest            # 单元测试（无需外部环境）
python -m pytest --run-integration   # 集成测试（需调试 Chrome / OBS 已启动）
```

- 单元测试在 `tests/`，用 pytest 风格（`def test_*` + `assert`）。
- 需要真实浏览器/OBS 的测试标 `@pytest.mark.integration`，默认跳过。
- `tests/conftest.py` 负责把 `src/` 加进模块搜索路径，并提供 `browser` / `obs` fixture。
- **改完代码至少跑一次 `python -m pytest`，确保 6 个单元测试全绿；改完务必 `python -m py_compile` 检查语法。**

---

## 5. 改代码的流程（给代理的纪律）

1. 先读本文件（已在上下文），再读要改的模块全文，**不要凭猜测改**。
2. 一次只改一个文件、一个功能点。改完立刻验证（编译 + 相关测试）。
3. 大改动前先说明方案（准备怎么拆、怎么改），不要闷头写几百行。
4. 不要删除看起来"没用"的代码，除非确认全库无引用（用搜索确认）。
5. 不要"顺手重构"无关代码；保持改动最小化。
6. 用户没有要求时，**不要修改需求文档**；需求有矛盾时以 `docs/需求文档v1.1.txt` 为准并指出冲突。
