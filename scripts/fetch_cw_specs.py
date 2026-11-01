#!/usr/bin/env python3
"""重新抓取已导入 camera-wiki 相机的页面（供规格解析用）。"""
import json, sys, time, urllib.parse, urllib.request

UA = {"User-Agent": "CameraMuseumBot/0.1 (spec enrichment)"}
API = "https://camera-wiki.org/api.php"

titles = json.load(open("/tmp/cw_imported_titles.json", encoding="utf-8"))
print(f"{len(titles)} titles", flush=True)
pages = {}
for i in range(0, len(titles), 50):
    batch = titles[i:i + 50]
    for attempt in range(4):
        try:
            time.sleep(1.2)
            url = API + "?" + urllib.parse.urlencode({
                "action": "query", "prop": "revisions", "rvprop": "content",
                "rvslots": "main", "titles": "|".join(batch),
                "redirects": 1, "format": "json"})
            d = json.load(urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=60))
            break
        except Exception as e:
            print(f"  retry {attempt}: {e}", flush=True)
            time.sleep(5 * (attempt + 1))
    for p in d["query"]["pages"].values():
        if "revisions" in p:
            pages[p["title"]] = p["revisions"][0]["slots"]["main"]["*"]
    print(f"  {min(i+50, len(titles))}/{len(titles)}", flush=True)

json.dump(pages, open("/tmp/cw_spec_pages.json", "w"), ensure_ascii=False)
print(f"saved {len(pages)} pages")
