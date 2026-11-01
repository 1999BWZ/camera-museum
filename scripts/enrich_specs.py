#!/usr/bin/env python3
"""从 camera-wiki 页面解析核心规格，生成中文介绍。

规格键映射为中文（快门/快门速度/感光度/镜头/胶片画幅/对焦/曝光测光/闪光灯/
重量/尺寸/电池/卡口/像素/传感器等），值保留英文原文（技术术语）。
description 改写为中文模板段落；英文原简介存入 specs["英文简介"]。

用法：python3 scripts/enrich_specs.py [--dry-run]
"""
import glob
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from import_camerawiki import clean_wiki, get_intro  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "data", "cameras")

# （正则, 中文键）按优先级排序
SPEC_PATTERNS = [
    (r"shutter\s*speeds?", "快门速度"),
    (r"\bshutter\b", "快门"),
    (r"lens\s*mount|mount", "卡口"),
    (r"\blens\b", "镜头"),
    (r"aperture", "光圈"),
    (r"film\s*speed|\biso\b|sensitivity", "感光度"),
    (r"sensor", "传感器"),
    (r"megapixel|resolution|\bpixel", "像素"),
    (r"\bfilm\b|format", "胶片/画幅"),
    (r"focus", "对焦"),
    (r"exposure|metering", "曝光测光"),
    (r"flash", "闪光灯"),
    (r"viewfinder", "取景器"),
    (r"weight", "重量"),
    (r"dimension|size", "尺寸"),
    (r"battery|batteries", "电池"),
    (r"self.?timer", "自拍"),
    (r"motor|winder|\bdrive\b", "卷片"),
    (r"\bzoom\b", "变焦"),
]


def parse_specs(wikitext):
    specs = {}
    for m in re.finditer(r"^\*\s*([^*\n]{1,60}?)\s*:\s*(.+?)\s*$", wikitext, re.M):
        key_raw, val = m.group(1).strip(), m.group(2).strip()
        key_low = key_raw.lower()
        zh_key = None
        for pat, zh in SPEC_PATTERNS:
            if re.search(pat, key_low):
                zh_key = zh
                break
        if not zh_key or zh_key in specs:
            continue
        val = clean_wiki(val)
        val = html.unescape(val)
        val = re.sub(r"\s+", " ", val).strip()
        if len(val) < 2 or len(val) > 200:
            continue
        # 跳过纯引用/链接行
        if re.match(r"^https?://", val) and len(val) > 80:
            continue
        specs[zh_key] = val
    # 正文高精度挖掘：尺寸 / 重量
    text = clean_wiki(wikitext[:6000])
    if "尺寸" not in specs:
        m = re.search(r"(\d{2,4}\s*[x×]\s*\d{2,4}\s*[x×]\s*\d{1,4}\s*mm)", text)
        if m:
            specs["尺寸"] = m.group(1)
    if "重量" not in specs:
        m = re.search(r"[Ww]eighs?\s+(\d{2,4})\s*g\b", text)
        if m:
            specs["重量"] = m.group(1) + "g"
    return specs


def build_description_zh(brand, year, type_cn, specs):
    parts = [f"{brand}于{year}年推出的{type_cn}。"]
    if "镜头" in specs:
        parts.append(f"配备{specs['镜头']}。")
    shutter = specs.get("快门速度") or specs.get("快门")
    if shutter:
        parts.append(f"快门速度{shutter}。")
    if "胶片/画幅" in specs:
        parts.append(f"使用{specs['胶片/画幅']}。")
    if "感光度" in specs:
        parts.append(f"感光度{specs['感光度']}。")
    if "像素" in specs:
        parts.append(f"有效像素{specs['像素']}。")
    if "对焦" in specs:
        parts.append(f"{specs['对焦']}。")
    return "".join(parts)


def main():
    dry = "--dry-run" in sys.argv
    pages = json.load(open("/tmp/cw_spec_pages.json", encoding="utf-8"))
    print(f"{len(pages)} pages loaded")
    # 标题 -> wikitext（重定向已解析，键为解析后标题）
    n_ok, n_noimg, total_specs = 0, 0, 0
    for f in glob.glob(os.path.join(DATA_DIR, "*.json")):
        d = json.load(open(f, encoding="utf-8"))
        if d.get("imported") != "camera-wiki-2026-11-01":
            continue
        # 找对应页面
        title = None
        for s in d.get("sources", []):
            u = s.get("url", "")
            if "camera-wiki.org/wiki/" in u:
                import urllib.parse
                title = urllib.parse.unquote(u.split("/wiki/")[1])
                break
        wt = pages.get(title)
        if not wt:
            n_noimg += 1
            continue
        specs = parse_specs(wt)
        total_specs += len(specs)
        # 英文简介（首句）
        intro = clean_wiki(get_intro(wt))
        en1 = re.split(r"(?<=[.!?])\s+", intro.strip(), maxsplit=1)[0][:400]
        brand = d.get("brand_cn") or d.get("brand")
        zh_desc = build_description_zh(brand, d["year"], d["type_cn"], specs)
        # 合并规格：新解析的优先，保留原有 Wikidata/Camera-wiki 键
        merged = dict(d.get("specs", {}))
        for k, v in specs.items():
            merged[k] = v
        if en1 and len(en1) > 30:
            merged["英文简介"] = en1
        d["specs"] = merged
        d["description"] = zh_desc
        if not dry:
            json.dump(d, open(f, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
        n_ok += 1
    print(f"enriched {n_ok}, no page {n_noimg}, avg specs {total_specs/max(n_ok,1):.1f}")
    print("[dry-run]" if dry else "written")


if __name__ == "__main__":
    main()
