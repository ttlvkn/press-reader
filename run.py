#!/usr/bin/env python3
"""外刊精读 · 命令行入口

    python run.py check        自检：key 通不通、模型是谁、各源能不能连
    python run.py all          抓取 → 翻译讲解 → 构建（每日定时任务用这个）
    python run.py fetch        只抓取（可加源 id，如：python run.py fetch guardian）
    python run.py enrich       只做翻译讲解
    python run.py build        只构建站点
    python run.py demo         灌入演示数据并构建（无需联网/API Key）
    python run.py serve        本地预览 http://127.0.0.1:8000

密钥两种放法（线上用 GitHub Secrets，本地用 .env，二选一）：
    1) 项目根目录的 .env 文件里写（推荐，改完不用重开终端）
    2) 环境变量 LLM_API_KEY / GUARDIAN_API_KEY

    LLM_API_KEY        大模型密钥（DeepSeek / 通义 / Kimi / GLM / OpenAI 均可）
    GUARDIAN_API_KEY   卫报开放平台密钥（免费申请，有它才能拿到卫报全文）
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src import sources, render  # noqa: E402
from src.enrich import enrich_all  # noqa: E402
from src.util import (  # noqa: E402
    DATA, ROOT, SITE, CST, load_config, load_env, log, now_cst, read_json, write_json,
)


def cmd_check(cfg: dict) -> int:
    """自检：key 通不通、模型是谁、各个 RSS 源能不能连上。"""
    import requests

    log("=== 自检 ===")
    llm = cfg["llm"]
    key_name = llm["api_key_env"]
    key = os.environ.get(key_name, "").strip()

    # 1. 大模型
    if not key:
        log(f"✗ 没有找到 {key_name}（.env 里填 Key=sk-... 或设置环境变量）")
    else:
        masked = key[:6] + "…" + key[-4:] if len(key) > 12 else "***"
        log(f"· {key_name} = {masked}")
        base = llm["base_url"].rstrip("/")
        try:
            r = requests.get(
                f"{base}/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=20,
            )
            if r.status_code == 200:
                ids = [m.get("id") for m in (r.json().get("data") or [])]
                log(f"✓ 模型服务可用，账号可用模型：{', '.join(filter(None, ids)) or '—'}")
                if llm["model"] not in ids and ids:
                    log(f"  ! config.yaml 里的 model={llm['model']} 不在列表中，请核对")
            elif r.status_code == 401:
                log("✗ Key 无效（401），检查是否复制完整、有没有多余空格")
            else:
                log(f"  · /models 返回 {r.status_code}，改用对话接口试一次")
                r2 = requests.post(
                    f"{base}/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": llm["model"], "messages": [{"role": "user", "content": "hi"}], "max_tokens": 4},
                    timeout=40,
                )
                if r2.status_code == 200:
                    log("✓ 对话接口可用")
                else:
                    log(f"✗ 对话接口返回 {r2.status_code}：{r2.text[:160]}")
        except Exception as exc:  # noqa: BLE001
            log(f"✗ 连不上 {base}：{type(exc).__name__} {exc}")

    # 2. 卫报 key
    gkey = os.environ.get("GUARDIAN_API_KEY", "").strip()
    if not gkey:
        log("· 未设置 GUARDIAN_API_KEY —— 卫报会降级为 RSS 摘要模式（不影响其他刊）")
    else:
        try:
            r = requests.get(
                "https://content.guardianapis.com/search",
                params={"api-key": gkey, "page-size": 1},
                timeout=20,
            )
            if r.status_code == 200:
                log("✓ 卫报 Open Platform 可用（卫报可拿全文）")
            else:
                log(f"✗ 卫报 API 返回 {r.status_code}，key 可能无效")
        except Exception as exc:  # noqa: BLE001
            log(f"✗ 连不上卫报 API：{type(exc).__name__}")

    # 3. 各源连通性
    log("· 检查订阅源连通性：")
    ua = cfg["fetch"]["user_agent"]
    bad = 0
    for src in cfg["sources"]:
        if src["type"] == "guardian_api":
            continue
        for feed in src.get("feeds", [])[:1]:
            try:
                r = requests.get(feed, headers={"User-Agent": ua}, timeout=12)
                ok = r.status_code == 200 and len(r.content) > 500
                log(f"  {'✓' if ok else '✗'} {src['name']:<10} {r.status_code}  {feed}")
                bad += 0 if ok else 1
            except Exception as exc:  # noqa: BLE001
                log(f"  ✗ {src['name']:<10} 连接失败 {type(exc).__name__}  {feed}")
                bad += 1

    if bad:
        log(f"\n有 {bad} 个源连不上——先确认网络/代理，其余源仍可正常更新。")
    else:
        log("\n全部就绪，可以运行：python run.py all")
    return 0


def existing_ids() -> set[str]:
    ids: set[str] = set()
    for path in (DATA / "enriched").glob("*.json"):
        for art in read_json(path, []) or []:
            if isinstance(art, dict) and art.get("id"):
                ids.add(art["id"])
    return ids


def day_file(dt: datetime | None = None) -> Path:
    dt = dt or now_cst()
    return DATA / "enriched" / f"{dt.strftime('%Y-%m-%d')}.json"


def cmd_fetch(cfg: dict, only: str = "") -> int:
    log("=== 1/3 抓取 ===")
    if only:
        cfg = {**cfg, "sources": [s for s in cfg["sources"] if s["id"] == only]}
        if not cfg["sources"]:
            log(f"! 没有 id 为 {only} 的源")
            return 0
        log(f"· 只抓取：{only}")
    path = day_file()
    prev = read_json(path, []) or []

    # 之前只抓到摘要的文章，如果现在能拿到全文（比如刚配好卫报 key），
    # 允许同 id 的全文版本覆盖它，并把旧译文作废重翻。
    upgrade = {a["id"] for a in prev if not (a.get("body_en") or [])}
    if upgrade:
        log(f"· {len(upgrade)} 篇旧文章只有摘要，本次尝试用全文覆盖")

    items = sources.collect(cfg, existing_ids() - upgrade)

    by_id = {a["id"]: a for a in prev}
    upgraded = sum(1 for i in items if i["id"] in by_id and (i.get("body_en") or []))
    for i in items:
        by_id[i["id"]] = i          # 新版本（含全文）直接覆盖旧记录

    # 早先只有导语的卫报文章，按文章路径回查 API 补全文（本次没抓到新文章也要做）
    for src in cfg["sources"]:
        for fixed in sources.upgrade_with_fulltext(list(by_id.values()), src, cfg):
            by_id[fixed["id"]] = fixed
            upgraded += 1

    if not items and not upgraded:
        log("· 没有抓到新文章（可能都抓过了，或网络/订阅源异常）")
        return 0

    merged = sorted(by_id.values(), key=lambda a: a.get("published", ""), reverse=True)
    write_json(path, merged)
    log(f"· 已写入 {path.relative_to(ROOT)}：新增/更新 {len(items)} 篇，升级为全文 {upgraded} 篇")
    return len(items)


def cmd_enrich(cfg: dict) -> int:
    log("=== 2/3 翻译与讲解 ===")
    path = day_file()
    arts = read_json(path, []) or []
    if not arts:
        log("· 今天没有待处理文章")
        return 0
    # 没有译文的一律重试：可能是上次网络/额度问题，也可能是阈值调整后新纳入的摘要文章
    todo = [a for a in arts if not a.get("enrich")]
    if not todo:
        log("· 今天文章都已有译文，无需重复翻译")
        return 0
    log(f"· 待翻译 {len(todo)} 篇（已有译文 {len(arts) - len(todo)} 篇）")
    enrich_all(todo, cfg)
    for a in arts:
        a["enrich_attempted"] = True
    write_json(path, arts)
    done = sum(1 for a in arts if a.get("enrich"))
    log(f"· 完成 {done}/{len(arts)} 篇")
    return done


def cmd_build(cfg: dict) -> int:
    log("=== 3/3 构建站点 ===")
    stats = render.build(cfg)
    if stats.get("count", 0) == 0:
        log("! 站点为空，先运行 python run.py demo 或 python run.py all")
        return 1
    return 0


def cmd_demo(cfg: dict) -> int:
    log("=== 灌入演示数据 ===")
    src_file = ROOT / "demo" / "articles.json"
    data = read_json(src_file, []) or []
    if not data:
        log("! 找不到 demo/articles.json")
        return 1

    today = now_cst().strftime("%Y-%m-%d")
    out = []
    for art in data:
        art = dict(art)
        t = art.pop("time", "08:00")
        art["published"] = f"{today}T{t}:00+08:00"
        art["is_demo"] = True
        art["digest"] = not bool(art.get("body_en"))
        art["enrich_attempted"] = True
        out.append(art)
    write_json(day_file(), out)
    log(f"· 写入 {len(out)} 篇演示文章（日期 {today}）")
    return cmd_build(cfg)


def cmd_serve(cfg: dict) -> int:
    import functools
    import http.server
    import socket
    import socketserver

    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "127.0.0.1")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    socketserver.TCPServer.allow_reuse_address = True
    if "--open" in sys.argv:
        import threading
        import webbrowser

        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")).start()
    with socketserver.TCPServer((host, port), handler) as httpd:
        log(f"→ 预览地址 http://127.0.0.1:{port}/")
        if host == "0.0.0.0":
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                log(f"→ 同一 Wi-Fi 下用手机访问：http://{s.getsockname()[0]}:{port}/")
                s.close()
            except OSError:
                pass
        log("（Ctrl+C 退出）")
        httpd.serve_forever()
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    loaded = load_env()
    if loaded:
        log(f"· 已从 .env 读取：{', '.join(loaded)}")
    cfg = load_config()

    if cmd == "check":
        return cmd_check(cfg)
    if cmd == "fetch":
        return cmd_fetch(cfg, sys.argv[2] if len(sys.argv) > 2 else "")
    if cmd == "enrich":
        return cmd_enrich(cfg)
    if cmd == "build":
        return cmd_build(cfg)
    if cmd == "demo":
        return cmd_demo(cfg)
    if cmd == "serve":
        return cmd_serve(cfg)
    if cmd == "all":
        cmd_fetch(cfg)
        cmd_enrich(cfg)
        return cmd_build(cfg)
    if cmd == "clean":
        for p in (DATA / "enriched", DATA / "cache"):
            if p.exists():
                shutil.rmtree(p)
            p.mkdir(parents=True, exist_ok=True)
        if SITE.exists():
            shutil.rmtree(SITE)
        SITE.mkdir(exist_ok=True)
        log("· 已清空数据与站点")
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
