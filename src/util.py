"""通用工具函数。"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SITE = ROOT / "site"

CST = timezone(timedelta(hours=8))


def log(msg: str) -> None:
    print(msg, flush=True)


TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def strip_html(raw: str) -> str:
    """粗暴但安全地剥掉 HTML，用于 RSS 摘要。"""
    if not raw:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>", "\n", text, flags=re.I)
    text = TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return clean_text(text)


PUNCT_SPACE = re.compile(r"\s+([,.;:!?%)\]])")


def clean_text(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    text = WS_RE.sub(" ", text)
    # 剥标签时会留下 "budget ." 这种标点前的空格，顺手收掉
    text = PUNCT_SPACE.sub(r"\1", text)
    return text.strip()


SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'“‘(])")


def _split_long(text: str, limit: int = 700) -> list[str]:
    """有些接口（比如卫报 bodyText）会把整篇正文压成一整块，一个换行都没有。
    这时按句子攒段，避免整篇文章被当成 1 段。"""
    if len(text) <= limit:
        return [text]
    sentences = SENT_SPLIT.split(text)
    chunks, buf = [], ""
    for sen in sentences:
        if buf and len(buf) + len(sen) > limit:
            chunks.append(buf.strip())
            buf = sen
        else:
            buf = f"{buf} {sen}".strip()
    if buf:
        chunks.append(buf.strip())
    return chunks


def paragraphs(raw: str) -> list[str]:
    """把正文切成段落列表。"""
    if not raw:
        return []
    if "<p" in raw.lower():
        chunks = re.split(r"</p\s*>", raw, flags=re.I)
        out = [clean_text(strip_html(c)) for c in chunks]
    elif "\n\n" in raw:
        out = [clean_text(strip_html(c)) for c in re.split(r"\n\s*\n", raw)]
    else:
        out = [clean_text(strip_html(l)) for l in raw.split("\n")]
    out = [p for p in out if len(p) > 40]

    # 一段都没切出来或者只剩一整块巨长文本 → 按句子重新攒段
    if len(out) <= 1 and out and len(out[0]) > 700:
        out = _split_long(out[0])
    return out


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def make_id(source: str, url: str, title: str = "") -> str:
    base = url or title
    return f"{source}-{sha1(base)[:10]}"


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text)[:60] or "article"


def now_cst() -> datetime:
    return datetime.now(CST)


def iso(dt: datetime) -> str:
    return dt.astimezone(CST).isoformat(timespec="seconds")


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log(f"  ! 跳过损坏的 JSON: {path}")
        return default


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config(path: Path | None = None) -> dict:
    import yaml

    path = path or (ROOT / "config.yaml")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def load_env(path: Path | None = None) -> list[str]:
    """读取项目根目录的 .env，把里面的 KEY=VALUE 灌进环境变量。

    已经存在的环境变量优先（线上用 GitHub Secrets，本地用 .env，互不干扰）。
    """
    p = path or (ROOT / ".env")
    loaded: list[str] = []
    if not p.exists():
        return loaded
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        val = val.strip().strip('"').strip("'")
        if key and val and key not in os.environ:
            os.environ[key] = val
            loaded.append(key)
    return loaded


def zh_date(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str)
    except (ValueError, TypeError):
        return iso_str or ""
    return f"{dt.month} 月 {dt.day} 日"


def main_guard(fn):
    try:
        fn()
    except KeyboardInterrupt:
        sys.exit(130)
