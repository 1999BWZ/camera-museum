#!/usr/bin/env python3
"""相机博物馆 · 静态站点生成器（仅用 Python 标准库）。

    python3 scripts/generate.py            # 生成 site/
    python3 scripts/generate.py --check     # 生成并校验站内链接
"""
import html
import json
import os
import re
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE, "data", "cameras")
TPL_DIR = os.path.join(BASE, "templates")
ASSETS_DIR = os.path.join(BASE, "assets")
SITE_DIR = os.path.join(BASE, "site")

REQUIRED = ["id", "brand", "brand_cn", "brand_slug", "country", "manufacturer",
            "model", "year", "type", "type_cn", "format", "mount", "milestone",
            "tagline", "description", "significance", "specs", "images", "sources"]


def esc(s):
    return html.escape(str(s), quote=True)


def load_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def render(tpl, mapping):
    out = tpl
    for k, v in mapping.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def load_cameras():
    cams = []
    for fn in sorted(os.listdir(DATA_DIR)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(DATA_DIR, fn), encoding="utf-8") as f:
            c = json.load(f)
        missing = [k for k in REQUIRED if k not in c]
        if missing:
            raise ValueError(f"{fn} 缺少字段: {missing}")
        cams.append(c)
    cams.sort(key=lambda c: (c["year"], c["id"]))
    return cams


def year_str(c):
    if c.get("end_year"):
        return f"{c['year']} – {c['end_year']}"
    return str(c["year"])


def card_html(c, link_prefix=""):
    ms = '<span class="tag milestone">里程碑</span>' if c.get("milestone") else ""
    return (
        f'<a class="card" href="{link_prefix}c/{esc(c["id"])}.html" '
        f'data-type="{esc(c["type"])}" data-brand="{esc(c["brand_slug"])}">'
        f'<div class="year serif">{c["year"]}</div>'
        f"<h4>{esc(c['model'])}</h4>"
        f'<div class="brandline">{esc(c["brand_cn"])} {esc(c["brand"])} · {esc(c["country"])}</div>'
        f'<span class="tag">{esc(c["type_cn"])}</span>{ms}'
        f'<p class="tagline">{esc(c["tagline"])}</p>'
        "</a>"
    )


def build_index(cams, css, js):
    brands = {}
    types = {}
    for c in cams:
        brands.setdefault(c["brand_slug"], []).append(c)
        types.setdefault((c["type"], c["type_cn"]), []).append(c)

    # 时间线：按十年分组
    decades = {}
    for c in cams:
        d = (c["year"] // 10) * 10
        decades.setdefault(d, []).append(c)
    timeline = []
    for d in sorted(decades):
        cards = "\n".join(card_html(c) for c in decades[d])
        timeline.append(
            f'<div class="decade"><h3 class="serif">{d}s<small>{d} 年代</small></h3>'
            f'<div class="cards">\n{cards}\n</div></div>'
        )

    brand_chips = "\n".join(
        f'<a class="chip" href="brand/{esc(slug)}.html">'
        f'{esc(cs[0]["brand_cn"])} {esc(cs[0]["brand"])}<small>{len(cs)}</small></a>'
        for slug, cs in sorted(brands.items(), key=lambda kv: kv[1][0]["brand_cn"])
    )
    type_chips = (
        '<button class="chip active" data-filter="">全部<small>' + str(len(cams)) + "</small></button>\n"
        + "\n".join(
            f'<button class="chip" data-filter="{esc(t)}">{esc(tcn)}<small>{len(cs)}</small></button>'
            for (t, tcn), cs in sorted(types.items(), key=lambda kv: kv[0][1])
        )
    )

    years = [c["year"] for c in cams]
    body = render(load_text(os.path.join(TPL_DIR, "index.html")), {
        "stats_cameras": len(cams),
        "stats_brands": len(brands),
        "stats_years": f"{min(years)}–{max(years)}",
        "timeline": "\n".join(timeline),
        "brand_chips": brand_chips,
        "type_chips": type_chips,
    })
    page = render(load_text(os.path.join(TPL_DIR, "base.html")), {
        "title": "首页",
        "root": "",
        "css": css,
        "js": js,
        "content": body,
    })
    with open(os.path.join(SITE_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)


def build_brands(cams, css, js):
    brands = {}
    for c in cams:
        brands.setdefault(c["brand_slug"], []).append(c)
    os.makedirs(os.path.join(SITE_DIR, "brand"), exist_ok=True)
    for slug, cs in brands.items():
        years = [c["year"] for c in cs]
        cards = "\n".join(card_html(c, link_prefix="../") for c in cs)
        body = render(load_text(os.path.join(TPL_DIR, "brand.html")), {
            "root": "../",
            "brand_cn": esc(cs[0]["brand_cn"]),
            "brand": esc(cs[0]["brand"]),
            "country": esc(cs[0]["country"]),
            "count": len(cs),
            "year_range": f"{min(years)}–{max(years)}",
            "cards": cards,
        })
        page = render(load_text(os.path.join(TPL_DIR, "base.html")), {
            "title": f"{cs[0]['brand_cn']} {cs[0]['brand']}",
            "root": "../",
            "css": css,
            "js": js,
            "content": body,
        })
        with open(os.path.join(SITE_DIR, "brand", f"{slug}.html"), "w", encoding="utf-8") as f:
            f.write(page)


def photo_block(c, root):
    if not c["images"]:
        return (
            '<div class="photo-plate"><div class="plaque">'
            "<b>展品照片整理中</b>"
            "<span>建议来源：Wikimedia Commons（CC 授权）<br>或厂商新闻资料图，登记后自动展示</span>"
            "</div></div>"
        )
    parts = []
    for im in c["images"]:
        src = f"{root}assets/images/{esc(im['file'])}" if im.get("file") \
            else esc(im.get("remote_url", ""))
        credit = esc(im["source"])
        if im.get("author"):
            credit += " · " + esc(im["author"])
        credit += " · " + esc(im["license"])
        if im.get("url"):
            credit = f'<a href="{esc(im["url"])}" target="_blank" rel="noopener" ' \
                     f'style="color:#B7A98C">{credit}</a>'
        parts.append(
            '<div class="photo-plate" style="display:block">'
            f'<img src="{src}" alt="{esc(im["caption"])}" loading="lazy">'
            f'<div class="photo-credit">{esc(im["caption"])}<br>来源：{credit}</div>'
            "</div>"
        )
    return "\n".join(parts)


def build_cameras(cams, css, js):
    os.makedirs(os.path.join(SITE_DIR, "c"), exist_ok=True)
    for i, c in enumerate(cams):
        rows = [
            ("品牌", f'{esc(c["brand_cn"])} {esc(c["brand"])}'),
            ("型号", esc(c["model"])),
            ("年份", esc(year_str(c))),
            ("类型", esc(c["type_cn"])),
            ("画幅", esc(c["format"])),
            ("卡口", esc(c["mount"])),
            ("产地", esc(c["country"])),
            ("制造商", esc(c["manufacturer"])),
        ]
        if c.get("series"):
            rows.append(("系列", esc(c["series"])))
        if c.get("designer"):
            rows.append(("设计师", esc(c["designer"])))
        info_rows = "\n".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows)
        spec_rows = "\n".join(
            f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in c["specs"].items()
        )
        srcs = []
        for s in c["sources"]:
            if s.get("url"):
                srcs.append(f'<li><a href="{esc(s["url"])}" target="_blank" rel="noopener">{esc(s["title"])}</a></li>')
            else:
                srcs.append(f"<li>{esc(s['title'])}</li>")

        def nav_link(j, label):
            if j is None:
                return '<span style="color:var(--muted)">—</span>'
            n = cams[j]
            return f'{label}：<a href="{esc(n["id"])}.html">{esc(n["model"])}（{n["year"]}）</a>'

        body = render(load_text(os.path.join(TPL_DIR, "camera.html")), {
            "root": "../",
            "brand_cn": esc(c["brand_cn"]),
            "brand_slug": esc(c["brand_slug"]),
            "model": esc(c["model"]),
            "year_str": esc(year_str(c)),
            "type_cn": esc(c["type_cn"]),
            "milestone_badge": '<span class="tag milestone">里程碑机型</span>' if c.get("milestone") else "",
            "photo_block": photo_block(c, "../"),
            "info_rows": info_rows,
            "significance": esc(c["significance"]),
            "description": esc(c["description"]),
            "spec_rows": spec_rows,
            "sources_list": "\n".join(srcs),
            "prev_link": nav_link(i - 1 if i > 0 else None, "上一款"),
            "next_link": nav_link(i + 1 if i < len(cams) - 1 else None, "下一款"),
        })
        page = render(load_text(os.path.join(TPL_DIR, "base.html")), {
            "title": f'{c["brand_cn"]} {c["model"]}',
            "root": "../",
            "css": css,
            "js": js,
            "content": body,
        })
        with open(os.path.join(SITE_DIR, "c", f"{c['id']}.html"), "w", encoding="utf-8") as f:
            f.write(page)


def check_links():
    """校验站内相对链接是否都有目标文件。"""
    problems = []
    for dirpath, _, filenames in os.walk(SITE_DIR):
        for fn in filenames:
            if not fn.endswith(".html"):
                continue
            path = os.path.join(dirpath, fn)
            html_text = load_text(path)
            for m in re.finditer(r'(?:href|src)="([^"#]+?)"', html_text):
                url = m.group(1)
                if url.startswith(("http://", "https://", "mailto:", "data:")):
                    continue
                target = os.path.normpath(os.path.join(dirpath, url))
                if not os.path.exists(target):
                    problems.append(f"{os.path.relpath(path, SITE_DIR)} -> {url}")
    # 模板占位符残留检查
    for dirpath, _, filenames in os.walk(SITE_DIR):
        for fn in filenames:
            if fn.endswith(".html") and "{{" in load_text(os.path.join(dirpath, fn)):
                problems.append(f"{fn}: 有未替换的模板占位符")
    return problems


def main():
    do_check = "--check" in sys.argv
    if os.path.exists(SITE_DIR):
        shutil.rmtree(SITE_DIR)
    os.makedirs(SITE_DIR)
    # 图片随构建拷贝
    img_src = os.path.join(ASSETS_DIR, "images")
    img_dst = os.path.join(SITE_DIR, "assets", "images")
    os.makedirs(img_dst, exist_ok=True)
    for fn in os.listdir(img_src):
        if fn.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            shutil.copy(os.path.join(img_src, fn), img_dst)

    css = load_text(os.path.join(ASSETS_DIR, "style.css"))
    js = load_text(os.path.join(ASSETS_DIR, "app.js"))
    cams = load_cameras()
    build_index(cams, css, js)
    build_brands(cams, css, js)
    build_cameras(cams, css, js)
    n_pages = 1 + len({c["brand_slug"] for c in cams}) + len(cams)
    print(f"生成完毕：{len(cams)} 款机型，{n_pages} 个页面 -> {SITE_DIR}")
    if do_check:
        problems = check_links()
        if problems:
            print("发现问题：")
            for p in problems:
                print("  -", p)
            sys.exit(1)
        print("链接校验通过，无断链、无占位符残留。")


if __name__ == "__main__":
    main()
