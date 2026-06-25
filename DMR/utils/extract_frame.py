import logging
import os

logger = logging.getLogger(__name__)

try:
    import cv2
    import numpy as np
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False


def _save_image(frame, path: str, quality: int = 97) -> bool:
    ret, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if ret:
        np.array(buf).tofile(path)
        return True
    return False


def _crop_face_center(frame, faces, ratio: float = 1.6):
    h, w = frame.shape[:2]
    logger.debug(f"[crop] 原始帧尺寸: w={w}, h={h}, ratio={ratio}")
    logger.debug(f"[crop] 检测到人脸数: {len(faces)}, faces={faces}")

    # 竖向以宽为基底，横向以高为基底
    if h > w:
        crop_w = w
        crop_h = int(w / ratio)
    else:
        crop_h = h
        crop_w = min(int(h * ratio), w)

    logger.debug(f"[crop] 目标裁剪尺寸: crop_w={crop_w}, crop_h={crop_h}")

    # 裁剪中心：有人脸取人脸中心，否则取画面中心
    if len(faces) > 0:
        fx, fy, fw, fh = faces[0]
        center_x = fx + fw // 2
        center_y = fy + fh // 2
        logger.debug(f"[crop] 人脸框: fx={fx}, fy={fy}, fw={fw}, fh={fh}")
        logger.debug(f"[crop] 人脸中心: center_x={center_x}, center_y={center_y}")
    else:
        center_x = w // 2
        center_y = h // 2
        logger.debug(f"[crop] 无人脸，使用画面中心: center_x={center_x}, center_y={center_y}")

    x1 = center_x - crop_w // 2
    y1 = center_y - crop_h // 2
    x2 = x1 + crop_w
    y2 = y1 + crop_h
    logger.debug(f"[crop] 裁剪框（平移前）: x1={x1}, y1={y1}, x2={x2}, y2={y2}")

    # 平移裁剪框使其不超出边界
    if x1 < 0:
        x2 -= x1; x1 = 0
    if y1 < 0:
        y2 -= y1; y1 = 0
    if x2 > w:
        x1 -= x2 - w; x2 = w
    if y2 > h:
        y1 -= y2 - h; y2 = h

    x1, y1 = max(x1, 0), max(y1, 0)
    logger.debug(f"[crop] 裁剪框（平移后）: x1={x1}, y1={y1}, x2={x2}, y2={y2}")
    logger.debug(f"[crop] 最终裁剪尺寸: w={x2-x1}, h={y2-y1}, 宽高比={(x2-x1)/(y2-y1):.3f}")
    return frame[y1:y2, x1:x2]


def extract_best_frame(video_path: str, output_dir: str = None, sample_count: int = 10, ratio='16/9') -> str:
    # 解析 ratio，支持 "16/9" 或 1.778 两种写法
    if isinstance(ratio, str) and '/' in ratio:
        a, b = ratio.split('/')
        ratio = float(a) / float(b)
    else:
        ratio = float(ratio)

    if not _HAS_CV2:
        raise RuntimeError('cv2 未安装，无法自动提取封面')

    if output_dir is None:
        output_dir = os.path.dirname(os.path.abspath(video_path))

    frames_dir = os.path.join(output_dir, 'frames')
    os.makedirs(frames_dir, exist_ok=True)

    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    eye_cascade  = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_eye.xml')

    cap = cv2.VideoCapture(video_path)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fw_img = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh_img = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # 检测 SAR（样本宽高比），有些直播录制格式像素非方形
    sar_num = cap.get(cv2.CAP_PROP_SAR_NUM)
    sar_den = cap.get(cv2.CAP_PROP_SAR_DEN)
    if sar_num > 0 and sar_den > 0 and abs(sar_num - sar_den) > 0.01:
        display_w = int(round(fw_img * sar_num / sar_den))
    else:
        display_w = fw_img

    sample_range = total_frames // 3
    step = max(1, sample_range // sample_count)
    frame_number = 1
    all_frames = []
    open_frames = []

    for i in range(0, sample_range, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ret, frame = cap.read()
        if not ret:
            continue

        # 应用 SAR 校正，确保画面比例正确
        if display_w != fw_img:
            frame = cv2.resize(frame, (display_w, fh_img))

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        brightness = np.mean(gray)
        if brightness < 30:
            frame_number += 1
            continue

        sharpness = cv2.Laplacian(gray, cv2.CV_64F).var()
        score = sharpness * (brightness / 255)

        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
        valid_faces = [(fx, fy, fw, fh) for (fx, fy, fw, fh) in faces if fw > fw_img * 0.1]

        eye_status = 'none'
        if len(valid_faces) > 0:
            for (fx, fy, fw, fh) in valid_faces:
                face_roi = gray[fy:fy + int(fh * 0.6), fx:fx + fw]
                eyes = eye_cascade.detectMultiScale(face_roi, scaleFactor=1.1, minNeighbors=8)
                valid_eyes = [(ex, ey, ew, eh) for (ex, ey, ew, eh) in eyes if fw * 0.10 < ew < fw * 0.40]
                if len(valid_eyes) >= 2:
                    eye_status = 'open'
                    break
            else:
                eye_status = 'closed'

        _save_image(frame, os.path.join(frames_dir, f'frame{frame_number}.jpg'))
        record = {'frame': frame, 'score': score, 'index': frame_number, 'faces': valid_faces}
        all_frames.append(record)
        if eye_status == 'open':
            open_frames.append(record)
        frame_number += 1

    cap.release()

    if open_frames:
        best = max(open_frames, key=lambda x: x['score'])
        logger.debug(f"[extract] 选用睁眼帧: index={best['index']}, score={best['score']:.2f}, faces={best['faces']}")
    elif all_frames:
        best = max(all_frames, key=lambda x: x['score'])
        logger.debug(f"[extract] 无睁眼帧，选最高分帧: index={best['index']}, score={best['score']:.2f}, faces={best['faces']}")
    else:
        cap2 = cv2.VideoCapture(video_path)
        cap2.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ret, frame = cap2.read()
        cap2.release()
        if not ret:
            raise RuntimeError(f'视频无法读取: {video_path}')
        if display_w != fw_img:
            frame = cv2.resize(frame, (display_w, fh_img))
        best = {'frame': frame, 'score': 0, 'index': 0, 'faces': []}
        logger.debug(f"[extract] 所有帧都被跳过，使用第0帧兜底")

    final = _crop_face_center(best['frame'], best['faces'], ratio=ratio)
    final_path = os.path.join(output_dir, 'extracted_frame.jpg')
    _save_image(final, final_path)
    return final_path
