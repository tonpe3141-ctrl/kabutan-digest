"""日経平均（日経225）の構成銘柄を、日経公式の指数サイトから取得する。

  https://indexes.nikkei.co.jp/nkave/index/component?idx=nk225

このページは業種区分ごとに表が分かれており、各行が
[コード, 銘柄名（略称）, 社名] になっている。株価は載っていないので、
値動きは CNBC 側（{コード}.T）で別途取得して突き合わせる。

構成銘柄は年に数回しか入れ替わらないので、取得できなかったときのために
前回の内容を docs/data 配下に残しておき、そこから読み直す。
"""
import json
import os

from bs4 import BeautifulSoup

from ..config import DATA_DIR
from ..http import get_text

COMPONENT_URL = "https://indexes.nikkei.co.jp/nkave/index/component?idx=nk225"
CACHE_PATH = os.path.join(DATA_DIR, "nikkei225.json")

CODE_LEN = 4


def _parse(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    sector = None

    # 文書を上から辿り、直近に現れた見出しをその後の表の業種として扱う
    for el in soup.find_all(["h1", "h2", "h3", "h4", "div", "table"]):
        if el.name == "table":
            for tr in el.find_all("tr"):
                cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) < 2:
                    continue
                code = cells[0]
                if len(code) != CODE_LEN or not code[0].isdigit():
                    continue          # ヘッダ行など
                out.append({"code": code, "name": cells[1],
                            "full_name": cells[2] if len(cells) > 2 else None,
                            "sector": sector})
        else:
            text = el.get_text(strip=True)
            # 業種名は短い見出し。長い文章や空要素は無視する
            if el.name in ("h1", "h2", "h3", "h4") and 2 <= len(text) <= 20:
                sector = text
            elif el.name == "div" and el.get("class"):
                cls = " ".join(el.get("class"))
                if "category" in cls.lower() and 2 <= len(text) <= 20:
                    sector = text

    # 同じ銘柄が複数回拾われることがあるので、最初の1件だけ残す
    seen, uniq = set(), []
    for item in out:
        if item["code"] in seen:
            continue
        seen.add(item["code"])
        uniq.append(item)
    return uniq


def _load_cache() -> list[dict]:
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f).get("components", [])
    except (OSError, json.JSONDecodeError):
        return []


def _save_cache(components: list[dict]) -> None:
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"count": len(components), "components": components},
                  f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, CACHE_PATH)


def fetch_components(min_expected: int = 200) -> list[dict]:
    """構成銘柄を返す。取得や解析に失敗したら前回の内容を使う。"""
    html = get_text(COMPONENT_URL, timeout=25)
    if html:
        components = _parse(html)
        if len(components) >= min_expected:
            sectors = len({c["sector"] for c in components if c["sector"]})
            print(f"    ✅ 日経225 構成銘柄: {len(components)}銘柄 / {sectors}業種")
            _save_cache(components)
            return components
        print(f"    ⚠️  構成銘柄の解析結果が {len(components)}件 と少なすぎます")
    else:
        print("    ⚠️  構成銘柄ページを取得できません")

    cached = _load_cache()
    if cached:
        print(f"    ↩︎ 前回の構成銘柄を使用: {len(cached)}銘柄")
    return cached
