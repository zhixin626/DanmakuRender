from manimlib import *
from datetime import datetime
start_file_path = 'D:/DanmakuRender/Tasks/佐佐酱/_livestart_times.txt'
end_file_path   = 'D:/DanmakuRender/Tasks/佐佐酱/_liveend_times.txt'

class Textz(Text):
    def __init__(self,
        text,
        font='WenYue XinQingNianTi (Authorization Required)',
        *args,**kargs):
        super().__init__(text,font=font,*args,**kargs)
def format_duration(start: datetime, end: datetime) -> str:
    delta = end - start
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        total_seconds = -total_seconds  # 允许反向计算

    days, rem = divmod(total_seconds, 86400)   # 一天 = 86400 秒
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)

    if days > 0:
        return f"{days}天{hours}小时{minutes}分钟"
    elif hours > 0:
        return f"{hours}小时{minutes}分钟"
    elif minutes > 0:
        return f"{minutes}分钟"
    else:
        return f"{seconds}秒"

class zuozuo_video(InteractiveScene):
    def construct(self):
        # init
        frame=self.frame
        light=self.camera.light_source
        # start
        with open(start_file_path,'r',encoding='utf-8') as f:
            start_time=f.readlines()[-1].strip()
        with open(end_file_path,'r',encoding='utf-8') as f:
            end_time=f.readlines()[-1].strip()
        st=datetime.fromisoformat(start_time)
        et=datetime.fromisoformat(end_time)

        # video
        t1=Textz("佐佐酱")
        t2=Textz(f'在{st.month}月{st.day}日直播了')
        t3=Textz(f'直播时间：{st.hour}时{st.minute}分到{et.hour}时{et.minute}分')
        t4=Textz(f'直播时长：{format_duration(st,et)}')
        t1.scale(2).set_color(LIGHT_PINK)
        t2.scale(1.5)
        t3.scale(0.8)
        t4.scale(0.8)
        grp=VGroup(t1,t2,t3,t4).arrange(DOWN)
        grp.set_width(FRAME_WIDTH-1)
        if grp.get_height()>FRAME_HEIGHT-1:
            grp.set_height(FRAME_HEIGHT-1)

        self.play(FadeIn(grp,lag_ratio=0.2),run_time=3)
        self.wait()
