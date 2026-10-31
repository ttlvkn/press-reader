"""用 GitHub REST API 推送本地提交 —— git 协议被墙 / 代理不稳时的备用通道。

原理：不推送 git 对象，而是比对「本地 HEAD 的树」和「远端 main 的树」，
把差异文件通过 Blobs API 逐个上传，再拼成一个 commit 更新 ref。
好处是只需要 api.github.com 可达；坏处是每次只产生一个提交，丢了历史细节。

用法：
    python tools/push_via_api.py            # 推到 origin 指向的仓库
    python tools/push_via_api.py --dry-run  # 只看会改哪些文件

令牌自动从 Git 凭据管理器读取（就是上次浏览器授权存下的那个），无需手动填写。
"""
from __future__ import annotations

import base64
import os
import re
import subprocess
import sys

import requests


def token() -> str:
    out = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    ).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    sys.exit("拿不到令牌：先在浏览器里对 GitHub 授权一次（git push 会触发）")


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def repo_path() -> str:
    url = git("remote", "get-url", "origin")
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
    if not m:
        sys.exit(f"无法从 remote 解析仓库：{url}")
    return m.group(1)


def main() -> None:
    dry = "--dry-run" in sys.argv
    api = f"https://api.github.com/repos/{repo_path()}"
    h = {"Authorization": f"Bearer {token()}", "Accept": "application/vnd.github+json"}

    local = git("rev-parse", "HEAD")
    r = requests.get(f"{api}/git/ref/heads/main", headers=h, timeout=20)
    r.raise_for_status()
    remote = r.json()["object"]["sha"]
    print(f"本地 {local[:8]} / 远端 {remote[:8]}")
    if local == remote:
        print("已经同步，无需推送")
        return

    local_tree: dict[str, tuple[str, str]] = {}
    for line in git("ls-tree", "-r", "HEAD").splitlines():
        meta, path = line.split("\t", 1)
        mode, _typ, sha = meta.split()
        local_tree[path] = (mode, sha)

    rt = requests.get(f"{api}/git/trees/{remote}", params={"recursive": "1"}, headers=h, timeout=30)
    rt.raise_for_status()
    remote_tree = {
        t["path"]: (t.get("mode", "100644"), t["sha"])
        for t in rt.json()["tree"]
        if t["type"] == "blob"
    }

    changed = [(p, s) for p, s in local_tree.items() if remote_tree.get(p) != s]
    deleted = [p for p in remote_tree if p not in local_tree]
    print(f"变更 {len(changed)} 个文件，删除 {len(deleted)} 个")
    for p, _ in changed:
        print(f"  ↑ {p}")
    for p in deleted:
        print(f"  ✗ {p}")
    if dry or not (changed or deleted):
        return

    entries = []
    for path, (mode, sha) in changed:
        blob = subprocess.check_output(["git", "cat-file", "-p", sha])
        resp = requests.post(
            f"{api}/git/blobs",
            headers=h,
            timeout=60,
            json={"content": base64.b64encode(blob).decode(), "encoding": "base64"},
        )
        resp.raise_for_status()
        entries.append({"path": path, "mode": mode, "type": "blob", "sha": resp.json()["sha"]})
    for path in deleted:
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})

    resp = requests.post(
        f"{api}/git/trees", headers=h, timeout=60, json={"base_tree": remote, "tree": entries}
    )
    resp.raise_for_status()
    message = git("log", "-1", "--pretty=%s")
    resp = requests.post(
        f"{api}/git/commits",
        headers=h,
        timeout=60,
        json={"message": message, "tree": resp.json()["sha"], "parents": [remote]},
    )
    resp.raise_for_status()
    commit = resp.json()["sha"]

    resp = requests.patch(
        f"{api}/git/refs/heads/main", headers=h, timeout=30, json={"sha": commit, "force": False}
    )
    resp.raise_for_status()
    print(f"✓ 已推送 {commit[:8]}：{message}")


if __name__ == "__main__":
    main()
