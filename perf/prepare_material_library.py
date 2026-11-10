"""Build the local platform material library used by the demo videos."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import copy
import argparse
from collections import Counter


ROOT = pathlib.Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "perf" / "samples"
CONFIGS = ROOT / "perf" / "configs_snapshot"
DEST = ROOT / "演示视频" / "00_素材与旧版"


def read_jsonl(path: pathlib.Path) -> tuple[dict, list[dict]]:
    meta: dict = {}
    events: list[dict] = []
    with path.open(encoding="utf-8-sig") as source:
        for line in source:
            record = json.loads(line)
            if record.get("type") == "meta":
                meta = record
            elif record.get("received_at") is not None:
                events.append(record)
    return meta, events


def write_jsonl(path: pathlib.Path, meta: dict, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as target:
        target.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for event in sorted(events, key=lambda item: item["received_at"]):
            target.write(json.dumps(event, ensure_ascii=False) + "\n")


def filter_window(source: pathlib.Path, duration: float) -> tuple[dict, list[dict]]:
    meta, events = read_jsonl(source)
    start = float(meta["segment_start_time"])
    selected = [event for event in events if 0 <= event["received_at"] - start < duration]
    return {**meta, "segment_start_time": start}, selected


def synthetic_douyin(meta: dict, events: list[dict]) -> list[dict]:
    start = float(meta["segment_start_time"])
    events = list(events)
    for index, price in enumerate((1, 5, 20, 50)):
        events.append({
            "type": "gift", "received_at": start + 30 + index,
            "uid": f"douyin-synthetic-gift-{index}", "uname": "抖音素材补充用户",
            "gift_name": f"抖音礼物{index + 1}", "gift_count": 1,
            "gift_price": price, "unit_price_cny": price, "price_cny": price,
            "content": f"送出 抖音礼物{index + 1}", "_synthetic": "handwritten",
        })
    events.append({
        "type": "danmaku", "received_at": start + 40,
        "uid": "douyin-synthetic-vip", "uname": "抖音VIP补充用户",
        "content": "VIP 😀 [dog]", "color": "ffffff", "_synthetic": "modified",
    })
    return events


def synthetic_douyu(meta: dict, events: list[dict]) -> list[dict]:
    start = float(meta["segment_start_time"])
    events = list(events)
    for index, price in enumerate((1, 5, 20, 50)):
        events.append({
            "type": "gift", "received_at": start + 30 + index,
            "uid": f"douyu-synthetic-gift-{index}", "uname": "斗鱼素材补充用户",
            "gift_name": f"斗鱼礼物{index + 1}", "gift_count": 1,
            "gift_price": price, "unit_price_cny": price, "price_cny": price,
            "content": f"送出 斗鱼礼物{index + 1}", "_synthetic": "handwritten",
        })
    events.append({
        "type": "superchat", "received_at": start + 45,
        "uid": "douyu-synthetic-sc", "uname": "斗鱼SC补充用户",
        "content": "斗鱼 SC 😀", "price": 300, "price_unit": "鱼翅",
        "price_cny": 30, "platform_duration": 60, "_synthetic": "handwritten",
    })
    events.append({
        "type": "member", "received_at": start + 55,
        "uid": "douyu-synthetic-member", "uname": "斗鱼会员补充用户",
        "member_name": "舰长", "member_price": 138, "member_count": 1,
        "member_unit": "月", "price_cny": 138, "content": "开通 舰长×1月",
        "_synthetic": "modified",
    })
    events.append({
        "type": "danmaku", "received_at": start + 65,
        "uid": "douyu-synthetic-vip", "uname": "斗鱼VIP补充用户",
        "content": "VIP 😀 [dog]", "color": "ffffff", "_synthetic": "modified",
    })
    return events


def ensure_bilibili_emoticon(meta: dict, events: list[dict]) -> list[dict]:
    """Keep a visible Bilibili pure-emoticon event in the supplemented sample."""
    events = list(events)
    if any(event.get("_synthetic") == "handwritten" and event.get("emoticon") for event in events):
        return events
    start = float(meta["segment_start_time"])
    events.append({
        "type": "danmaku", "received_at": start + 42,
        "uid": "bilibili-synthetic-emoticon", "uname": "B站表情补充用户",
        "content": "[上热榜]", "color": "ffffff",
        "emoticon": {
            "emoticon_unique": "official_134", "name": "上热榜",
            "url": "http://i0.hdslb.com/bfs/live/83d5b9cdaaa820c2756c013031d34dac1fd4156b.png",
            "width": 168, "height": 60, "is_dynamic": 1,
        },
        "_synthetic": "handwritten",
    })
    return events


def copy_config(destination: pathlib.Path, task_name: str) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("global.yml", f"DMR-{task_name}.yml"):
        shutil.copy2(CONFIGS / name, destination / name)


def enhance_bilibili(path: pathlib.Path) -> dict:
    """Extend only the augmented JSONL; keep source footage and events intact."""
    meta, existing = read_jsonl(path)
    start = float(meta['segment_start_time'])
    events = [event for event in existing if event.get('_scenario') != 'panel_coverage']
    templates = {}
    for event in events:
        templates.setdefault(event['type'],event)
    real_meta, real = read_jsonl(SAMPLES/'full_coverage/原始/峰哥_8月18日14点19分_1860-2160_source.jsonl')
    for event in real:
        if event['type'] in ('superchat','member'):
            templates[event['type']] = event
    additions = []

    def add(kind, seconds, price=None, **overrides):
        event = copy.deepcopy(templates[kind])
        event.update(received_at=start+seconds,_synthetic='modified',_scenario='panel_coverage',
                     _source_note='复制隔离素材中的真实事件结构并改写测试参数')
        event.update(overrides)
        if price is not None:
            event['price_cny'] = price
            if kind == 'gift':
                event.update(gift_price=price*10,unit_price_cny=price,gift_count=1,price_unit='电池')
            elif kind == 'superchat':
                event.update(price=price)
            elif kind == 'member':
                event.update(member_price=price,member_count=1)
        event['uname'] = f'覆盖{kind}{len(additions)+1}'
        # Distinct users avoid a future combo-merger accidentally collapsing a
        # deliberately simultaneous cohort.
        event['uid'] = f'coverage-{len(additions)+1}'
        additions.append(event)

    # Seven values cover all six color ranges AND all four duration ranges.
    # The expensive 180s panels enter first, so they also leave within this clip.
    for seconds,price in zip((.75,1.25,2,3,4,5,6),(1000,100,50,30,10,1,.5)):
        add('gift',seconds,price,gift_name=f'档位{price}元',content=f'礼物档位{price}')
        add('superchat',seconds,price,content=f'SC {price}元\n价格与时长覆盖 😀')
    # Five gifts + one member at the exact same timestamp. The five low-price
    # gifts and the three SCs all expire at 110s, leaving the member to reflow.
    for price in (1,5,10,20,25):
        add('gift',80,price,gift_name='同时进入',content='同时进入/110秒同时退出')
    add('member',80,138,member_name='舰长',member_unit='月',content='同刻会员保留')
    for price in (1,5,10):
        add('superchat',80,price,content='同刻 SC\n110秒同时退出')
    for index,price in enumerate((1,5,30,50,100,1000,10,1)):
        add('gift',120+index*.8,price,gift_name=f'连续{index+1}',content='10秒内8条')
    registry = json.loads((ROOT/'emoji_pack/bilibili/emoticon/emoticon.json').read_text(encoding='utf-8'))
    pure = [(key,value) for key,value in registry.items() if value.get('url')][:5]
    for index,(key,asset) in enumerate(pure):
        emote = {k:asset[k] for k in ('name','url','width','height','is_dynamic') if k in asset}
        emote['emoticon_unique'] = key
        token = '['+str(asset['name']).strip('[]')+']'
        add('danmaku',16+index*2,content=token,color='ffffff',emoticon=emote)
        if index<3:
            add('danmaku',28+index*2,content=f'文字混排 {token} 下载表情',color='ffffff',emoticon=emote)
    write_jsonl(path,meta,events+additions)
    return {'added':len(additions),'types':dict(Counter(e['type'] for e in additions)),
            'price_values':[.5,1,10,30,50,100,1000],
            'moments':{'price_tiers':'.75–6s','pure_emoticon':'16/18/20/22/24s',
                       'mixed_emoticon':'28/30/32s','simultaneous_enter':80,
                       'simultaneous_exit':110,'burst':'120–125.6s',
                       'staggered_exit':'34/35/36/63/122/180.75/181.25s'},
            'total_types':dict(Counter(e['type'] for e in events+additions))}


def enhance_text_coverage(path: pathlib.Path) -> dict:
    """Modify real local event structures to exercise panel text and overlap."""
    meta, existing = read_jsonl(path)
    events = [event for event in existing if event.get('_scenario') != 'text_coverage']
    templates = {kind: next(event for event in events if event['type']==kind)
                 for kind in ('superchat','gift','danmaku')}
    start = float(meta['segment_start_time'])
    additions = []
    for index,seconds in enumerate((12,52,94),1):
        sc = copy.deepcopy(templates['superchat'])
        sc.update(received_at=start+seconds,uname=f'长SC{index} 😀 [比心]',price=1,price_cny=1,
                  content=('长 SC 中英混排：Pillow measures the exact pixels used for drawing. '
                           '[比心] 😀 [啊] 🎉 请检查换行、图片和底部留白。')*3)
        additions.append(sc)
        gift = copy.deepcopy(templates['gift'])
        gift.update(received_at=start+seconds+6,uname=f'礼物用户{index} 😀 🎉',
                    gift_name='Emoji用户名',gift_count=1,gift_price=10,price_cny=1,unit_price_cny=1)
        additions.append(gift)
    # Cover the upper/middle panel stack with accepted ordinary text lanes.
    for index in range(16):
        dm = copy.deepcopy(templates['danmaku'])
        dm.pop('emoticon',None)
        dm.update(received_at=start+80+index*.08,content='层级重叠：普通弹幕始终在礼物面板上方 '+str(index),color='ffffff')
        additions.append(dm)
    for index,event in enumerate(additions):
        event.update(uid=f'text-coverage-{index}',_synthetic='modified',_scenario='text_coverage',
                     _source_note='从隔离素材中的真实事件复制结构，改写正文、用户名与时间')
    write_jsonl(path,meta,events+additions)
    return {'added':len(additions),'long_sc_seconds':[12,52,94],
            'emoji_gift_seconds':[18,58,100],'overlap_seconds':'80–90'}


def copy_case(name: str, video: pathlib.Path, raw: pathlib.Path, augmented: pathlib.Path, config_name: str) -> dict:
    destination = DEST / name
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(video, destination / video.name)
    shutil.copy2(raw, destination / raw.name)
    shutil.copy2(augmented, destination / augmented.name)
    copy_config(destination / "配置", config_name)
    return {
        "name": name, "video": destination / video.name,
        "raw": destination / raw.name, "augmented": destination / augmented.name,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--enhance-bilibili',type=pathlib.Path)
    args = parser.parse_args()
    if args.enhance_bilibili:
        print(json.dumps(enhance_bilibili(args.enhance_bilibili),ensure_ascii=False,indent=2))
        return
    DEST.mkdir(parents=True, exist_ok=True)
    cases: list[dict] = []

    bili_video = SAMPLES / "full_coverage" / "原始" / "峰哥_8月18日14点19分_1860-2160.flv"
    bili_raw = SAMPLES / "full_coverage" / "原始" / "峰哥_8月18日14点19分_1860-2160_source.jsonl"
    bili_aug = SAMPLES / "full_coverage" / "补充" / "峰哥_8月18日14点19分_1860-2160_augmented.jsonl"
    bili_meta, bili_events = read_jsonl(bili_aug)
    bili_aug_local = ROOT / "perf" / "tmp" / "material_library" / "bilibili_augmented.jsonl"
    write_jsonl(bili_aug_local, bili_meta, ensure_bilibili_emoticon(bili_meta, bili_events))
    cases.append(copy_case("bilibili_full_coverage_峰哥_1860-2160", bili_video, bili_raw, bili_aug_local, "峰哥"))

    douyin_root = SAMPLES / "normal_light" / "normal_light_不可一世杀手_11月8日01点19分"
    douyin_video = douyin_root / "11月8日01点19分_clip.flv"
    douyin_raw = douyin_root / "11月8日01点19分_clip.jsonl"
    douyin_meta, douyin_events = filter_window(douyin_raw, 300)
    douyin_aug = ROOT / "perf" / "tmp" / "material_library" / "douyin_augmented.jsonl"
    write_jsonl(douyin_aug, douyin_meta, synthetic_douyin(douyin_meta, douyin_events))
    cases.append(copy_case("douyin_不可一世杀手_01点19分", douyin_video, douyin_raw, douyin_aug, "不可一世杀手"))

    douyu_root = SAMPLES / "final_2h" / "原始"
    douyu_source = douyu_root / "少年不太冷_11月9日14点20分.flv"
    douyu_video = ROOT / "perf" / "tmp" / "material_library" / "少年不太冷_0-300.flv"
    douyu_video.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-nostdin", "-i", str(douyu_source),
        "-t", "300", "-c", "copy", str(douyu_video),
    ], check=True)
    douyu_raw_source = douyu_root / "少年不太冷_11月9日14点20分.jsonl"
    douyu_meta, douyu_events = filter_window(douyu_raw_source, 300)
    douyu_raw = ROOT / "perf" / "tmp" / "material_library" / "少年不太冷_0-300.jsonl"
    write_jsonl(douyu_raw, douyu_meta, douyu_events)
    douyu_aug = ROOT / "perf" / "tmp" / "material_library" / "少年不太冷_0-300_augmented.jsonl"
    write_jsonl(douyu_aug, douyu_meta, synthetic_douyu(douyu_meta, douyu_events))
    cases.append(copy_case("douyu_少年不太冷_0-300", douyu_video, douyu_raw, douyu_aug, "少年不太冷"))

    summary = []
    for case in cases:
        meta, events = read_jsonl(case["augmented"])
        counts = Counter(event.get("type") for event in events)
        start = float(meta["segment_start_time"])
        moments = {}
        for kind in ("superchat", "gift", "member"):
            moments[kind] = [round(event["received_at"] - start, 1) for event in events if event.get("type") == kind]
        summary.append({**case, "counts": dict(counts), "moments": moments})
    (DEST / "library.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
