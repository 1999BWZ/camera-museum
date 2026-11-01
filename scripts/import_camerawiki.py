#!/usr/bin/env python3
"""camera-wiki.org 相机导入。

枚举 Category:A-Z/0-9（全站相机总目录）下所有页面，解析简介段落：
品牌（标题/内链）、年份（released/introduced 等）、类型（map_type）、
图片（仅 image_rights 为 CC 系列的 Flickr 图，wp=仅授权 camera-wiki 使用的不取）。

用法：python3 scripts/import_camerawiki.py [--dry-run] [--limit N]
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
    map_brand, map_brand_strict, detect_brand_from_label, strip_brand_prefix, map_type)
from import_wikipedia import DATA_DIR  # noqa: E402

CW_API = "https://camera-wiki.org/api.php"
UA = {"User-Agent": "CameraMuseumBot/0.1 (camera-museum import; polite 1.2s delay)"}
LETTERS = [chr(c) for c in range(ord("A"), ord("Z") + 1)] + [str(d) for d in range(10)]
DELAY = 1.2


def cw_api(params, retries=4):
    for attempt in range(retries):
        try:
            time.sleep(DELAY)
            url = CW_API + "?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers=UA)
            return json.load(urllib.request.urlopen(req, timeout=60))
        except Exception as e:
            wait = 5 * (attempt + 1)
            print(f"  API 错误 {e}，{wait}s 后重试", flush=True)
            time.sleep(wait)
    raise RuntimeError("camera-wiki API 多次失败")


def enum_titles():
    titles = []
    for L in LETTERS:
        cmcontinue = None
        while True:
            p = {"action": "query", "list": "categorymembers",
                 "cmtitle": f"Category:{L}", "cmtype": "page",
                 "cmlimit": 500, "format": "json"}
            if cmcontinue:
                p["cmcontinue"] = cmcontinue
            d = cw_api(p)
            titles.extend(x["title"] for x in d["query"]["categorymembers"])
            cmcontinue = d.get("continue", {}).get("cmcontinue")
            if not cmcontinue:
                break
        print(f"  Category:{L} 完成，累计 {len(titles)}", flush=True)
    # 去重保序
    seen, out = set(), []
    for t in titles:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def fetch_pages(titles):
    pages = {}
    for i in range(0, len(titles), 50):
        batch = titles[i:i + 50]
        d = cw_api({"action": "query", "prop": "revisions", "rvprop": "content",
                    "rvslots": "main", "titles": "|".join(batch),
                    "redirects": 1, "format": "json"})
        for p in d["query"]["pages"].values():
            if "revisions" not in p:
                continue
            pages[p["title"]] = p["revisions"][0]["slots"]["main"]["*"]
        print(f"  页面 {min(i+50, len(titles))}/{len(titles)}", flush=True)
    return pages


def clean_wiki(t):
    # 反复剥离模板（处理嵌套），再清掉截断残留的未闭合模板
    prev = None
    while prev != t:
        prev = t
        t = re.sub(r"\{\{[^\{\}]*\}\}", "", t)
    t = re.sub(r"\{\{[^\}]*$", "", t)
    t = re.sub(r"\[\[[^\]|]*\|([^\]]+)\]\]", r"\1", t)
    t = re.sub(r"\[\[([^\]]+)\]\]", r"\1", t)
    t = t.replace("'''", "").replace("''", "")
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


def get_intro(wikitext):
    m = re.search(r"==\s*Introduction\s*==\n", wikitext)
    if m:
        rest = wikitext[m.end():]
        rest = re.split(r"\n==[^=]", rest, maxsplit=1)[0]
        return rest.strip()
    # 无 Introduction 标题：取第一个二级标题之前的内容
    rest = re.split(r"\n==[^=]", wikitext, maxsplit=1)[0]
    return rest.strip()


YEAR_VERB_RES = [
    re.compile(r"(?:released|introduced|launched|announced|manufactured|produced|marketed|made|available|sold|starting|beginning)\s+(?:in\s+)?(\d{4})", re.I),
    re.compile(r"(?:released|introduced|launched|announced|manufactured)\b[^.\n]{0,80}?\bin\s+(?:[A-Za-z]+\s+)?(\d{4})", re.I),
    re.compile(r"\bis\s+a\s+(\d{4})\s+(?:digital\s+)?camera\b", re.I),
    re.compile(r"(?:date\s+as|dated)\s+c?\.?\s*(\d{4})", re.I),
    re.compile(r"\bc\.?\s*(18[3-9]\d|19\d{2}|20[0-2]\d)\b", re.I),
    re.compile(r"\b(19\d{2}|20[0-2]\d)\s*[–—-]\s*(?:19\d{2}|20[0-2]\d)?\b"),
    re.compile(r"\bfrom\s+(\d{4})\b", re.I),
]


def parse_year(intro):
    for pat in YEAR_VERB_RES:
        m = pat.search(intro)
        if m:
            y = int(m.group(1))
            if 1839 <= y <= 2026:
                return y
    return None


def parse_image(wikitext):
    m = re.search(r"\{\{Flickr_image(.*?)\}\}", wikitext, re.S | re.I)
    if not m:
        return None
    body = m.group(1)
    rights = re.search(r"\|image_rights\s*=\s*([^\n|}]+)", body, re.I)
    rights = rights.group(1).strip().lower() if rights else ""
    if not rights.startswith("cc"):
        return None  # wp 等非 CC 授权不取
    img = re.search(r"\|image\s*=\s*([^\n|}]+)", body, re.I)
    by = re.search(r"\|image_by\s*=\s*([^\n|}]+)", body, re.I)
    src = re.search(r"\|image_source\s*=\s*([^\n|}]+)", body, re.I)
    if not img:
        return None
    return {"url": img.group(1).strip(),
            "by": by.group(1).strip() if by else "未知",
            "rights": rights,
            "source": src.group(1).strip() if src else ""}


def parse_brand(title, intro_clean):
    """标题优先（未知品牌自动建条目）；标题无品牌时从简介内链严格匹配已知制造商。"""
    detected, prefix = detect_brand_from_label(title)
    if detected:
        return detected, prefix, ""
    m = re.search(r"(?:from|by|made by|produced by)\s+([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*)?)",
                  intro_clean)
    if m:
        info = map_brand_strict(m.group(1))
        if info:
            return info, "", ""
        return None, "", m.group(1)  # 候选制造商（诊断用）
    return None, "", ""


JUNK_TITLE_WORDS = re.compile(r"pseudo|unidentified|unknown|generic|disguised", re.I)
CAMERA_KW = re.compile(r"camera|rangefinder|single-lens reflex|twin-lens|\bslr\b|\btlr\b", re.I)

# ---- 基于 [[Category:]] 的结构化解析 ----
YEAR_CAT_RE = re.compile(r"^(18[3-9]\d|19\d{2}|20[0-2]\d)$")
DECADE_CAT_RE = re.compile(r"^(18[3-9]\d|19\d{2}|20[0-2]\d)s$")

COUNTRY_CAT = {
    "japanese": "日本", "japan": "日本",
    "german": "德国", "germany": "德国",
    "russian": "俄罗斯", "russia": "俄罗斯", "soviet": "苏联", "ussr": "苏联",
    "american": "美国", "usa": "美国", "united states": "美国",
    "british": "英国", "uk": "英国", "england": "英国",
    "french": "法国", "france": "法国",
    "italian": "意大利", "italy": "意大利",
    "chinese": "中国", "china": "中国",
    "czech": "捷克", "czechoslovak": "捷克斯洛伐克",
    "swedish": "瑞典", "sweden": "瑞典",
    "dutch": "荷兰", "netherlands": "荷兰",
    "austrian": "奥地利", "austria": "奥地利",
    "swiss": "瑞士", "switzerland": "瑞士",
    "polish": "波兰", "poland": "波兰",
    "hungarian": "匈牙利", "hungary": "匈牙利",
    "spanish": "西班牙", "spain": "西班牙",
    "canadian": "加拿大", "canada": "加拿大",
    "australian": "澳大利亚", "australia": "澳大利亚",
    "indian": "印度", "india": "印度",
    "korean": "韩国", "korea": "韩国",
    "taiwan": "台湾",
    "romanian": "罗马尼亚", "romania": "罗马尼亚",
    "yugoslav": "南斯拉夫",
}

# 分类关键词 -> (type, type_cn)，按顺序优先匹配
TYPE_CAT_RULES = [
    ("twin-lens", "tlr", "双镜头反光相机"),
    ("single-lens reflex", "slr", "胶片单反相机"),
    ("rangefinder", "rangefinder", "旁轴相机"),
    ("instant", "instant", "一次成像相机"),
    ("medium format", "medium_format", "中画幅相机"),
    ("large format", "large_format", "大画幅相机"),
    ("panoram", "compact", "全景相机"),
    ("stereo", "compact", "立体相机"),
    ("subminiature", "compact", "微型相机"),
    ("disposable", "compact", "一次性相机"),
    ("toy", "compact", "玩具相机"),
    ("box", "compact", "盒式相机"),
    ("folding", "compact", "折叠相机"),
    ("viewfinder", "compact", "取景器相机"),
    ("digital", "compact", "数码相机"),
]

# 非制造商的分类词（小写匹配，含则跳过该分类作为品牌候选）
NON_MAKER_CAT_RE = re.compile(
    r"viewfinder|rangefinder|reflex|tlr|slr|folding|box|compact|digital|instant|"
    r"panoram|stereo|subminiature|toy|disposable|medium format|large format|"
    r"35mm|120|126|127|110|6x6|6x7|6x9|4x5|sheet film|rollfilm|film|"
    r"bakelite|plastic|metal|wood|leather|brass|maker|processor|distributor|"
    r"cine|mount|"
    r"japan|german|france|italy|china|czech|swed|dutch|austri|swiss|poland|"
    r"hungar|spain|canad|australia|india|korea|taiwan|romania|russia|soviet|"
    r"british|american|uk\b|usa|england|yugoslav", re.I)

# 标题首词不能当品牌的通用词
GENERIC_FIRST_WORDS = {"the", "original", "a", "an", "japanese", "german",
                       "american", "french", "british", "soviet", "russian",
                       "chinese", "new", "old", "special", "stereo", "automatic"}

# 分类级垃圾：命中则整页跳过（电影机等非静态相机）
CAT_JUNK_RE = re.compile(r"cine", re.I)


def parse_categories(wikitext):
    return [c.strip() for c in re.findall(r"\[\[Category:([^\]|]+)", wikitext)]


def split_categories(cats):
    """返回 (years, countries, type_hits, maker_cands)。years 含年代折算。"""
    years, countries, type_hits, makers = [], [], [], []
    for c in cats:
        cl = c.strip()
        m = YEAR_CAT_RE.match(cl)
        if m:
            years.append(int(m.group(1)))
            continue
        m = DECADE_CAT_RE.match(cl)
        if m:
            years.append(int(m.group(1)))  # 年代取首年
            continue
        low = cl.lower()
        if low in COUNTRY_CAT:
            countries.append(COUNTRY_CAT[low])
            continue
        hit = None
        for kw, t, tcn in TYPE_CAT_RULES:
            if kw in low:
                hit = (t, tcn)
                break
        if hit:
            type_hits.append(hit)
            continue
        if len(cl) <= 2 or re.search(r"\d", cl):
            continue  # 字母索引 / 含数字的非品牌分类
        if NON_MAKER_CAT_RE.search(cl):
            continue
        makers.append(cl)
    return years, countries, type_hits, makers


def maker_from_categories(makers):
    """制造商分类 -> 品牌信息。优先 MFR_MAP 已知，其次自动建条目。"""
    for m in makers:
        info = map_brand_strict(m)
        if info:
            return info
    for m in makers:
        info = map_brand(m)  # 自动建条目（未知制造商）
        if info:
            return info
    return None


def existing_ids():
    return {f[:-5] for f in os.listdir(DATA_DIR) if f.endswith(".json")}


def main():
    limit, dry_run = None, False
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--dry-run":
            dry_run = True
        elif a == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])

    print("[1/4] 枚举标题…")
    titles = enum_titles()
    print(f"共 {len(titles)} 个标题")
    json.dump(titles, open("/tmp/cw_titles.json", "w"), ensure_ascii=False)

    print("[2/4] 抓取页面…")
    cache_path = "/tmp/cw_pages.json"
    if os.path.exists(cache_path) and "--no-cache" not in args:
        pages = json.load(open(cache_path, encoding="utf-8"))
        print(f"使用缓存 {len(pages)} 页")
    else:
        pages = fetch_pages(titles)
        print(f"拿到 {len(pages)} 页")
        json.dump(pages, open(cache_path, "w"), ensure_ascii=False)

    print("[3/4] 解析…")
    exist_ids = existing_ids()
    if limit is None:
        limit = 100000
    cands, stats = [], Counter()
    for title, wt in pages.items():
        if wt.lstrip().upper().startswith("#REDIRECT"):
            stats["重定向"] += 1
            continue
        if JUNK_TITLE_WORDS.search(title):
            stats["标题泛化"] += 1
            continue
        cats = parse_categories(wt)
        if any(CAT_JUNK_RE.search(c) for c in cats):
            stats["分类垃圾"] += 1
            continue
        years, countries, type_hits, maker_cats = split_categories(cats)
        intro_raw = get_intro(wt)
        # 先清洗全文再使用，避免模板被拦腰截断残留 {{
        intro = clean_wiki(intro_raw)
        # 软重定向/合并提示页
        if re.match(r"(?i)this article is\s", intro):
            stats["软重定向"] += 1
            continue
        # 非相机页（简介无相机关键词且分类无类型命中）
        if not type_hits and not CAMERA_KW.search(intro):
            stats["非相机页"] += 1
            continue
        # 品牌：标题已知品牌 > 制造商分类 > 标题首词（无分类时） > 简介内链（严格）
        brand_info, prefix = detect_brand_from_label(title)
        if brand_info:
            brand_en, brand_cn, bslug, country = brand_info
        else:
            brand_info = maker_from_categories(maker_cats)
            if not brand_info:
                title_wo_paren = re.sub(r"\s*\(.*?\)\s*$", "", title).strip()
                words = title_wo_paren.split()
                if len(words) >= 2 and words[0].lower() not in GENERIC_FIRST_WORDS \
                        and len(words[0]) > 2 and not re.search(r"\d", words[0]):
                    brand_info = map_brand(words[0])  # 标题首词自动建品牌
            if brand_info:
                brand_en, brand_cn, bslug, country = brand_info
                prefix = ""
            else:
                bi, _, _ = parse_brand(title, intro)
                if not bi:
                    stats["无品牌"] += 1
                    continue
                brand_en, brand_cn, bslug, country = bi
                prefix = ""
        if countries and country in ("", "待补充"):
            country = countries[0]
        # 年份：分类 > 简介
        year = years[0] if years else parse_year(intro_raw)
        if not year:
            stats["无年份"] += 1
            continue
        model_raw = re.sub(r"\s*\(.*?\)\s*$", "", title).strip()
        model = strip_brand_prefix(model_raw, brand_en, brand_cn)
        if not model or len(model) > 80:
            stats["型号异常"] += 1
            continue
        # 若标题以制造商分类名开头，剥离
        for mc in maker_cats:
            nm = re.sub(r"^" + re.escape(mc) + r"(?=\s|$|[A-Z0-9])", "", model,
                        flags=re.I).strip()
            if nm and nm != model:
                model = nm
                break
        model_part = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", model.lower())).strip("-")
        cid = f"{bslug}-{model_part}" if model_part else None
        if not cid or cid in exist_ids:
            stats["已存在/空cid"] += 1
            continue
        # 类型：分类 > 简介 map_type
        if type_hits:
            t, tcn = type_hits[0]
        else:
            t, tcn = map_type(intro, "", model, bslug, year)
        img = parse_image(wt)
        desc1 = re.split(r"(?<=[.!?])\s+", intro.strip(), maxsplit=1)[0]
        if len(desc1) < 20:  # 简介缺失时用分类信息兜底
            desc1 = f"{brand_cn or brand_en}于{year}年发布的{tcn}。"
        cands.append({"title": title, "cid": cid, "brand_en": brand_en,
                      "brand_cn": brand_cn, "bslug": bslug, "country": country,
                      "model": model, "year": year, "type": t, "type_cn": tcn,
                      "desc1": desc1[:300], "img": img})
        exist_ids.add(cid)
        if len(cands) >= limit:
            break
    print(f"候选 {len(cands)}；过滤：{dict(stats)}")
    tc = Counter((c["type"], c["type_cn"]) for c in cands)
    print("[类型分布 Top12]")
    for (t, tcn), n in tc.most_common(12):
        print(f"  {n:4d}  {t} / {tcn}")
    bc = Counter(c["bslug"] for c in cands)
    print(f"[品牌数 {len(bc)}，Top10] " + ", ".join(f"{b}:{n}" for b, n in bc.most_common(10)))
    print("[抽样]")
    for c in cands[:8]:
        print(f"  {c['title']} | {c['brand_en']} | {c['year']} | {c['type_cn']} | 图:{'Y' if c['img'] else 'N'}")

    if dry_run:
        print("\n[dry-run] 结束，未写文件。")
        return

    print("[4/4] 写 JSON…")
    written = 0
    for c in cands:
        img = c["img"]
        images = ([{"remote_url": img["url"], "caption": c["model"],
                    "source": f"Flickr（{img['by']}）",
                    "license": img["rights"].upper() + "，见原页面",
                    "url": img["source"] or f"https://camera-wiki.org/wiki/{urllib.parse.quote(c['title'])}"}]
                  if img else [])
        cam = {
            "id": c["cid"],
            "brand": c["brand_en"],
            "brand_cn": c["brand_cn"],
            "brand_slug": c["bslug"],
            "country": c["country"],
            "manufacturer": c["brand_en"],
            "model": c["model"],
            "year": c["year"],
            "type": c["type"],
            "type_cn": c["type_cn"],
            "format": "待补充",
            "mount": "待补充",
            "milestone": True,
            "tagline": f"{c['brand_cn'] or c['brand_en']}{c['type_cn']}，{c['year']} 年发布",
            "description": c["desc1"] or f"{c['brand_cn'] or c['brand_en']}于{c['year']}年发布的{c['type_cn']}。",
            "significance": "待补充（需人工撰写）",
            "specs": {"Camera-wiki": c["title"]},
            "images": images,
            "sources": [{"title": f"Camera-wiki – {c['title']}",
                         "url": f"https://camera-wiki.org/wiki/{urllib.parse.quote(c['title'])}"}],
            "imported": "camera-wiki-2026-11-01",
        }
        with open(os.path.join(DATA_DIR, c["cid"] + ".json"), "w", encoding="utf-8") as f:
            json.dump(cam, f, ensure_ascii=False, indent=2)
        written += 1
    print(f"写入 {written} 个 JSON。")


if __name__ == "__main__":
    main()
