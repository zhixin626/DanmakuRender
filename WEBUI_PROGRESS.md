# WebUI `webui/logs-polling` 进度

## 已完成

- 控制台日志按序号写入 `logs/console-启动时间.txt`（二进制、锁内写、每秒 flush、每 1000 行一个偏移索引）；旧文件清理复用 `log_manager` 的保留天数/容量策略。
- 首次加载历史走 gzip 的 `/api/console/history`；向上滚动按序号从内存/文件分页，可翻到本次启动第一行。
- SSE 每行带 `id`，`Last-Event-ID`/`last_id` 续传；页面切后台断开、回前台续上；前端停在底部时最多保留 50000 行。
- 录制页时长和倒计时由浏览器本地推算；任务状态 3 秒一次、请求不重叠。

## 本轮审查修复（云端会话）

- `get_console_history`：flush 挪进 `_lock`；内存够用时不再读文件；文件只读缺的那部分（≤ limit + 1000 行）；关闭日志文件也放进锁里，避免与读取竞争。
- 控制台镜像：一次写入大段文本时按行切分由平方复杂度改为线性。
- 日志页（`logs.html`）：
  - **修复**：断线续传的历史原来被插到页面顶部，并把 `firstId` 改错，导致向上翻页重复；现在首尾相接就追加，否则（缺口超出 5000 行、服务重启）整体替换。
  - **修复**：续传时会丢弃已收到但未渲染的行；现在按序号去重，发现缺口（如服务端队列满丢行）自动续传补齐。
  - **修复**：代理返回 502 / 登录过期等非 200 响应时 EventSource 会永久停止，现在 3 秒后手动重连。
  - 实时追加改为只追加文本节点，超出上限 2000 行才整体重绘一次（原来每帧重建 5 万行文本）。
- 录制页（`record.html`）/ `webapi.get_tasks_data`：
  - **修复**：第一个任务不在录制时 `/api/tasks` 抛 `UnboundLocalError`（后面的任务还会串用前一个任务的时间）。
  - 时长改为服务器给“已录制秒数”、浏览器按收到时刻累加，不再依赖手机/电脑时钟与服务器一致。
  - **修复**：`next_check_remaining` 为空时原本显示 `00:00`，现在恢复“即将检测…”。
  - 合并重复的时间格式化函数；首屏数据也记录收到时刻。
- `record.html`/`process.html` 的 `refreshTasks` 返回 Promise，`startPolling` 的防重叠才真正生效。

## 删除的无用代码

原则：不用的代码直接删，不为兼容旧代码保留分支和兜底判断。

- `/api/console` 接口和 `get_console_lines`（前端早已改用 SSE + `/api/console/history`）。
- `/api/tasks` 的 ETag/304：有任务录制或等待检测时数据每秒都变，基本命中不了，只增加判断。
- `get_tasks_data` 的 `duration`/`live_duration` 字符串和录制页模板里的首屏时间文本，统一由 JS 根据已录制秒数渲染。
- `install_console_tee` 不再可选目录，由 `configure_logging` 在创建 StreamHandler 前直接调用，`main.py` 不再重复计算日志目录。
- 日志页、录制页里接口必定返回的字段的 `|| []`、`|| Date.now()` 兜底。
- 服务重启后的 `last_id` 兜底：页面通过 `/api/instance` 检测到重启会自动刷新。

## 验证

- `python -m pytest tests`：8 passed（本地约 15 秒）。
  - `test_console_buffer.py`：并发序号、跨内存/文件分页、U+2028、10 万行逐页翻到第一行（每页读文件 ≤ limit+1000 行且序号连续）、边写边翻页。
  - `test_webui.py`：`get_tasks_data` 回归；Playwright 端到端断网恢复（Flask + 假日志线程，断开 SSE 3 秒，分 network 请求失败和 proxy 502 两种），断言页面行序号连续、无缺失、无重复。端到端测试需要 `node` 和 Node 版 `playwright`，没有时自动跳过。
- 用旧版 `logs.html` / `webapi.py` 跑新测试会失败，确认测试能抓到上面的问题。
- Playwright 冒烟：录制页、处理页无 JS 报错，录制时长/倒计时/“即将检测…”显示正确；`configure_logging` 会生成 `console-*.txt` 并写入 logging 输出。

## Windows 上跑测试

在 worktree 根目录用 Python 3.12 建独立 venv（目录名 `venv`，已被 `.gitignore` 忽略），不要往全局 Python 装包：

```
py -3.12 -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt pytest
venv\Scripts\python.exe -m pytest tests -v
```

- 项目需要 Python 3.10+（代码使用 `tuple[int, int] | None`），系统默认的 3.8 不能用。
- 断网恢复测试依赖 Node 版 Playwright，没有时显示 skipped，属正常。
- 主 DMR 在运行时不要在 worktree 里再启动 `main.py`（WebUI 端口冲突、重复录制）。

## 需要用户做

- 在电脑和手机浏览器上实际试用日志页、录制页、处理页：切后台再回来、一直往上翻到第一行、断网（关 Wi-Fi/Tailscale）再恢复。
- 观察关掉 Tailscale 低电量模式后的外网访问情况。
- 决定是否用 Vue 重写、界面风格和手机适配（方案见下）。

## 留给用户决定 / 已知限制

- 单次运行的 `console-*.txt` 不限大小，只在启动时按保留策略清理旧文件。
- 安全加固、Vue 重写按要求未做。

## Vue 方案对比（未开始）

| 方案 | 工作量 | 部署 | 维护 |
| --- | --- | --- | --- |
| A. 不用 Vue，继续优化现有 Jinja 模板 | 最小，按页面逐步改 | 不变 | 页面 JS 拼 HTML 字符串，逻辑多了难读 |
| B. Vue 3 单文件 `vue.global.prod.js` 放进 `static/`，模板里直接写组件 | 中等，可一页一页迁移 | 不变，无需 Node | 无构建、无类型检查；组件写在模板字符串里，规模大了会乱 |
| C. Vue 3 + Vite 构建，产物放进 `static/` | 最大，需要重写全部页面 | 改前端要在有 Node 的机器上构建再提交产物 | 组件化、类型检查、热更新，长期最好 |

推荐 **B**：DMR 是单机工具，部署保持“拉代码就能跑”最重要；B 能按页面渐进迁移（先迁录制页这种状态最多的页面），不需要 Node 环境。将来页面明显变多再考虑 C。
