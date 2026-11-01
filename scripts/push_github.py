#!/usr/bin/env python3
"""把 camera-museum 本地目录经 GitHub git database API 推送为一次提交。

用法：
    python3 scripts/push_github.py [--dry-run]

流程：blobs（逐文件）→ tree → commit → 更新 refs/heads/main。
凭证经 custom.github-pat 连接（Secure Vault），永不落盘。
"""
import base64
import json
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import add_surrogate_to_request, read_json_response  # noqa: E402

API = "https://api.github.com"
OWNER, REPO, BRANCH = "1999BWZ", "camera-museum", "main"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"site", ".git", "__pycache__", ".venv"}
SKIP_FILES = {".gitignore"}  # 保留 .github，其余按清单


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise RuntimeError("redirect refused")


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Accept": "application/vnd.github+json",
                 "Content-Type": "application/json",
                 "User-Agent": "CameraMuseumBot/0.1"})
    add_surrogate_to_request(req, "custom.github-pat", allowed_hosts=["api.github.com"])
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=90) as r:
        return read_json_response(r)


def collect_files():
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in sorted(filenames):
            if fn in SKIP_FILES and dirpath == ROOT:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, ROOT)
            out.append((rel, full))
    return out


def make_blob(args):
    rel, full = args
    with open(full, "rb") as f:
        content = base64.b64encode(f.read()).decode()
    sha = call("POST", f"/repos/{OWNER}/{REPO}/git/blobs",
               {"content": content, "encoding": "base64"})["sha"]
    return {"path": rel.replace(os.sep, "/"), "mode": "100644",
            "type": "blob", "sha": sha}


def main():
    dry = "--dry-run" in sys.argv
    files = collect_files()
    print(f"files: {len(files)}")
    if dry:
        for rel, _ in files[:5]:
            print("  ", rel)
        print("   ...")
        return

    # 仓库当前 main ref（空仓库没有，走无 parent 首提交）
    try:
        base = call("GET", f"/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}")
        parent_sha = base["object"]["sha"]
        print("parent:", parent_sha)
    except Exception:
        parent_sha = None
        print("empty repo, initial commit")

    with ThreadPoolExecutor(max_workers=6) as ex:
        tree_entries = list(ex.map(make_blob, files))
    print(f"blobs created: {len(tree_entries)}")

    tree = call("POST", f"/repos/{OWNER}/{REPO}/git/trees",
                {"tree": sorted(tree_entries, key=lambda e: e["path"])})
    print("tree:", tree["sha"][:12])

    commit_body = {"message": "相机博物馆 v0.1：脚手架 + 201 款机型数据 + Pages 自动部署",
                   "tree": tree["sha"]}
    if parent_sha:
        commit_body["parents"] = [parent_sha]
    commit = call("POST", f"/repos/{OWNER}/{REPO}/git/commits", commit_body)
    print("commit:", commit["sha"])

    if parent_sha:
        ref = call("PATCH", f"/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}",
                   {"sha": commit["sha"]})
    else:
        ref = call("POST", f"/repos/{OWNER}/{REPO}/git/refs",
                   {"ref": f"refs/heads/{BRANCH}", "sha": commit["sha"]})
    print("ref updated:", ref["object"]["sha"] == commit["sha"])
    print("DONE", commit["sha"])


if __name__ == "__main__":
    main()
