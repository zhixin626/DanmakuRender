"""A URL-only platform emoticon must survive a genuinely empty cache."""
import io
from pathlib import Path
from unittest.mock import Mock

import numpy as np
from PIL import Image

from DMR.Render import sprites


def test_url_only_emoticon_downloads_once_and_enters_atlas(monkeypatch,tmp_path):
    payload = io.BytesIO()
    Image.new('RGBA',(24,24),(255,50,10,255)).save(payload,format='PNG')
    content = payload.getvalue()
    class Response(io.BytesIO):
        headers = {'Content-Length':str(len(content))}
    download = Mock(side_effect=lambda *_,**__:Response(content))
    monkeypatch.setattr(sprites.urllib.request,'urlopen',download)
    style = {'font':'Microsoft YaHei','bold':True,'size':30,'color':'#FFFFFF',
             'opacity':1,'outline':1,'outline_color':'#000000'}
    def plan():
        return {'duration':2,'events':[{'id':'platform','bounds':{'width':80,'height':40},
            'path':[[0,10,10],[2,10,10]],'visual':{'type':'line','text':'[测试]',
            'style':style,'images':{'[测试]':{'emoticon_unique':'official_test','name':'测试',
            'url':'http://i0.hdslb.com/bfs/live/test.png','width':24,'height':24}}}}]}
    cache = tmp_path/'emoji_pack/bilibili/emoticon'
    for _ in range(2):
        source = plan()
        atlas = sprites.build_atlas(source,emoticon_dir=cache)
        asset = source['events'][0]['visual']['images']['[测试]']
        assert Path(asset['path']).is_file() and Path(asset['path']).parent==cache
        assert np.any(atlas['a']) and len(atlas['events'])==1
    assert download.call_count==1
    assert (cache/'emoticon.json').is_file()
