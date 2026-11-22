# -*- coding: utf-8 -*-
"""
douyu_login.py
打开浏览器登录斗鱼，检测到登录成功后自动保存 Cookie 并关闭窗口。
保存的文件填到配置的 douyu_cookies，录制时就能拿到最高画质。

用法示例（在项目根目录下运行）：
    python DMR/Login/douyu_login.py 小号            # 保存到 .login_info/斗鱼_小号.json
    python DMR/Login/douyu_login.py 小号 --renew    # 后台续期，不弹窗
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from DMR.Login.browser_login import main

if __name__ == '__main__':
    raise SystemExit(main(['douyu', *sys.argv[1:]]))
