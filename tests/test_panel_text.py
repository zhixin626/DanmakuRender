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


def test_wrap_keeps_words_numbers_and_explicit_paragraphs():
    measure = PillowText('Microsoft YaHei',30)
    budget = measure.measure('alpha beta')
    assert measure.wrap('alpha beta gamma',budget)==['alpha beta','gamma']
    assert measure.wrap('12345 67890',measure.measure('12345'))==['12345','67890']
    assert measure.wrap('123.456 789.012',measure.measure('123.456'))==['123.456','789.012']
    assert measure.wrap('one\n\nsecond',300)==['one','','second']
    assert measure.wrap('alpha ',measure.measure('alpha'))==['alpha']


def test_only_words_wider_than_whole_line_split_inside():
    measure = PillowText('Microsoft YaHei',30,1)
    word = 'Supercalifragilisticexpialidocious123456789'
    lines = measure.wrap(word,90)
    assert ''.join(lines)==word and len(lines)>1
    assert all(measure.measure(line)<=90 for line in lines)


@pytest.mark.parametrize('width',[90,130,210,300])
def test_wrap_cjk_kinsoku_and_pixel_width(width):
    measure = PillowText('Microsoft YaHei',30,1)
    text = '这是一句中文，后面还有（括号）和「引号」。还有English 12345！'
    lines = measure.wrap(text,width)
    assert ''.join(lines).replace(' ','')==text.replace(' ','')
    assert all(not line.startswith(tuple('。，、！？；：」』）》】）)]}')) for line in lines)
    assert all(not line.endswith(tuple('（([{【《〈「『')) for line in lines)
    # Verify actual drawing, including the existing uniform fit for a word
    # that fits alone but cannot fit together with its attached punctuation.
    assert all(measure.factory.get_sprite_image(line,(255,255,255),outline=1,fit_width=width).width<=width for line in lines)


def test_indivisible_number_and_punctuation_fit_without_reordering():
    measure = PillowText('Microsoft YaHei',30,1)
    text = '12345！'
    width = measure.measure('12345')+measure.measure('！')//2
    assert measure.measure('12345')<=width<measure.measure(text)
    assert measure.wrap(text,width)==[text]
    raw = measure.factory.get_sprite_image(text,(255,255,255),outline=1)
    actual = measure.factory.get_sprite_image(text,(255,255,255),outline=1,fit_width=width)
    assert actual.width==width and actual.height==round(raw.height*width/raw.width)
