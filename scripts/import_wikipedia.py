#!/usr/bin/env python3
"""从 Wikipedia 分类 + Wikidata 导入里程碑相机数据。

不依赖 WDQS（故障时可用）：遍历 en.wikipedia 相机分类 -> 解析 Wikidata QID ->
批量取实体（标签/描述/P176 制造商/P571 年份/P18 图片/词条数），按词条数取前 N。

    python3 scripts/import_wikipedia.py            # 导入 200 款
    python3 scripts/import_wikipedia.py --limit 50 # 测试
    python3 scripts/import_wikipedia.py --dry-run  # 只看分布，不写文件

已存在的 data/cameras/<id>.json 会被跳过，可重复运行增量更新。
类型/品牌映射规则复用 import_wikidata.py。
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE, "data", "cameras")
sys.path.insert(0, os.path.join(BASE, "scripts"))
from import_wikidata import (  # noqa: E402
    DESC_RULES, NAME_RULES, MFR_MAP, map_brand, strip_brand_prefix,
    detect_brand_from_label, map_type, parse_year,
)

UA = "CameraMuseumBot/0.1 (personal research project; data import for camera-museum)"
WIKI_API = "https://en.wikipedia.org/w/api.php"
WD_API = "https://www.wikidata.org/w/api.php"

SEED_CATS = [
    "Single-lens reflex cameras",
    "Digital single-lens reflex cameras",
    "Rangefinder cameras",
    "Twin-lens reflex cameras",
    "Instant cameras",
    "Point-and-shoot cameras",
    "Digital cameras",
    "Box cameras",
    "Folding cameras",
    "Cameras by brand",
    "Cameras by type",
    "Stereo cameras",
    "View cameras",
    "Bridge cameras",
    "Digital rangefinder cameras",
]

SKIP_TITLE_RE = re.compile(
    r"^(List of|Comparison of|Template|Category|File|Wikipedia)|"
    r"lens(es)?\b|\bLens\b|镜头", re.I)

# 描述里出现这些词的不是相机整机，直接排除
JUNK_DESC_RE = re.compile(
    r"smartphone|smart phone|mobile phone|telephone|手机|"
    r"accessory|配件|camera brand|camera manufacturer|"
    r"video game|printer|打印|tablet|laptop|watch|手表|television|"
    r"webcam|movie camera|cinema|shaver|"
    r"\bbrand\b|品牌|"
    r"range of|trademark|\bcompany\b|公司", re.I)

JUNK_TITLE_RE = re.compile(
    r"iphone|galaxy s|galaxy note|pixel \d|nokia [a-z0-9]|huawei|xiaomi|oneplus|"
    r"playstation|xbox|cinema", re.I)


def api(url, params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"User-Agent": UA,
                                          "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def cat_members(cat, depth=0, max_depth=2, seen=None):
    """返回 (pages, subcats)；递归进 subcat。"""
    if seen is None:
        seen = set()
    pages, subcats = [], []
    cmcontinue = None
    while True:
        p = {"action": "query", "format": "json", "list": "categorymembers",
             "cmtitle": f"Category:{cat}", "cmtype": "page|subcat",
             "cmlimit": "500", "cmcontinue": cmcontinue} if cmcontinue else \
            {"action": "query", "format": "json", "list": "categorymembers",
             "cmtitle": f"Category:{cat}", "cmtype": "page|subcat", "cmlimit": "500"}
        try:
            d = api(WIKI_API, p)
        except Exception as e:
            print(f"  分类 {cat} 读取失败：{e}")
            return pages, subcats
        for m in d["query"]["categorymembers"]:
            if m["ns"] == 14:
                name = m["title"].split(":", 1)[1]
                if name not in seen and depth < max_depth:
                    seen.add(name)
                    subcats.append(name)
            elif m["ns"] == 0:
                pages.append(m["title"])
        cmcontinue = d.get("continue", {}).get("cmcontinue")
        if not cmcontinue:
            break
    return pages, subcats


def collect_titles():
    titles, seen_cats = set(), set()
    for cat in SEED_CATS:
        print(f"分类：{cat}", flush=True)
        pages, subcats = cat_members(cat, 0, 2, seen_cats)
        titles.update(p for p in pages if not SKIP_TITLE_RE.search(p))
        print(f"  直接页面 {len(pages)}，子分类 {len(subcats)}")
        for sub in subcats:
            sp, _ = cat_members(sub, 1, 2, seen_cats)
            titles.update(p for p in sp if not SKIP_TITLE_RE.search(p))
    print(f"共收集 {len(titles)} 个不重复标题")
    return sorted(titles)


def resolve_qids(titles):
    """标题 -> QID（50 个一批）。"""
    out = {}
    for i in range(0, len(titles), 50):
        batch = titles[i:i + 50]
        d = api(WIKI_API, {"action": "query", "format": "json",
                           "titles": "|".join(batch),
                           "prop": "pageprops", "ppprop": "wikibase_item"})
        for p in d["query"]["pages"].values():
            if "pageprops" in p and "wikibase_item" in p["pageprops"]:
                out[p["title"]] = p["pageprops"]["wikibase_item"]
    print(f"解析到 {len(out)} 个 QID")
    return out


def wb_entities(qids):
    """50 个一批取实体。"""
    out = {}
    qids = list(qids)
    for i in range(0, len(qids), 50):
        batch = qids[i:i + 50]
        d = api(WD_API, {"action": "wbgetentities", "format": "json",
                         "ids": "|".join(batch),
                         "props": "labels|descriptions|claims|sitelinks",
                         "languages": "zh-cn|zh|en", "languagefallback": "1"})
        out.update(d.get("entities", {}))
        print(f"  实体 {min(i+50, len(qids))}/{len(qids)}", flush=True)
    return out


def pick_label(ent):
    labels = ent.get("labels", {})
    for lang in ("zh-cn", "zh", "en"):
        if lang in labels:
            return labels[lang]["value"]
    return ""


def claim_values(ent, pid):
    vals = []
    for c in ent.get("claims", {}).get(pid, []):
        dv = c.get("mainsnak", {}).get("datavalue")
        if not dv:
            continue
        if dv["type"] == "wikibase-entityid":
            vals.append(("item", dv["value"]["id"]))
        elif dv["type"] == "string":
            vals.append(("string", dv["value"]))
        elif dv["type"] == "time":
            vals.append(("time", dv["value"]["time"]))
    return vals


def mfr_labels(qids):
    """制造商 QID -> 标签。"""
    out = {}
    qids = list(qids)
    for i in range(0, len(qids), 50):
        batch = qids[i:i + 50]
        d = api(WD_API, {"action": "wbgetentities", "format": "json",
                         "ids": "|".join(batch), "props": "labels",
                         "languages": "zh-cn|zh|en", "languagefallback": "1"})
        for qid, ent in d.get("entities", {}).items():
            out[qid] = pick_label(ent)
    return out


def main():
    limit, dry_run = 200, False
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--dry-run":
            dry_run = True
        elif a == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])

    titles = collect_titles()
    title2qid = resolve_qids(titles)
    entities = wb_entities(set(title2qid.values()))

    # 制造商标签
    mfr_qids = set()
    for ent in entities.values():
        for kind, v in claim_values(ent, "P176"):
            if kind == "item":
                mfr_qids.add(v)
    mfr_name = mfr_labels(mfr_qids)
    print(f"制造商 {len(mfr_name)} 个")

    cands = []
    for title, qid in title2qid.items():
        ent = entities.get(qid)
        if not ent or ent.get("missing"):
            continue
        label = pick_label(ent) or title
        if re.search(r"lens|镜头", label, re.I):
            continue
        if JUNK_TITLE_RE.search(label):
            continue
        desc = ent.get("descriptions", {}).get("en", {}).get("value", "")
        if re.search(r"\blens\b|镜头", desc, re.I):
            continue
        if JUNK_DESC_RE.search(desc):
            continue
        mfrs = [v for k, v in claim_values(ent, "P176") if k == "item"]
        if not mfrs:
            continue
        years = [parse_year(v) for k, v in claim_values(ent, "P571") if k == "time"]
        year = next((y for y in years if y), None)
        if not year:
            continue
        imgs = [v for k, v in claim_values(ent, "P18") if k == "string"]
        n_sites = len(ent.get("sitelinks", {}))
        cands.append({"qid": qid, "title": title, "label": label, "desc": desc,
                      "mfr_qid": mfrs[0], "year": year,
                      "img": imgs[0] if imgs else None, "sitelinks": n_sites})
    cands.sort(key=lambda c: -c["sitelinks"])
    print(f"候选 {len(cands)} 个（有制造商+年份+非镜头）")

    existing_ids = {f[:-5] for f in os.listdir(DATA_DIR) if f.endswith(".json")}
    selected, stats = [], Counter()
    for c in cands:
        if len(selected) >= limit:
            break
        mfr_label = mfr_name.get(c["mfr_qid"], "")
        # 品牌优先从型号标签识别（如“宾得 K-5”虽制造商为豪雅，仍归宾得）
        detected, det_prefix = detect_brand_from_label(c["label"])
        if detected:
            brand_en, brand_cn, bslug, country = detected
        else:
            brand_info = map_brand(mfr_label)
            if brand_info is None:
                stats["制造商被排除"] += 1
                continue
            brand_en, brand_cn, bslug, country = brand_info
        model = strip_brand_prefix(c["label"], brand_en, brand_cn, mfr_label)
        if det_prefix:  # 用识别到的品牌前缀精确剥离
            model = re.sub(r"^" + re.escape(det_prefix) + r"(?=\s|$|[A-Z0-9])",
                           "", c["label"]).strip() or model
        model_part = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", model.lower())).strip("-")
        cid = f"{bslug}-{model_part}" if model_part else f"{bslug}-{c['qid'].lower()}"
        if cid in existing_ids:
            stats["已存在"] += 1
            continue
        existing_ids.add(cid)
        c.update({"cid": cid, "brand_en": brand_en, "brand_cn": brand_cn,
                  "bslug": bslug, "country": country, "mfr_label": mfr_label,
                  "model": model})
        selected.append(c)
    print(f"选中 {len(selected)} 款；跳过：{dict(stats)}")

    type_counter, fallback_cases, unmapped = Counter(), [], set()
    for s in selected:
        t, tcn = map_type(s["desc"], "", s["model"], s["bslug"], s["year"])
        s["type"], s["type_cn"] = t, tcn
        type_counter[f"{t} / {tcn}"] += 1
        if tcn in ("数码相机", "胶片相机"):
            fallback_cases.append(s)
        if not s["brand_cn"]:
            unmapped.add(s["mfr_label"])
    print("\n[类型分布]")
    for k, n in type_counter.most_common():
        print(f"  {n:4d}  {k}")
    if unmapped:
        print("\n[未映射制造商]")
        for m in sorted(unmapped):
            print("  -", m)
    if fallback_cases:
        print(f"\n[兜底 {len(fallback_cases)} 款，前 15]")
        for s in fallback_cases[:15]:
            print(f"  {s['label']} ({s['year']}) | {s['desc'][:45]} -> {s['type_cn']}")
    print("\n[抽样 Top12]")
    for s in selected[:12]:
        print(f"  {s['label']} | {s['brand_en']} | {s['year']} | {s['type_cn']} | {s['desc'][:40]}")

    if dry_run:
        print("\n[dry-run] 结束，未写文件。")
        return

    written = 0
    for s in selected:
        img_url = ("https://commons.wikimedia.org/wiki/Special:FilePath/" +
                   urllib.parse.quote(s["img"]) + "?width=800") if s["img"] else None
        cam = {
            "id": s["cid"],
            "brand": s["brand_en"],
            "brand_cn": s["brand_cn"],
            "brand_slug": s["bslug"],
            "country": s["country"],
            "manufacturer": s["mfr_label"],
            "model": s["model"],
            "year": s["year"],
            "type": s["type"],
            "type_cn": s["type_cn"],
            "format": "待补充",
            "mount": "待补充",
            "milestone": True,
            "tagline": f"{s['brand_cn'] or s['brand_en']}{s['type_cn']}，{s['year']} 年发布",
            "description": f"{s['brand_cn'] or s['brand_en']}于{s['year']}年发布的{s['type_cn']}。",
            "significance": "待补充（需人工撰写）",
            "specs": {"Wikidata": s["qid"], "多语言词条数": str(s["sitelinks"])},
            "images": ([{"remote_url": img_url, "caption": s["model"],
                         "source": "Wikimedia Commons", "license": "见文件页授权",
                         "url": f"https://www.wikidata.org/wiki/{s['qid']}"}] if img_url else []),
            "sources": [{"title": f"Wikidata – {s['label']}",
                         "url": f"https://www.wikidata.org/wiki/{s['qid']}"}],
            "qid": s["qid"],
            "sitelinks": s["sitelinks"],
            "imported": "wikipedia-wikidata-2026-11-01",
        }
        with open(os.path.join(DATA_DIR, s["cid"] + ".json"), "w", encoding="utf-8") as f:
            json.dump(cam, f, ensure_ascii=False, indent=2)
        written += 1
    print(f"\n写入 {written} 个 JSON。")


if __name__ == "__main__":
    main()
