import logging
import os

from .baseevents import BaseEvents
from send2trash import send2trash
from .merge_mp4 import *
from ..utils import *
from pathlib import Path

class LiveEvents(BaseEvents):
    def __init__(self, name, config):
        super().__init__(name, config)
        self.state_dict = {}
        self.ended_dict = {}
        self.logger = logging.getLogger(__name__)
        self.is_add_to_list=False
        self.is_live_end=False
        self.bvid=None
        self.is_desc_offtime=False
        self.dmvideo_path = Path(str(self.config['download_args']['output_dir']) + '（弹幕版）')

    def _state_snapshot(self):
        """只提取可读信息：status / file.path / wait"""
        snap = {}
        for gid, vss in self.state_dict.items():
            snap[gid] = []
            for vs in vss:
                row = {}
                for vt, info in vs.items():
                    f = info.get('file')
                    row[vt] = {
                        'status': info.get('status'),
                        'wait': list(info.get('wait') or []),
                        'file': getattr(f, 'path', str(f)) if f else None,
                    }
                snap[gid].append(row)
        return snap

    def _log_state(self, where, extra=None):
        snap = self._state_snapshot()
        self.logger.debug(f"[STATE@{where}] {snap} {(' '+str(extra)) if extra else ''}")

    def _log_clean_task(self, group_id, idx, vtype, files, arg):
        short = []
        for f in files:
            p = getattr(f, 'path', str(f))
            try:
                p = str(Path(p).name)
            except Exception:
                pass
            short.append(p)
        self.logger.debug(f"[CLEAN-TASK] gid={group_id} idx={idx} vtype={vtype} method={arg.get('method')} "
                          f"delay={arg.get('delay')} files={short} opts={{w_srcfile:{arg.get('w_srcfile')}, w_srcpre:{arg.get('w_srcpre')}}}")

    @property
    def event_dict(self):
        return {
            'ready': self.onReady,
            'exit': self.onExit,
            'downloader/livestart': self.defaultEvent, 
            'downloader/livesegment': self.onLiveSegment,
            'downloader/liveend': self.onLiveEnd,
            'downloader/livestop': self.onLiveEnd,
            'render/end': self.onRenderEnd,
            'render/error': self.defaultEvent,
            'uploader/end': self.onUploadEnd,
            'uploader/error': self.defaultEvent,
            'cleaner/end': self.defaultEvent,
            'cleaner/error': self.defaultEvent,
            'default': self.defaultEvent,
        }
    
    def defaultEvent(self, message:PipeMessage):
        self.logger.info(f'{self.name}: {message.msg}')

    def onReady(self, *args, **kwargs):
        return PipeMessage(
            source=self.name,
            target='downloader',
            event='newtask',
            data={
                'dltype': self.config['download_args']['dltype'],
                'taskname': self.name,
                'config': self.config['download_args'],
            }
        )
    
    def onLiveSegment(self, message:PipeMessage):
        self.logger.info(f'{self.name}: {message.msg}')
        video:VideoInfo = message.data
        video_state = {
            # 'video_id': uuid(8),
            'src_video': {'status': None, 'file': None, 'wait': []},
            'src_video_pre': {'status': None, 'file': None, 'wait': []},
            'dm_video': {'status': None, 'file': None, 'wait': []},
        }
        if self.state_dict.get(video.group_id):
            self.state_dict[video.group_id].append(video_state)
        else:
            self.state_dict[video.group_id] = [video_state]

        ret_msgs = []
        if self.config['common_event_args'].get('auto_transcode'):
            transcode_args = self.config['render_args']['transcode']
            if transcode_args.get('output_name'):
                filename = replace_keywords(transcode_args['output_name'], video, replace_invalid=True) + \
                        f".{transcode_args.get('format','mp4')}"
            else:
                filename = os.path.splitext(os.path.basename(video.path))[0] + \
                        f"（转码后）.{transcode_args.get('format','mp4')}"
            if transcode_args.get('output_dir'):
                output_dir = transcode_args.get('output_dir')
            else:
                output_dir = os.path.dirname(video.path) + '（转码后）'
            output = os.path.join(output_dir, filename)
            transcode_msg = PipeMessage(
                source=self.name,
                target='render',
                event='newtask',
                request_id=uuid(),
                data={
                    'taskname': self.name,
                    'mode': 'transcode',
                    'video': video,
                    'output': output,
                    'args': transcode_args,
                }
            )
            self.state_dict[video.group_id][-1]['src_video_pre'].update({'status': 'ready', 'file': video})
            self.state_dict[video.group_id][-1]['src_video']['status'] = 'rendering'
            self.state_dict[video.group_id][-1]['src_video']['wait'].append(transcode_msg.request_id)
            ret_msgs.append(transcode_msg)
        else:
            # self.state_dict[video.group_id][-1]['src_video'].update({'status': 'ready', 'file': video})
            # 此处修改为就算不转码flv也放在src_video_pre里，方便清理
            self.state_dict[video.group_id][-1]['src_video_pre'].update({'status': 'ready', 'file': video})
        
        if self.config['common_event_args'].get('auto_render'):
            render_args = self.config['render_args']['dmrender']
            if render_args.get('output_name'):
                filename = replace_keywords(render_args['output_name'], video, replace_invalid=True) + \
                        f".{render_args.get('format','mp4')}"
            else:
                filename = os.path.splitext(os.path.basename(video.path))[0] + \
                        f"（弹幕版）.{render_args.get('format','mp4')}"
            if render_args.get('output_dir'):
                output_dir = render_args.get('output_dir')
            else:
                output_dir = os.path.dirname(video.path) + '（弹幕版）'
            output = os.path.join(output_dir, filename)
            render_msg = PipeMessage(
                source=self.name,
                target='render',
                event='newtask',
                request_id=uuid(),
                data={
                    'taskname': self.name,
                    'mode': 'dmrender',
                    'video': video,
                    'output': output,
                    'args': render_args,
                }
            )
            self.state_dict[video.group_id][-1]['dm_video']['status'] = 'rendering'
            self.state_dict[video.group_id][-1]['dm_video']['wait'].append(render_msg.request_id)
            ret_msgs.append(render_msg)

        if self.config['common_event_args'].get('auto_upload'):
            ret_msgs += self._check_for_upload(video.group_id, len(self.state_dict[video.group_id])-1)

        # onLiveSegment 结尾处----------------------
        self._log_state("onLiveSegment:end", extra={"auto_transcode": self.config['common_event_args'].get('auto_transcode'),
                                                "auto_render": self.config['common_event_args'].get('auto_render')})

        return ret_msgs
    
    def onLiveEnd(self, message:PipeMessage):
        self.logger.info(f'{self.name}: {message.msg}.')
        group_id = message.data
        if group_id is None:
            return
        
        if group_id in self.state_dict:
            self.ended_dict[group_id] = time.time()
        else:
            self.logger.debug(f'No such group:{group_id}.')

        if self.config['common_event_args'].get('auto_merge') and group_id in self.ended_dict:
            # self.logger.info("onLiveEnd正在检查是否merge")
            self.check_for_merge(group_id)

        ret_msgs = []
        if self.config['common_event_args'].get('auto_upload'):
            upload_msgs = self._check_for_upload(group_id)
            ret_msgs += upload_msgs

        self._free_state_memory()

        # 判断是否写入下播时间到简介里
        dm_video = self.config.get('upload_args', {}).get('dm_video')
        time_template=dm_video[0].get('add_livetime_todesc')
        if time_template and self.bvid and not self.is_desc_offtime:
            self.add_livetime_wrapper(time_template,self.bvid)
        
        self.is_live_end=True
        return ret_msgs
    
    def add_livetime_wrapper(self,time_template,bvid):
        dm_video = self.config.get('upload_args', {}).get('dm_video')
        account=dm_video[0].get('account')
        start_time= read_live_time_from_path(self.dmvideo_path,is_start=True)
        end_time  = read_live_time_from_path(self.dmvideo_path,is_start=False)
        duration  = format_duration(start_time,end_time)
        result = replace_keywords(
            time_template,
            {"stime": start_time,
             "etime": end_time,
             "totaltime":duration}
             )
        try:
            add_livetime(bvid,result,account)
            self.is_desc_offtime=True
        except Exception as e:
            self.logger.error(e)


    def _check_for_upload(self, group_id:str, _idx:int=None):
        ret_msgs = []
        if not self.state_dict.get(group_id):
            return ret_msgs
        
        upload_args = self.config['upload_args']
        for idx, video_state in enumerate(self.state_dict[group_id]):
            if _idx is not None and idx != _idx:
                continue
            for vtype, info in video_state.items():
                if info['status'] != 'ready':
                    continue
                for upload_file_types, upload_arg in upload_args.items():
                    # 判断当前视频是否需要上传
                    if vtype in upload_file_types.split('+'):
                        for upid, arg in enumerate(upload_arg):
                            # 实时上传
                            if not arg.get('realtime'):
                                continue
                            if info['file'].duration < arg.get('min_length', 0):
                                self.logger.info(f'视频{info["file"].path}时长为{info["file"].duration}s，设置{arg.get("min_length", 0)}s，跳过上传.')
                                continue
                            upload_group_id = info['file'].upload_group_id if hasattr(info['file'], 'upload_group_id') else group_id
                            upload_msg = PipeMessage(
                                source=self.name,
                                target='uploader',
                                event='newtask',
                                request_id=uuid(),
                                data={
                                    'taskname': self.name,
                                    'files': [info['file']],
                                    'engine': arg['engine'],
                                    'stateless': False,
                                    'upload_group': upload_group_id+'_'+upload_file_types+'_'+str(upid),
                                    'args': arg,
                                }
                            )
                            self.state_dict[group_id][idx][vtype]['status'] = 'uploading'
                            self.state_dict[group_id][idx][vtype]['wait'].append(upload_msg.request_id)
                            ret_msgs.append(upload_msg)
        
        # 如果当前视频组已经被标记结束，检查是否有视频组完全准备好上传（用于非实时上传）
        if group_id in self.ended_dict:
            # 遍历所有视频类型
            video_types = list(self.state_dict[group_id][-1].keys())
            for vtype in video_types:
                # 检查是否全部准备上传
                videos = []
                for idx, video_state in enumerate(self.state_dict[group_id]):
                    if video_state[vtype]['status'] == 'ready':
                        videos.append(video_state[vtype]['file'])
                    elif video_state[vtype]['status'] == 'merged':
                        continue
                    else:
                        videos = []
                        break
                if not videos:
                    continue

                # 判断当前视频是否需要上传
                for upload_file_types, upload_arg in upload_args.items():
                    if vtype in upload_file_types.split('+'):
                        for upid, arg in enumerate(upload_arg):
                            # 此处只做非实时上传
                            if arg.get('realtime'):
                                continue
                            up_videos = [video for video in videos if video.duration >= arg.get('min_length', 0)]
                            upload_group_id = up_videos[0].upload_group_id if hasattr(up_videos[0], 'upload_group_id') else group_id
                            upload_msg = PipeMessage(
                                source=self.name,
                                target='uploader',
                                event='newtask',
                                request_id=uuid(),
                                data={
                                    'taskname': self.name,
                                    'files': up_videos,
                                    'engine': arg['engine'],
                                    'stateless': True,
                                    'upload_group': upload_group_id+'_'+upload_file_types+'_'+str(upid),
                                    'args': arg,
                                }
                            )
                            # 标记状态信息
                            for idx, _ in enumerate(self.state_dict[group_id]):
                                self.state_dict[group_id][idx][vtype]['status'] = 'uploading'
                                self.state_dict[group_id][idx][vtype]['wait'].append(upload_msg.request_id)
                            ret_msgs.append(upload_msg)

        return ret_msgs
    
    def onRenderEnd(self, message:PipeMessage):
        self.logger.info(f'{self.name}: {message.msg}.')
        # debug-------------------------------
        # self._log_state("onRenderEnd:before", extra={"request_id": message.request_id})
        # debug-------------------------------

        request_id = message.request_id
        video:VideoInfo = message.data.get('output')
        video_states = self.state_dict[video.group_id]
        # 将状态信息中request_id对应的等待移除
        for idx, video_state in enumerate(video_states):
            for vtype, info in video_state.items():
                if request_id in info['wait']:
                    self.state_dict[video.group_id][idx][vtype]['wait'].remove(request_id)
                    if len(self.state_dict[video.group_id][idx][vtype]['wait']) == 0:
                        self.state_dict[video.group_id][idx][vtype]['status'] = 'ready'
                        self.state_dict[video.group_id][idx][vtype]['file'] = video
        
        # debug-------------------------------
        # self._log_state("onRenderEnd:middle1", extra={"request_id": message.request_id})
        # debug-------------------------------

        if self.config['common_event_args'].get('auto_merge') and video.group_id in self.ended_dict:
            # self.logger.info("onRenderEnd正在检查是否merge")
            self.check_for_merge(video.group_id)
        
        # debug-------------------------------
        # self._log_state("onRenderEnd:middle2", extra={"request_id": message.request_id})
        # debug-------------------------------

        ret_msgs = []
        if self.config['common_event_args'].get('auto_upload'):
            upload_msgs = self._check_for_upload(video.group_id)
            ret_msgs += upload_msgs

        # debug-------------------------------
        # self._log_state("onRenderEnd:after")
        # debug-------------------------------

        return ret_msgs

    def check_for_merge(self, group_id, vtype = "dm_video"):
        # 保留：所有都 ready 才能合并（all-ready gate）
        for idx, video_state in enumerate(self.state_dict[group_id]):
            if video_state[vtype]['status'] != 'ready':
                return

        videos = []
        videos_paths = []
        new_seg_id = 1

        changed_entries = []
        for idx, video_state in enumerate(self.state_dict[group_id]):
            new_seg_id += 1
            entry = video_state[vtype]
            entry['status'] = 'merging'     # ← 改动点1
            changed_entries.append(entry)    # ← 改动点1
            videos_paths.append(entry['file'].path)
            videos.append(entry['file'])

        self.logger.info("弹幕视频有%s个，开始合并",len(videos_paths))

        try:
            # 改动点2：不再提前 append 空 state；这里先做真正合并
            final_path, meta = merge_amplify_mp4(videos_paths,return_info=True)
            if len(videos_paths)>1:
                for video in videos_paths: send2trash(video)
        except Exception as e:
            # 改动点3：失败回滚——只回滚我设为 merging 的那批（merging -> ready）
            for entry in changed_entries:
                entry['status'] = 'ready'
            self.logger.debug("合并 mp4 时出错，已退回分段上传：%s", e)
            return

        # 改动点4：成功后把旧条目标记为 merged（merging -> merged）
        for entry in changed_entries:
            entry['status'] = 'merged'

        # 改动点5：合并成功后再 append 新占位，并写入新的 ready 条目
        video_state_new = {
            'src_video':     {'status': None, 'file': None, 'wait': []},
            'src_video_pre': {'status': None, 'file': None, 'wait': []},
            'dm_video':      {'status': None, 'file': None, 'wait': []},
        }

        newvideo = VideoInfo(
            path=final_path,
            dtype='dm_video',
            file_id=uuid(),                     
            size=os.path.getsize(final_path),
            ctime=datetime.now(),
            stime=read_live_time_from_path(self.dmvideo_path,is_start=True),
            etime=read_live_time_from_path(self.dmvideo_path,is_start=False),
            dm_file_id=None,
            duration=meta["duration"],
            segment_id=new_seg_id,
            taskname=videos[-1].taskname,
            group_id=group_id,
            streamer=videos[-1].streamer,
            title=videos[-1].title,
            resolution=meta["resolution"],
        )

        video_state_new['dm_video'].update({'status': 'ready', 'file': newvideo})
        self.state_dict[group_id].append(video_state_new)   # ← 改动点5（成功后再 append）
    
    def _check_for_clean(self, group_id=None):
        ret_msgs = []
        clean_args = self.config['clean_args']
        # debug-----------------
        # self._log_state("check_for_clean:enter")
        # self.logger.debug(f"[CLEAN] clean_args={clean_args}")
        # debug-----------------
        for group_id, video_states in self.state_dict.items():
            for idx, video_state in enumerate(video_states):
                for vtype, info in video_state.items():
                    if info['status'] != 'uploaded':
                        # debug-------------------------------------------
                        # self.logger.debug(f"[CLEAN] skip gid={group_id} idx={idx} vtype={vtype} status={info['status']} (need 'uploaded')")
                        continue
                    for clean_file_types, clean_arg in clean_args.items():
                        # debug-------------------------------------------
                        # self.logger.debug(f"[CLEAN] rule={clean_file_types} vtype={vtype} "
                        #                   f"match={vtype in clean_file_types.split('+') or clean_file_types == 'all'} "
                        #                   f"type(clean_arg)={type(clean_arg)}")
                        # debug-------------------------------------------
                        # 判断当前视频是否需要清理
                        if vtype in clean_file_types.split('+') or clean_file_types == 'all':
                            for arg in clean_arg:
                                files = [info['file']]

                                # 判断是否需要清理源文件
                                # if vtype == 'dm_video' and arg.get('w_srcfile', False) == True and video_state['src_video']['file'] is not None:
                                #     files.append(video_state['src_video']['file'])
                                #     self.state_dict[group_id][idx]['src_video']['status'] = 'cleaned'

                                # w_srcfile为转码文件
                                if arg.get('w_srcfile', False) == True and video_state['src_video']['file'] is not None:
                                    files.append(video_state['src_video']['file'])
                                    self.state_dict[group_id][idx]['src_video']['status'] = 'cleaned'

                                # 判断是否需要清理源文件（转码前）
                                # if vtype == 'src_video' and arg.get('w_srcpre', True) == True and video_state['src_video_pre']['file'] is not None:
                                #     files.append(video_state['src_video_pre']['file'])
                                #     self.state_dict[group_id][idx]['src_video_pre']['status'] = 'cleaned'

                                # w_srcpre为flv源文件
                                if arg.get('w_srcpre', True) == True and video_state['src_video_pre']['file'] is not None:
                                    files.append(video_state['src_video_pre']['file'])
                                    self.state_dict[group_id][idx]['src_video_pre']['status'] = 'cleaned'
                                
                                # debug-----------------
                                # file_names = [str(getattr(f, 'path', f)) for f in files if f]
                                # self.logger.debug(f"[CLEAN] group={group_id} idx={idx} vtype={vtype} "
                                #                   f"→ method={arg['method']} delay={arg['delay']} files={file_names}")
                                # debug-----------------

                                clean_msg = PipeMessage(
                                    source=self.name,
                                    target='cleaner',
                                    event='newtask',
                                    request_id=uuid(),
                                    data={
                                        'taskname': self.name,
                                        'files': files,
                                        'method': arg['method'],
                                        'delay': arg['delay'],
                                        'args': arg,
                                    }
                                )
                                ret_msgs.append(clean_msg)
                    self.state_dict[group_id][idx][vtype]['status'] = 'cleaned'
        # debug-----------------
        # self.logger.debug(f"[CLEAN] done, total tasks={len(ret_msgs)}")
        # self._log_state("check_for_clean:after")
        # debug-----------------
        return ret_msgs
    
    def _free_state_memory(self):
        final_status = 'ready'
        if self.config['common_event_args'].get('auto_upload'):
            final_status = 'uploaded'
        if self.config['common_event_args'].get('auto_clean'):
            final_status = 'cleaned'
        
        for group_id in list(self.ended_dict.keys()):
            need_free = True
            for idx, video_state in enumerate(self.state_dict[group_id]):
                for vtype, info in video_state.items():
                    if info['status'] is not None and info['status'] != final_status:
                        need_free = False
                        break
                if not need_free: break
            if need_free:
                self.logger.debug(f'视频组{group_id}处理完成，视频信息已被释放.')
                self.ended_dict.pop(group_id)
                self.state_dict.pop(group_id)

        for group_id in list(self.ended_dict.keys()):
            if time.time() - self.ended_dict[group_id] > 72*3600:
                self.logger.debug(f'视频组{group_id}处理超时，视频信息将被释放.')
                self.ended_dict.pop(group_id)
                self.state_dict.pop(group_id)

    def onUploadEnd(self, message:PipeMessage):
        self.logger.info(f'{self.name}: {message.msg}.')
        #debug------------------
        # self._log_state("onUploadEnd:before", extra={"request_id": message.request_id})
        #debug------------------

        request_id = message.request_id
        # 将状态信息中request_id对应的等待移除
        for group_id, video_states in self.state_dict.items():
            for idx, video_state in enumerate(video_states):
                for vtype, info in video_state.items():
                    if request_id in info['wait']:
                        self.state_dict[group_id][idx][vtype]['wait'].remove(request_id)
                        if len(self.state_dict[group_id][idx][vtype]['wait']) == 0:
                            self.state_dict[group_id][idx][vtype]['status'] = 'uploaded'
        #debug------------------
        # self._log_state("onUploadEnd:middle", extra={"request_id": message.request_id})
        #debug------------------

        ret_msgs = []
        if self.config['common_event_args'].get('auto_clean'):
            clean_msgs = self._check_for_clean()
            ret_msgs += clean_msgs

        # 判断是否加入合集
        self.bvid=message.bvid
        sectionId = None
        dm_video = self.config.get('upload_args', {}).get('dm_video')
        account=dm_video[0].get('account')
        sectionId = dm_video[0].get('sectionId')
        if sectionId and not self.is_add_to_list:
            # 开始加入合集
            try:
                add_to_list(self.bvid,sectionId,account)
                self.is_add_to_list=True
            except Exception as e:
                self.logger.error(e)

        # 判断是否写入下播时间到简介里
        time_template=dm_video[0].get('add_livetime_todesc')
        if time_template and self.is_live_end and not self.is_desc_offtime:
            self.add_livetime_wrapper(time_template,self.bvid)
        #debug-------------
        # self._log_state("onRenderEnd:after")
        #debug-------------
        return ret_msgs

    def onExit(self, *args, **kwargs) -> None:
        self.logger.info(f'{self.name}: 任务结束.')
        self.state_dict.clear()
        self.ended_dict.clear()
        return PipeMessage(
            source=self.name,
            target='downloader',
            event='stoptask',
            data=self.name,
        )
