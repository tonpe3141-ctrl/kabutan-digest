"""HTTP 取得の共通レイヤ。

方針:
  - 収集は「落ちない」ことを最優先にする。呼び出し側は例外ではなく
    None / 空リストを受け取り、その区画だけを「取得失敗」として描画する。
  - 相手サイトに負荷をかけないよう、ホスト単位で最小間隔を空ける。
"""
import time
import threading
import requests

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "ja,en;q=0.9",
}

# ホストごとの最小リクエスト間隔（秒）
_MIN_INTERVAL = {
    "kabutan.jp": 0.25,
    "stooq.com": 0.35,
}
_DEFAULT_INTERVAL = 0.5

_last_hit: dict[str, float] = {}
_lock = threading.Lock()
_session = requests.Session()
_session.headers.update(HEADERS)


def _throttle(host: str) -> None:
    interval = _MIN_INTERVAL.get(host, _DEFAULT_INTERVAL)
    with _lock:
        wait = interval - (time.monotonic() - _last_hit.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last_hit[host] = time.monotonic()


def get(url: str, *, timeout: int = 15, retries: int = 2,
        cookies: dict | None = None, allow_redirects: bool = True):
    """成功時は Response、最終的に失敗したら None を返す（例外を投げない）。"""
    host = url.split("/")[2] if "://" in url else url
    for attempt in range(retries + 1):
        _throttle(host)
        try:
            res = _session.get(url, timeout=timeout, cookies=cookies,
                               allow_redirects=allow_redirects)
            if res.status_code == 200:
                return res
            # 4xx はリトライしても無駄なので即座に諦める
            if 400 <= res.status_code < 500 and res.status_code != 429:
                return None
        except requests.RequestException:
            pass
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    return None


def get_text(url: str, **kw) -> str | None:
    res = get(url, **kw)
    return res.text if res is not None else None
