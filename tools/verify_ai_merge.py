"""claude/* ブランチを main に自動マージしてよいか検査する。

安全に倒す: 少しでも想定外なら「マージしない」を返し、人の目に委ねる。
判定基準:
  1. main から見て fast-forward できること（履歴が分岐していない）
  2. 変更ファイルが docs/data/latest.json の1つだけであること
  3. その中の date フィールドが変わっていないこと（別日のデータの混入を防ぐ）
  4. 変更後も正しい JSON として読めること

  python tools/verify_ai_merge.py <branch>
終了コード 0 = マージしてよい / 1 = 見送り（理由を標準出力に書く）
"""
import json
import subprocess
import sys


def sh(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


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
    if changed != ["docs/data/latest.json"]:
        print(f"見送り: docs/data/latest.json 以外の変更があります: {changed}")
        return 1

    try:
        before = json.loads(sh("git", "show", "origin/main:docs/data/latest.json"))
        after = json.loads(sh("git", "show", f"origin/{branch}:docs/data/latest.json"))
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        print(f"見送り: JSON として読めません（{e}）")
        return 1

    if before.get("date") != after.get("date"):
        print(f"見送り: date が変わっています（{before.get('date')} → {after.get('date')}）")
        return 1

    print(f"マージ可: {branch} → main（docs/data/latest.json のみ、date={after.get('date')}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
