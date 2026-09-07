"""index.html が読み込む app.js / style.css に内容ハッシュを付ける。

ブラウザが古い app.js を掴んだままだと、データだけ新しくなって
表示が変わらないという分かりにくい状態になる。中身が変わったときだけ
クエリ文字列が変わるようにして、変わっていないときは再取得させない。

  python tools/stamp_assets.py
"""
import hashlib
import re
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
ASSETS = ("app.js", "style.css")


def main() -> int:
    index = DOCS / "index.html"
    html = index.read_text(encoding="utf-8")
    original = html

    for name in ASSETS:
        path = DOCS / name
        if not path.exists():
            print(f"  ⚠️  {name} が見つかりません")
            continue
        digest = hashlib.md5(path.read_bytes()).hexdigest()[:8]
        # href="style.css" も href="style.css?v=xxxx" も同じ形に置き換える
        pattern = re.compile(rf'((?:src|href)=")({re.escape(name)})(\?v=[0-9a-f]+)?(")')
        html, n = pattern.subn(rf'\g<1>\g<2>?v={digest}\g<4>', html)
        print(f"  {name}: v={digest}（{n}箇所）")

    if html != original:
        index.write_text(html, encoding="utf-8")
        print("  ✅ index.html を更新しました")
    else:
        print("  変更なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
