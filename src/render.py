"""把 JSON 数据渲染成静态站点（纯 HTML/CSS/JS，无需框架，GitHub Pages 可直接托管）。"""
from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from .lexicon import Lexicon, build_for_article
from .util import DATA, ROOT, SITE, CST, log, read_json, zh_date


# ---------- 数据装载 ----------

def load_articles(cfg: dict) -> list[dict]:
    keep_days = int(cfg["fetch"].get("days_keep", 45))
    cutoff = datetime.now(CST) - timedelta(days=keep_days)
    by_id: dict[str, dict] = {}

    for path in sorted((DATA / "enriched").glob("*.json")):
        items = read_json(path, []) or []
        for art in items:
            if not isinstance(art, dict) or not art.get("id"):
                continue
            try:
                pub = datetime.fromisoformat(art.get("published", ""))
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=CST)
            except (ValueError, TypeError):
                pub = datetime.now(CST)
            if pub < cutoff:
                continue
            art["_dt"] = pub
            # 「今日」按抓取日归档：外刊什么时候发是它的事，用户关心的是今天拿到了什么
            fetched = art.get("fetched_at")
            art["_day"] = (fetched[:10] if isinstance(fetched, str) and len(fetched) >= 10
                           else path.stem)
            by_id[art["id"]] = art

    arts = sorted(by_id.values(), key=lambda a: a["_dt"], reverse=True)
    return arts


# ---------- 词汇高亮 ----------

PUNCT_SPACE = re.compile(r"\s+([,.;:!?%)\]])")


def highlight(text: str, vocab: list[dict] | None) -> Markup:
    # 老数据里存了 "budget ." 这种标点前空格，渲染时兜底修一下
    text = PUNCT_SPACE.sub(r"\1", text or "")
    if not vocab:
        return Markup(escape(text))
    spans: list[tuple[int, int, dict]] = []
    used = [False] * len(text)
    for v in sorted(vocab, key=lambda x: -len(x.get("word", ""))):
        w = (v.get("word") or "").strip()
        if len(w) < 3:
            continue
        for m in re.finditer(r"\b" + re.escape(w) + r"(\b|s\b|es\b|ed\b|ing\b)", text, re.I):
            s, e = m.span()
            if any(used[s:e]):
                continue
            for i in range(s, e):
                used[i] = True
            spans.append((s, e, v))
    spans.sort(key=lambda x: x[0])

    out, pos = [], 0
    for s, e, v in spans:
        out.append(escape(text[pos:s]))
        tip = f"{v.get('pos', '')} {v.get('meaning_zh', '')}".strip()
        out.append(
            f'<b class="vw" tabindex="0" data-tip="{escape(tip)}">{escape(text[s:e])}</b>'
        )
        pos = e
    out.append(escape(text[pos:]))
    return Markup("".join(out))


# ---------- 词典负载 ----------

def _trim_lex(lexmap: dict) -> dict:
    """压缩内嵌词条体积：每条最多 2 个义项、每个 1 条例句。"""
    out = {}
    for w, e in lexmap.items():
        out[w] = {
            "p": e.get("p", ""),
            "s": e.get("s", 0),
            "src": e.get("src", ""),
            "e": [
                {"pos": x.get("pos", ""), "tip": x.get("tip", ""), "en": x.get("en", ""),
                 "zh": x.get("zh", ""), "ex": (x.get("ex") or [])[:1]}
                for x in (e.get("e") or [])[:2]
            ],
            "d": (e.get("d") or [])[:3],
        }
    return out


def _notes(a: dict) -> dict:
    """把大模型给的重点词讲解挂进同一个查询面板。"""
    out = {}
    for v in ((a.get("enrich") or {}).get("vocab") or []):
        w = (v.get("word") or "").strip().lower()
        if not w:
            continue
        out[w] = {
            "pos": v.get("pos", ""),
            "zh": v.get("meaning_zh", ""),
            "ph": v.get("phonetic", ""),
            "ex_en": v.get("example_en", ""),
            "ex_zh": v.get("example_zh", ""),
        }
    return out


# ---------- 渲染 ----------

def build(cfg: dict) -> dict:
    site_cfg = cfg["site"]
    env = Environment(
        loader=FileSystemLoader(str(ROOT / "templates")),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["zh_date"] = zh_date
    env.filters["hl"] = highlight

    arts = load_articles(cfg)
    if not arts:
        log("! 没有任何文章数据，先运行 python run.py all")
        return {"count": 0}

    SITE.mkdir(parents=True, exist_ok=True)
    art_dir = SITE / "a"
    art_dir.mkdir(exist_ok=True)

    base = (site_cfg.get("base_url") or "").rstrip("/")
    if not base:
        base = ""

    # 按日期分组
    by_date: dict[str, list[dict]] = defaultdict(list)
    for a in arts:
        by_date[a["_day"]].append(a)
    dates = sorted(by_date.keys(), reverse=True)
    today_key = dates[0]

    common = {
        "site": site_cfg,
        "base": base,
        "sources": cfg["sources"],
        "total": len(arts),
        "updated_at": datetime.now(CST).strftime("%Y-%m-%d %H:%M"),
        "has_translation": any(a.get("enrich") for a in arts),
    }

    # 词典：为每篇文章准备好点词查询的词条
    lex = Lexicon(cfg)
    lex_cap = int(cfg.get("lexicon", {}).get("max_words_per_article", 170))
    log(f"→ 准备词典（已缓存 {len(lex.data)} 词）")
    for a in arts:
        paras = ((a.get("enrich") or {}).get("paragraphs") or [])
        texts = [p.get("en", "") for p in paras] or ([a.get("excerpt_en", "")] if a.get("excerpt_en") else [])
        try:
            a["_lex"] = build_for_article(lex, texts, cap=lex_cap)
        except Exception as exc:  # noqa: BLE001
            log(f"  ! 词典失败 {a.get('id')}: {exc}")
            a["_lex"] = {}

    # 文章页
    for i, a in enumerate(arts):
        a["slug"] = a["id"]
        prev_a = arts[i + 1] if i + 1 < len(arts) else None
        next_a = arts[i - 1] if i > 0 else None

        payload = {"w": _trim_lex(a.get("_lex") or {}), "n": _notes(a)}
        html = env.get_template("article.html").render(
            **common,
            a=a,
            prev=prev_a,
            next=next_a,
            page_title=a["title_en"],
            root="..",
            lex_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
        (art_dir / f"{a['slug']}.html").write_text(html, encoding="utf-8")

    # 首页：最新一期 + 最近几天
    recent_dates = dates[:5]
    html = env.get_template("index.html").render(
        **common,
        today=by_date[today_key],
        today_key=today_key,
        recent=[(d, by_date[d]) for d in recent_dates[1:]],
        page_title=site_cfg["subtitle"],
        is_index=True,
        root=".",
    )
    (SITE / "index.html").write_text(html, encoding="utf-8")

    # 归档页
    html = env.get_template("archive.html").render(
        **common,
        groups=[(d, by_date[d]) for d in dates],
        page_title="全部归档",
        root=".",
    )
    (SITE / "archive.html").write_text(html, encoding="utf-8")

    # 关于页
    html = env.get_template("about.html").render(**common, page_title="关于", root=".")
    (SITE / "about.html").write_text(html, encoding="utf-8")

    # 搜索索引（客户端全文标题检索用）
    index = [
        {
            "i": a["id"],
            "t": a["title_en"],
            "z": (a.get("enrich") or {}).get("title_zh", ""),
            "s": a["source"],
            "d": a["_day"][5:],
            "v": len((a.get("enrich") or {}).get("vocab", [])),
        }
        for a in arts
    ]
    (SITE / "search-index.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8"
    )

    # 静态资源
    shutil.copytree(ROOT / "assets", SITE / "assets", dirs_exist_ok=True)

    # PWA manifest
    manifest = {
        "name": f"{site_cfg['title']} · {site_cfg['subtitle']}",
        "short_name": site_cfg["title"],
        "start_url": f"{base}/index.html" if base else "./index.html",
        "scope": f"{base}/" if base else "./",
        "display": "standalone",
        "background_color": "#0f1115",
        "theme_color": "#0f1115",
        "lang": "zh-CN",
        "icons": [
            {"src": f"{base}/assets/icon.svg" if base else "assets/icon.svg",
             "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}
        ],
    }
    (SITE / "manifest.webmanifest").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Service Worker 版本号随构建变化，保证手机端能拿到新内容
    sw = (ROOT / "assets" / "sw.js").read_text(encoding="utf-8")
    ver = datetime.now(CST).strftime("%Y%m%d%H%M%S")
    (SITE / "sw.js").write_text(sw.replace("__VERSION__", ver), encoding="utf-8")

    # 让 GitHub Pages 不去跑 Jekyll
    (SITE / ".nojekyll").write_text("", encoding="utf-8")

    log(f"✓ 站点已生成：{SITE}（{len(arts)} 篇）")
    return {"count": len(arts), "dates": len(dates)}
