#!/usr/bin/env python3
"""从 Wikidata 导入里程碑相机数据（定稿版）。

数据锚点：Q20888659 "camera model"（P31/P279* 闭包，约 8000+ 机型）。
选取标准：按多语言 Wikipedia 词条数（sitelinks）排序取前 N。
类型判定优先级：英文描述关键词 > 类名关键词 > 品牌型号命名规律 > 兜底。

    python3 scripts/import_wikidata.py            # 导入 200 款
    python3 scripts/import_wikidata.py --limit 50 # 测试
    python3 scripts/import_wikidata.py --dry-run  # 只看分布，不写文件

已存在的 data/cameras/<id>.json 会被跳过（手工精修版优先），可重复运行增量更新。
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
UA = "CameraMuseumBot/0.1 (personal research project; data import for camera-museum)"

QUERY = """
SELECT ?item ?itemLabel ?classLabel ?manufacturerLabel ?inception ?image ?sitelinks WHERE {
  ?item wdt:P31/wdt:P279* wd:Q20888659.
  ?item wdt:P31 ?class.
  ?item wikibase:sitelinks ?sitelinks.
  FILTER(?sitelinks >= 4)
  OPTIONAL { ?item wdt:P176 ?manufacturer. }
  OPTIONAL { ?item wdt:P571 ?inception. }
  OPTIONAL { ?item wdt:P18 ?image. }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "zh-cn,zh,en". }
}
ORDER BY DESC(?sitelinks)
LIMIT 600
"""

EXCLUDE_CLASS = ["webcam", "网络摄像头", "智能手机", "smartphone", "电玩",
                 "video game", "列表条目", "企业", "品牌", "enterprise", "廢棄", "废弃"]

# 配件/外设描述关键词（EyeToy 这类 PS 配件，不是相机）
ACCESSORY_KW = ["camera accessory", "peripheral", "外设", "accessory for"]

# 从英文描述里抢救年份：P571 缺失的历史机型常用（多为胶片时代里程碑）
YEAR_RES = [
    re.compile(r"(?:introduced|released|launched|announced|manufactured|produced|marketed|made)\s+(?:in|from|between)\s+(\d{4})", re.I),
    re.compile(r"(?:from|between)\s+(\d{4})\s*(?:and|to|[-–])", re.I),
    re.compile(r"\b(19\d{2}|20[0-2]\d)\s+model\b", re.I),
]

# 描述/类名关键词 -> (type, type_cn)，顺序优先。
# 分两组：具体类型先行；film/analog/35mm 这类泛关键词后行（以免盖掉品牌命名规律，如 FM10）
DESC_SPECIFIC = [
    ("mirrorless", ("mirrorless", "无反相机")),
    ("digital single-lens reflex", ("dslr", "数码单反相机")),
    ("digital slr", ("dslr", "数码单反相机")),
    ("slr", ("dslr", "数码单反相机")),  # 描述里出现 SLR 且无 film 字样时多为数码
    ("slt", ("dslr", "数码单反相机")),  # 索尼半透镜，如 SLR-A55 系列按单反归类
    ("twin-lens reflex", ("tlr", "双镜头反光相机")),
    ("rangefinder", ("rangefinder", "旁轴相机")),
    ("instant camera", ("instant", "一次成像相机")),
    ("medium format", ("medium_format", "中画幅相机")),
    ("large format", ("large_format", "大画幅相机")),
    ("point-and-shoot", ("compact", "便携相机")),
    ("compact camera", ("compact", "便携相机")),
    ("compact digital", ("compact", "便携数码相机")),
    ("bridge camera", ("compact", "便携相机（桥机）")),
    ("box camera", ("compact", "盒式相机")),
    ("folding camera", ("rangefinder", "旁轴相机（折叠）")),
    ("view camera", ("large_format", "大画幅相机")),
    ("toy camera", ("compact", "玩具相机")),
    ("stereo camera", ("compact", "立体相机")),
    ("subminiature", ("compact", "微型相机")),
]
DESC_GENERIC = [
    ("film", ("slr", "胶片相机")),
    ("analog", ("slr", "胶片相机")),
    ("35mm", ("slr", "胶片单反相机")),
]

# 为兼容旧引用保留别名
DESC_RULES = DESC_SPECIFIC + DESC_GENERIC

# 品牌型号命名规律（仅用于描述过于笼统时），(brand_slug, [(regex, type, type_cn)])
NAME_RULES = {
    "canon": [(r"^EOS R", "mirrorless", "无反相机"), (r"^EOS M", "mirrorless", "无反相机"),
              (r"^EOS \d+D|^EOS-", "dslr", "数码单反相机"), (r"^EOS", "dslr", "数码单反相机"),
              (r"PowerShot|IXUS|ELPH|Prima", "compact", "便携相机"),
              (r"^(AE|AV|AL|T|A|F)-?1|FTb|FT\b|EF\b|New F-1", "slr", "胶片单反相机")],
    "nikon": [(r"^Z ?\d|^Z ?f", "mirrorless", "无反相机"), (r"^D\d", "dslr", "数码单反相机"),
              (r"^1\b", "mirrorless", "无反相机"),  # Nikon 1 系列
              (r"^S\d?\b", "rangefinder", "旁轴相机"),  # S / S2 / S3 / SP 等旁轴
              (r"^F-?\d", "slr", "胶片单反相机"),  # F-301 / F90 / F100 等胶片单反
              (r"Coolpix", "compact", "便携相机"),
              (r"^F[2-6]?$|^FM|^FE|^FA|^FG|^Nikkormat|^Nikonos", "slr", "胶片单反相机")],
    "sony": [(r"^(α|A)(7|9|1)\b|α ?[719]C?", "mirrorless", "无反相机"),
             (r"^NEX", "mirrorless", "无反相机"),
             (r"SLT", "dslr", "数码单反相机"),
             (r"^A\d{2,3}\b", "dslr", "数码单反相机"),
             (r"^RX|Cyber-shot|DSC-", "compact", "便携相机")],
    "fujifilm": [(r"^GFX", "medium_format", "中画幅相机"),
                 (r"^X-|X100|X-Pro|X-E", "mirrorless", "无反相机"),
                 (r"FinePix|^S\d", "compact", "便携相机")],
    "olympus": [(r"^OM-D|^PEN|^E-M|^E-P", "mirrorless", "无反相机"),
                (r"^E-\d", "dslr", "数码单反相机"),  # 4/3 系统单反
                (r"^OM-[12]\b|^OM-3|^OM-4", "slr", "胶片单反相机"),
                (r"^XZ\b", "compact", "便携相机"),
                (r"μ|mju|Stylus|XA\b", "compact", "便携相机")],
    "panasonic": [(r"^(GH|G9?|GX|GM|S1|S5)\b|Lumix G", "mirrorless", "无反相机"),
                  (r"Lumix|DMC-|TZ|LX|FZ", "compact", "便携相机")],
    "pentax": [(r"^645", "medium_format", "中画幅相机"),
               (r"^K-?\d|^K-|ist D", "dslr", "数码单反相机"),
               (r"^(SF|MZ|ZX|PZ)", "slr", "胶片单反相机"),  # 80-90 年代胶片单反
               (r"Spotmatic|K1000|MX\b|LX\b|ME\b|MG\b|MV\b|P30|Super A", "slr", "胶片单反相机"),
               (r"Optio|Espio", "compact", "便携相机")],
    "kmz": [(r"^Zorki", "rangefinder", "旁轴相机"),
            (r"^Zenit", "slr", "胶片单反相机")],
    "leica": [(r"^M(\d|\b|-|$)", "rangefinder", "旁轴相机"),
              (r"^I{2,3}\b", "rangefinder", "旁轴相机"),  # II / III
              (r"^I(f)?\b", "scale_focus", "估焦 135 相机"),  # I / If
              (r"^Q\d?", "compact", "便携相机"),
              (r"^X(\d|\b)", "compact", "便携相机"),  # X1 / X (Typ 113)
              (r"^SL\b", "mirrorless", "无反相机"),
              (r"^S\b", "medium_format", "中画幅相机"),
              (r"^R\d", "slr", "胶片单反相机"), (r"^D-Lux|^C-Lux|^V-Lux", "compact", "便携相机")],
    "hoya": [(r"^645", "medium_format", "中画幅相机"),
             (r"^K", "dslr", "数码单反相机")],  # 豪雅时代的宾得数码单反
    "ricoh": [(r"^GR", "compact", "便携相机"),
              (r"^645", "medium_format", "中画幅相机"),
              (r"^K", "dslr", "数码单反相机")],  # 理光时代的宾得数码单反
    "hasselblad": [(r".*", "medium_format", "中画幅相机")],
    "mamiya": [(r".*", "medium_format", "中画幅相机")],
    "bronica": [(r".*", "medium_format", "中画幅相机")],
    "polaroid": [(r".*", "instant", "一次成像相机")],
    "kodak": [(r"^DCS", "dslr", "数码单反相机"), (r".*", "compact", "便携相机")],
    "contax": [(r"^G[12]\b", "rangefinder", "旁轴相机"), (r"^T", "compact", "便携相机"),
               (r"RTS|139|137|167|159|AX|RX\b", "slr", "胶片单反相机")],
    "minolta": [(r"^(X-|SRT|XD|Maxxum|Dynax)", "slr", "胶片单反相机"),
                (r"TC-1|Riva|Freedom", "compact", "便携相机")],
    "yashica": [(r"Mat\b", "tlr", "双镜头反光相机"), (r"^T[245]\b", "compact", "便携相机"),
                (r"Electro", "rangefinder", "旁轴相机"), (r"^FX", "slr", "胶片单反相机")],
    "rollei": [(r"Rolleiflex|Rolleicord", "tlr", "双镜头反光相机"),
               (r"^35\b", "compact", "便携相机")],
    "ricoh": [(r"^GR", "compact", "便携相机")],
    "konica": [(r"Hexar", "compact", "便携相机"), (r".*", "slr", "胶片单反相机")],
    "samsung": [(r"^NX", "mirrorless", "无反相机"), (r".*", "compact", "便携相机")],
    "sigma": [(r"sd Quattro", "mirrorless", "无反相机"), (r"^SD", "dslr", "数码单反相机"),
              (r"^DP", "compact", "便携相机")],
    "gopro": [(r".*", "compact", "运动相机")],
    "dji": [(r".*", "compact", "数码相机")],
    "casio": [(r".*", "compact", "便携相机")],
}

# 制造商标签 -> (brand_en, brand_cn, slug, country)
MFR_MAP = {
    "佳能": ("Canon", "佳能", "canon", "日本"), "Canon Inc.": ("Canon", "佳能", "canon", "日本"),
    "Canon": ("Canon", "佳能", "canon", "日本"),
    "尼康": ("Nikon", "尼康", "nikon", "日本"), "Nikon Corporation": ("Nikon", "尼康", "nikon", "日本"),
    "Nikon": ("Nikon", "尼康", "nikon", "日本"),
    "索尼": ("Sony", "索尼", "sony", "日本"), "Sony": ("Sony", "索尼", "sony", "日本"),
    "奥林巴斯": ("Olympus", "奥林巴斯", "olympus", "日本"), "奧林巴斯": ("Olympus", "奥林巴斯", "olympus", "日本"),
    "Olympus": ("Olympus", "奥林巴斯", "olympus", "日本"),
    "宾得": ("Pentax", "宾得", "pentax", "日本"), "宾得士": ("Pentax", "宾得", "pentax", "日本"),
    "賓得士": ("Pentax", "宾得", "pentax", "日本"), "Pentax": ("Pentax", "宾得", "pentax", "日本"),
    "松下": ("Panasonic", "松下", "panasonic", "日本"), "松下电器": ("Panasonic", "松下", "panasonic", "日本"),
    "Panasonic": ("Panasonic", "松下", "panasonic", "日本"),
    "徕卡": ("Leica", "徕卡", "leica", "德国"), "徕卡相机": ("Leica", "徕卡", "leica", "德国"),
    "Leica": ("Leica", "徕卡", "leica", "德国"), "Leica Camera": ("Leica", "徕卡", "leica", "德国"),
    "富士": ("Fujifilm", "富士", "fujifilm", "日本"), "富士胶片": ("Fujifilm", "富士", "fujifilm", "日本"),
    "Fujifilm": ("Fujifilm", "富士", "fujifilm", "日本"),
    "三星": ("Samsung", "三星", "samsung", "韩国"), "三星集团": ("Samsung", "三星", "samsung", "韩国"),
    "三星集團": ("Samsung", "三星", "samsung", "韩国"), "Samsung": ("Samsung", "三星", "samsung", "韩国"),
    "柯达": ("Kodak", "柯达", "kodak", "美国"), "伊士曼柯达公司": ("Kodak", "柯达", "kodak", "美国"),
    "Kodak": ("Kodak", "柯达", "kodak", "美国"),
    "适马": ("Sigma", "适马", "sigma", "日本"), "適馬": ("Sigma", "适马", "sigma", "日本"),
    "Sigma": ("Sigma", "适马", "sigma", "日本"),
    "理光": ("Ricoh", "理光", "ricoh", "日本"), "Ricoh": ("Ricoh", "理光", "ricoh", "日本"),
    "美能达": ("Minolta", "美能达", "minolta", "日本"), "美能達": ("Minolta", "美能达", "minolta", "日本"),
    "Minolta": ("Minolta", "美能达", "minolta", "日本"),
    "哈苏": ("Hasselblad", "哈苏", "hasselblad", "瑞典"), "Hasselblad": ("Hasselblad", "哈苏", "hasselblad", "瑞典"),
    "禄来": ("Rollei", "禄来", "rollei", "德国"), "Rollei": ("Rollei", "禄来", "rollei", "德国"),
    "玛米亚": ("Mamiya", "玛米亚", "mamiya", "日本"), "Mamiya": ("Mamiya", "玛米亚", "mamiya", "日本"),
    "康泰时": ("Contax", "康泰时", "contax", "德国"), "Contax": ("Contax", "康泰时", "contax", "德国"),
    "宝丽来": ("Polaroid", "宝丽来", "polaroid", "美国"), "Polaroid": ("Polaroid", "宝丽来", "polaroid", "美国"),
    "雅西卡": ("Yashica", "雅西卡", "yashica", "日本"), "Yashica": ("Yashica", "雅西卡", "yashica", "日本"),
    "柯尼卡": ("Konica", "柯尼卡", "konica", "日本"), "Konica": ("Konica", "柯尼卡", "konica", "日本"),
    "柯尼卡美能達": ("Konica Minolta", "柯尼卡美能达", "konica-minolta", "日本"),
    "Konica Minolta": ("Konica Minolta", "柯尼卡美能达", "konica-minolta", "日本"),
    "理光": ("Ricoh", "理光", "ricoh", "日本"), "Ricoh": ("Ricoh", "理光", "ricoh", "日本"),
    "Ricoh Imaging": ("Ricoh", "理光", "ricoh", "日本"),
    "ABC": None,  # 占位排除（map_brand 遇到 None 时丢弃）
    "卡西欧": ("Casio", "卡西欧", "casio", "日本"), "Casio": ("Casio", "卡西欧", "casio", "日本"),
    "大疆": ("DJI", "大疆", "dji", "中国"), "DJI": ("DJI", "大疆", "dji", "中国"),
    "蔡司": ("Zeiss", "蔡司", "zeiss", "德国"), "Zeiss": ("Zeiss", "蔡司", "zeiss", "德国"),
    "福伦达": ("Voigtländer", "福伦达", "voigtlander", "德国"), "Voigtlander": ("Voigtländer", "福伦达", "voigtlander", "德国"),
    "勃朗尼卡": ("Bronica", "勃朗尼卡", "bronica", "日本"), "Bronica": ("Bronica", "勃朗尼卡", "bronica", "日本"),
    "爱克发": ("Agfa", "爱克发", "agfa", "德国"), "Agfa": ("Agfa", "爱克发", "agfa", "德国"),
    "潘泰康": ("Praktica", "潘泰康", "praktica", "德国"), "Praktica": ("Praktica", "潘泰康", "praktica", "德国"),
    "苹果": ("Apple", "苹果", "apple", "美国"), "Apple": ("Apple", "苹果", "apple", "美国"),
    "苹果公司": ("Apple", "苹果", "apple", "美国"),
    "豪威": ("OmniVision", "豪威", "omnivision", "美国"), "豪威科技": ("OmniVision", "豪威", "omnivision", "美国"),
    "三星电子": ("Samsung", "三星", "samsung", "韩国"), "三星集团公司": ("Samsung", "三星", "samsung", "韩国"),
    "松下電器": ("Panasonic", "松下", "panasonic", "日本"),
    "禄莱": ("Rollei", "禄来", "rollei", "德国"),
    "Blackmagic Design": ("Blackmagic", "Blackmagic", "blackmagic", "澳大利亚"),
    "Lomography": ("Lomography", "Lomography", "lomography", "奥地利"),
    "LOMO公司": ("Lomography", "LOMO", "lomography", "奥地利"),
    "华为": ("Huawei", "华为", "huawei", "中国"), "Huawei": ("Huawei", "华为", "huawei", "中国"),
    "格拉菲": ("Graflex", "格拉菲", "graflex", "美国"), "Graflex": ("Graflex", "格拉菲", "graflex", "美国"),
    "诺基亚": ("Nokia", "诺基亚", "nokia", "芬兰"), "Nokia": ("Nokia", "诺基亚", "nokia", "芬兰"),
    "豪雅": ("Hoya", "豪雅", "hoya", "日本"), "Hoya": ("Hoya", "豪雅", "hoya", "日本"),
    "阿莱": ("ARRI", "阿莱", "arri", "德国"), "ARRI": ("ARRI", "阿莱", "arri", "德国"),
    "飞利浦": ("Philips", "飞利浦", "philips", "荷兰"), "Philips": ("Philips", "飞利浦", "philips", "荷兰"),
    "克拉斯诺戈尔斯克工厂": ("KMZ", "克拉斯诺戈尔斯克", "kmz", "俄罗斯"),
    "克拉斯諾戈爾斯克工廠": ("KMZ", "克拉斯诺戈尔斯克", "kmz", "俄罗斯"),
    "罗技": ("Logitech", "罗技", "logitech", "瑞士"), "Logitech": ("Logitech", "罗技", "logitech", "瑞士"),
    "创新科技": ("Creative", "创新", "creative", "新加坡"),
    "鸿海科技集团": ("Foxconn", "富士康", "foxconn", "中国台湾"), "Foxconn": ("Foxconn", "富士康", "foxconn", "中国台湾"),
}

SUFFIX_RE = re.compile(
    r"\s+((Inc|Corporation|Corp|Company|Co|Ltd|Limited|GmbH|AG|S\.A\.|AB|K\.K\.|S\.p\.A)\.?)+$", re.I)


def sparql(query, retries=4):
    params = urllib.parse.urlencode({"query": query, "format": "json"})
    req = urllib.request.Request(
        "https://query.wikidata.org/sparql?" + params,
        headers={"User-Agent": UA, "Accept": "application/sparql-results+json"})
    import time
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                wait = 65 * (attempt + 1)
                print(f"WDQS 限流，等待 {wait}s 后重试…", flush=True)
                time.sleep(wait)
                continue
            raise


def wb_descriptions(qids):
    """批量取英文描述，50 个一批。"""
    out = {}
    for i in range(0, len(qids), 50):
        batch = qids[i:i + 50]
        params = urllib.parse.urlencode({
            "action": "wbgetentities", "ids": "|".join(batch),
            "props": "descriptions", "languages": "en", "format": "json"})
        req = urllib.request.Request(
            "https://www.wikidata.org/w/api.php?" + params, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            ents = json.load(r)["entities"]
        for qid, ent in ents.items():
            out[qid] = ent.get("descriptions", {}).get("en", {}).get("value", "")
    return out


def map_brand(mfr_label):
    if mfr_label in MFR_MAP:
        info = MFR_MAP[mfr_label]
        return info  # 可能为 None（显式排除），调用方负责跳过
    name = SUFFIX_RE.sub("", mfr_label or "").strip()
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "unknown"
    return (name, "", slug, "待补充")


def map_brand_strict(mfr_label):
    """只认 MFR_MAP 内的条目（含显式排除的 None），未知制造商返回 None。"""
    return MFR_MAP.get(mfr_label)


def detect_brand_from_label(label):
    """从型号标签开头识别品牌（如“宾得 K-5”->Pentax），用于制造商是集团母公司的情况。
    返回 ((brand_en, brand_cn, slug, country), 匹配到的前缀) 或 (None, "")。"""
    cands, seen = [], set()
    for key, info in MFR_MAP.items():
        if info is None:
            continue  # 显式排除的占位条目
        for prefix in (key, info[1], info[0]):
            if prefix and prefix not in seen:
                seen.add(prefix)
                cands.append((prefix, info))
    cands.sort(key=lambda x: len(x[0]), reverse=True)
    for prefix, info in cands:
        if re.match(r"^" + re.escape(prefix) + r"(?=\s|$|[A-Z0-9])", label):
            return info, prefix
    return None, ""


def strip_brand_prefix(label, brand_en, brand_cn, mfr_label=""):
    # 中文标签里品牌与型号间常无空格（尼康D300），允许零空格但要求后接空白/大写字母/数字；
    # 优先用完整制造商名（如 宾得士） stripping，避免剩下半截
    prefixes = [p for p in (mfr_label, brand_cn, brand_en) if p]
    prefixes.sort(key=len, reverse=True)
    for prefix in prefixes:
        new_label = re.sub(r"^" + re.escape(prefix) + r"(?=\s|$|[A-Z0-9])", "", label).strip()
        if new_label and new_label != label:
            return new_label
    return label.strip()


def map_type(desc, class_label, model, bslug, year):
    text = f"{desc} {class_label}".lower()
    # 1) 具体类型关键词；描述明确写 film 时优先胶片
    for kw, (t, tcn) in DESC_SPECIFIC:
        if kw in text:
            if t == "dslr" and ("film" in text or "analog" in text):
                return "slr", "胶片单反相机"
            return t, tcn
    # 2) 品牌型号命名规律（无反 2008 年后才出现，老机型跳过误判；
    #    数码单反 1991 年（Kodak DCS-100）前不存在；描述明确胶片时 dslr 转 slr）
    for pat, t, tcn in NAME_RULES.get(bslug, []):
        if re.search(pat, model, re.I):
            if t == "mirrorless" and year and year < 2008:
                continue
            if t == "dslr" and ("film" in text or "analog" in text
                                or re.search(r"35\s?mm", text)):
                return "slr", "胶片单反相机"
            if t == "dslr" and year and year < 1991:
                continue
            return t, tcn
    # 3) 泛关键词
    for kw, (t, tcn) in DESC_GENERIC:
        if kw in text:
            if "slr" in text:
                return "slr", "胶片单反相机"
            return t, tcn
    # 4) 兜底
    if "数码" in class_label or "digital" in text:
        return "compact", "数码相机"
    return "slr", "胶片相机"


def parse_year(t):
    m = re.match(r"\+?(\d{1,4})-", t or "")
    return int(m.group(1)) if m else None


def recover_year(desc):
    """从英文描述中提取年份（P571 缺失时用），返回 (year, pattern_idx) 或 (None, None)。"""
    for i, pat in enumerate(YEAR_RES):
        m = pat.search(desc or "")
        if m:
            y = int(m.group(1))
            if 1839 <= y <= 2026:
                return y, i
    return None, None


def thumb_url(p18_url):
    m = re.search(r"/([^/]+)$", p18_url or "")
    if not m:
        return None
    return "https://commons.wikimedia.org/wiki/Special:FilePath/" + \
        urllib.parse.quote(m.group(1)) + "?width=800"


def main():
    limit, dry_run = 200, False
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--dry-run":
            dry_run = True
        elif a == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])

    print("正在查询 Wikidata…", flush=True)
    rows = sparql(QUERY)["results"]["bindings"]
    print(f"取回 {len(rows)} 条")

    # 过滤垃圾类目
    clean = [r for r in rows
             if not any(k.lower() in r["classLabel"]["value"].lower() for k in EXCLUDE_CLASS)]
    print(f"排除垃圾类目后 {len(clean)} 条")

    existing_ids = {f[:-5] for f in os.listdir(DATA_DIR) if f.endswith(".json")}

    print("正在取英文描述（全部候选，用于类型判定）…", flush=True)
    all_qids = [r["item"]["value"].rsplit("/", 1)[-1] for r in clean]
    all_descs = wb_descriptions(all_qids)

    # 构建候选池：年份（P571 缺失则从描述抢救）、品牌（缺失则从型号标签识别）、类型
    pool, stats = [], Counter()
    for r in clean:
        qid = r["item"]["value"].rsplit("/", 1)[-1]
        label = r["itemLabel"]["value"]
        desc = all_descs.get(qid, "")
        if re.search(r"lens|镜头", label, re.I):
            stats["镜头误标"] += 1
            continue
        if any(k in desc.lower() for k in ACCESSORY_KW):
            stats["配件/外设"] += 1
            continue
        mfr = r.get("manufacturerLabel", {}).get("value")
        brand_info = None
        if mfr:
            brand_info = map_brand(mfr)
            if brand_info is None:  # MFR_MAP 显式排除
                stats["排除品牌"] += 1
                continue
        if not brand_info:
            brand_info, _prefix = detect_brand_from_label(label)
            if brand_info:
                stats["品牌(由型号识别)"] += 1
        if not brand_info or not brand_info[0]:
            stats["无制造商"] += 1
            continue
        brand_en, brand_cn, bslug, country = brand_info
        year = parse_year(r.get("inception", {}).get("value"))
        year_src = "P571"
        if not year:
            year, _ = recover_year(desc)
            year_src = "描述"
        if not year:
            stats["无年份"] += 1
            continue
        if year_src == "描述":
            stats["年份(由描述恢复)"] += 1
        model = strip_brand_prefix(label, brand_en, brand_cn, mfr or "")
        t, tcn = map_type(desc, r["classLabel"]["value"], model, bslug, year)
        model_part = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", model.lower())).strip("-")
        cid = f"{bslug}-{model_part}" if model_part else f"{bslug}-{qid.lower()}"
        if cid in existing_ids:
            stats["已存在"] += 1
            continue
        existing_ids.add(cid)
        pool.append({"r": r, "qid": qid, "label": label, "cid": cid,
                     "brand_en": brand_en, "brand_cn": brand_cn, "bslug": bslug,
                     "country": country, "mfr": mfr or label.split()[0], "model": model,
                     "year": year, "year_src": year_src, "type": t, "type_cn": tcn,
                     "desc": desc, "sitelinks": int(r["sitelinks"]["value"])})
    print(f"候选池 {len(pool)} 款；跳过：{dict(stats)}")

    # 分层抽取：胶片时代保底配额，数码时代按类型封顶，避免被 DSLR 淹没
    QUOTA_FILM, QUOTA_DIGITAL = 90, 110
    TYPE_CAP_FILM = {"slr": 45, "rangefinder": 20, "tlr": 8, "compact": 12,
                     "medium_format": 6, "instant": 5, "scale_focus": 4,
                     "large_format": 3}
    TYPE_CAP_DIGITAL = {"dslr": 55, "mirrorless": 40, "compact": 15, "instant": 5}
    selected, film_n, dig_n = [], 0, 0
    film_type_n, dig_type_n = Counter(), Counter()
    for s in pool:  # pool 保持 sitelinks 降序
        if len(selected) >= limit:
            break
        if s["year"] < 2000:
            if film_n >= QUOTA_FILM:
                continue
            if film_type_n[s["type"]] >= TYPE_CAP_FILM.get(s["type"], 2):
                continue
            film_n += 1
            film_type_n[s["type"]] += 1
        else:
            if dig_n >= QUOTA_DIGITAL:
                continue
            if dig_type_n[s["type"]] >= TYPE_CAP_DIGITAL.get(s["type"], 3):
                continue
            dig_n += 1
            dig_type_n[s["type"]] += 1
        selected.append(s)
    print(f"选中 {len(selected)} 款（胶片 {film_n} / 数码 {dig_n}）；"
          f"胶片类型 {dict(film_type_n)}；数码类型 {dict(dig_type_n)}")

    type_counter, fallback_cases, unmapped_mfr = Counter(), [], set()
    for s in selected:
        type_counter[f"{s['type']} / {s['type_cn']}"] += 1
        if s["type_cn"] in ("数码相机", "胶片相机"):
            fallback_cases.append(s)
        if not s["brand_cn"]:
            unmapped_mfr.add(s["mfr"])

    print("\n[类型分布]")
    for k, n in type_counter.most_common():
        print(f"  {n:4d}  {k}")
    if unmapped_mfr:
        print("\n[未映射的制造商（brand_cn 为空）]")
        for m in sorted(unmapped_mfr):
            print("  -", m)
    if fallback_cases:
        print(f"\n[兜底分类 {len(fallback_cases)} 款，前 15 个待人工复核]")
        for s in fallback_cases[:15]:
            print(f"  {s['label']} | {s['desc'][:50]} | -> {s['type_cn']}")

    if dry_run:
        print("\n[dry-run] 结束，未写文件。")
        return

    written = 0
    for s in selected:
        r = s["r"]
        img = thumb_url(r.get("image", {}).get("value"))
        sitelinks = int(r["sitelinks"]["value"])
        cam = {
            "id": s["cid"],
            "brand": s["brand_en"],
            "brand_cn": s["brand_cn"],
            "brand_slug": s["bslug"],
            "country": s["country"],
            "manufacturer": s["mfr"],
            "model": s["model"],
            "year": s["year"],
            "year_source": s["year_src"],  # P571 / 描述（抢救）
            "type": s["type"],
            "type_cn": s["type_cn"],
            "format": "待补充",
            "mount": "待补充",
            "milestone": True,
            "tagline": f"{s['brand_cn'] or s['brand_en']}{s['type_cn']}，{s['year']} 年发布",
            "description": f"{s['brand_cn'] or s['brand_en']}于{s['year']}年发布的{s['type_cn']}。",
            "significance": "待补充（需人工撰写）",
            "specs": {"Wikidata": s["qid"], "多语言词条数": str(sitelinks)},
            "images": ([{"remote_url": img, "caption": s["model"],
                         "source": "Wikimedia Commons", "license": "见文件页授权",
                         "url": f"https://www.wikidata.org/wiki/{s['qid']}"}] if img else []),
            "sources": [{"title": f"Wikidata – {s['label']}",
                         "url": f"https://www.wikidata.org/wiki/{s['qid']}"}],
            "qid": s["qid"],
            "sitelinks": sitelinks,
            "imported": "wikidata-2026-11-01",
        }
        with open(os.path.join(DATA_DIR, s["cid"] + ".json"), "w", encoding="utf-8") as f:
            json.dump(cam, f, ensure_ascii=False, indent=2)
        written += 1
    print(f"\n写入 {written} 个 JSON。country/format/mount/significance 待后续补全。")


if __name__ == "__main__":
    main()
