import copy
import easydict
from typing import Tuple
from datetime import datetime

class cpdict(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for key, value in self.items():
            setattr(self, key, value)

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        super().__setitem__(name, value)

    __setitem__ = __setattr__

    def copy(self) -> dict:
        return copy.deepcopy(self)

class PipeMessage(cpdict):
    def __init__(self, 
                 source:str,
                 target:str,
                 event:str,
                 request_id:str=None,
                 msg:str='',
                 dtype:str=None,
                 data:dict=None,
                 **kwargs):
        self.source = source
        self.target = target
        self.event = event
        self.request_id = request_id
        self.msg = msg
        self.dtype = dtype
        self.data = data
        super().__init__(
                source=source,
                target=target,
                event=event,
                request_id=request_id,
                msg=msg,
                dtype=dtype,
                data=data,
                **kwargs
                )

class StreamerInfo(cpdict):
    def __init__(self, 
                 name:str,
                 uid:str=None,
                 platform:str=None,
                 room_id:str=None,
                 url:str=None,
                 face_url:str=None,
                 cover_url:str=None,
                 **kwargs):
        self.name = name
        self.uid = uid
        self.platform = platform
        self.room_id = room_id
        self.url = url
        self.face_url = face_url
        super().__init__(
                name=name,
                uid=uid,
                platform=platform,
                room_id=room_id,
                url=url,
                face_url=face_url,
                **kwargs
                )


class StreamInfo(cpdict):
    def __init__(self, 
                 streamer:StreamerInfo,
                 title:str=None,
                 description:str=None,
                 stream_start_time:datetime=None,
                 resolution:Tuple[int, int]=None,
                 cover_url:str=None,
                 **kwargs):
        self.streamer = streamer
        self.title = title
        self.description = description
        self.stream_start_time = stream_start_time
        self.resolution = resolution
        self.cover_url = cover_url
        super().__init__(
                streamer=streamer,
                title=title,
                description=description,
                stream_start_time=stream_start_time,
                resolution=resolution,
                cover_url=cover_url,
                **kwargs
                )
        
class FileInfo(cpdict):
    def __init__(self, 
                 path:str,
                 file_id:str=None,
                 dtype:str=None,
                 size:int=None,
                 ctime:datetime=None,
                 **kwargs):
        self.file_id = file_id
        self.dtype = dtype
        self.path = path
        self.size = size
        self.ctime = ctime
        super().__init__(
                file_id=file_id,
                dtype=dtype,
                path=path,
                size=size,
                ctime=ctime,
                **kwargs
                )
        
class VideoInfo(FileInfo):
    def __init__(self,
                path:str,
                file_id:str=None,
                dtype:str=None,
                size:int=None,
                ctime:datetime=None,
                stime:datetime=None,
                etime:datetime=None,
                totaltime:str="",
                duration:int=None,
                resolution:Tuple[int, int]=None,
                title:str=None,
                streamer:StreamerInfo=None,
                group_id:str=None,
                segment_id:int=None,
                taskname:str=None,
                dm_video_id:str=None,
                src_video_id:str=None,
                dm_file_id:str=None,
                gifts_revenue:str="",
                num_of_gifters:str="",
                sc_revenue:str="",
                num_of_sc:str="",
                member_revenue:str="",
                member_info:str="",
                total_revenue:str="",
                top_ranking:str="",
                **kwargs):
        self.etime=etime
        self.stime=stime
        self.totaltime=totaltime
        self.streamer = streamer
        self.duration = duration
        self.resolution = resolution
        self.title = title
        self.taskname = taskname
        self.dm_video_id = dm_video_id
        self.src_video_id = src_video_id
        self.dm_file_id = dm_file_id
        self.group_id = group_id
        self.segment_id = segment_id
        self.gifts_revenue = gifts_revenue
        self.num_of_gifters = num_of_gifters
        self.top_ranking = top_ranking
        self.sc_revenue = sc_revenue
        self.num_of_sc = num_of_sc
        self.member_revenue = member_revenue
        self.member_info = member_info
        self.total_revenue = total_revenue
        super().__init__(
                file_id=file_id,
                dtype=dtype,
                path=path,
                size=size,
                ctime=ctime,
                streamer=streamer,
                duration=duration,
                resolution=resolution,
                title=title,
                taskname=taskname,
                dm_video_id=dm_video_id,
                src_video_id=src_video_id,
                dm_file_id=dm_file_id,
                group_id=group_id,
                segment_id=segment_id,
                gifts_revenue=gifts_revenue,
                num_of_gifters=num_of_gifters,
                sc_revenue=sc_revenue,
                num_of_sc=num_of_sc,
                member_revenue=member_revenue,
                member_info=member_info,
                total_revenue=total_revenue,
                top_ranking=top_ranking,
                **kwargs
                )
