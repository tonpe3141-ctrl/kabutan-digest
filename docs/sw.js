/* 最小のサービスワーカー。
   ネットワーク優先・失敗時はキャッシュから返す。ホーム画面から開いたときに
   圏外でも「最後に見た状態」が出るようにするためのもので、鮮度は常にネットワークを優先する。 */
const CACHE = 'market-v1';

self.addEventListener('install', (e) => { self.skipWaiting(); });
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;       // GitHub API などは触らない
  e.respondWith(
    fetch(req).then((res) => {
      if (res.ok) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
      }
      return res;
    }).catch(() => caches.match(req, { ignoreSearch: true }))
  );
});
