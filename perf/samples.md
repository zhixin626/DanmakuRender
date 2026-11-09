# 阶段 0b 素材清单

## normal_dense/normal_dense_peak_7月30日20点23分
- 原始视频：D:\DanmakuRender\Tasks文件\峰哥\7月30日20点23分.flv
- 原始弹幕：D:\DanmakuRender\Tasks文件\峰哥\7月30日20点23分.jsonl
- 截取视频：D:\DanmakuRender-perf\perf\samples\normal_dense\normal_dense_peak_7月30日20点23分\7月30日20点23分_clip.flv
- 截取区间：前 300.0s
- 源分辨率：1080x1920；画布：1080x1920；worker：25fps
- JSONL：946；布局：721；raster：91；平均/峰值同屏：2.834/8
- E 输出：perf/outputs/phase0b/normal_dense/normal_dense_peak_7月30日20点23分/E_emoji_dmrender.mp4
- F 输出：perf/outputs/phase0b/normal_dense/normal_dense_peak_7月30日20点23分/F_dmrender.mp4

## normal_light/normal_light_不可一世杀手_11月8日01点19分
- 原始视频：D:\DanmakuRender\Tasks文件\不可一世杀手\11月8日01点19分.flv
- 原始弹幕：D:\DanmakuRender\Tasks文件\不可一世杀手\11月8日01点19分.jsonl
- 截取视频：D:\DanmakuRender-perf\perf\samples\normal_light\normal_light_不可一世杀手_11月8日01点19分\11月8日01点19分_clip.flv
- 截取区间：前 300.0s
- 源分辨率：1920x1080；画布：1920x1080；worker：60fps
- JSONL：127；布局：118；raster：10；平均/峰值同屏：0.319/3
- E 输出：perf/outputs/phase0b/normal_light/normal_light_不可一世杀手_11月8日01点19分/E_emoji_dmrender.mp4
- F 输出：perf/outputs/phase0b/normal_light/normal_light_不可一世杀手_11月8日01点19分/F_dmrender.mp4

## problem_resolution/problem_resolution_7月30日22点50分
- 原始视频：D:\DanmakuRender\Tasks文件\有问题的视频测试\分辨率切换\1\7月30日22点50分.flv
- 原始弹幕：D:\DanmakuRender\Tasks文件\有问题的视频测试\分辨率切换\1\7月30日22点50分.jsonl
- 截取视频：D:\DanmakuRender-perf\perf\samples\problem_resolution\problem_resolution_7月30日22点50分\7月30日22点50分_clip.flv
- 截取区间：前 300.0s
- 源分辨率：1600x1280；画布：1920x1080；worker：25fps
- JSONL：2469；布局：773；raster：124；平均/峰值同屏：3.697/13
- E 输出：perf/outputs/phase0b/problem_resolution/problem_resolution_7月30日22点50分/E_emoji_dmrender.mp4
- F 输出：perf/outputs/phase0b/problem_resolution/problem_resolution_7月30日22点50分/F_dmrender.mp4

## problem_sc_swap/problem_sc_swap_7月29日22点02分
- 原始视频：D:\DanmakuRender\Tasks文件\有问题的视频测试\跨段sc异常换位\7月29日22点02分.mp4
- 原始弹幕：D:\DanmakuRender\Tasks文件\有问题的视频测试\跨段sc异常换位\7月29日22点02分.jsonl
- 截取视频：D:\DanmakuRender-perf\perf\samples\problem_sc_swap\problem_sc_swap_7月29日22点02分\7月29日22点02分_clip.mp4
- 截取区间：前 300.0s
- 源分辨率：1080x1920；画布：1080x1920；worker：25fps
- JSONL：289；布局：187；raster：46；平均/峰值同屏：1.374/9
- E 输出：perf/outputs/phase0b/problem_sc_swap/problem_sc_swap_7月29日22点02分/E_emoji_dmrender.mp4
- F 输出：perf/outputs/phase0b/problem_sc_swap/problem_sc_swap_7月29日22点02分/F_dmrender.mp4

## full_coverage/峰哥 8月18日14点19分
- 原始视频：D:\DanmakuRender\Tasks文件\峰哥\8月18日14点19分.flv
- 原始弹幕：D:\DanmakuRender\Tasks文件\峰哥\8月18日14点19分.jsonl
- 截取：1860s–2160s；输出：D:\DanmakuRender-perf\perf\samples\full_coverage\峰哥_8月18日14点19分_1860-2160.flv
- 增强弹幕：D:\DanmakuRender-perf\perf\samples\full_coverage\峰哥_8月18日14点19分_1860-2160_augmented.jsonl；真实事件数量：392；总事件数量：406；类型：{'danmaku': 274, 'member': 3, 'gift': 117, 'superchat': 12, 'emoji_or_emoticon': 56}
- 真实 SC 价格（片段相对秒）：30 元（54.1s）, 50 元（121.5s）, 2 元（153.9s）, 30 元（192.8s）, 50 元（235.7s）, 30 元（297.9s）
- 测试配置副本：D:\DanmakuRender-perf\perf\tmp\full_coverage_config\global.yml；三层继承快照：D:\DanmakuRender-perf\perf\tmp\full_coverage_config\resolved_DMR-峰哥.yml；VIP 用户：477061263（原配置无 vip_users，未修改正式配置）
- 合成事件：6 个 handwritten SC，价格 0.5/5/20/75/300/1200 元，在 30–35 秒内密集进入；0.5 元至 30 秒档先退出，验证 slide_left reflow、重叠进出和六档颜色/时长。
- 合成事件：6 个 handwritten 礼物，价格 0.5/5/20/75/300/1200 元，集中在片段 20–25 秒；0.5 元礼物低于 slide_right.gift.min_price_cny=1，预期不显示。
- 合成事件：2 个 modified 会员，22s 和 24s，与礼物同段进入，验证 slide_right 共用队列。
