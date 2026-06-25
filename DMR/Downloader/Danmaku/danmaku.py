import asyncio
import logging
import os
import re
import time
import threading
import platform

from datetime import datetime
from os.path import *
from DMR.LiveAPI.danmaku import DanmakuClient
from DMR.utils import SimpleDanmaku, replace_keywords
from DMR.utils.danmaku import GiftDanmaku
from DMR.utils.gifts_utils import save_gift_to_jsonl
from typing import Union
from .sc_converter import SCConverter
from .gift_converter import GiftConverter
from send2trash import send2trash
__all__ = ['DanmakuDownloader']

class DanmakuDownloader():
    def __init__(self,
                 url:str,
                 output:str,
                 segment:float,
                 dm_format:str,
                 dm_filter:dict=None,
                 dm_template:dict=None,
                 dm_stream_option:dict={},
                 advanced_dm_args:dict={},
                 gifts_file_path:Union[str,None]=None,
                 gift_dm_args:dict={},
                 sc_dm_args:dict={},
                 enable_gift_recorder=False,
                 gift_minimum_cny=None,
                 vip_lists=None,
                 uid_lists=None,   # 旧名，兼容保留（纯 uid 列表，无专属药丸色）
                 **kwargs) -> None:

        self.gift_minimum_cny=gift_minimum_cny   # 来自 gift_dm_args.gift_min_cny，None=不过滤
        self.enable_gift_recorder=enable_gift_recorder
        self.gift_dm_args = gift_dm_args
        self.sc_dm_args = sc_dm_args
        self.gifts_file_path=gifts_file_path if gifts_file_path else os.path.join(os.path.dirname(output), "gifts.jsonl")
        self.stoped = False

        self.logger = logging.getLogger(__name__)
        self.url = url
        self.output = output
        self.segment = segment
        self.dm_format = dm_format
        self.dm_stream_option = dm_stream_option
        self.advanced_dm_args = advanced_dm_args
        self.dm_template = dm_template if dm_template else {}
        self.dm_delay_fixed = self.advanced_dm_args.get('dm_delay_fixed', 6)
        self.dm_auto_restart = self.advanced_dm_args.get('dm_auto_restart', 300)
        self.dm_extra_inputs = self.advanced_dm_args.get('dm_extra_inputs', []) # dm_extra_inputs是个list！
        self.dm_file_min_time = self.advanced_dm_args.get('dm_file_min_time', 10)

        self.dm_filter = dm_filter.copy() if dm_filter else {}
        # vip_lists 归一为 {uid(str): pill_color(str或None)}。
        # 支持三种写法：dict{uid:color} / [uid,...] / [{uid:..,pill_color:..}, [uid,color], ...]
        # pill_color 为 None 表示该 VIP 的药丸用弹幕自身颜色。uid_lists（旧）并入，颜色一律 None。
        vip_map = {}
        for u in (uid_lists or []):
            vip_map[str(u)] = None
        _vl = vip_lists
        if isinstance(_vl, dict):
            for u, c in _vl.items():
                vip_map[str(u)] = (str(c) if c else None)
        elif isinstance(_vl, (list, tuple)):
            for it in _vl:
                if isinstance(it, dict):
                    if it.get('uid') is not None:
                        vip_map[str(it['uid'])] = (str(it.get('pill_color')) if it.get('pill_color') else None)
                elif isinstance(it, (list, tuple)) and it:
                    vip_map[str(it[0])] = (str(it[1]) if len(it) > 1 and it[1] else None)
                else:
                    vip_map[str(it)] = None
        self.vip_map = vip_map


        try:
            keywords_filter = dm_filter['keywords']
            if not keywords_filter:
                keywords_filter = []
            elif isinstance(keywords_filter, str):
                keywords_filter = [keywords_filter]
            elif isinstance(keywords_filter, list):
                pass
            else:
                raise ValueError('dm_filter.keywords must be a list or str.')
            keywords_filter = [re.compile(str(x)) for x in keywords_filter]
            self.dm_filter['keywords'] = keywords_filter
        except Exception as e:
            self.logger.warning(f'弹幕屏蔽词{keywords_filter}设置错误:{e}，此功能将不会生效.')
            self.dm_filter['keywords'] = []
        
        try:
            username_filter = dm_filter['username']
            if not username_filter:
                username_filter = []
            elif isinstance(username_filter, str):
                username_filter = [username_filter]
            elif isinstance(username_filter, list):
                pass
            else:
                raise ValueError('dm_filter.username must be a list or str.')
            username_filter = [re.compile(str(x)) for x in username_filter]
            self.dm_filter['username'] = username_filter
        except Exception as e:
            self.logger.warning(f'用户屏蔽{username_filter}设置错误:{e}，此功能将不会生效.')
            self.dm_filter['username'] = []
        
        self.kwargs = kwargs
        self.part = 0

        if platform.system() == 'Windows':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
        if dm_format == 'ass':
            from .asswriter import AssWriter
            self.dmwriter = AssWriter(gift_dm_args=self.gift_dm_args,**self.kwargs)
        else:
            raise NotImplementedError(f"unsupported danmaku format {dm_format}")

    def time_fix(self, time_error):
        self.part_start_time -= time_error

    def start(self, self_segment=False):
        self.start_time = datetime.now().timestamp()
        self.part_start_time = self.start_time
        self.dm_file = self.output.replace(f'%03d','%03d'%self.part)
        self.dmwriter.open(self.dm_file)

        def monitor():
            while not self.stoped:
                self.split()
                time.sleep(self.segment)
        
        if self_segment:
            self.monitor = threading.Thread(target=monitor,daemon=True)
            self.monitor.start()
        
        return self.start_dmc()
    
    def split(self, filename:str=None):
        self.part += 1
        self.part_start_time = datetime.now().timestamp()
        old_dm_file = self.dm_file
        if not self.stoped:
            new_dm_file = self.output.replace(f'%03d','%03d'%self.part)
            self.logger.debug(f'New DMfile: {new_dm_file}')
            self.dmwriter.open(new_dm_file)
            self.dm_file = new_dm_file
        if filename:
            try:
                os.rename(old_dm_file, filename)
            except Exception as e:
                self.logger.error(f'弹幕 {old_dm_file} 分段失败: {e}.')
                filename = None

        # 只有启用了 superchat 录制才做 SC 动态转换
        if filename:
            dm_type = self.dm_filter.get('dm_type') or ''
            if 'superchat' in dm_type:
                self._convert_sc_dynamic(filename)
            if 'gift' in dm_type or 'member' in dm_type:
                self._convert_gift_dynamic(filename)

    def dm_available(self, dm:SimpleDanmaku) -> bool:

        if dm.time < 0 \
                or not dm.text \
                or not dm.uname \
                or dm.dtype in ['other', 'others'] \
                or not dm.dtype:
            return False

        dm_type = self.dm_filter.get('dm_type') or 'danmaku'

        if dm_type != 'all':
            if dm.dtype not in dm_type:
                return False

        for keyword in self.dm_filter['keywords']:
            if keyword.search(dm.text):
                return False

        for username in self.dm_filter['username']:
            if username.fullmatch(dm.uname):
                return False
            
        if max_length := self.dm_filter.get('max_length'):
            if len(dm.text) > max_length:
                return False

        return True

    def gift_dm_available(self,dm:GiftDanmaku):
        gift_min = self.gift_minimum_cny
        #说明用户没有配置这个参数，表示不进行价格过滤
        if gift_min is None:
            return True
        # 如果礼物没有 total_price_cny
        # 无法过滤，直接True
        if getattr(dm, "total_price_cny", None) is None:
            # print("total_price_cny is None")
            return True

        if float(dm.total_price_cny) < float(gift_min):
            # print("dm.total_price_cny < float(gift_min)")
            return False
        else:
            # print("dm.total_price_cny > float(gift_min)")
            return True

    def start_dmc(self):
        async def danmu_monitor(url:str=None): # 监督员
            if not url:
                url = self.url  
            q = asyncio.Queue() # 可以调用 q.put() q.get()来放置 拿弹幕 ，异步的，不阻塞cpu

            async def dmc_task(): # “工人 + 保险”
                dmc = DanmakuClient(url, q, **self.dm_stream_option) # 会往q里放弹幕
                try:
                    await dmc.start()
                except asyncio.CancelledError: #说明task被cancel了（弹幕获取超时或外部取消）
                    await dmc.stop()
                    self.logger.debug('Cancel the future.')
                except Exception as e: # 可能内部有raise RuntimeError之类的
                    await dmc.stop()
                    self.logger.exception(e)
                
            task = asyncio.create_task(dmc_task())
            last_dm_time = datetime.now().timestamp()
            retry = 0

            while not self.stoped: #这是 弹幕监控调度器（做ABC三件事）
                # ✔ A. 消费弹幕
                try:
                    dm = q.get_nowait() # 立即取队列元素，如果队列空就抛异常
                    if not isinstance(dm, SimpleDanmaku):
                        dm = SimpleDanmaku(
                            dtype=dm.get('msg_type', 'other'),
                            uname=dm.get('name', ''),
                            content=dm.get('content', ''),
                            timestamp=dm.get('timestamp', datetime.now().timestamp()),
                            color=dm.get('color', 'ffffff'),
                        )
                    # 将绝对时间转换为相对时间
                    dm.time = dm.timestamp - self.part_start_time - self.dm_delay_fixed
                    if self.enable_gift_recorder and dm.dtype in ("gift", "superchat", "member"):
                        save_gift_to_jsonl(dm, self.gifts_file_path)

                    # 载入弹幕模板
                    if dm_templ := self.dm_template.get(dm.dtype):
                        dm.text = replace_keywords(dm_templ, dm)

                    # vip弹幕
                    vip_pill_color = None
                    if self.vip_map and (uid := str(getattr(dm, "uid", ""))) in self.vip_map:
                        # 这里是为了给asswriter的get_length能获取到正确的长度
                        # vip弹幕最终的格式由asswriter决定，默认是下面这样
                        dm.text=f"{dm.uname}:{dm.content}"
                        dm.is_vip=True
                        vip_pill_color = self.vip_map[uid]   # None=用弹幕自身颜色

                    if not self.dm_available(dm):
                        continue
                    # print(dm.text)
                    if isinstance(dm, GiftDanmaku):
                        if not self.gift_dm_available(dm):
                            # print(f"过滤掉：{dm.text}")
                            continue

                    retry = 0
                    if self.dmwriter.add(dm, pill_color=vip_pill_color):
                        last_dm_time = datetime.now().timestamp()
                    else:
                        # print(f"未写入{dm.text}")
                        pass
                    continue

                except asyncio.QueueEmpty:
                    pass
                
                # ✔ B. 监控弹幕获取是否断开（task.done）
                if task.done():
                    # task.done() 为 True 的几种可能：
                    # dmc_task 正常结束（几乎不可能，因为里面是长期跑的）
                    # dmc_task 里抛了普通异常（最常见）
                    # 之前某处把这个 task cancel 掉并且协程已经处理完 CancelledError 退出了
                    self.logger.error(f'{self.url} 弹幕下载线程异常退出，正在重试...')
                    try:
                        self.logger.debug(task.result())
                    except:
                        self.logger.exception(task.exception())
                    task.cancel() # 这里估计是多余的
                    retry += 1
                    last_dm_time = datetime.now().timestamp()
                    await asyncio.sleep(min(15*retry,60))
                    task = asyncio.create_task(dmc_task())
                    self.logger.info(f"{self.url} 弹幕下载线程已重启。")
                    continue

                # ✔ C. 监控弹幕是否超时（dm_auto_restart）
                if self.dm_auto_restart and datetime.now().timestamp()-last_dm_time>self.dm_auto_restart:
                    self.logger.error(f'{self.url} 获取弹幕超时，正在重试...')
                    # 调用task.cancel()后task 里执行到某个 await 时，立刻抛出 asyncio.CancelledError
                    task.cancel()
                    last_dm_time = datetime.now().timestamp()
                    task = asyncio.create_task(dmc_task())
                    self.logger.info(f"{self.url} 弹幕下载线程已重启。")
                    continue
                
                # ✔ D. 避免大量空循环（await sleep(0.1)）
                await asyncio.sleep(0.1)

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                self.logger.debug("DMC task cancelled.")

        danmu_monitor_sets = []
        for url in [self.url] + self.dm_extra_inputs: # list + list = newlist
            danmu_monitor_sets.append(danmu_monitor(url))
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        asyncio.get_event_loop().run_until_complete(asyncio.gather(*danmu_monitor_sets))

    def stop(self):
        self.stoped = True
        self.logger.debug('danmaku writer stoped.')

        # 删除过短的弹幕文件
        if datetime.now().timestamp() - self.part_start_time < self.dm_file_min_time:
            try:
                os.remove(self.dm_file)
            except Exception as e:
                self.logger.debug(e)
        return True

    def _convert_sc_dynamic(self, ass_file: str):
        """
        将 ASS 文件中的 SC 预览行替换为动态动画版本。
        流程：先转换到临时文件 → 原文件移入回收站 → 临时文件重命名为原文件名。
        转换失败时临时文件会被清理，原文件保持不变。
        """
        tmp = ass_file + '.tmp'
        try:
            sc = self.sc_dm_args or {}
            sc_count = SCConverter(
                screen_width=self.dmwriter.width,
                screen_height=self.dmwriter.height,
                anchor_y_ratio=sc.get('anchor_y_ratio', 0.93),   # SC 最新一条底边位置（屏幕高度比例）
            ).convert(ass_file, tmp)
            send2trash(ass_file)         # 原文件移入回收站（可还原）
            os.rename(tmp, ass_file)     # 临时文件改回原名
            self.logger.info(f'SC成功加入：{ass_file}（共{sc_count}条）')
        except Exception as e:
            self.logger.warning(f'SC加入失败，跳过：{e}')
            if os.path.exists(tmp):
                os.remove(tmp)           # 清理残留临时文件

    def _convert_gift_dynamic(self, ass_file: str):
        """将 ASS 文件中的 GIFT_DATA 注释行替换为动态礼物动画（右侧滑入）。"""
        g = self.gift_dm_args or {}
        if not g.get('gift_box_enable', True):
            return   # 未开启礼物框：GIFT_DATA 注释行保留为注释（不渲染），不做转换
        tmp = ass_file + '.gift.tmp'
        try:
            n = GiftConverter(
                screen_width=self.dmwriter.width,
                screen_height=self.dmwriter.height,
                duration=g.get('gift_duration', 10),
                anchor_y_ratio=g.get('anchor_y_ratio', 0.88),   # 由 gift_dm_args 传入，默认 0.88
            ).convert(ass_file, tmp)
            send2trash(ass_file)
            os.rename(tmp, ass_file)
            self.logger.info(f'礼物成功加入：{ass_file}（共{n}条）')
        except Exception as e:
            self.logger.warning(f'礼物加入失败，跳过：{e}')
            if os.path.exists(tmp):
                os.remove(tmp)
