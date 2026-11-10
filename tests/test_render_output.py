import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from DMR.Render.gpu_render import NV12Buffer, mux_audio, parse_encoder_args, schedule_frames, validate_av_duration
from DMR.Render.resolution import fit_canvas, resolve_render_canvas
from DMR.Render.sprites import draw_round, draw_panel, round_mask


@pytest.mark.parametrize('source,canvas,expected', [
    ((1600,1280),(1920,1080),(1350,1080,284,0)),
    ((1080,1920),(1920,1080),(608,1080,656,0)),
    ((1920,1080),(1080,1920),(1080,608,0,656)),
    ((1920,1080),(1920,1080),(1920,1080,0,0)),
])
def test_fit_canvas(source,canvas,expected):
    assert fit_canvas(source,canvas) == expected
    assert resolve_render_canvas((1600,1280),rescale_to_1080p=True) == (1920,1080)


def test_canvas_pixels_and_black_bars():
    # Exercise the production copy/indexing path on NumPy, using a CPU CAI shim.
    class Plane:
        def __init__(self,array):
            self.array = array
            self.__cuda_array_interface__ = dict(array.__array_interface__)
            self.__cuda_array_interface__['strides'] = array.strides
    class CP:
        empty, uint8, int32, float32 = np.empty, np.uint8, np.int32, np.float32
        arange, minimum, maximum = np.arange, np.minimum, np.maximum
        @staticmethod
        def asarray(view, copy=False):
            return arrays[view.__cuda_array_interface__['data'][0]]
        @staticmethod
        def RawKernel(code,name):
            # CPU implementation of the sampling contract; native CUDA pixels
            # are independently compared to a scalar reference in perf.
            def run(grid,block,args):
                sy,suv,dst = args[:3]
                cw,ch,fw,fh,ox,oy = map(int,args[3:9])
                targets = [dst[:ch],dst[ch:].reshape(ch//2,cw//2,2)]
                targets[0].fill(16)
                targets[1].fill(128)
                for index,(src,target) in enumerate(zip((sy,suv),targets)):
                    factor = 2**index
                    x,wx,y,wy = args[16+4*index:20+4*index]
                    x1,y1 = np.minimum(x+1,src.shape[1]-1),np.minimum(y+1,src.shape[0]-1)
                    if index:
                        wx,wy = wx[:,None],wy[:,None]
                    top = src[y[:,None],x[None,:]]*(1-wx)+src[y[:,None],x1[None,:]]*wx
                    bottom = src[y1[:,None],x[None,:]]*(1-wx)+src[y1[:,None],x1[None,:]]*wx
                    values = np.floor(top*(1-wy[:,None])+bottom*wy[:,None]+.5).astype(np.uint8)
                    target[oy//factor:(oy+fh)//factor,ox//factor:(ox+fw)//factor] = values
            return run
    y = np.full((8,10,1),200,np.uint8)
    uv = np.full((4,5,2),90,np.uint8)
    arrays = {a.__array_interface__['data'][0]:a for a in (y,uv)}
    class Frame:
        def cuda(self): return [Plane(y),Plane(uv)]
    canvas = NV12Buffer(CP,20,10)
    canvas.load_into_canvas(Frame())
    w,h,x,y_offset = fit_canvas((10,8),(20,10))
    assert np.all(canvas.y[:, :x] == 16)
    assert np.all(canvas.y[:, x+w:] == 16)
    assert np.all(canvas.y[y_offset:y_offset+h,x:x+w] == 200)
    assert np.all(canvas.uv[:, :x//2] == 128)
    # The next decoded frame changes from landscape to portrait.
    y=np.full((10,8,1),80,np.uint8)
    uv=np.full((5,4,2),110,np.uint8)
    arrays.update({a.__array_interface__['data'][0]:a for a in (y,uv)})
    canvas.load_into_canvas(Frame())
    assert np.all(canvas.y[:,:6] == 16)
    assert np.all(canvas.y[:,6:14] == 80)
    assert np.all(canvas.y[:,14:] == 16)


@pytest.mark.parametrize('args,expected', [
    ([],{}), (['-b:v','4M'],{'bitrate':'4000000'}),
    (['-b:v','4000K','-maxrate','6M','-bufsize','8M'],{'bitrate':'4000000','maxbitrate':'6000000','vbvbufsize':'8000000'}),
    (['-preset','p4','-rc','vbr'],{'preset':'p4','rc':'vbr'}),
])
def test_encoder_options(args,expected): assert parse_encoder_args(args) == expected


@pytest.mark.parametrize('args',[['-b:v'],['-maxrate','-bufsize'],['-b:v','-1'],['-b:v','invalid']])
def test_encoder_bad_options(args):
    with pytest.raises(ValueError): parse_encoder_args(args)


def test_final_frame_and_intentional_limit():
    frames = [(index, index/25) for index in range(5)]
    full = list(schedule_frames(iter(frames[1:]), frames[0],50,source_fps=25))
    assert [x[0] for x in full] == [0,0,1,1,2,2,3,3,4,4]
    short = list(schedule_frames(iter(frames[1:]),frames[0],50,source_fps=25,max_frames=3))
    assert len(short) == 3


def test_common_audio_video_origin_and_single_frame_tail():
    frames = [(0,0.1),(1,0.14)]
    result=list(schedule_frames(iter(frames[1:]),frames[0],25,source_fps=25,start_offset=0.1))
    assert len(result) == 5
    assert result[-1][0] == 1
    assert list(schedule_frames(iter([]),(0,0),50,source_fps=25)) == [(0,0),(0,0.02)]


def test_schedule_rejects_nonmonotonic_source():
    with pytest.raises(ValueError):
        list(schedule_frames(iter([(1,0.04),(2,0.02)]),(0,0),25))


def test_decoder_drains_tail_with_empty_packet(monkeypatch):
    from types import SimpleNamespace
    from DMR.Render import gpu_render as gpu
    packet, eof = object(), object()
    class Demux:
        def __iter__(self): return iter([packet])
        def GetNvCodecId(self): return 'h264'
        def GetTimebaseNum(self): return 1
        def GetTimebaseDen(self): return 25
    calls=[]
    def decode(item):
        calls.append(item)
        return [SimpleNamespace(getPTS=lambda: 0 if item is packet else 1)]
    nvc=SimpleNamespace(CreateDemuxer=lambda **_:Demux(), CreateDecoder=lambda **_:SimpleNamespace(Decode=decode), PacketData=lambda:eof)
    cp=SimpleNamespace(cuda=SimpleNamespace(Event=lambda **_:SimpleNamespace(record=lambda:None,synchronize=lambda:None)))
    monkeypatch.setattr(gpu,'load_cuda_modules',lambda:(cp,nvc))
    monkeypatch.setattr(gpu,'frame_geometry',lambda _:(2,2))
    monkeypatch.setattr(gpu,'NV12Buffer',lambda *_:SimpleNamespace(load=lambda _:None))
    frames=list(gpu.decode_frames(Path('source.flv'),nvc,0))
    assert calls == [packet,eof]
    assert [item[1] for item in frames] == [0,0.04]


def test_mux_trim_only_when_requested():
    for duration in (None,1.2):
        with patch('DMR.Render.gpu_render.subprocess.run') as run:
            mux_audio(Path('input.hevc'),Path('source.flv'),Path('out.mp4'),25,duration)
        command = run.call_args.args[0]
        assert ('-t' in command) == (duration is not None)


def test_full_duration_disagreement_fails():
    with patch('DMR.Render.gpu_render.subprocess.run') as run:
        run.return_value.stdout = json.dumps({'streams':[{'codec_type':'video','duration':'2.0'}, {'codec_type':'audio','duration':'2.2'}]})
        with pytest.raises(ValueError): validate_av_duration(Path('out.mp4'))


@pytest.mark.parametrize('shape',['round_top','round_bottom','stadium'])
def test_round_reference_and_antialias(shape):
    actual=draw_round((100,60),20,shape,fill=(50,120,200,255),outline=(0,0,0,255),border=2)
    reference=Image.open(Path(__file__).parent/'references'/f'{shape}.png').convert('RGBA')
    assert np.array_equal(np.asarray(actual),np.asarray(reference))
    alpha=np.asarray(actual.getchannel('A'))
    assert np.any((alpha>0)&(alpha<255))
    assert alpha[30,50] == 255
    assert alpha[0,0] < 30 if shape != 'round_bottom' else alpha[0,0] > 200


def test_panel_reference_multiline_and_border():
    folder=Path(__file__).parent/'references'
    visual=json.loads((folder/'panel.json').read_text(encoding='utf-8'))
    actual=draw_panel(visual,250,150)
    assert np.array_equal(np.asarray(actual),np.asarray(Image.open(folder/'panel.png')))
    assert np.asarray(actual)[60:140,10:200,3].max() == 255


def test_half_panel_has_square_join():
    top=np.asarray(round_mask((100,40),30,'round_top'))
    bottom=np.asarray(round_mask((100,60),30,'round_bottom'))
    assert np.all(top[-1,:] == 255)
    assert np.all(bottom[0,:] == 255)
