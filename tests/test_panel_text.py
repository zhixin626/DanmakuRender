"""Pillow panel layout must use the same pixels as the atlas renderer."""
from pathlib import Path

import numpy as np
from PIL import Image
import pytest
import yaml

from DMR.Render.gpu_render import EventScanner
from DMR.Render.layout_compiler import LayoutCompiler
from DMR.Render.layout_models import RenderEvent
from DMR.Render.sprites import PillowText, draw_panel, load_pack


@pytest.mark.parametrize('size,outline', [(30,1),(46,3),(64,0)])
def test_long_mixed_sc_actual_pixels_and_height(size,outline):
    config = yaml.safe_load(Path('DMR/Config/default.yml').read_text(encoding='utf8'))['render_args']['overlay']
    config['slide_left']['fontsize'] = size
    config['slide_left']['content_border_width'] = outline
    pack = load_pack('emoji_pack/bilibili')
    class Emoji:
        def resolve_images(self,text):
            return {}
    emoji = Emoji()
    emoji.pack = pack
    compiler = LayoutCompiler(config,engine='gpu_render',engine_config=config)
    panel = compiler._prepare_panel('long','slide_left',0,30,'用户名 😀',
        '中英混排 Pillow wrapping must fit the real panel [比心] 😀 ' * 8,
        1080,1920,config['slide_left']['superchat'],config['slide_left'],
        panel_class='superchat',record={'price_cny':30},emoji_measurer=emoji)
    nodes = panel['visual']['nodes']
    body = nodes[-1]
    lines = body['text'].splitlines()
    assert len(lines)>=3
    measure = PillowText(body['font'],body['size'],body['outline'],pack)
    for line in lines:
        # No clipping or forced fit here: normal wrapped lines must fit by
        # measurement alone, including the real outline and image widths.
        sprite = measure.factory.get_sprite_image(line,(255,255,255),outline=outline)
        bbox = sprite.getbbox()
        assert measure.measure(line)==sprite.width
        assert sprite.width<=body['width']
        assert bbox is None or bbox[2]-bbox[0]<=body['width']
        assert measure.height(line,body['width'])==sprite.height
    assert nodes[1]['height']==len(lines)*body['line_height']+9+14
    assert panel['bounds']['height']==nodes[0]['height']+nodes[1]['height']
    assert draw_panel(panel['visual'],**panel['bounds'],pack=pack).getbbox()


def test_panel_shortcodes_use_same_platform_pack(tmp_path):
    path = tmp_path/'green.png'
    Image.new('RGBA',(20,20),(0,255,0,255)).save(path)
    visual = {'nodes':[{'type':'text','font':'Microsoft YaHei','size':30,
        'outline':0,'outline_color':'#000000','color':'#ffffff','opacity':1,
        'anchor':'top_left','x':0,'y':0,'text':'[green] 😀','images':{},'layer':1}]}
    panel = np.asarray(draw_panel(visual,200,60,{'green':str(path)}))
    assert np.count_nonzero((panel[:,:,1]>200)&(panel[:,:,0]<20)&(panel[:,:,3]>200))>100
    measure = PillowText('Microsoft YaHei',30,pack={'green':str(path)})
    assert list(measure.units('[green]'))==['[green]']
    assert measure.wrap('[green][green]',measure.measure('[green]'))==['[green]','[green]']


def test_single_wide_emoticon_fits_and_zwj_stays_atomic(tmp_path):
    path = tmp_path/'wide.png'
    Image.new('RGBA',(1000,60),'green').save(path)
    measure = PillowText('Microsoft YaHei',30,pack={'wide':str(path)})
    assert measure.wrap('[wide]',40)==['[wide]']
    sprite = measure.factory.get_sprite_image('[wide]',(255,255,255),outline=0,fit_width=40)
    assert sprite.width==40
    assert sprite.height==measure.height('[wide]',40)
    assert list(measure.units('👨‍👩‍👧‍👦😀'))==['👨‍👩‍👧‍👦','😀']


def test_panel_order_keeps_r2l_on_top_and_stable_within_track():
    def event(name,track):
        return RenderEvent(name,0,2,2,0,1,((0,0,0),(1,0,0)),{},track)
    events = [event('text','r2l'),event('gift','slide_right'),event('sc','slide_left')]
    active = EventScanner(events).visible(.5)
    assert [item[0].id for item in active]==['gift','sc','text']
    # Exercise alpha compositing at the overlapping pixel with scanner order.
    colors = {'text':'red','gift':'green','sc':'blue'}
    frame = Image.new('RGBA',(2,2))
    for item in active:
        frame.alpha_composite(Image.new('RGBA',(2,2),colors[item[0].id]))
    assert frame.getpixel((1,1))==(255,0,0,255)
