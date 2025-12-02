from manimlib import *
from datetime import datetime
import os
folder = "D:/DanmakuRender/佐佐酱"
start_file_path = os.path.join(folder, "_livestart_times.txt")
end_file_path   = os.path.join(folder, "_liveend_times.txt")
class Textz(Text):
    def __init__(self,
        text,
        font='WenYue XinQingNianTi (Authorization Required)',
        *args,**kargs):
        super().__init__(text,font=font,*args,**kargs)

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
        duration = et - st
        seconds = duration.total_seconds()
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        height=FRAME_HEIGHT
        width=height*4/3
        rec4_3=Rectangle(width,height) #4：3封面

        # video
        t1=Textz("佐佐酱").set_color(LIGHT_PINK)
        t2=Textz(f'{st.month}月{st.day}日直播回放')
        t3=Textz(f'直播时间：{st.hour}时{st.minute}分到{et.hour}时{et.minute}分')
        t4=Textz("私,懂？").set_color(LIGHT_PINK)
        t1.scale(2)
        t2.scale(1.5)
        t3.scale(1)
        t4.scale(1.3)
        grp=VGroup(t1,t2,t3,t4).arrange(DOWN)
        grp.set_width(FRAME_WIDTH-1)

        self.play(FadeIn(grp,lag_ratio=0.2),run_time=3)
        self.wait()
