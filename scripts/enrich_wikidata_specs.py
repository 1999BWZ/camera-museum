#!/usr/bin/env python3
"""为 Wikidata 导入的相机从英文维基百科 Infobox 补全核心规格。

解析 {{Infobox camera}} / {{Infobox digital camera}} 等模板的
lens/shutter/iso/film/focus 等字段，映射为中文规格键。
用法：python3 scripts/enrich_wikidata_specs.py [--dry-run]
"""
import glob
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "CameraMuseumBot/0.1 (wikidata spec enrichment)"}
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "data", "cameras")

# infobox 字段 -> 中文规格键
INFOBOX_MAP = [
    (r"lens", "镜头"),
    (r"shutter", "快门速度"),
    (r"\biso\b|film_speed|asa", "感光度"),
    (r"sensor", "传感器"),
    (r"resolution|megapixel|max_resolution", "像素"),
    (r"film", "胶片/画幅"),
    (r"focus", "对焦"),
    (r"exposure|metering", "曝光测光"),
    (r"flash", "闪光灯"),
    (r"weight", "重量"),
    (r"dimensions", "尺寸"),
    (r"battery", "电池"),
    (r"mount", "卡口"),
    (r"viewfinder", "取景器"),
    (r"storage|memory", "存储"),
    (r"display|lcd", "屏幕"),
    (r"video", "视频"),
]


def clean_val(v):
    prev = None
    while prev != v:
        prev = v
        v = re.sub(r"\{\{[^\{\}]*\}\}", "", v)
    v = re.sub(r"\{\{[^\}]*$", "", v)
    v = re.sub(r"\[\[[^\]|]*\|([^\]]+)\]\]", r"\1", v)
    v = re.sub(r"\[\[([^\]]+)\]\]", r"\1", v)
    v = re.sub(r"<[^>]+>", "", v)
    v = re.sub(r"'''?", "", v)
    v = html.unescape(v)
    v = re.sub(r"\s+", " ", v).strip()
    return v


def parse_infobox(wt):
    specs = {}
    # 找 Infobox camera 类模板（处理嵌套取首层）
    m = re.search(r"\{\{\s*[Ii]nfobox [Cc]amer[^\n]*\n(.*?)\n\}\}", wt, re.S)
    if not m:
        m = re.search(r"\{\{\s*[Ii]nfobox [Dd]igital[^\n]*\n(.*?)\n\}\}", wt, re.S)
    if not m:
        return specs
    body = m.group(1)
    for fm in re.finditer(r"^\|\s*([a-zA-Z_]+)\s*=\s*(.+?)\s*$", body, re.M):
        key, val = fm.group(1).lower(), fm.group(2)
        zh = None
        for pat, zhk in INFOBOX_MAP:
            if re.search(pat, key):
                zh = zhk
                break
        if not zh or zh in specs:
            continue
        val = clean_val(val)
        if 2 <= len(val) <= 200:
            specs[zh] = val
    return specs


def wp_api(title):
    url = ("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query", "prop": "revisions", "rvprop": "content",
        "rvslots": "main", "titles": title, "format": "json"}))
    time.sleep(0.5)
    d = json.load(urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=30))
    for p in d["query"]["pages"].values():
        if "revisions" in p:
            return p["revisions"][0]["slots"]["main"]["*"]
    return None


def main():
    dry = "--dry-run" in sys.argv
    # 收集需要补的（wikidata 导入且规格少于3个有效键）
    targets = []
    for f in glob.glob(os.path.join(DATA_DIR, "*.json")):
        d = json.load(open(f, encoding="utf-8"))
        imp = d.get("imported", "")
        if "wikidata" not in imp:
            continue
        real_specs = [k for k in d.get("specs", {}) if k not in ("Wikidata", "多语言词条数")]
        if len(real_specs) >= 3:
            continue
        qid = d.get("specs", {}).get("Wikidata")
        if qid:
            targets.append((f, d, qid))
    print(f"{len(targets)} targets")
    # QID -> enwiki title
    qids = [q for _, _, q in targets]
    titles = {}
    for i in range(0, len(qids), 50):
        batch = qids[i:i + 50]
        url = ("https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode({
            "action": "wbgetentities", "ids": "|".join(batch),
            "props": "sitelinks", "sitefilter": "enwiki", "format": "json"}))
        time.sleep(0.5)
        d = json.load(urllib.request.urlopen(
            urllib.request.Request(url, headers=UA), timeout=30))
        for q, ent in d.get("entities", {}).items():
            sl = ent.get("sitelinks", {}).get("enwiki", {}).get("title")
            if sl:
                titles[q] = sl
    print(f"{len(titles)} enwiki titles")
    n_ok, total = 0, 0
    for f, d, qid in targets:
        title = titles.get(qid)
        if not title:
            continue
        try:
            wt = wp_api(title)
        except Exception as e:
            print(f"  {qid} fetch fail: {e}")
            continue
        if not wt:
            continue
        specs = parse_infobox(wt)
        if specs:
            merged = dict(d.get("specs", {}))
            merged.update(specs)
            d["specs"] = merged
            # 丰富中文描述
            brand = d.get("brand_cn") or d.get("brand")
            parts = [f"{brand}于{d['year']}年推出的{d['type_cn']}。"]
            if "镜头" in specs:
                parts.append(f"配备{specs['镜头']}。")
            if "快门速度" in specs:
                parts.append(f"快门速度{specs['快门速度']}。")
            if "感光度" in specs:
                parts.append(f"感光度{specs['感光度']}。")
            if "像素" in specs:
                parts.append(f"有效像素{specs['像素']}。")
            d["description"] = "".join(parts)
            if not dry:
                json.dump(d, open(f, "w", encoding="utf-8"),
                          ensure_ascii=False, indent=2)
            n_ok += 1
            total += len(specs)
    print(f"enriched {n_ok}, avg {total/max(n_ok,1):.1f} specs")
    print("[dry-run]" if dry else "written")


if __name__ == "__main__":
    main()
