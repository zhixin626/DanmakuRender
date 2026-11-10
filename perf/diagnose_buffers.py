"""Inspect the installed NVENC binding's packet timestamps and input lifetime."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from DMR.Render.gpu_render import load_cuda_modules, NV12Buffer


def main():
    cp, nvc = load_cuda_modules()
    encoder = nvc.CreateEncoder(1080, 1920, 'NV12', False, codec='hevc', fps='50')
    print('Bframes', encoder.GetNumBFrames())
    buffers = [NV12Buffer(cp, 1080, 1920) for _ in range(50)]
    for index, buffer in enumerate(buffers):
        buffer.y.fill(16 + index)
        buffer.uv.fill(128)
        cp.cuda.Stream.null.synchronize()
        params = nvc.NV_ENC_PIC_PARAMS()
        params.inputTimeStamp = index
        params.frameIdx = index
        packets = encoder.Encode(buffer, params)
        print(index, [(item['timestamp'], item['picture_type']) for item in packets])
    print('drain', [item['timestamp'] for item in encoder.EndEncode()])


if __name__ == '__main__':
    main()
