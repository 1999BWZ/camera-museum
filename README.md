# 相机博物馆 Camera Museum

整理相机历史上的经典机型资料，做成类似 Canon Camera Museum 形式、但覆盖**所有品牌**的在线博物馆。

当前版本：脚手架 v0.1（含 12 款里程碑机型示例数据）。

## 目录结构

```
camera-museum/
├── README.md                 # 本文件
├── data/
│   ├── schema.json           # 数据模型（JSON Schema）
│   └── cameras/              # 每款机型一个 JSON 文件
│       ├── leica-i-model-a.json
│       └── ...
├── templates/                # 页面模板（{{占位符}} 形式）
│   ├── base.html
│   ├── index.html            # 首页：展厅入口 + 时间线 + 品牌/类型索引
│   ├── brand.html            # 品牌展厅页
│   └── camera.html           # 机型详情页（博物馆展品页形式）
├── assets/
│   ├── style.css             # 博物馆风格样式（构建时内联进页面）
│   └── images/               # 机型照片（见 images/README.md 规范）
├── scripts/
│   └── generate.py           # 静态站点生成器（仅用 Python 标准库）
└── site/                     # 生成产物（可直接部署到 GitHub Pages）
    ├── index.html
    ├── brand/<slug>.html
    └── c/<id>.html
```

## 部署（GitHub Pages）

仓库推送到 GitHub 后自动部署：`.github/workflows/pages.yml` 会在每次 push 到
main 时运行 `scripts/generate.py --check` 并发布 `site/`。

首次使用需在仓库 Settings → Pages → Build and deployment → Source 选择
**GitHub Actions**（只需点一次）。之后每次更新数据并 push，网站自动重新生成上线。

## 持续更新数据

```bash
python3 scripts/import_wikipedia.py --limit 200  # 增量导入（跳过已有）
python3 scripts/generate.py --check              # 本地验证
# git push（或经由 Muse 推送）-> 自动部署
```

## 快速开始

```bash
cd ~/workspace/camera-museum
python3 scripts/generate.py        # 生成 site/
python3 scripts/generate.py --check  # 生成并校验站内链接
```

用浏览器打开 `site/index.html` 即可预览。部署时把 `site/` 目录推到 GitHub Pages / Cloudflare Pages。

## 数据模型

每款机型一个 JSON，字段定义见 `data/schema.json`，核心字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| id | 全站唯一 slug | `nikon-f` |
| brand / brand_cn / brand_slug | 品牌英文/中文/链接 slug | `Nikon` / `尼康` / `nikon` |
| country / manufacturer | 产地 / 制造商 | `日本` / `日本光学工业` |
| model | 型号 | `Nikon F` |
| year / end_year | 发布年 / 停产年（可选） | `1959` / `1973` |
| type / type_cn | 类型枚举（见下） | `slr` / `135 单反相机` |
| format | 画幅/胶卷规格 | `135 胶卷（24×36mm）` |
| mount | 卡口 | `Nikon F 卡口` |
| designer | 设计师（可选） | `Giorgetto Giugiaro` |
| milestone | 是否里程碑机型 | `true` |
| tagline | 卡片用一句话 | `专业系统单反的鼻祖` |
| description | 简介（2~4 句，须核对事实） | … |
| significance | 历史意义（1~2 句） | … |
| specs | 规格参数表（键值对） | `{"快门": "…"}` |
| images | 照片列表（见图片规范） | … |
| sources | 资料来源（标题+链接） | … |

类型枚举（`type`）：`scale_focus` 估焦 / `rangefinder` 旁轴 / `tlr` 双反 /
`slr` 胶片单反 / `dslr` 数码单反 / `mirrorless` 无反 /
`compact` 便携 / `instant` 一次成像 / `medium_format` 中画幅 / `large_format` 大画幅

## 新增一款机型

1. 在 `data/cameras/` 新建 `<id>.json`（复制一份已有的改）；
2. 事实核对：年份、规格以 Wikipedia / 厂商博物馆 / Camerapedia 为准，`sources` 里注明；
3. 照片放到 `assets/images/`，按规范命名并在 JSON 的 `images` 里登记来源与授权；
4. 运行 `python3 scripts/generate.py --check`，确认无断链。

## 图片规范（重要）

见 `assets/images/README.md`。核心原则：**只用有明确授权的图片**
（Wikimedia Commons CC 授权、厂商新闻资料图、自摄），每张图登记
`source / author / license`，页面上自动展示来源。

## 路线图

- [ ] 从 Wikidata 用 SPARQL 批量拉取机型种子数据（品牌/年份/类型），转成 data/cameras/*.json
- [ ] 补全图片（Wikimedia Commons）
- [ ] 增加镜头展厅（Lens Hall，对标 Canon 博物馆）
- [ ] 增加大事记时间线（History Hall）
- [ ] 接入 GitHub Pages 自动部署
- [ ] 开放投稿：GitHub PR 新增机型 JSON
