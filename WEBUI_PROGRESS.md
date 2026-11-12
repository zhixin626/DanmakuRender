# WebUI `webui/logs-polling` 进度

已完成：

- 控制台镜像按序号写入 `logs/console-启动时间.txt`；旧日志清理复用 `log_manager` 的保留天数/容量策略。
- SSE 每行发送 `id:`，支持 `Last-Event-ID` 和查询参数续传；断线重连不会重复历史。
- 初始历史通过 gzip 的 `/api/console/history` 一次加载；日志页滚动到顶部时按序号从文件加载更早内容。
- 日志页切到后台会主动断开 EventSource，回到前台按最后序号续接。
- 录制页时长和倒计时改为浏览器本地每秒计算；网络状态请求改为 3 秒一次，`startPolling` 防止请求重叠。

验证：`python -m compileall -q DMR main.py`；Flask smoke test 验证 gzip 历史返回、SSE `id`/续传；分支提交 `8ede4b2`、`8625b62`、`89ecb76`。

后续修正：SSE history 事件带最终 id，重连只追加断点后的行；日志文件句柄常驻并每 1000 行建立偏移索引，分页读取不再整文件扫描；日志页上限 50000 行且向上翻不裁掉最新内容；`/api/tasks` 支持 ETag/304。最新修复待提交。

验证补充：`tests/test_console_buffer.py` 3 passed（并发序号、跨内存/文件分页、U+2028/Last-Event-ID）；Playwright smoke 已验证初始历史、向上滚动、后台切换回来日志不丢。浏览器断网重连的完整网络故障模拟仍可在明天继续；Vue 重写按要求未做。
