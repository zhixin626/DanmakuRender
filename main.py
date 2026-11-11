from tools import check_pypi, check_update
check_pypi()

import time
import argparse
import os
import sys
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.append('./tools')

VERSION = '2026.05.01'

from DMR import DanmakuRender
from DMR.Config import Config
from DMR.utils.console_buffer import install_console_tee
from DMR.utils.log_manager import configure_logging

if __name__ == '__main__':    
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='configs/global.yml')
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--version', action='store_true')
    parser.add_argument('--skip_update', action='store_true')
    args = parser.parse_args()

    if args.version:
        print(f'DanmakuRender-5 {VERSION}.')
        print('https://github.com/SmallPeaches/DanmakuRender')
        exit(0)
    
    if not args.skip_update:
        check_update(VERSION)
    
    config = Config(args.config)

    # 在 logging handler 创建前接管 stdout，并把带序号的控制台副本落盘。
    install_console_tee((config.global_config.get('logging_args') or {}).get('directory', './logs'))

    logger = configure_logging(
        args.config,
        config.global_config.get("logging_args") or {},
    )

    logger.debug(f'VERSION: {VERSION}')

    dmr = DanmakuRender(config, logger=logger, debug=args.debug)
    dmr.start()
    
    try:
        while 1:
            time.sleep(60)
    except KeyboardInterrupt:
        dmr.stop()
        exit(0)

