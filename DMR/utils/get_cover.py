from manimlib import *
import sys
class cover(InteractiveScene):
    default_camera_config = {
        "background_color": BLACK,
    }
    def construct(self):
        # init
        frame=self.frame
        light=self.camera.light_source
        # start
        if len(sys.argv) >= 8:
            name = sys.argv[3]
            time = sys.argv[4]
            color= sys.argv[5]
        else:
            name = "不可一世杀手"
            time = "12月1日"
            color = "#83C167"

        # F

        height=FRAME_HEIGHT
        width=height*4/3
        safe_line=Line()
        safe_line.set_length(FRAME_WIDTH)
        y=-2.7
        safe_line.set_y(y)
        rec43=Rectangle(width,height)
        safe_height=FRAME_HEIGHT//2-y
        safe_rec=rec43.set_height(safe_height,stretch=True,about_edge=UP)
        kwargs={"font":"WenYue XinQingNianTi (Authorization Required)"}
        t1=Text(name,**kwargs)
        t2=Text(time,**kwargs)
        t3=Text("直播回放",**kwargs)
        t1.set_color(color).scale(1.8)
        t2.set_color(WHITE).scale(1.5)
        t3.set_color(WHITE).scale(1)
        grp=VGroup(t1,t2,t3).arrange(DOWN)
        grp.set_width(safe_rec.get_width()-1)
        if grp.get_height() > safe_rec.get_height():
            grp.set_height(safe_rec.get_height()-1)
        grp.move_to(safe_rec)
        year=Text("2025",font='Freestyle Script')
        year.scale(2.5).set_color(YELLOW)
        year.to_frame_corner(safe_rec,DR,buff=0.2)
        year.align_to(t3,DOWN)
        self.add(year)
        self.add(grp)