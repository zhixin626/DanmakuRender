# B站平台表情缓存同步

- 扫描目录：`D:\DanmakuRender\Tasks文件\峰哥` 下 `6 个原始 JSONL`。
- 发现 `emoticon_unique`：96 个，记录总数：96 个唯一键对应的注册项。
- 复制来源：`emoji_pack/bilibili/emoticon`。
- 目标：`演示视频/00_素材与旧版/bilibili_full_coverage_峰哥_1860-2160/emoticon`。
- 已复制文件：93 个；缺失注册/文件：3 个。
- 后续对这 3 个仅有 URL 的条目使用共享 `DMR/Render/sprites.py:EmoticonStore` 下载并写入目标缓存，最终 96/96 个键均已解析。

## 缺失项

- `upower_[Mygo表情包_溜了溜了]`：[Mygo表情包_溜了溜了]，缓存文件 `未登记`，URL `https://i0.hdslb.com/bfs/garb/item/9c8a1429487e0aff3df8a4a73ed04f1cf51cbca8.png`
- `upower_[千恋万花表情包_zzz]`：[千恋万花表情包_zzz]，缓存文件 `未登记`，URL `https://i0.hdslb.com/bfs/garb/635aaeed91adaf5c551de3c3ad10d743479d3158.png`
- `upower_[千恋万花表情包_哼哼]`：[千恋万花表情包_哼哼]，缓存文件 `未登记`，URL `https://i0.hdslb.com/bfs/garb/cd3202427654e052f2fec1978c99d30dbb5a31b3.png`

索引字段使用每条原始记录中的嵌套 `emoticon.emoticon_unique/name/url`，包含 `official_*` 和 `upower_*` 两类平台表情。
