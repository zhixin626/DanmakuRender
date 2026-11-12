import json
import os
import queue
import shutil
import subprocess
import threading
import time
from collections import deque
from types import SimpleNamespace

import pytest
from werkzeug.serving import make_server

import DMR.utils.console_buffer as console
from DMR.WebService.webapi import WebApi
from test_console_buffer import _close, _reset


def _webapi(engine=None):
    return WebApi((queue.Queue(), queue.Queue()), engine=engine, force_login=False)


def test_tasks_data_handles_offline_task_before_recording_one():
    started = time.time() - 65
    from datetime import datetime
    offline = SimpleNamespace(plat="bilibili", url="u1", stoped=True)
    recording = SimpleNamespace(
        plat="douyin", url="u2", stoped=False,
        segment_start_time=datetime.fromtimestamp(started),
        live_start_time=datetime.fromtimestamp(started - 60),
    )
    downloader = SimpleNamespace(download_tasks={"a": offline, "b": recording})
    engine = SimpleNamespace(plugin_dict={"downloader": {"class": downloader}}, task_dict={})

    tasks = {t["name"]: t for t in _webapi(engine).get_tasks_data()["recording_tasks"]}

    assert tasks["a"]["segment_elapsed"] is None
    assert 64 <= tasks["b"]["segment_elapsed"] <= 66
    assert 124 <= tasks["b"]["live_elapsed"] <= 126


def _node_playwright_available():
    if not shutil.which("node"):
        return False
    probe = subprocess.run(["node", "-e", "require('playwright')"], capture_output=True)
    return probe.returncode == 0


class _Outage:
    """测试替身：包住 WSGI app，访问 /__outage?on=1 时立刻结束正在推送的 SSE 并对新连接返回 502。"""

    def __init__(self, app):
        self.app = app
        self.active = threading.Event()

    def __call__(self, environ, start_response):
        if environ["PATH_INFO"] == "/__outage":
            if "on=1" in environ["QUERY_STRING"]:
                self.active.set()
            else:
                self.active.clear()
            start_response("204 No Content", [])
            return []
        if environ["PATH_INFO"] != "/api/console/stream":
            return self.app(environ, start_response)
        if self.active.is_set():   # 断网期间经反向代理访问时的表现
            start_response("502 Bad Gateway", [])
            return []
        return self._until_outage(self.app(environ, start_response))

    def _until_outage(self, body):
        try:
            for chunk in body:
                if self.active.is_set():
                    return
                yield chunk
        finally:
            body.close()


# 打开日志页 → 断开 SSE，几秒内重连都失败（network：请求发不出去；proxy：返回 502）→ 恢复，输出页面上的行与序号范围
_RESUME_SCRIPT = r"""
const { chromium } = require('playwright');
const base = process.env.BASE_URL;
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto(base + '/logs');
  await page.waitForFunction(() => lastId > 50);
  const network = process.env.OUTAGE_MODE === 'network';
  if (network) await page.route('**/api/console/stream**', route => route.abort('internetdisconnected'));
  await fetch(base + '/__outage?on=1');
  await page.waitForTimeout(500);
  const cutAt = await page.evaluate(() => lastId);
  await page.waitForTimeout(Number(process.env.OUTAGE_MS));
  const duringOutage = await page.evaluate(() => lastId);
  const statusDuringOutage = await page.textContent('#log-status');
  await fetch(base + '/__outage?on=0');
  if (network) await page.unroute('**/api/console/stream**');
  await page.waitForFunction(n => lastId > n, cutAt + Number(process.env.EXPECT_AFTER), { timeout: 20000 });
  const result = await page.evaluate(() => {
    flush();   // 把本帧尚未渲染的行刷到页面上，再与序号范围比对
    return { firstId, lastId, lines: document.getElementById('console-box').textContent.split('\n') };
  });
  console.log(JSON.stringify({ ...result, cutAt, duringOutage, statusDuringOutage }));
  await browser.close();
})().catch(err => { console.error(err); process.exit(1); });
"""


@pytest.mark.skipif(not _node_playwright_available(), reason="需要 node 和 playwright")
@pytest.mark.parametrize("mode", ["network", "proxy"])
def test_logs_page_resumes_without_gaps_after_outage(tmp_path, mode):
    tee = _reset(tmp_path)
    console._console_lines = deque(maxlen=1000)   # 断网期间的行都还在内存里，走续传而不是整页替换
    stop = threading.Event()

    def produce():
        i = 0
        while not stop.is_set():
            i += 1
            tee.write(f"tick-{i}\n")
            time.sleep(0.01)

    server = make_server("127.0.0.1", 0, _Outage(_webapi().create_app()), threaded=True)
    threads = [threading.Thread(target=produce), threading.Thread(target=server.serve_forever)]
    for thread in threads:
        thread.start()
    try:
        env = dict(
            os.environ,
            BASE_URL=f"http://127.0.0.1:{server.server_port}",
            OUTAGE_MODE=mode,
            OUTAGE_MS="3000",
            EXPECT_AFTER="300",
        )
        run = subprocess.run(
            ["node", "-e", _RESUME_SCRIPT], env=env, capture_output=True, text=True, timeout=60
        )
        assert run.returncode == 0, run.stderr
        result = json.loads(run.stdout)
    finally:
        stop.set()
        server.shutdown()
        for thread in threads:
            thread.join()
        _close()

    assert result["duringOutage"] == result["cutAt"]       # 断网期间确实没收到新行
    assert "断开" in result["statusDuringOutage"]
    assert result["lastId"] > result["cutAt"] + 300
    expected = [f"tick-{i}" for i in range(result["firstId"], result["lastId"] + 1)]
    assert result["lines"] == expected
