"""数据源抓取：卫报开放平台 + 各刊 RSS。

设计原则
--------
1. 只用官方公开渠道（RSS / 官方 API），不做 HTML 逆向爬取、不绕付费墙。
2. 受版权保护的刊物只保存「标题 + 摘要 + 配图 + 原文链接」，正文留在原站。
3. 单源失败不影响整体：任何异常都被兜住，最后返回空列表并打印原因。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import feedparser
import requests

from .util import clean_text, iso, log, make_id, now_cst, paragraphs, strip_html

GUARDIAN_API = "https://content.guardianapis.com/search"


def _err(exc: Exception) -> str:
    """把 requests 的异常翻译成人话：带上 HTTP 状态码，光看 HTTPError 没用。"""
    resp = getattr(exc, "response", None)
    if resp is not None:
        return f"HTTP {resp.status_code} {getattr(resp, 'reason', '')}".strip()
    return type(exc).__name__


def _session(ua: str) -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": ua,
            "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.7",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Cache-Control": "no-cache",
        }
    )
    return s


def _pick_image(entry) -> str:
    for key in ("media_content", "media_thumbnail"):
        items = entry.get(key) or []
        for it in items:
            url = it.get("url")
            if url:
                return url
    for enc in entry.get("enclosures") or []:
        if (enc.get("type") or "").startswith("image"):
            return enc.get("href") or enc.get("url") or ""
    # 从摘要里找第一张图
    summary = entry.get("summary") or ""
    if "<img" in summary:
        import re

        m = re.search(r'<img[^>]+src="([^"]+)"', summary)
        if m:
            return m.group(1)
    return ""


def _published(entry) -> str:
    for key in ("published_parsed", "updated_parsed"):
        tm = entry.get(key)
        if tm:
            # feedparser 的 *_parsed 一律是 UTC，必须显式打上时区，否则会被当成本地时间
            return iso(datetime(*tm[:6], tzinfo=timezone.utc))
    return iso(now_cst())


def fetch_rss(src: dict, cfg: dict) -> list[dict[str, Any]]:
    """抓取一组 RSS，返回统一结构的文章列表。"""
    s = _session(cfg["fetch"]["user_agent"])
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for feed_url in src.get("feeds", []):
        try:
            resp = s.get(feed_url, timeout=cfg["fetch"]["timeout"])
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as exc:  # noqa: BLE001
            log(f"  ! [{src['name']}] feed 失败 {feed_url}: {_err(exc)}")
            continue

        for entry in feed.entries:
            link = (entry.get("link") or "").strip()
            title = clean_text(strip_html(entry.get("title") or ""))
            if not link or not title or link in seen:
                continue
            seen.add(link)

            body_paras: list[str] = []
            if src.get("full_text"):
                # 极少数开放授权的源才使用 content 字段
                body_paras = paragraphs(entry.get("content", [{}])[0].get("value", "")
                                        if entry.get("content") else "")

            out.append(
                {
                    "id": make_id(src["id"], link, title),
                    "source": src["id"],
                    "source_name": src["name"],
                    "source_name_en": src["name_en"],
                    "color": src.get("color", "#444"),
                    "section": clean_text(entry.get("tags", [{}])[0].get("term", "") if entry.get("tags") else ""),
                    "url": link,
                    "title_en": title,
                    "excerpt_en": clean_text(strip_html(entry.get("summary") or entry.get("description") or ""))[:600],
                    "body_en": body_paras,
                    "image": _pick_image(entry),
                    "published": _published(entry),
                    "fetched_at": iso(now_cst()),
                    "is_demo": False,
                }
            )
    return out


def fetch_guardian(src: dict, cfg: dict, limit: int = 3) -> list[dict[str, Any]]:
    """卫报开放平台：官方 API，可获取正文（非商用 + 署名 + 回链）。"""
    import os

    key = os.environ.get("GUARDIAN_API_KEY", "").strip()
    if not key:
        log(f"  · [{src['name']}] 未配置 GUARDIAN_API_KEY → 降级为 RSS（只能拿到导语段，不是全文）")
        feeds = [f"https://www.theguardian.com/{sec}/rss" for sec in src.get("sections", ["world"])]
        return fetch_rss({**src, "feeds": feeds or [src.get("fallback_rss", "https://www.theguardian.com/world/rss")]}, cfg)

    s = _session(cfg["fetch"]["user_agent"])
    out: list[dict[str, Any]] = []
    per = max(1, -(-limit // max(1, len(src["sections"]))))  # 向上取整

    for section in src.get("sections", ["world"]):
        params = {
            "api-key": key,
            "section": section,
            "page-size": per,
            "order-by": "newest",
            "show-fields": "headline,trailText,body,bodyText,byline,thumbnail",
        }
        try:
            resp = s.get(GUARDIAN_API, params=params, timeout=cfg["fetch"]["timeout"])
            resp.raise_for_status()
            results = resp.json().get("response", {}).get("results", [])
        except Exception as exc:  # noqa: BLE001
            log(f"  ! [{src['name']}] API 失败 section={section}: {_err(exc)}")
            continue

        for item in results:
            fields = item.get("fields", {})
            # 优先用 HTML 版正文（有 <p> 段落结构）；bodyText 是一整块无换行文本，只在兜底时用
            body = paragraphs(fields.get("body", "") or fields.get("bodyText", ""))
            if len(body) < 2:
                continue
            out.append(
                {
                    "id": make_id(src["id"], item["webUrl"], item["webTitle"]),
                    "source": src["id"],
                    "source_name": src["name"],
                    "source_name_en": src["name_en"],
                    "color": src.get("color", "#052962"),
                    "section": item.get("sectionName", section),
                    "url": item["webUrl"],
                    "title_en": clean_text(item["webTitle"]),
                    "excerpt_en": clean_text(fields.get("trailText", ""))[:600],
                    "body_en": body[:22],
                    "image": fields.get("thumbnail", ""),
                    "published": item.get("webPublicationDate") or iso(now_cst()),
                    "fetched_at": iso(now_cst()),
                    "is_demo": False,
                }
            )
        time.sleep(0.4)
    return out


def upgrade_with_fulltext(items: list[dict], src: dict, cfg: dict) -> list[dict]:
    """把早先用 RSS 抓到、只有导语的卫报文章，按文章路径回查 API 补成全文。"""
    import os

    key = os.environ.get("GUARDIAN_API_KEY", "").strip()
    if not key or src.get("type") != "guardian_api":
        return []

    targets: dict[str, dict] = {}
    for it in items:
        if src["id"] != it.get("source") or (it.get("body_en") or []):
            continue
        path = it["url"].split("theguardian.com/")[-1].strip("/")
        if path:
            targets[path] = it
    if not targets:
        return []

    log(f"  · 回查 {len(targets)} 篇旧卫报文章的全文")
    sess = _session(cfg["fetch"]["user_agent"])
    out: list[dict] = []
    keys = list(targets.keys())
    for i in range(0, len(keys), 40):
        chunk = keys[i : i + 40]
        try:
            resp = sess.get(
                GUARDIAN_API,
                params={
                    "api-key": key,
                    "ids": ",".join(chunk),
                    "page-size": 40,
                    "show-fields": "headline,trailText,body,byline,thumbnail",
                },
                timeout=cfg["fetch"]["timeout"],
            )
            resp.raise_for_status()
            results = resp.json().get("response", {}).get("results", [])
        except Exception as exc:  # noqa: BLE001
            log(f"  ! 回查失败：{_err(exc)}")
            continue
        for item in results:
            old = targets.get(item.get("id", ""))
            if not old:
                continue
            fields = item.get("fields", {})
            body = paragraphs(fields.get("body", ""))
            if len(body) < 2:
                continue
            new = dict(old)
            new["body_en"] = body[:22]
            new["excerpt_en"] = clean_text(fields.get("trailText", "")) or old.get("excerpt_en", "")
            new["image"] = fields.get("thumbnail", "") or old.get("image", "")
            new["enrich"] = None            # 原文变了，旧译文作废
            new["enrich_attempted"] = False
            out.append(new)
    log(f"  · 成功补全 {len(out)} 篇")
    return out


def collect(cfg: dict, existing_ids: set[str]) -> list[dict[str, Any]]:
    """遍历所有源，去重后按时间排序，截断到 total_cap。"""
    all_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    cap = cfg["fetch"]["total_cap"]
    per = cfg["fetch"]["max_per_source"]

    for src in cfg["sources"]:
        if src.get("enabled", True) is False:
            log(f"· 跳过 {src['name']}（已在 config.yaml 中禁用）")
            continue
        limit = int(src.get("max_items", per))
        log(f"→ 抓取 {src['name']} ({src['name_en']})")
        try:
            if src["type"] == "guardian_api":
                items = fetch_guardian(src, cfg, limit)
            else:
                items = fetch_rss(src, cfg)
        except Exception as exc:  # noqa: BLE001
            log(f"  ! {src['name']} 整体失败: {exc}")
            items = []

        fresh = []
        for it in items:
            if it["id"] in existing_ids or it["url"] in seen:
                continue
            seen.add(it["url"])
            fresh.append(it)
            if len(fresh) >= limit:
                break
        log(f"  · 新文章 {len(fresh)} 篇")
        all_items.extend(fresh)

    # 有全文的优先占额度：全文文章的学习价值远高于摘要，不能被日期挤掉
    all_items.sort(key=lambda x: (bool(x.get("body_en")), x["published"]), reverse=True)
    return all_items[:cap]
