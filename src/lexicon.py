"""词典层：为文章里的每个词取「柯林斯英汉双解」词条。

数据源说明
----------
柯林斯官方（api.collinsdictionary.com）需要商业授权，且只提供英文释义。
国内可用的柯林斯双解内容由有道词典提供公开查询接口，返回的 `collins` 字段即完整的
柯林斯词条：星级、词性、英文释义、中文释义、双语例句。

因此默认 provider = "youdao"，取 collins 块优先，缺失时退回简明英汉释义（ec 块）。
若你拿到了柯林斯官方授权 key，把 provider 改成 "collins_official" 即可切换（见 fetch_collins_official）。

所有词条写入 data/lexicon.json 长期缓存，同一个词只查一次。
"""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from .util import DATA, log, read_json, write_json

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]{2,}")
TAG_RE = re.compile(r"<[^>]+>")

STOP = set(
    """the a an and or but if of to in on at by for with from as is are was were be been being
    this that these those it its he she they we you i not no nor so than then there their them
    has have had do does did can could will would shall should may might must about into over
    under after before between during such other more most some any each both few many own same
    too very just also only even still while when where what which who whom whose how why all
    out up down off again further once here because through against among within without upon
    would could should one two three first last new said says say like make made get got go going
    went come came take took see seen know knew think thought want need use used way even back
    thing things people time times year years day days now then well much back still yet ever
    never always often sometimes every another something anything nothing everything
    """.split()
)


def _strip(s: str) -> str:
    return TAG_RE.sub("", s or "").replace("\u200b", "").strip()


def _split_en_zh(text: str) -> tuple[str, str]:
    """柯林斯释义形如 'Arithmetic is the part of ... numbers. 算术'，
    把结尾的中文译义切出来。"""
    text = _strip(text)
    m = re.search(r"([\u4e00-\u9fff][^\x00-\x7f]*)$", text)
    if m and m.start() > 0:
        en = text[: m.start()].strip()
        zh = m.group(1).strip()
        return en, zh
    return text, ""


def _as_list(x) -> list:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def parse_youdao(word: str, d: dict) -> dict | None:
    collins = d.get("collins") or {}
    ec = d.get("ec") or {}
    ec_word = (ec.get("word") or [{}])[0] if isinstance(ec.get("word"), list) else {}

    phonetic = collins.get("phonetic") or ec_word.get("usphone") or ec_word.get("ukphone") or ""
    uk = ec_word.get("ukphone") or ""
    star = 0
    for ce in _as_list(collins.get("collins_entries")):
        raw = (ce or {}).get("star") or collins.get("star")
        try:
            star = max(star, int(raw or 0))
        except (TypeError, ValueError):
            pass

    entries: list[dict] = []
    for ce in _as_list(collins.get("collins_entries")):
        for entry in _as_list((ce or {}).get("entries")):
            for te in _as_list((entry or {}).get("entry")):
                if not isinstance(te, dict):
                    continue
                for tr in _as_list(te.get("tran_entry")):
                    if not isinstance(tr, dict):
                        continue
                    pos_info = tr.get("pos_entry") or {}
                    def_en, def_zh = _split_en_zh(tr.get("tran", ""))
                    examples = []
                    for s in _as_list((tr.get("exam_sents") or {}).get("sent"))[:2]:
                        if isinstance(s, dict) and s.get("eng_sent"):
                            examples.append(
                                {"en": _strip(s.get("eng_sent")), "zh": _strip(s.get("chn_sent"))}
                            )
                    if not (def_en or def_zh):
                        continue
                    entries.append(
                        {
                            "pos": (pos_info.get("pos") or "").strip(),
                            "tip": (pos_info.get("pos_tips") or "").strip(),
                            "en": def_en,
                            "zh": def_zh,
                            "ex": examples,
                        }
                    )

    simple = []
    for tr in _as_list((ec_word or {}).get("trs")):
        for item in _as_list((tr or {}).get("tr")):
            for line in _as_list(((item or {}).get("l") or {}).get("i")):
                text = _strip(line if isinstance(line, str) else "")
                if text:
                    simple.append(text)

    if not entries and not simple:
        return None

    return {
        "w": word,
        "p": phonetic,
        "uk": uk,
        "s": star,
        "src": "柯林斯" if entries else "英汉",
        "e": entries[:4],
        "d": simple[:4],
    }


def fetch_youdao(word: str, timeout: int = 12) -> dict | None:
    url = "https://dict.youdao.com/jsonapi"
    try:
        r = requests.get(
            url,
            params={"q": word, "dicts": '{"count":99,"dicts":[["ec","collins"]]}'},
            headers={"User-Agent": UA, "Referer": "https://dict.youdao.com/"},
            timeout=timeout,
        )
        r.raise_for_status()
        return parse_youdao(word, r.json())
    except Exception:  # noqa: BLE001
        return None


def fetch_collins_official(word: str, key: str, timeout: int = 12) -> dict | None:
    """如果你有柯林斯官方授权（api.collinsdictionary.com），走这里。
    返回结构对齐 parse_youdao 的输出（但只有英文释义）。"""
    try:
        r = requests.get(
            f"https://api.collinsdictionary.com/api/v1/dictionaries/collins/entries/{word}",
            headers={"accessKey": key, "Accept": "application/json"},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
        entries = []
        for e in (data.get("entries") or [])[:4]:
            for sense in (e.get("senses") or [])[:2]:
                entries.append(
                    {
                        "pos": (e.get("partOfSpeech") or "").strip(),
                        "tip": "",
                        "en": _strip(sense.get("definition") or ""),
                        "zh": "",
                        "ex": [],
                    }
                )
        if not entries:
            return None
        return {"w": word, "p": "", "uk": "", "s": 0, "src": "Collins", "e": entries, "d": []}
    except Exception:  # noqa: BLE001
        return None


class Lexicon:
    """词条缓存。data/lexicon.json 跨天累积，词只查一次。"""

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or {}
        self.path = DATA / "lexicon.json"
        self.data: dict[str, dict] = read_json(self.path, {}) or {}
        self._lock = threading.Lock()
        self._fetched = 0
        self._budget = int(self.cfg.get("lexicon", {}).get("max_new_words_per_run", 900))
        self.provider = self.cfg.get("lexicon", {}).get("provider", "youdao")

    # ---------- 查询 ----------
    def has(self, word: str) -> bool:
        return word.lower() in self.data

    def get(self, word: str) -> dict | None:
        return self.data.get(word.lower())

    # ---------- 批量补齐 ----------
    def ensure(self, words: list[str], workers: int = 6) -> int:
        """把缺失的词补进缓存，返回本次新增数量。"""
        missing = [w for w in dict.fromkeys(w.lower() for w in words) if w not in self.data]
        if not missing:
            return 0

        missing = missing[: max(0, self._budget - self._fetched)]
        if not missing:
            log("  · 词典本次查询额度已用完，剩余生词留到下次")
            return 0

        log(f"  · 词典：{len(missing)} 个生词待查询")
        added = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._fetch_one, w): w for w in missing}
            for fut in as_completed(futures):
                w = futures[fut]
                try:
                    entry = fut.result()
                except Exception:  # noqa: BLE001
                    entry = None
                with self._lock:
                    # 查不到的也记空值，避免每天重复查同一个词
                    self.data[w] = entry if entry else {"w": w, "miss": True}
                    self._fetched += 1
                    if entry:
                        added += 1
        self.save()
        log(f"  · 词典：新增 {added} 条，未收录 {len(missing) - added} 个")
        return added

    def _fetch_one(self, word: str) -> dict | None:
        if self.provider == "collins_official":
            key = (self.cfg.get("lexicon", {}).get("collins_api_key") or "").strip()
            if key:
                return fetch_collins_official(word, key)
        entry = fetch_youdao(word)
        time.sleep(0.05)
        return entry

    def save(self) -> None:
        write_json(self.path, self.data)

    # ---------- 供页面内嵌 ----------
    def sub(self, words) -> dict:
        out = {}
        for w in words:
            e = self.get(w)
            if e and not e.get("miss"):
                out[w.lower()] = e
        return out


def tokenize(paragraphs: list[str]) -> list[str]:
    """抽出文章里值得查的实词，按出现频次降序。"""
    freq: dict[str, int] = {}
    for p in paragraphs:
        for m in WORD_RE.finditer(p or ""):
            w = m.group(0).lower().strip("'-")
            if len(w) < 3 or w in STOP:
                continue
            freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))]


def build_for_article(lex: Lexicon, paragraphs: list[str], cap: int = 170) -> dict:
    words = tokenize(paragraphs)[:cap]
    lex.ensure(words)
    return lex.sub(words)
