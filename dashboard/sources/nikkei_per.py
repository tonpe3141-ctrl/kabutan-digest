"""日経平均の PER（日経公式）。予想EPS（= 指数 ÷ 指数ベースPER）の変化で「業績」を測る。

  https://indexes.nikkei.co.jp/nkave/archives/data?list=per

ページには当月の日次（日付・加重平均・指数ベース）しか載らない（年月の指定は効かない。
2026-09-23 に Actions で実測）。そのため取れた値を docs/data/cache/nikkei_per.json に
積み上げ、月をまたいだ比較はキャッシュから行う。
"""
import json
import os
import re

from bs4 import BeautifulSoup

from ..config import PER_CACHE_PATH
from ..http import get_text

URL = "https://indexes.nikkei.co.jp/nkave/archives/data?list=per"


def parse(html: str) -> dict[str, float]:
    """{YYYY-MM-DD: 指数ベースPER}。表の列は [日付, 加重平均(倍), 指数ベース(倍)]。"""
    out: dict[str, float] = {}
    soup = BeautifulSoup(html or "", "html.parser")
    for table in soup.find_all("table"):
        head = [c.get_text(strip=True) for c in (table.find("tr") or soup.new_tag("tr")).find_all(["th", "td"])]
        idx = next((i for i, h in enumerate(head) if "指数ベース" in h), 2)
        for tr in table.find_all("tr")[1:]:
            cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
            if len(cells) <= idx:
                continue
            m = re.match(r"(\d{4})[./-](\d{1,2})[./-](\d{1,2})", cells[0])
            if not m:
                continue
            try:
                per = float(cells[idx].replace(",", ""))
            except ValueError:
                continue
            if per > 0:
                out[f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"] = per
    return out


def load_cache(path: str = PER_CACHE_PATH) -> dict[str, float]:
    try:
        with open(path, encoding="utf-8") as f:
            return {k: float(v) for k, v in (json.load(f).get("per") or {}).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def update(path: str = PER_CACHE_PATH, keep: int = 400) -> dict[str, float]:
    """当月分を取ってキャッシュに足す。取れなくてもキャッシュを返す。"""
    cache = load_cache(path)
    html = get_text(URL, timeout=20)
    fresh = parse(html) if html else {}
    if fresh:
        cache.update(fresh)
        print(f"    ✅ 日経平均PER: {len(fresh)} 日分（最新 {max(fresh)} {fresh[max(fresh)]:.2f}倍）")
        keys = sorted(cache)[-keep:]
        cache = {k: cache[k] for k in keys}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"source": URL, "per": cache}, f, ensure_ascii=False, indent=0)
        os.replace(tmp, path)
    else:
        print("    ⚠️  日経平均PER を取得できません（キャッシュを使用）")
    return cache
