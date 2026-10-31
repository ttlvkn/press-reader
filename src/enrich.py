"""翻译与讲解生成：调用 OpenAI 兼容接口（DeepSeek / 通义 / Kimi / GLM / OpenAI 均可）。

输出严格 JSON，包含：中文标题、中文摘要、逐段对照翻译、重点词汇、长难句解析、背景说明。
结果按内容哈希缓存在 data/cache/，重复运行不会重复计费。
"""
from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from .util import DATA, log, sha1

PROMPT_VERSION = "v3"
CACHE = DATA / "cache"

SYSTEM_PROMPT = """你是一位资深的英语外刊精读教师，服务对象是中国的英语学习者（大学六级到考研/雅思 7 分水平）。
你的任务是把一篇英文外刊文章加工成高质量的对照精读材料。

要求：
1. 翻译必须准确、通顺、符合中文表达习惯，不要逐字硬译，也不要意译到失真。专有名词保留英文原名或使用通行译名。
2. 逐段对照：输入有几段，输出就有几段，顺序完全一致，en 字段尽量保留原文（只修正明显的断行、多余空格）。
3. 词汇讲解只挑真正影响理解的词，优先「熟词僻义」和「外刊高频表达」，不要挑 the / of 这类词。
4. 长难句解析要指出句子主干（主谓宾）、修饰成分挂在哪里、以及为什么这样写。
5. 背景说明解释文章中读者可能不熟悉的事件、机构、制度、文化背景，不要复述文章内容。
6. 全部输出使用简体中文（英文原文、词汇、例句除外）。
7. 输入可能是全文、也可能是该刊 RSS 只放出的摘要或一句话提要。**只依据给出的英文材料**，绝不能编造文章里没有的观点、数据或引述；材料越少，讲解就要越克制，把力气放在标题与提要本身的用词和背景上。

只输出 JSON，不要任何解释文字、不要 markdown 代码块。JSON 结构：
{
  "title_zh": "中文标题",
  "summary_zh": "80-120 字中文摘要，说清文章讲了什么、结论是什么",
  "difficulty": "B2 或 C1 或 C2",
  "paragraphs": [{"en": "英文段落", "zh": "中文翻译"}],
  "vocab": [{"word": "词或短语", "phonetic": "/音标/", "pos": "词性缩写", "meaning_zh": "在此语境下的中文释义", "example_en": "英文例句", "example_zh": "例句翻译"}],
  "sentences": [{"en": "原句", "structure": "主干：... / 修饰：...", "analysis_zh": "解析"}],
  "background": [{"title": "小标题", "text_zh": "说明"}]
}"""

USER_TMPL = """刊物：{source}
栏目：{section}
标题：{title}

{kind_hint}
（共 {count} 段，请严格按此数量和顺序输出对照翻译）
{body}

请输出 JSON。{quota}"""


KIND_HINT = {
    "full": "下面是文章正文，请做逐段对照精读。",
    "digest": "下面只有该刊 RSS 放出的摘要（不是全文），请做「摘要精读」：把这段摘要讲透，不要假设或补充文章里没有的内容。",
    "blurb": (
        "该刊只开放标题和一句提要，没有正文。请把这一句提要讲透——"
        "它通常是编辑写的 standfirst，句子精炼、常有修辞，很值得学。"
        "背景说明只讲与该主题相关的事实性知识（人物、机构、事件），严禁推测文章的论点。"
    ),
}

KIND_QUOTA = {
    "full": "vocab 给 6-10 个，sentences 给 2-3 个，background 给 1-2 条。",
    "digest": "vocab 给 4-6 个，sentences 给 1-2 个，background 给 1-2 条。",
    "blurb": "vocab 给 3-5 个，sentences 最多 1 个（提要本身没什么可拆就留空数组），background 给 1 条足够。",
}


def detect_kind(article: dict) -> str:
    """按可用英文材料的多少决定讲解深度，避免对一句话的提要硬写出「全文精读」。"""
    body = [p for p in (article.get("body_en") or []) if p.strip()]
    if len(body) >= 3:
        return "full"
    words = len((article.get("excerpt_en") or "").split())
    return "digest" if words >= 15 else "blurb"


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("模型未返回合法 JSON")


def _call_llm(cfg: dict, messages: list[dict], key: str) -> dict:
    llm = cfg["llm"]
    url = llm["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": llm["model"],
        "messages": messages,
        "temperature": llm.get("temperature", 0.3),
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=llm.get("timeout", 180))
            if resp.status_code == 400 and "response_format" in resp.text:
                payload.pop("response_format", None)  # 部分服务商不支持 JSON mode
                continue
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return _extract_json(content)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"LLM 调用失败: {last_err}")


def _normalize(raw: dict, paras: list[str], excerpt: str) -> dict:
    """校验并对齐模型输出，尽量保证页面不会因为模型跑偏而崩。"""
    out_paras: list[dict] = []
    model_paras = raw.get("paragraphs") or []
    if len(model_paras) == len(paras):
        for en, item in zip(paras, model_paras):
            out_paras.append({"en": en, "zh": (item.get("zh") or "").strip()})
    else:
        # 段落数对不上：以原文为准，逐段对齐能对的
        for i, en in enumerate(paras):
            zh = (model_paras[i].get("zh") if i < len(model_paras) else "") or ""
            out_paras.append({"en": en, "zh": zh.strip()})
    if not out_paras and excerpt:
        out_paras = [{"en": excerpt, "zh": (raw.get("summary_zh") or "").strip()}]

    vocab = []
    for v in (raw.get("vocab") or [])[:12]:
        if not isinstance(v, dict) or not v.get("word"):
            continue
        vocab.append(
            {
                "word": str(v.get("word", "")).strip(),
                "phonetic": str(v.get("phonetic", "") or "").strip(),
                "pos": str(v.get("pos", "") or "").strip(),
                "meaning_zh": str(v.get("meaning_zh", "") or "").strip(),
                "example_en": str(v.get("example_en", "") or "").strip(),
                "example_zh": str(v.get("example_zh", "") or "").strip(),
            }
        )

    sentences = []
    for s in (raw.get("sentences") or [])[:4]:
        if not isinstance(s, dict) or not s.get("en"):
            continue
        sentences.append(
            {
                "en": str(s.get("en", "")).strip(),
                "structure": str(s.get("structure", "") or "").strip(),
                "analysis_zh": str(s.get("analysis_zh", "") or "").strip(),
            }
        )

    background = []
    for b in (raw.get("background") or [])[:3]:
        if not isinstance(b, dict):
            continue
        background.append(
            {
                "title": str(b.get("title", "") or "背景").strip(),
                "text_zh": str(b.get("text_zh", "") or "").strip(),
            }
        )

    words = sum(len(p["en"].split()) for p in out_paras)
    return {
        "title_zh": str(raw.get("title_zh", "") or "").strip(),
        "summary_zh": str(raw.get("summary_zh", "") or "").strip(),
        "difficulty": str(raw.get("difficulty", "") or "").strip().upper()[:3],
        "read_minutes": max(2, round(words / 200)) if words else 3,
        "paragraphs": out_paras,
        "vocab": vocab,
        "sentences": sentences,
        "background": background,
    }


def enrich_one(article: dict, cfg: dict) -> dict | None:
    llm = cfg["llm"]
    key = os.environ.get(llm["api_key_env"], "").strip()
    if not llm.get("enabled", True) or not key:
        return None

    kind = detect_kind(article)
    body_src = [p for p in (article.get("body_en") or []) if p.strip()] if kind == "full" else []
    text = "\n\n".join(body_src) if body_src else article.get("excerpt_en", "")
    text = text[:9000]
    if len(text.split()) < 5:
        return None

    paras = body_src if body_src else [article.get("excerpt_en", "")]
    paras = [p for p in paras if p.strip()]

    cache_key = sha1(f"{PROMPT_VERSION}|{kind}|{llm['model']}|{article['id']}|{text}")
    cache_file = CACHE / f"{cache_key}.json"
    if cache_file.exists():
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        return _normalize(cached, paras, article.get("excerpt_en", ""))

    user = USER_TMPL.format(
        source=article["source_name_en"],
        section=article.get("section") or "—",
        title=article["title_en"],
        kind_hint=KIND_HINT[kind],
        quota=KIND_QUOTA[kind],
        count=len(paras),
        body="\n\n".join(f"[{i+1}] {p}" for i, p in enumerate(paras)),
    )
    raw = _call_llm(
        cfg,
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
        key,
    )
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return _normalize(raw, paras, article.get("excerpt_en", ""))


def enrich_all(articles: list[dict], cfg: dict) -> list[dict]:
    llm = cfg["llm"]
    key = os.environ.get(llm["api_key_env"], "").strip()
    if not llm.get("enabled", True) or not key:
        log(f"! 未设置 {llm['api_key_env']}，跳过翻译讲解，站点将以「原文模式」生成")
        for a in articles:
            a["enrich"] = None
            a["kind"] = detect_kind(a)
            a["digest"] = a["kind"] != "full"
        return articles

    log(f"→ 开始生成翻译与讲解（{len(articles)} 篇，模型 {llm['model']}）")
    workers = max(1, int(llm.get("concurrency", 4)))
    ok = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(enrich_one, a, cfg): a for a in articles}
        for fut in as_completed(futures):
            art = futures[fut]
            try:
                art["enrich"] = fut.result()
                art["kind"] = detect_kind(art)
                art["digest"] = art["kind"] != "full"
                if art["enrich"]:
                    ok += 1
                    log(f"  ✓ {art['title_en'][:58]}")
                else:
                    log(f"  - 跳过（有效内容不足 5 词）：{art['title_en'][:50]}")
            except Exception as exc:  # noqa: BLE001
                art["enrich"] = None
                art["kind"] = detect_kind(art)
                art["digest"] = art["kind"] != "full"
                log(f"  ✗ 失败 {art['title_en'][:40]}：{exc}")
    log(f"→ 完成 {ok}/{len(articles)} 篇")
    return articles
