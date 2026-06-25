import sys
sys.path.append("D:/zhixin_videos")
from manim_imports_custom import *
class cover(InteractiveScene):
    default_camera_config = {"background_color": BLACK}
    def construct(self):
        # start
        if len(sys.argv) >= 8:
            name  = sys.argv[3]
            time  = sys.argv[4]
            color = sys.argv[5]
            year  = sys.argv[6]
            image_path  = sys.argv[7]
        else:
            name  = "月亮3"
            time  = "6月7日"
            color = "#FFFF00"
            year  = "2026"
            image_path=R"D:\DanmakuRender\Tasks文件\月亮3（弹幕版）\extracted_frame.jpg"
        # F
        if image_path:
            image =ImageMobject(image_path)
            image.set_opacity(0.2)
            image.set_height(FRAME_HEIGHT)
            self.add(image)
        safe_rec  = Safe(y=-2.5).rec
        kwargs    = {"font":"WenYue XinQingNianTi (Authorization Required)"}
        name      = Text(name,**kwargs).set_color(color).scale(1.8).set_backstroke(BLACK,6)
        time      = Text(time,**kwargs).set_color(WHITE).scale(1.5).set_backstroke(BLACK,6)
        replay    = Text("直播回放",**kwargs).set_color(WHITE).scale(1).set_backstroke(BLACK,6)
        year     = Text(year,font='Freestyle Script')
        year.scale(2.3).set_color(color).set_backstroke(BLACK,6)
        grp       = VGroup(name,time,replay).arrange(DOWN)
        max_width  = safe_rec.get_width() - 1
        grp.set_width(max_width)
        max_height = safe_rec.get_height() - 1
        if grp.get_height() > max_height:
            grp.set_height(max_height)
        grp.move_to(safe_rec)
        max_top  = np.array([0,3.4,0])
        name.shift(max_top-name.get_top())
        line     = Underline(year,stroke_color=color)
        year_grp = VGroup(year,line)
        year_grp.next_to(time,RIGHT,aligned_edge=DOWN)
        self.add(year_grp,grp)
