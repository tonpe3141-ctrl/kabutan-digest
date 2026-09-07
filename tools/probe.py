"""データ源の疎通診断ツール（相場振り返り記事の本文抽出方法を確定）。"""
import json
import re
import sys

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "ja,en;q=0.9"}


def get(url, timeout=35):
    try:
        return requests.get(url, headers=HEADERS, timeout=timeout)
    except Exception as e:
        print(f"    例外: {e}")
        return None


def inspect(label, url):
    print(f"\n=== {label} ===\n    {url}")
    r = get(url)
    if r is None or r.status_code != 200:
        print(f"    status={r.status_code if r else 'なし'}")
        return
    html = r.text
    print(f"    len={len(html)}")

    m = re.search(r'__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>', html, re.S)
    if not m:
        print("    __PRELOADED_STATE__ 見つからず。他のJSON埋め込みを探索:")
        for key in re.findall(r'(\w+)\s*=\s*\{"', html):
            pass
        # __NEXT_DATA__ 形式も試す
        m2 = re.search(r'id="__NEXT_DATA__"[^>]*>(\{.*?\})</script>', html, re.S)
        if m2:
            print("    __NEXT_DATA__ が見つかりました")
            m = m2
    if not m:
        print("    JSON埋め込みなし。<article>/<main>から抽出を試みる:")
        soup = BeautifulSoup(html, "html.parser")
        for tag in ("article", "main"):
            el = soup.find(tag)
            if el:
                text = el.get_text("\n", strip=True)
                print(f"    <{tag}> 文字数={len(text)} 先頭300字: {text[:300]}")
        return

    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        print(f"    JSON解析失敗: {e}")
        print(f"    先頭500字: {m.group(1)[:500]}")
        return

    print(f"    トップレベルキー: {list(data.keys())}")

    # 記事本文らしきキーを再帰的に探す
    def walk(obj, path="", depth=0, hits=None):
        if hits is None:
            hits = []
        if depth > 6:
            return hits
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, str) and len(v) > 200:
                    hits.append((f"{path}.{k}", v[:200]))
                else:
                    walk(v, f"{path}.{k}", depth + 1, hits)
        elif isinstance(obj, list):
            for i, v in enumerate(obj[:20]):
                walk(v, f"{path}[{i}]", depth + 1, hits)
        return hits

    hits = walk(data)
    print(f"    長文フィールド候補 {len(hits)}件:")
    for path, snippet in hits[:8]:
        print(f"      {path}: {snippet[:150]}")


print(f"python {sys.version.split()[0]}")
inspect("AIマーケット記事(米雇用)", "https://finance.yahoo.co.jp/news/ai-market/detail/2959")
inspect("AIマーケット記事(日経+2.21%)", "https://finance.yahoo.co.jp/news/ai-market/detail/2956")
inspect("フィスコ配信記事(前日に動いた銘柄)",
        "https://finance.yahoo.co.jp/news/detail/e8226f7a4eca8fb48509a5e85c2e39")
