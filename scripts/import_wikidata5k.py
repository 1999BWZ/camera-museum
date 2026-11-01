#!/usr/bin/env python3
"""Wikidata 相机型号全量导入（目标约 5000 款）。

QID 来源：CirrusSearch haswbstatement:P31=<class>，class 取
Q20888659（相机型号）及其主要子类 Q20741022、Q140559271
（WDQS 不可用时的替代路线；三者实例数约 8429）。

复用 import_wikidata.py 的品牌映射/类型分类/年份解析，
复用 import_wikipedia.py 的实体批量获取/垃圾过滤/JSON 写入格式。

用法：python3 scripts/import_wikidata5k.py [--dry-run] [--limit N]
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from import_wikidata import (  # noqa: E402
    MFR_MAP, map_brand, detect_brand_from_label, strip_brand_prefix,
    map_type, parse_year, recover_year, EXCLUDE_CLASS, ACCESSORY_KW)
from import_wikipedia import (  # noqa: E402
    wb_entities, claim_values, mfr_labels, pick_label,
    JUNK_DESC_RE, JUNK_TITLE_RE, DATA_DIR)

UA = {"User-Agent": "CameraMuseumBot/0.1 (5000-camera import)"}
CLASS_QIDS = ["Q20888659", "Q20741022", "Q140559271"]
TARGET_TOTAL = 5000


def wd_search(srsearch, limit=500, offset=0, retries=3):
    params = {"action": "query", "list": "search", "srsearch": srsearch,
              "srlimit": limit, "sroffset": offset, "format": "json"}
    url = "https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            time.sleep(0.4)
            req = urllib.request.Request(url, headers=UA)
            d = json.load(urllib.request.urlopen(req, timeout=60))
            if "query" not in d:
                raise RuntimeError(f"API 返回异常: {str(d)[:120]}")
            return d
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(3 * (attempt + 1))


def enumerate_qids():
    qids = set()
    for cq in CLASS_QIDS:
        offset, total = 0, None
        while True:
            r = wd_search(f"haswbstatement:P31={cq}", offset=offset)
            hits = r["query"]["search"]
            if total is None:
                total = r["query"]["searchinfo"]["totalhits"]
                print(f"  {cq}: {total} hits")
            if not hits:
                break
            qids.update(h["title"] for h in hits)
            offset += len(hits)
            if offset >= total:
                break
    return qids


def existing_qids_and_ids():
    qids, ids = set(), set()
    for f in os.listdir(DATA_DIR):
        if not f.endswith(".json"):
            continue
        ids.add(f[:-5])
        try:
            d = json.load(open(os.path.join(DATA_DIR, f), encoding="utf-8"))
            if d.get("qid"):
                qids.add(d["qid"])
        except Exception:
            pass
    return qids, ids


def is_junk(label, desc):
    if re.search(r"lens|镜头", label or "", re.I):
        return True
    if JUNK_TITLE_RE.search(label or ""):
        return True
    if re.search(r"\blens\b|镜头", desc or "", re.I):
        return True
    if JUNK_DESC_RE.search(desc or ""):
        return True
    low = (label or "" + " " + desc or "").lower()
    if any(k.lower() in low for k in EXCLUDE_CLASS):
        return True
    if any(k.lower() in (desc or "").lower() for k in ACCESSORY_KW):
        return True
    return False


def main():
    limit, dry_run = None, False
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--dry-run":
            dry_run = True
        elif a == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])

    print("[1/5] 枚举 QID…")
    all_qids = enumerate_qids()
    print(f"去重后 {len(all_qids)} 个 QID")
    exist_qids, exist_ids = existing_qids_and_ids()
    print(f"已有 {len(exist_ids)} 款（含 {len(exist_qids)} 个 QID）")
    new_qids = [q for q in all_qids if q not in exist_qids]
    print(f"待处理 {len(new_qids)} 个新 QID")
    if limit is None:
        limit = max(0, TARGET_TOTAL - len(exist_ids))
    print(f"目标新增 {limit} 款")

    print("[2/5] 批量获取实体…")
    entities = wb_entities(set(new_qids))
    print(f"拿到 {len(entities)} 个实体")

    print("[3/5] 过滤候选…")
    cands, stats = [], Counter()
    for qid in new_qids:
        ent = entities.get(qid)
        if not ent or ent.get("missing"):
            stats["实体缺失"] += 1
            continue
        label = pick_label(ent) or qid
        desc = ent.get("descriptions", {}).get("en", {}).get("value", "")
        if is_junk(label, desc):
            stats["垃圾过滤"] += 1
            continue
        mfrs = [v for k, v in claim_values(ent, "P176") if k == "item"]
        mfr_qid = mfrs[0] if mfrs else None
        years = [parse_year(v) for k, v in claim_values(ent, "P571") if k == "time"]
        year = next((y for y in years if y), None)
        year_src = "P571"
        if not year:
            year, _ = recover_year(desc)
            year_src = "描述推断" if year else None
        if not year or not (1839 <= year <= 2026):
            stats["无有效年份"] += 1
            continue
        imgs = [v for k, v in claim_values(ent, "P18") if k == "string"]
        n_sites = len(ent.get("sitelinks", {}))
        cands.append({"qid": qid, "label": label, "desc": desc,
                      "mfr_qid": mfr_qid, "year": year, "year_src": year_src,
                      "img": imgs[0] if imgs else None, "sitelinks": n_sites})
    cands.sort(key=lambda c: -c["sitelinks"])
    print(f"候选 {len(cands)} 个；过滤掉：{dict(stats)}")

    print("[4/5] 解析制造商标签…")
    mfr_qids = {c["mfr_qid"] for c in cands if c["mfr_qid"]}
    mfr_name = mfr_labels(mfr_qids)
    print(f"制造商 {len(mfr_name)} 个")

    print("[5/5] 品牌映射与类型分类…")
    selected, skip = [], Counter()
    nobrand_examples = []
    for c in cands:
        if len(selected) >= limit:
            break
        mfr_label = mfr_name.get(c["mfr_qid"], "") if c["mfr_qid"] else ""
        # 品牌：先从型号标签识别（如“佳能 EOS 400D”），再用制造商映射
        # （map_brand 对未知制造商自动建品牌条目，仅显式排除的返回 None）
        detected, det_prefix = detect_brand_from_label(c["label"])
        if detected:
            brand_en, brand_cn, bslug, country = detected
            mfr_label = mfr_label or brand_en
        elif mfr_label:
            brand_info = map_brand(mfr_label)
            if brand_info is None:
                skip["制造商被排除"] += 1
                continue
            brand_en, brand_cn, bslug, country = brand_info
        else:
            skip["无品牌线索"] += 1
            if len(nobrand_examples) < 15:
                nobrand_examples.append(f"{c['label']} ({c['year']}) | {c['desc'][:50]}")
            continue
        model = strip_brand_prefix(c["label"], brand_en, brand_cn, mfr_label)
        if det_prefix:
            model = re.sub(r"^" + re.escape(det_prefix) + r"(?=\s|$|[A-Z0-9])",
                           "", c["label"]).strip() or model
        model_part = re.sub(r"-+", "-",
                            re.sub(r"[^a-z0-9]+", "-", model.lower())).strip("-")
        cid = f"{bslug}-{model_part}" if model_part else f"{bslug}-{c['qid'].lower()}"
        if cid in exist_ids:  # cid 碰撞：缀 QID
            cid = f"{cid}-{c['qid'].lower()}"
        if cid in exist_ids:
            skip["cid重复"] += 1
            continue
        exist_ids.add(cid)
        t, tcn = map_type(c["desc"], "", model, bslug, c["year"])
        c.update({"cid": cid, "brand_en": brand_en, "brand_cn": brand_cn,
                  "bslug": bslug, "country": country, "mfr_label": mfr_label,
                  "model": model, "type": t, "type_cn": tcn})
        selected.append(c)
    print(f"选中 {len(selected)} 款；跳过：{dict(skip)}")

    type_counter = Counter((s["type"], s["type_cn"]) for s in selected)
    print("\n[类型分布 Top15]")
    for (t, tcn), n in type_counter.most_common(15):
        print(f"  {n:4d}  {t} / {tcn}")
    if nobrand_examples:
        print(f"\n[无品牌线索示例，前 {len(nobrand_examples)}]")
        for e in nobrand_examples:
            print("  -", e)
    print("\n[抽样 Top10]")
    for s in selected[:10]:
        print(f"  {s['label']} | {s['brand_en']} | {s['year']} | {s['type_cn']} | 站点{s['sitelinks']}")

    if dry_run:
        print("\n[dry-run] 结束，未写文件。")
        return

    written = 0
    for s in selected:
        img_url = ("https://commons.wikimedia.org/wiki/Special:FilePath/" +
                   urllib.parse.quote(s["img"]) + "?width=800") if s["img"] else None
        specs = {"Wikidata": s["qid"], "多语言词条数": str(s["sitelinks"])}
        if s["year_src"] == "描述推断":
            specs["年份来源"] = "据英文描述推断"
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
            "specs": specs,
            "images": ([{"remote_url": img_url, "caption": s["model"],
                         "source": "Wikimedia Commons", "license": "见文件页授权",
                         "url": f"https://www.wikidata.org/wiki/{s['qid']}"}] if img_url else []),
            "sources": [{"title": f"Wikidata – {s['label']}",
                         "url": f"https://www.wikidata.org/wiki/{s['qid']}"}],
            "qid": s["qid"],
            "sitelinks": s["sitelinks"],
            "imported": "wikidata-5k-2026-11-01",
        }
        with open(os.path.join(DATA_DIR, s["cid"] + ".json"), "w", encoding="utf-8") as f:
            json.dump(cam, f, ensure_ascii=False, indent=2)
        written += 1
    print(f"\n写入 {written} 个 JSON。")


if __name__ == "__main__":
    main()
