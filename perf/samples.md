# 阶段 0b 素材清单

## normal_dense/normal_dense_peak_7月30日20点23分
- 原始视频：Tasks（原始来源，已复制到 worktree）\峰哥\7月30日20点23分.flv
- 原始弹幕：Tasks（原始来源，已复制到 worktree）\峰哥\7月30日20点23分.jsonl
- 截取视频：D:\DanmakuRender-perf\perf\samples\normal_dense\normal_dense_peak_7月30日20点23分\7月30日20点23分_clip.flv
- 截取区间：前 300.0s
- 源分辨率：1080x1920；画布：1080x1920；worker：25fps
- JSONL：946；布局：721；raster：91；平均/峰值同屏：2.834/8
- E 输出：perf/outputs/phase0b/normal_dense/normal_dense_peak_7月30日20点23分/E_emoji_dmrender.mp4
- F 输出：perf/outputs/phase0b/normal_dense/normal_dense_peak_7月30日20点23分/F_dmrender.mp4

## normal_light/normal_light_不可一世杀手_11月8日01点19分
- 原始视频：Tasks（原始来源，已复制到 worktree）\不可一世杀手\11月8日01点19分.flv
- 原始弹幕：Tasks（原始来源，已复制到 worktree）\不可一世杀手\11月8日01点19分.jsonl
- 截取视频：D:\DanmakuRender-perf\perf\samples\normal_light\normal_light_不可一世杀手_11月8日01点19分\11月8日01点19分_clip.flv
- 截取区间：前 300.0s
- 源分辨率：1920x1080；画布：1920x1080；worker：60fps
- JSONL：127；布局：118；raster：10；平均/峰值同屏：0.319/3
- E 输出：perf/outputs/phase0b/normal_light/normal_light_不可一世杀手_11月8日01点19分/E_emoji_dmrender.mp4
- F 输出：perf/outputs/phase0b/normal_light/normal_light_不可一世杀手_11月8日01点19分/F_dmrender.mp4

## problem_resolution/problem_resolution_7月30日22点50分
- 原始视频：Tasks（原始来源，已复制到 worktree）\有问题的视频测试\分辨率切换\1\7月30日22点50分.flv
- 原始弹幕：Tasks（原始来源，已复制到 worktree）\有问题的视频测试\分辨率切换\1\7月30日22点50分.jsonl
- 截取视频：D:\DanmakuRender-perf\perf\samples\problem_resolution\problem_resolution_7月30日22点50分\7月30日22点50分_clip.flv
- 截取区间：前 300.0s
- 源分辨率：1600x1280；画布：1920x1080；worker：25fps
- JSONL：2469；布局：773；raster：124；平均/峰值同屏：3.697/13
- E 输出：perf/outputs/phase0b/problem_resolution/problem_resolution_7月30日22点50分/E_emoji_dmrender.mp4
- F 输出：perf/outputs/phase0b/problem_resolution/problem_resolution_7月30日22点50分/F_dmrender.mp4

## problem_sc_swap/problem_sc_swap_7月29日22点02分
- 原始视频：Tasks（原始来源，已复制到 worktree）\有问题的视频测试\跨段sc异常换位\7月29日22点02分.mp4
- 原始弹幕：Tasks（原始来源，已复制到 worktree）\有问题的视频测试\跨段sc异常换位\7月29日22点02分.jsonl
- 截取视频：D:\DanmakuRender-perf\perf\samples\problem_sc_swap\problem_sc_swap_7月29日22点02分\7月29日22点02分_clip.mp4
- 截取区间：前 300.0s
- 源分辨率：1080x1920；画布：1080x1920；worker：25fps
- JSONL：289；布局：187；raster：46；平均/峰值同屏：1.374/9
- E 输出：perf/outputs/phase0b/problem_sc_swap/problem_sc_swap_7月29日22点02分/E_emoji_dmrender.mp4
- F 输出：perf/outputs/phase0b/problem_sc_swap/problem_sc_swap_7月29日22点02分/F_dmrender.mp4

## full_coverage/峰哥 8月18日14点19分
- 原始视频：Tasks（原始来源，已复制到 worktree）\峰哥\8月18日14点19分.flv
- 原始弹幕：Tasks（原始来源，已复制到 worktree）\峰哥\8月18日14点19分.jsonl
- 截取：1860s–2160s；输出：D:\DanmakuRender-perf\perf\samples\full_coverage\原始\峰哥_8月18日14点19分_1860-2160.flv
- 增强弹幕：D:\DanmakuRender-perf\perf\samples\full_coverage\补充\峰哥_8月18日14点19分_1860-2160_augmented.jsonl；真实事件数量：392；总事件数量：406；类型：{'danmaku': 274, 'member': 3, 'gift': 117, 'superchat': 12, 'emoji_or_emoticon': 56}
- 真实 SC 价格（片段相对秒）：30 元（54.1s）, 50 元（121.5s）, 2 元（153.9s）, 30 元（192.8s）, 50 元（235.7s）, 30 元（297.9s）
- 测试配置副本：D:\DanmakuRender-perf\perf\samples\full_coverage\配置\global.yml；三层继承快照：D:\DanmakuRender-perf\perf\samples\full_coverage\配置\resolved_DMR-峰哥.yml；VIP 用户：477061263（原配置无 vip_users，未修改正式配置）
- 合成事件：6 个 handwritten SC，价格 0.5/5/20/75/300/1200 元，在 30–35 秒内密集进入；0.5 元至 30 秒档先退出，验证 slide_left reflow、重叠进出和六档颜色/时长。
- 合成事件：6 个 handwritten 礼物，价格 0.5/5/20/75/300/1200 元，集中在片段 20–25 秒；0.5 元礼物低于 slide_right.gift.min_price_cny=1，预期不显示。
- 合成事件：2 个 modified 会员，22s 和 24s，与礼物同段进入，验证 slide_right 共用队列。

## final_2h/少年不太冷 11月9日14点20分
- 原始视频：Tasks（原始来源，已复制到 worktree）\少年不太冷\11月9日14点20分.flv
- 原始弹幕：Tasks（原始来源，已复制到 worktree）\少年不太冷\11月9日14点20分.jsonl
- 隔离副本：D:\DanmakuRender-perf\perf\samples\final_2h\原始\（视频和原始弹幕）；增强弹幕：D:\DanmakuRender-perf\perf\samples\final_2h\补充\少年不太冷_11月9日14点20分_augmented.jsonl；类型统计：{'danmaku': 885, 'gift': 97, 'superchat': 36, 'member': 12}
- 合成事件：每 20 分钟一批，SC/礼物各六档价格、会员共用 slide_right、VIP 药丸和 Emoji；0.5 元礼物按 min_price_cny=1 预期不显示。
- 三层配置副本：D:\DanmakuRender-perf\perf\samples\final_2h\配置；任务配置保留正式 VIP 用户 187495453/199828655/9881469。

## 演示视频/00_素材与旧版 平台素材库
- B 站：演示视频/00_素材与旧版/bilibili_full_coverage_峰哥_1860-2160/；旧版 E、原始/补充 JSONL、FLV 和配置齐全。
- 抖音：演示视频/00_素材与旧版/douyin_不可一世杀手_01点19分/；平台不支持 SC/会员，本目录只补礼物与 VIP。
- 斗鱼：演示视频/00_素材与旧版/douyu_少年不太冷_0-300/；含礼物、SC、会员、VIP/Emoji。
- 虎牙：Tasks 未发现可确认录播，未建立目录。
- 统一配置快照：perf/configs_snapshot/；统一表情包：moji_pack/。


## 2026-11-11：B站中段覆盖增强
仅更新 `演示视频/00_素材与旧版/bilibili_峰哥_7月30日20点23分_中段/7月30日20点23分_中段_augmented.jsonl`；原始638条（弹幕602、礼物36）保留。
- 新增39条modified事件：20礼物、10SC、1会员、8普通/表情弹幕；总补充54条。所有新事件带`_scenario=panel_coverage`，复跑幂等。
- 0.75–6s：礼物和SC的0.5/1/10/30/50/100/1000元，全部6种颜色/4种时长档；34/35/36/63/122/180.75/181.25s陆续退场。
- 80s：5礼物+1会员同刻进入，另3SC；110s：5礼物和3SC同刻退出，会员保留。
- 120–125.6s：8礼物/0.8s间隔；16–24s：5纯URL平台emoticon输入；28/30/32s：3条文字混排。实际布局计划有10条URL表情（原始+补充+混排）；密集轨道仍按原有拥挤规则决定接纳。
- 表情元数据来自本地真实emoticon登记，保留emoticon_unique与URL，输入不带path；冷缓存下载与复用另有CPU回归测试。
- 只在测试副本改gift最小价0、SC使用配置颜色、圆角20px。演示与完整配置见`演示视频/2026-11-11_06_闪烁与素材覆盖/说明.txt`。
