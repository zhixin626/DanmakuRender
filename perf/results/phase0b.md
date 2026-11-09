# 阶段 0b：修正测量与分类补测

## 测量环境
- CPU：Intel(R) Core(TM) i7-14700KF
- GPU：NVIDIA GeForce RTX 4070 Ti SUPER
- ffmpeg：ffmpeg version 2025-12-10-git-4f947880bd-full_build-www.gyan.dev Copyright (c) 2000-2025 the FFmpeg developers
- 逻辑核数：28
- 每组 3 次；组顺序 A→B→C→D→E→F；每组前背景 CPU 采样 30 秒；运行中每 0.5 秒采样。
- A/B/C 使用 worker guessed_rate/average_rate；帧数来自 ffmpeg progress；D/E 来自输出计数与 worker stats；F 来自 ffprobe。

## 素材

- normal_dense/normal_dense_peak_7月30日20点23分：源 1080x1920，画布 1080x1920，worker 25fps，raster 91，平均/峰值同屏 2.834/8。
  原始视频：D:\DanmakuRender\Tasks文件\峰哥\7月30日20点23分.flv
  原始弹幕：D:\DanmakuRender\Tasks文件\峰哥\7月30日20点23分.jsonl
  截取视频：D:\DanmakuRender-perf\perf\samples\normal_dense\normal_dense_peak_7月30日20点23分\7月30日20点23分_clip.flv
  E 输出：perf/outputs/phase0b/normal_dense/normal_dense_peak_7月30日20点23分/E_emoji_dmrender.mp4
  F 输出：perf/outputs/phase0b/normal_dense/normal_dense_peak_7月30日20点23分/F_dmrender.mp4

- normal_light/normal_light_不可一世杀手_11月8日01点19分：源 1920x1080，画布 1920x1080，worker 60fps，raster 10，平均/峰值同屏 0.319/3。
  原始视频：D:\DanmakuRender\Tasks文件\不可一世杀手\11月8日01点19分.flv
  原始弹幕：D:\DanmakuRender\Tasks文件\不可一世杀手\11月8日01点19分.jsonl
  截取视频：D:\DanmakuRender-perf\perf\samples\normal_light\normal_light_不可一世杀手_11月8日01点19分\11月8日01点19分_clip.flv
  E 输出：perf/outputs/phase0b/normal_light/normal_light_不可一世杀手_11月8日01点19分/E_emoji_dmrender.mp4
  F 输出：perf/outputs/phase0b/normal_light/normal_light_不可一世杀手_11月8日01点19分/F_dmrender.mp4

- problem_resolution/problem_resolution_7月30日22点50分：源 1600x1280，画布 1920x1080，worker 25fps，raster 124，平均/峰值同屏 3.697/13。
  原始视频：D:\DanmakuRender\Tasks文件\有问题的视频测试\分辨率切换\1\7月30日22点50分.flv
  原始弹幕：D:\DanmakuRender\Tasks文件\有问题的视频测试\分辨率切换\1\7月30日22点50分.jsonl
  截取视频：D:\DanmakuRender-perf\perf\samples\problem_resolution\problem_resolution_7月30日22点50分\7月30日22点50分_clip.flv
  E 输出：perf/outputs/phase0b/problem_resolution/problem_resolution_7月30日22点50分/E_emoji_dmrender.mp4
  F 输出：perf/outputs/phase0b/problem_resolution/problem_resolution_7月30日22点50分/F_dmrender.mp4

- problem_sc_swap/problem_sc_swap_7月29日22点02分：源 1080x1920，画布 1080x1920，worker 25fps，raster 46，平均/峰值同屏 1.374/9。
  原始视频：D:\DanmakuRender\Tasks文件\有问题的视频测试\跨段sc异常换位\7月29日22点02分.mp4
  原始弹幕：D:\DanmakuRender\Tasks文件\有问题的视频测试\跨段sc异常换位\7月29日22点02分.jsonl
  截取视频：D:\DanmakuRender-perf\perf\samples\problem_sc_swap\problem_sc_swap_7月29日22点02分\7月29日22点02分_clip.mp4
  E 输出：perf/outputs/phase0b/problem_sc_swap/problem_sc_swap_7月29日22点02分/E_emoji_dmrender.mp4
  F 输出：perf/outputs/phase0b/problem_sc_swap/problem_sc_swap_7月29日22点02分/F_dmrender.mp4

用户限定目录下最高密度的是峰哥竖屏素材；它作为 normal_dense 代表，输出保持源画布。计划目标 raster 500/平均同屏 10 未达到，横屏不可一世杀手素材更低，具体数值见 samples.md。

## 中位数结果
| 分类/素材 | 组 | 墙钟s | 帧数 | fps | 进程树CPU均/峰 | 核数均/峰 | 核秒 | 系统减背景核数均/峰 | D/E 解码/worker/编码CPU | py-spy |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| normal_dense/normal_dense_peak_7月30日20点23分 | A | 9.56 | 7501 | 785.0 | 3.9/70.6 | 0.04/0.71 | 0.4 | 0.29/0.97 | 3.9/0.0/0.0 | 不可用 |
| normal_dense/normal_dense_peak_7月30日20点23分 | B | 9.53 | 7506 | 787.3 | 4.4/79.4 | 0.04/0.79 | 0.4 | 0.56/1.08 | 4.4/0.0/0.0 | 不可用 |
| normal_dense/normal_dense_peak_7月30日20点23分 | C | 15.91 | 7506 | 471.7 | 3.7/111.6 | 0.04/1.12 | 0.6 | 1.03/1.96 | 3.7/0.0/0.0 | 不可用 |
| normal_dense/normal_dense_peak_7月30日20点23分 | D | 21.45 | 7500 | 349.7 | 294.5/480.0 | 2.95/4.80 | 63.2 | 3.35/6.21 | 188.0/61.3/46.7 | 不可用 |
| normal_dense/normal_dense_peak_7月30日20点23分 | E | 24.24 | 7500 | 309.4 | 347.3/550.3 | 3.47/5.50 | 84.6 | 3.75/7.18 | 188.4/111.8/47.1 | 不可用 |
| normal_dense/normal_dense_peak_7月30日20点23分 | F | 21.89 | 7500 | 342.7 | 154.6/217.7 | 1.55/2.18 | 33.8 | 1.51/2.68 | 154.6/0.0/0.0 | 不可用 |
| normal_light/normal_light_不可一世杀手_11月8日01点19分 | A | 20.17 | 18001 | 892.5 | 2.0/76.5 | 0.02/0.77 | 0.4 | 0.61/1.51 | 2.0/0.0/0.0 | 不可用 |
| normal_light/normal_light_不可一世杀手_11月8日01点19分 | B | 19.66 | 18001 | 915.8 | 2.5/91.0 | 0.02/0.91 | 0.5 | 0.99/2.64 | 2.5/0.0/0.0 | 不可用 |
| normal_light/normal_light_不可一世杀手_11月8日01点19分 | C | 21.86 | 18001 | 823.6 | 2.0/82.4 | 0.02/0.82 | 0.4 | 1.17/2.10 | 2.0/0.0/0.0 | 不可用 |
| normal_light/normal_light_不可一世杀手_11月8日01点19分 | D | 38.46 | 18000 | 468.1 | 349.3/544.3 | 3.49/5.44 | 134.3 | 4.00/6.65 | 194.8/88.5/68.5 | 不可用 |
| normal_light/normal_light_不可一世杀手_11月8日01点19分 | E | 41.05 | 18000 | 438.5 | 352.0/551.3 | 3.52/5.51 | 144.4 | 3.96/6.46 | 185.6/101.2/64.9 | 不可用 |
| normal_light/normal_light_不可一世杀手_11月8日01点19分 | F | 37.74 | 18000 | 477.0 | 123.9/188.3 | 1.24/1.88 | 46.8 | 1.37/2.97 | 123.9/0.0/0.0 | 不可用 |
| problem_resolution/problem_resolution_7月30日22点50分 | A | 9.07 | 7480 | 825.1 | 4.5/76.4 | 0.04/0.76 | 0.4 | 0.47/2.02 | 4.5/0.0/0.0 | 不可用 |
| problem_resolution/problem_resolution_7月30日22点50分 | B | 10.37 | 7502 | 723.7 | 12.2/231.4 | 0.12/2.31 | 1.3 | 4.99/7.45 | 12.2/0.0/0.0 | 不可用 |
| problem_resolution/problem_resolution_7月30日22点50分 | C | 41.55 | 7502 | 180.5 | 3.1/241.3 | 0.03/2.41 | 1.3 | 1.75/4.44 | 3.1/0.0/0.0 | 不可用 |
| problem_resolution/problem_resolution_7月30日22点50分 | D | 48.87 | 7500 | 153.5 | 272.3/596.7 | 2.72/5.97 | 133.9 | 2.86/7.00 | 218.0/31.3/22.8 | 不可用 |
| problem_resolution/problem_resolution_7月30日22点50分 | E | 51.17 | 7500 | 146.6 | 299.8/759.1 | 3.00/7.59 | 151.9 | 3.45/8.70 | 217.7/59.3/21.8 | 不可用 |
| problem_resolution/problem_resolution_7月30日22点50分 | F | 97.83 | 7500 | 76.7 | 116.5/169.6 | 1.17/1.70 | 114.0 | 1.27/3.86 | 116.5/0.0/0.0 | 不可用 |
| problem_sc_swap/problem_sc_swap_7月29日22点02分 | A | 9.57 | 7500 | 783.5 | 5.1/91.0 | 0.05/0.91 | 0.5 | 0.58/1.50 | 5.1/0.0/0.0 | 不可用 |
| problem_sc_swap/problem_sc_swap_7月29日22点02分 | B | 9.57 | 7508 | 784.6 | 4.2/76.5 | 0.04/0.77 | 0.4 | 0.54/1.73 | 4.2/0.0/0.0 | 不可用 |
| problem_sc_swap/problem_sc_swap_7月29日22点02分 | C | 10.65 | 7508 | 705.3 | 4.2/84.8 | 0.04/0.85 | 0.5 | 1.17/1.85 | 4.2/0.0/0.0 | 不可用 |
| problem_sc_swap/problem_sc_swap_7月29日22点02分 | D | 17.31 | 7500 | 433.3 | 329.7/502.2 | 3.30/5.02 | 57.7 | 3.76/6.64 | 195.7/75.0/57.7 | 不可用 |
| problem_sc_swap/problem_sc_swap_7月29日22点02分 | E | 20.58 | 7500 | 364.4 | 345.2/526.5 | 3.45/5.26 | 71.0 | 4.06/6.47 | 179.3/115.3/52.9 | 不可用 |
| problem_sc_swap/problem_sc_swap_7月29日22点02分 | F | 16.49 | 7500 | 454.8 | 153.0/220.7 | 1.53/2.21 | 25.2 | 1.76/2.40 | 153.0/0.0/0.0 | 不可用 |

## 每次运行

| 分类/素材 | 组 | 次数 | 墙钟s | 帧数 | fps | 进程树平均CPU | 平均核数 | 核秒 | 返回码 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| normal_dense/normal_dense_peak_7月30日20点23分 | A | 1 | 9.74 | 7501 | 770.3 | 3.5 | 0.03 | 0.3 | 0 |
| normal_dense/normal_dense_peak_7月30日20点23分 | B | 1 | 9.55 | 7506 | 785.9 | 4.7 | 0.05 | 0.5 | 0 |
| normal_dense/normal_dense_peak_7月30日20点23分 | C | 1 | 15.90 | 7506 | 472.1 | 3.7 | 0.04 | 0.6 | 0 |
| normal_dense/normal_dense_peak_7月30日20点23分 | D | 1 | 21.44 | 7500 | 349.8 | 292.4 | 2.92 | 62.7 | 0 |
| normal_dense/normal_dense_peak_7月30日20点23分 | E | 1 | 24.15 | 7500 | 310.6 | 347.3 | 3.47 | 83.9 | 0 |
| normal_dense/normal_dense_peak_7月30日20点23分 | F | 1 | 21.77 | 7500 | 344.5 | 152.7 | 1.53 | 33.2 | 0 |
| normal_dense/normal_dense_peak_7月30日20点23分 | A | 2 | 9.54 | 7501 | 785.9 | 4.6 | 0.05 | 0.4 | 0 |
| normal_dense/normal_dense_peak_7月30日20点23分 | B | 2 | 9.52 | 7506 | 788.1 | 4.4 | 0.04 | 0.4 | 0 |
| normal_dense/normal_dense_peak_7月30日20点23分 | C | 2 | 15.91 | 7506 | 471.7 | 3.2 | 0.03 | 0.5 | 0 |
| normal_dense/normal_dense_peak_7月30日20点23分 | D | 2 | 21.45 | 7500 | 349.7 | 294.5 | 2.95 | 63.2 | 0 |
| normal_dense/normal_dense_peak_7月30日20点23分 | E | 2 | 24.85 | 7500 | 301.9 | 343.6 | 3.44 | 85.4 | 0 |
| normal_dense/normal_dense_peak_7月30日20点23分 | F | 2 | 21.89 | 7500 | 342.7 | 154.6 | 1.55 | 33.8 | 0 |
| normal_dense/normal_dense_peak_7月30日20点23分 | A | 3 | 9.56 | 7501 | 785.0 | 3.9 | 0.04 | 0.4 | 0 |
| normal_dense/normal_dense_peak_7月30日20点23分 | B | 3 | 9.53 | 7506 | 787.3 | 4.4 | 0.04 | 0.4 | 0 |
| normal_dense/normal_dense_peak_7月30日20点23分 | C | 3 | 16.45 | 7506 | 456.3 | 4.2 | 0.04 | 0.7 | 0 |
| normal_dense/normal_dense_peak_7月30日20点23分 | D | 3 | 21.52 | 7500 | 348.5 | 300.4 | 3.00 | 64.6 | 0 |
| normal_dense/normal_dense_peak_7月30日20点23分 | E | 3 | 24.24 | 7500 | 309.4 | 348.9 | 3.49 | 84.6 | 0 |
| normal_dense/normal_dense_peak_7月30日20点23分 | F | 3 | 22.41 | 7500 | 334.6 | 155.3 | 1.55 | 34.8 | 0 |
| normal_light/normal_light_不可一世杀手_11月8日01点19分 | A | 1 | 20.17 | 18001 | 892.5 | 1.7 | 0.02 | 0.3 | 0 |
| normal_light/normal_light_不可一世杀手_11月8日01点19分 | B | 1 | 20.18 | 18001 | 892.1 | 2.9 | 0.03 | 0.6 | 0 |
| normal_light/normal_light_不可一世杀手_11月8日01点19分 | C | 1 | 22.34 | 18001 | 805.7 | 2.0 | 0.02 | 0.4 | 0 |
| normal_light/normal_light_不可一世杀手_11月8日01点19分 | D | 1 | 39.02 | 18000 | 461.3 | 358.0 | 3.58 | 139.7 | 0 |
| normal_light/normal_light_不可一世杀手_11月8日01点19分 | E | 1 | 41.01 | 18000 | 438.9 | 352.0 | 3.52 | 144.4 | 0 |
| normal_light/normal_light_不可一世杀手_11月8日01点19分 | F | 1 | 37.70 | 18000 | 477.4 | 122.4 | 1.22 | 46.2 | 0 |
| normal_light/normal_light_不可一世杀手_11月8日01点19分 | A | 2 | 20.14 | 18001 | 893.7 | 2.2 | 0.02 | 0.5 | 0 |
| normal_light/normal_light_不可一世杀手_11月8日01点19分 | B | 2 | 19.61 | 18001 | 918.0 | 2.3 | 0.02 | 0.5 | 0 |
| normal_light/normal_light_不可一世杀手_11月8日01点19分 | C | 2 | 21.86 | 18001 | 823.6 | 2.4 | 0.02 | 0.5 | 0 |
| normal_light/normal_light_不可一世杀手_11月8日01点19分 | D | 2 | 38.28 | 18000 | 470.2 | 349.3 | 3.49 | 133.7 | 0 |
| normal_light/normal_light_不可一世杀手_11月8日01点19分 | E | 2 | 41.05 | 18000 | 438.5 | 356.3 | 3.56 | 146.3 | 0 |
| normal_light/normal_light_不可一世杀手_11月8日01点19分 | F | 2 | 37.74 | 18000 | 477.0 | 123.9 | 1.24 | 46.8 | 0 |
| normal_light/normal_light_不可一世杀手_11月8日01点19分 | A | 3 | 20.21 | 18001 | 890.7 | 2.0 | 0.02 | 0.4 | 0 |
| normal_light/normal_light_不可一世杀手_11月8日01点19分 | B | 3 | 19.66 | 18001 | 915.8 | 2.5 | 0.02 | 0.5 | 0 |
| normal_light/normal_light_不可一世杀手_11月8日01点19分 | C | 3 | 21.81 | 18001 | 825.5 | 2.0 | 0.02 | 0.4 | 0 |
| normal_light/normal_light_不可一世杀手_11月8日01点19分 | D | 3 | 38.46 | 18000 | 468.1 | 349.1 | 3.49 | 134.3 | 0 |
| normal_light/normal_light_不可一世杀手_11月8日01点19分 | E | 3 | 41.64 | 18000 | 432.3 | 345.0 | 3.45 | 143.7 | 0 |
| normal_light/normal_light_不可一世杀手_11月8日01点19分 | F | 3 | 37.78 | 18000 | 476.4 | 126.7 | 1.27 | 47.9 | 0 |
| problem_resolution/problem_resolution_7月30日22点50分 | A | 1 | 9.05 | 7480 | 826.9 | 5.4 | 0.05 | 0.5 | 0 |
| problem_resolution/problem_resolution_7月30日22点50分 | B | 1 | 10.39 | 7502 | 722.2 | 13.7 | 0.14 | 1.4 | 0 |
| problem_resolution/problem_resolution_7月30日22点50分 | C | 1 | 42.58 | 7502 | 176.2 | 1.7 | 0.02 | 0.7 | 0 |
| problem_resolution/problem_resolution_7月30日22点50分 | D | 1 | 50.54 | 7500 | 148.4 | 271.1 | 2.71 | 137.0 | 0 |
| problem_resolution/problem_resolution_7月30日22点50分 | E | 1 | 51.17 | 7500 | 146.6 | 314.7 | 3.15 | 161.0 | 0 |
| problem_resolution/problem_resolution_7月30日22点50分 | F | 1 | 98.58 | 7500 | 76.1 | 115.8 | 1.16 | 114.1 | 0 |
| problem_resolution/problem_resolution_7月30日22点50分 | A | 2 | 9.08 | 7480 | 823.5 | 4.2 | 0.04 | 0.4 | 0 |
| problem_resolution/problem_resolution_7月30日22点50分 | B | 2 | 10.35 | 7502 | 724.8 | 12.2 | 0.12 | 1.3 | 0 |
| problem_resolution/problem_resolution_7月30日22点50分 | C | 2 | 41.55 | 7502 | 180.5 | 3.3 | 0.03 | 1.4 | 0 |
| problem_resolution/problem_resolution_7月30日22点50分 | D | 2 | 48.87 | 7500 | 153.5 | 274.0 | 2.74 | 133.9 | 0 |
| problem_resolution/problem_resolution_7月30日22点50分 | E | 2 | 51.36 | 7500 | 146.0 | 295.2 | 2.95 | 151.6 | 0 |
| problem_resolution/problem_resolution_7月30日22点50分 | F | 2 | 97.31 | 7500 | 77.1 | 116.5 | 1.17 | 113.4 | 0 |
| problem_resolution/problem_resolution_7月30日22点50分 | A | 3 | 9.07 | 7480 | 825.1 | 4.5 | 0.04 | 0.4 | 0 |
| problem_resolution/problem_resolution_7月30日22点50分 | B | 3 | 10.37 | 7502 | 723.7 | 11.5 | 0.11 | 1.2 | 0 |
| problem_resolution/problem_resolution_7月30日22点50分 | C | 3 | 41.49 | 7502 | 180.8 | 3.1 | 0.03 | 1.3 | 0 |
| problem_resolution/problem_resolution_7月30日22点50分 | D | 3 | 48.77 | 7500 | 153.8 | 272.3 | 2.72 | 132.8 | 0 |
| problem_resolution/problem_resolution_7月30日22点50分 | E | 3 | 50.67 | 7500 | 148.0 | 299.8 | 3.00 | 151.9 | 0 |
| problem_resolution/problem_resolution_7月30日22点50分 | F | 3 | 97.83 | 7500 | 76.7 | 116.5 | 1.17 | 114.0 | 0 |
| problem_sc_swap/problem_sc_swap_7月29日22点02分 | A | 1 | 9.56 | 7500 | 784.6 | 4.0 | 0.04 | 0.4 | 0 |
| problem_sc_swap/problem_sc_swap_7月29日22点02分 | B | 1 | 9.56 | 7508 | 785.8 | 4.2 | 0.04 | 0.4 | 0 |
| problem_sc_swap/problem_sc_swap_7月29日22点02分 | C | 1 | 10.63 | 7508 | 706.0 | 3.7 | 0.04 | 0.4 | 0 |
| problem_sc_swap/problem_sc_swap_7月29日22点02分 | D | 1 | 18.76 | 7500 | 399.8 | 313.5 | 3.14 | 58.8 | 0 |
| problem_sc_swap/problem_sc_swap_7月29日22点02分 | E | 1 | 20.52 | 7500 | 365.5 | 345.2 | 3.45 | 70.8 | 0 |
| problem_sc_swap/problem_sc_swap_7月29日22点02分 | F | 1 | 16.49 | 7500 | 454.8 | 153.0 | 1.53 | 25.2 | 0 |
| problem_sc_swap/problem_sc_swap_7月29日22点02分 | A | 2 | 9.58 | 7500 | 783.1 | 5.1 | 0.05 | 0.5 | 0 |
| problem_sc_swap/problem_sc_swap_7月29日22点02分 | B | 2 | 9.57 | 7508 | 784.6 | 5.4 | 0.05 | 0.5 | 0 |
| problem_sc_swap/problem_sc_swap_7月29日22点02分 | C | 2 | 10.66 | 7508 | 704.5 | 4.6 | 0.05 | 0.5 | 0 |
| problem_sc_swap/problem_sc_swap_7月29日22点02分 | D | 2 | 17.27 | 7500 | 434.2 | 329.7 | 3.30 | 56.9 | 0 |
| problem_sc_swap/problem_sc_swap_7月29日22点02分 | E | 2 | 20.60 | 7500 | 364.1 | 351.2 | 3.51 | 72.4 | 0 |
| problem_sc_swap/problem_sc_swap_7月29日22点02分 | F | 2 | 16.53 | 7500 | 453.7 | 153.9 | 1.54 | 25.4 | 0 |
| problem_sc_swap/problem_sc_swap_7月29日22点02分 | A | 3 | 9.57 | 7500 | 783.5 | 5.2 | 0.05 | 0.5 | 0 |
| problem_sc_swap/problem_sc_swap_7月29日22点02分 | B | 3 | 9.58 | 7508 | 784.0 | 4.2 | 0.04 | 0.4 | 0 |
| problem_sc_swap/problem_sc_swap_7月29日22点02分 | C | 3 | 10.65 | 7508 | 705.3 | 4.2 | 0.04 | 0.5 | 0 |
| problem_sc_swap/problem_sc_swap_7月29日22点02分 | D | 3 | 17.31 | 7500 | 433.3 | 333.4 | 3.33 | 57.7 | 0 |
| problem_sc_swap/problem_sc_swap_7月29日22点02分 | E | 3 | 20.58 | 7500 | 364.4 | 344.9 | 3.45 | 71.0 | 0 |
| problem_sc_swap/problem_sc_swap_7月29日22点02分 | F | 3 | 16.46 | 7500 | 455.6 | 151.3 | 1.51 | 24.9 | 0 |

## py-spy

版本：py-spy 0.4.3

## 结论
A/B/C 已按 worker 帧率和实际帧数测量；同时报告进程树 CPU 与系统减背景核数。D/E 提供解码、worker、编码 CPU 拆分。阶段 0b 完成后停止。
