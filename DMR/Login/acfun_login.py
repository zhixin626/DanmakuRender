# -*- coding: utf-8 -*-
"""
acfun_login.py
打开浏览器登录 AcFun，检测到登录成功后自动保存 Cookie 并关闭窗口。
每个账号保留一份浏览器资料，之后可以在网页端一键更新（或加 --renew）。

依赖安装（仅需一次）：
    pip install playwright
    playwright install chromium

用法示例（在项目根目录下运行）：
    python DMR/Login/acfun_login.py 主账号           # 保存到 .login_info/A站_主账号.json
    python DMR/Login/acfun_login.py 主账号 --renew   # 后台续期，不弹窗
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from DMR.Login.browser_login import main

if __name__ == '__main__':
    raise SystemExit(main(['acfun', *sys.argv[1:]]))
