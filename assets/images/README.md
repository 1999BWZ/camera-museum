# 图片规范

详情页的照片从 `assets/images/` 读取，构建时会自动拷贝到 `site/assets/images/`。

## 命名

`<机型 id>-<序号>.jpg`，例如：

```
nikon-f-1.jpg        # 机身正面
nikon-f-2.jpg        # 机身顶部 / 细节
```

然后在对应 `data/cameras/<id>.json` 的 `images` 数组里登记：

```json
"images": [
  {
    "file": "nikon-f-1.jpg",
    "caption": "Nikon F 配 Photomic 取景器",
    "source": "Wikimedia Commons",
    "author": "作者名",
    "license": "CC BY-SA 4.0",
    "url": "https://commons.wikimedia.org/wiki/File:xxx.jpg"
  }
]
```

## 授权要求（硬性）

只用有明确授权的图片，按优先级：

1. **自己拍摄**：最稳妥，`license` 填 `自摄`；
2. **Wikimedia Commons**：必须是 CC BY / CC BY-SA / 公有领域，
   登记作者与许可，页面会自动展示来源；
3. **厂商新闻资料图**：注明 `source` 为厂商名，仅限资料整理用途。

不要从电商、评测网站、论坛直接扒图。`images` 为空时，
详情页会显示「展品照片整理中」铭牌，不会出断链。
