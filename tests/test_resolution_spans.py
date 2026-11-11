from DMR.Render.resolution import decide_session_parts
from DMR.utils import VideoInfo


def test_video_info_keeps_gpu_resolution_timeline():
    video = VideoInfo(
        path="segment.mp4",
        duration=120,
        resolution_spans=[(0.0, 120.0, 1920, 1080)],
    )
    assert video.resolution_spans == [(0.0, 120.0, 1920, 1080)]
    decision = decide_session_parts(video.resolution_spans, minority_minutes=10)
    assert decision["action"] == "single"
