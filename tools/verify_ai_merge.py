"""claude/* ブランチを main に自動マージしてよいか検査する。

安全に倒す: 少しでも想定外なら「マージしない」を返し、人の目に委ねる。
判定基準:
  1. main から見て fast-forward できること（履歴が分岐していない）
  2. 変更ファイルが「Routine が書いてよいファイル」だけであること
       docs/data/latest.json         AI 分析（ai_commentary）
       docs/data/trigger/*.txt       収集の合図（時刻1行）
       docs/data/themes.json         銘柄→テーマ辞書（未知銘柄の追記）
       docs/data/ledger.json         発掘台帳（候補の理由づけ）
       docs/data/weekly.json         週報
       docs/index.html               stamp_assets によるハッシュの打ち直し
  3. latest.json を変えている場合、その date フィールドが変わっていないこと
     （別日のデータの混入を防ぐ）
  4. 変更後の JSON がすべて正しく読めること

  python tools/verify_ai_merge.py <branch>
終了コード 0 = マージしてよい / 1 = 見送り（理由を標準出力に書く）
"""
import fnmatch
import json
import subprocess
import sys

ALLOWED = [
    "docs/data/latest.json",
    "docs/data/trigger/*.txt",
    "docs/data/themes.json",
    "docs/data/ledger.json",
    "docs/data/weekly.json",
    "docs/index.html",
]


def sh(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def _allowed(path: str) -> bool:
    return any(fnmatch.fnmatch(path, pat) for pat in ALLOWED)


def main() -> int:
    if len(sys.argv) != 2:
        print("使い方: verify_ai_merge.py <branch>")
        return 1
    branch = sys.argv[1]

    try:
        sh("git", "merge-base", "--is-ancestor", "origin/main", f"origin/{branch}")
    except subprocess.CalledProcessError:
        print(f"見送り: main から {branch} への単純な前進ではありません（要手動対応）")
        return 1

    changed = [f for f in sh("git", "diff", "--name-only",
                             "origin/main", f"origin/{branch}").splitlines() if f]
    if not changed:
        print("見送り: 変更がありません")
        return 1
    bad = [f for f in changed if not _allowed(f)]
    if bad:
        print(f"見送り: Routine が書いてよいファイル以外の変更があります: {bad}")
        return 1

    for path in changed:
        if not path.endswith(".json"):
            continue
        try:
            after = json.loads(sh("git", "show", f"origin/{branch}:{path}"))
        except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
            print(f"見送り: {path} が JSON として読めません（{e}）")
            return 1
        if path == "docs/data/latest.json":
            try:
                before = json.loads(sh("git", "show", "origin/main:docs/data/latest.json"))
            except (subprocess.CalledProcessError, json.JSONDecodeError):
                before = {}
            if before.get("date") != after.get("date"):
                print(f"見送り: latest.json の date が変わっています"
                      f"（{before.get('date')} → {after.get('date')}）")
                return 1

    print(f"マージ可: {branch} → main（{', '.join(changed)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
