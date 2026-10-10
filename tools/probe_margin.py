"""信用残・需給の材料が取れるかの実測（GitHub Actions 上で実行。コミットしない）。

  python tools/probe_margin.py [コード ...]

Yahoo!ファイナンスの銘柄ページ（信用買残・信用売残・信用倍率）、JPX の銘柄別信用取引週末残高・空売り残高・
投資部門別売買状況、日証金の貸借残高に届くか、どんな形で載っているかを出す。届かない（相手の遮断）のか、
読めない（パーサ）のかを切り分ける。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests                                     # noqa: E402
from bs4 import BeautifulSoup                       # noqa: E402

from dashboard.http import HEADERS                  # noqa: E402

CODES = sys.argv[1:] or ["5803", "285A", "7203"]


def get(url, timeout=30):
    try:
        return requests.get(url, headers=HEADERS, timeout=timeout)
    except Exception as e:                          # noqa: BLE001
        print(f"    例外: {e}")
        return None


def links(label, url, pat, n=25):
    print(f"\n=== {label} ===\n    {url}")
    r = get(url)
    if r is None:
        return []
    print(f"    status={r.status_code} len={len(r.text)}")
    if r.status_code != 200:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        if re.search(pat, a["href"], re.I) and a["href"] not in out:
            out.append(a["href"])
    print(f"    '{pat}' のリンク: {len(out)}")
    for h in out[:n]:
        a = soup.find("a", href=h)
        print(f"      {h} | {a.get_text(' ', strip=True)[:60] if a else ''}")
    return out


def absolute(base, href):
    if href.startswith("http"):
        return href
    host = "/".join(base.split("/")[:3])
    return host + (href if href.startswith("/") else "/" + href)


# ---- 0. 信用残の時系列の表（/quote/{code}.T/history?styl=margin） ----
for code in CODES[:2]:
    for page in (1, 2, 8):
        u = f"https://finance.yahoo.co.jp/quote/{code}.T/history?styl=margin" + (f"&page={page}" if page > 1 else "")
        r = get(u)
        print(f"\n=== 信用残の時系列の表 {code} page={page} ===\n    {u} status={getattr(r, 'status_code', None)}")
        if r is None or r.status_code != 200:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        tb = soup.find("table", attrs={"aria-label": re.compile("信用残")})
        if not tb:
            print("    表が無い")
            continue
        heads = [th.get_text(" ", strip=True) for th in tb.find("thead").find_all("th")] if tb.find("thead") else []
        rows = tb.find("tbody").find_all("tr") if tb.find("tbody") else tb.find_all("tr")
        print(f"    見出し: {heads}  行数: {len(rows)}")
        for tr in rows[:4] + rows[-2:]:
            print("      " + " | ".join("/".join(c.stripped_strings) for c in tr.find_all(["th", "td"])))
        pg = [a.get_text(strip=True) + ">" + a["href"] for a in soup.find_all("a", href=True) if "page=" in a["href"]][:12]
        print(f"    ページ送り: {pg}")
        nxt = soup.find(string=re.compile("次へ|件中"))
        print(f"    件数の表示: {nxt.strip()[:80] if nxt else None}")
if not os.environ.get("PROBE_MARGIN_ALL"):
    print("\n完了0")
    sys.exit(0)

# ---- 1. Yahoo!ファイナンスの銘柄ページ ----
for code in []:  # 1回目で確認済み（tools の履歴）
    url = f"https://finance.yahoo.co.jp/quote/{code}.T"
    print(f"\n=== Yahoo 銘柄ページ {code} ===\n    {url}")
    r = get(url)
    if r is None:
        continue
    print(f"    status={r.status_code} len={len(r.text)}")
    if r.status_code != 200:
        continue
    html = r.text
    text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
    for kw in ("信用買残", "信用売残", "信用倍率", "貸借倍率", "前週比", "逆日歩"):
        print(f"    '{kw}' 本文 {text.count(kw)} / HTML {html.count(kw)}")
    i = text.find("信用買残")
    if i >= 0:
        print("    本文の前後:\n      " + text[max(0, i - 60): i + 400].replace("\n", " | "))
    for m in re.finditer(r'"(margin[A-Za-z]*|credit[A-Za-z]*|[a-zA-Z]*Margin[A-Za-z]*)"\s*:', html):
        print(f"    JSON のキー: {m.group(1)}  前後: {html[m.start(): m.start() + 220]!r}")
        break
    keys = sorted(set(re.findall(r'"([A-Za-z]*(?:[Mm]argin|[Cc]redit|[Ss]hort)[A-Za-z]*)"\s*:', html)))
    print(f"    信用らしいキー: {keys[:40]}")
    for k in keys[:12]:
        j = html.find(f'"{k}"')
        print(f"      {k}: {html[j: j + 200]!r}")

    for sub in ("margin", "credit", "history"):
        u2 = f"https://finance.yahoo.co.jp/quote/{code}.T/{sub}"
        r2 = get(u2)
        print(f"    /{sub}: status={getattr(r2, 'status_code', None)} len={len(r2.text) if r2 is not None else 0}"
              f" 信用買残 {r2.text.count('信用買残') if r2 is not None else 0}")

# ---- 2. JPX ----
for label, url, pat in [
    ("JPX 銘柄別信用取引週末残高", "https://www.jpx.co.jp/markets/statistics-equities/margin/05.html", r"\.(pdf|xls|xlsx|csv|zip)$"),
    ("JPX 信用取引残高等（市場全体）", "https://www.jpx.co.jp/markets/statistics-equities/margin/index.html", r"\.(pdf|xls|xlsx|csv|zip)$|margin/"),
    ("JPX 空売り残高（個別）", "https://www.jpx.co.jp/markets/public/short-selling/index.html", r"\.(pdf|xls|xlsx|csv|zip)$"),
    ("JPX 空売りの集計", "https://www.jpx.co.jp/markets/statistics-equities/short-selling/index.html", r"\.(pdf|xls|xlsx|csv|zip)$"),
    ("JPX 投資部門別売買状況", "https://www.jpx.co.jp/markets/statistics-equities/investor-type/index.html", r"\.(pdf|xls|xlsx|csv|zip)$"),
]:
    found = links(label, url, pat)
    files = [h for h in found if re.search(r"\.(xls|xlsx|csv|zip)$", h, re.I)]
    if files:
        u = absolute(url, files[0])
        r = get(u, timeout=60)
        if r is not None:
            print(f"    先頭のファイル {u}: status={r.status_code} bytes={len(r.content)} "
                  f"ctype={r.headers.get('content-type', '')[:40]} 先頭={r.content[:16]!r}")

# ---- 3. 日証金 ----
for url in ["https://www.taisyaku.jp/", "https://www.taisyaku.jp/search/",
            "https://www.taisyaku.jp/data/", f"https://www.taisyaku.jp/search/result/index/1/?code={CODES[0]}"]:
    found = links("日証金", url, r"\.(csv|xls|xlsx|zip)$|search|data", n=15)
    files = [h for h in found if re.search(r"\.(xls|xlsx|csv|zip)$", h, re.I)]
    if files:
        u = absolute(url, files[0])
        r = get(u, timeout=60)
        if r is not None:
            print(f"    先頭のファイル {u}: status={r.status_code} bytes={len(r.content)} 先頭={r.content[:200]!r}")

print("\n完了")


# ---- 4. 信用残の時系列（銘柄ページのリンク先）と、日証金の銘柄ページの本文 ----
def xlsx_rows(content, limit=40):
    """xlsx を標準ライブラリだけで読む（最初のシート）。"""
    import io
    import zipfile
    import xml.etree.ElementTree as ET
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    z = zipfile.ZipFile(io.BytesIO(content))
    print(f"    シート: {[n for n in z.namelist() if n.startswith('xl/worksheets/')]}")
    ss = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
            ss.append("".join(t.text or "" for t in si.iter("{%s}t" % ns["m"])))
    sheet = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))[0]
    out = []
    for row in ET.fromstring(z.read(sheet)).iter("{%s}row" % ns["m"]):
        vals = []
        for c in row.findall("m:c", ns):
            v = c.find("m:v", ns)
            t = c.get("t")
            x = v.text if v is not None else ""
            if t == "s" and x:
                x = ss[int(x)]
            elif t == "inlineStr":
                x = "".join(tt.text or "" for tt in c.iter("{%s}t" % ns["m"]))
            vals.append(f"{c.get('r')}={x}")
        out.append(vals)
    for r in out[:limit]:
        print("      " + " | ".join(r)[:300])
    print(f"    行数: {len(out)}")
    return out


for code in CODES[:2]:
    r = get(f"https://finance.yahoo.co.jp/quote/{code}.T")
    if r is None or r.status_code != 200:
        continue
    soup = BeautifulSoup(r.text, "html.parser")
    a = next((a for a in soup.find_all("a", href=True) if "信用残時系列" in a.get_text()), None)
    print(f"\n=== 信用残の時系列 {code} === リンク: {a['href'] if a else None}")
    if a:
        u = absolute("https://finance.yahoo.co.jp/", a["href"])
        r2 = get(u)
        if r2 is not None:
            print(f"    {u} status={r2.status_code} len={len(r2.text)}")
            t2 = BeautifulSoup(r2.text, "html.parser").get_text("\n", strip=True)
            i = t2.find("信用買残")
            print("    本文: " + t2[max(0, i - 100): i + 1500].replace("\n", " | ") if i >= 0 else f"    信用買残が無い。先頭: {t2[:600]!r}")
            for kw in ("marginBuy", "creditBuy", "margin", "Margin"):
                j = r2.text.find(kw)
                if j >= 0:
                    print(f"    '{kw}' 前後: {r2.text[j - 80: j + 400]!r}")
                    break

r = get(f"https://www.taisyaku.jp/search/result/index/1/?code={CODES[0]}")
if r is not None and r.status_code == 200:
    t = BeautifulSoup(r.text, "html.parser").get_text("\n", strip=True)
    i = max(t.find("融資"), 0)
    print(f"\n=== 日証金 {CODES[0]} 本文 ===\n    " + t[i: i + 1500].replace("\n", " | "))

for label, url, pat in [
    ("JPX 信用取引現在高 過去推移", "https://www.jpx.co.jp/markets/statistics-equities/margin/05.html", r"\.xlsx$"),
    ("JPX 信用取引現在高（日々）", "https://www.jpx.co.jp/markets/statistics-equities/margin/index.html", r"\.xlsx$"),
    ("JPX 投資部門別（週）", "https://www.jpx.co.jp/markets/statistics-equities/investor-type/index.html", r"stock_1_w_\d+_\d+\.xlsx$"),
]:
    found = links(label, url, pat, n=3)
    if found:
        u = absolute(url, found[0])
        r = get(u, timeout=60)
        if r is not None and r.status_code == 200:
            print(f"    {u}")
            try:
                xlsx_rows(r.content, limit=60)
            except Exception as e:                  # noqa: BLE001
                print(f"    読めない: {e}")
print("\n完了2")
