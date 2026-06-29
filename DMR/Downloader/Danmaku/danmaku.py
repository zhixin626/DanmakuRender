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
from DMR.utils import replace_keywords
from DMR.utils.danmaku import GiftDanmaku, BaseDanmaku
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
                 danmaku_args:dict=None,   # 弹幕全部参数（格式/连接/高级/四类型 render·filter·record）
                 description:str='',
                 width:int=1920,
                 height:int=1080,
                 **kwargs) -> None:
        da = danmaku_args or {}
        self.logger = logging.getLogger(__name__)
        self.stoped = False
        self.part = 0
        self.kwargs = kwargs
        self.url = url
        self.output = output
        self.segment = segment
        self.dm_format = da.get('format', 'ass')
        self.dm_stream_option = da.get('stream_option') or {}

        self._init_advanced(da)
        self._init_types(da)
        self._init_danmaku_filter(da)
        self._init_writer(da, description, width, height)

    def _init_advanced(self, da):
        """高级连接参数（advanced_args）。"""
        adv = da.get('advanced_args') or {}
        self.dm_delay_fixed   = adv.get('dm_delay_fixed', 6)     # 弹幕相对时间的固定延迟补偿(秒)
        self.dm_auto_restart  = adv.get('dm_auto_restart', 300)  # 多久收不到弹幕就重连(秒)
        self.dm_extra_inputs  = adv.get('dm_extra_inputs', [])   # 额外弹幕输入源(list)
        self.dm_file_min_time = adv.get('dm_file_min_time', 10)  # 弹幕文件过短则删除的时长阈值(秒)

    def _init_types(self, da):
        """四类型(弹幕/礼物/SC/上舰)的渲染开关、记录开关、礼物过滤、营收落盘路径。"""
        gift   = da.get('gift')      or {}
        sc     = da.get('superchat') or {}
        member = da.get('member')    or {}
        dan_render = (da.get('danmaku') or {}).get('render') or {}
        self._gift = gift   # 供 _add_gift_to_ass 取 render.box
        self._sc   = sc     # 供 _add_sc_to_ass 取 render
        # 渲染开关（dtype -> 是否渲染）。未配置的类型(如 entry/other)一律不渲染。
        self.render_enable = {
            'danmaku':   dan_render.get('enable', True),
            'gift':      (gift.get('render')   or {}).get('enable', True),
            'superchat': (sc.get('render')     or {}).get('enable', True),
            'member':    (member.get('render') or {}).get('enable', True),
        }
        # 记录开关（只有 gift/superchat/member 进营收统计 gifts.jsonl）
        self.record_enable = {
            'gift':      (gift.get('record')   or {}).get('enable', False),
            'superchat': (sc.get('record')     or {}).get('enable', False),
            'member':    (member.get('record') or {}).get('enable', False),
        }
        self.gift_min_cny    = (gift.get('filter') or {}).get('min_cny', None)   # 礼物价格门槛(None=不按价过滤)
        self.gifts_file_path = os.path.join(os.path.dirname(self.output), "gifts.jsonl")

    def _init_danmaku_filter(self, da):
        """普通弹幕的过滤(关键词/用户名/最大长度) + 文本装饰(模板/VIP)。"""
        dan        = da.get('danmaku') or {}
        dan_render = dan.get('render') or {}
        dan_filter = dan.get('filter') or {}
        self.dm_keywords     = self._compile_patterns(dan_filter.get('keywords'), '弹幕屏蔽词')
        self.dm_usernames    = self._compile_patterns(dan_filter.get('username'), '用户屏蔽')
        self.max_length_spec = self._parse_max_length(dan_filter.get('max_length'))   # (模式, 值)
        self.dm_template     = dan_render.get('template') or None   # 普通弹幕模板(礼物/上舰模板在 AssWriter 内)
        self.vip_map         = self._build_vip_map(dan_render.get('vip_lists'))   # {uid(str): 药丸色(str或None)}

    def _init_writer(self, da, description, width, height):
        """弹幕写入器（目前仅支持 ass）。"""
        if platform.system() == 'Windows':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        if self.dm_format != 'ass':
            raise NotImplementedError(f"unsupported danmaku format {self.dm_format}")
        gift   = da.get('gift')      or {}
        sc     = da.get('superchat') or {}
        member = da.get('member')    or {}
        from .asswriter import AssWriter
        self.dmwriter = AssWriter(
            description=description, width=width, height=height,
            danmaku_render = (da.get('danmaku') or {}).get('render') or {},
            gift_render    = gift.get('render')   or {},
            sc_render      = sc.get('render')     or {},
            member_render  = member.get('render') or {},
        )

    def _compile_patterns(self, raw, label):
        """关键词/用户名过滤项 -> 编译后的正则列表；非法配置告警并返回空列表。"""
        try:
            if not raw:
                return []
            if isinstance(raw, str):
                raw = [raw]
            elif not isinstance(raw, list):
                raise ValueError('必须是 list 或 str')
            return [re.compile(str(x)) for x in raw]
        except Exception as e:
            self.logger.warning(f'{label}{raw}设置错误:{e}，此功能将不会生效.')
            return []

    def _parse_max_length(self, raw):
        """max_length -> (模式, 值)。'100%'→('ratio',1.0)、'1920px'→('px',1920)、
        '30char'→('char',30)、0/~→('off',0)。格式不对则告警并关闭(不静默兜底)。"""
        if raw is None or raw == 0 or str(raw).strip() in ('0', ''):
            return ('off', 0)
        s = str(raw).strip().lower()
        try:
            if s.endswith('%'):
                return ('ratio', float(s[:-1]) / 100.0)
            if s.endswith('px'):
                return ('px', float(s[:-2]))
            if s.endswith('char'):
                return ('char', int(float(s[:-4])))
        except Exception:
            pass
        self.logger.warning(f"max_length '{raw}' 格式不对（应为 100% / 1920px / 30char / 0），过滤已关闭。")
        return ('off', 0)

    def _build_vip_map(self, vip_lists):
        """vip_lists 归一为 {uid(str): pill_color(str或None)}。
        支持 dict{uid:color} / [uid,...] / [{uid:..,pill_color:..}, [uid,color], ...]。
        pill_color 为 None 表示该 VIP 的药丸用弹幕自身颜色。"""
        vip_map = {}
        vl = vip_lists
        if isinstance(vl, dict):
            for u, c in vl.items():
                vip_map[str(u)] = (str(c) if c else None)
        elif isinstance(vl, (list, tuple)):
            for it in vl:
                if isinstance(it, dict):
                    if it.get('uid') is not None:
                        vip_map[str(it['uid'])] = (str(it.get('pill_color')) if it.get('pill_color') else None)
                elif isinstance(it, (list, tuple)) and it:
                    vip_map[str(it[0])] = (str(it[1]) if len(it) > 1 and it[1] else None)
                else:
                    vip_map[str(it)] = None
        return vip_map

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

        # 分段完成后，把占位行转成动画：按各类型 render.enable 决定做不做
        if filename:
            if self.render_enable.get('superchat'):
                self._add_sc_to_ass(filename)
            if self.render_enable.get('gift') or self.render_enable.get('member'):
                self._add_gift_to_ass(filename)

    def danmaku_available(self, dm:BaseDanmaku) -> bool:
        """普通弹幕过滤：时间合法、有文本/发送者，且不命中关键词/用户名。
        （最大长度不在这里过滤，改为在装饰后截断，见 _truncate_max_length）"""
        if dm.time < 0 or not getattr(dm, 'text', None) or not getattr(dm, 'uname', None):
            return False
        for keyword in self.dm_keywords:
            if keyword.search(dm.text):
                return False
        for username in self.dm_usernames:
            if username.fullmatch(dm.uname):
                return False
        return True

    def _truncate_max_length(self, text:str) -> str:
        """超过 max_length 的弹幕不丢弃，截到「不超上限的最长前缀」。char 直接切片；
        px/ratio 是像素宽（非线性），用测宽器二分找最长前缀。"""
        mode, val = self.max_length_spec
        if mode == 'off' or not text:
            return text
        if mode == 'char':
            return text[:int(val)]
        limit_px = val if mode == 'px' else val * self.dmwriter.width   # ratio: 屏宽的倍数
        if self.dmwriter._get_length(text) <= limit_px:
            return text
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.dmwriter._get_length(text[:mid]) <= limit_px:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo]

    def gift_available(self, dm:GiftDanmaku) -> bool:
        """礼物按价格过滤：未设门槛 / 无人民币价 一律放行；否则价 >= 门槛才放行。"""
        if self.gift_min_cny is None:
            return True
        if getattr(dm, "price_cny", None) is None:
            return True
        return float(dm.price_cny) >= float(self.gift_min_cny)

    def process_danmaku(self, dm:BaseDanmaku) -> bool:
        """消费一条弹幕：记账 → 渲染开关 → 过滤 → 文本装饰 → 写入 ASS。
        返回是否成功写入（调用方据此刷新"最近收到弹幕"的时间戳）。"""
        # 相对时间：相对本段起点，并补偿固定延迟
        dm.time = dm.timestamp - self.part_start_time - self.dm_delay_fixed

        # 记账：按类型 record.enable，独立于渲染（被过滤掉也照记营收）
        if self.record_enable.get(dm.dtype):
            save_gift_to_jsonl(dm, self.gifts_file_path)

        # 渲染开关：未配置/关闭的类型(含 entry、未识别消息)不渲染
        if not self.render_enable.get(dm.dtype):
            return False

        # 过滤：普通弹幕走内容过滤；礼物走价格过滤；SC/上舰不过滤
        if dm.dtype == 'danmaku':
            if not self.danmaku_available(dm):
                return False
        elif isinstance(dm, GiftDanmaku):
            if not self.gift_available(dm):
                return False

        # 文本装饰：普通弹幕套模板 + VIP 改写打标（礼物/上舰文案模板在 AssWriter.add_slide_R 内）
        pill_color = None
        if dm.dtype == 'danmaku':
            if self.dm_template:
                dm.text = replace_keywords(self.dm_template, dm)
            if self.vip_map and (uid := str(getattr(dm, "uid", ""))) in self.vip_map:
                dm.text = f"{dm.uname}:{dm.content}"
                dm.is_vip = True
                pill_color = self.vip_map[uid]   # None=用弹幕自身颜色
            # 超长截断（对最终文本，含模板/VIP 改写后的结果）
            dm.text = self._truncate_max_length(dm.text)

        return self.dmwriter.add(dm, pill_color=pill_color)

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
                # ✔ A. 消费弹幕（各平台 decoder 已统一产出 BaseDanmaku 及其子类）
                try:
                    dm = q.get_nowait() # 立即取队列元素，如果队列空就抛异常
                    retry = 0           # 取到弹幕说明连接正常，重置重连计数
                    if self.process_danmaku(dm):
                        last_dm_time = datetime.now().timestamp()
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
                    except Exception:
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

    def _add_sc_to_ass(self, ass_file: str):
        """
        将 ASS 文件中的 SC 预览行替换为动态动画版本。
        流程：先转换到临时文件 → 原文件移入回收站 → 临时文件重命名为原文件名。
        转换失败时临时文件会被清理，原文件保持不变。
        """
        tmp = ass_file + '.tmp'
        try:
            sc = self._sc.get('render') or {}
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

    def _add_gift_to_ass(self, ass_file: str):
        """将 ASS 文件中的 GIFT_DATA 注释行替换为动态礼物动画（右侧滑入）。
        礼物/上舰是否渲染由上游 render.enable 决定，这里不再判断开关。"""
        g   = self._gift.get('render') or {}
        box = g.get('box') or {}
        # box 里只挑用户填了的项传给 GiftConverter，其余走 GiftConverter 默认值
        box_kwargs = {k: box[k] for k in (
            'top_bg', 'top_opacity', 'bot_bg', 'bot_opacity',
            'name_color', 'cont_color', 'text_border_color') if k in box and box[k] is not None}
        tmp = ass_file + '.gift.tmp'
        try:
            n = GiftConverter(
                screen_width=self.dmwriter.width,
                screen_height=self.dmwriter.height,
                duration=g.get('duration', 10),
                anchor_y_ratio=g.get('anchor_y_ratio', 0.88),
                **box_kwargs,
            ).convert(ass_file, tmp)
            send2trash(ass_file)
            os.rename(tmp, ass_file)
            self.logger.info(f'礼物成功加入：{ass_file}（共{n}条）')
        except Exception as e:
            self.logger.warning(f'礼物加入失败，跳过：{e}')
            if os.path.exists(tmp):
                os.remove(tmp)
