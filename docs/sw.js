/* 最小のサービスワーカー。
   ネットワーク優先・失敗時は控えから返す。ホーム画面から開いたときに圏外でも「最後に見た状態」が出るようにし、
   アプリは開いた瞬間にこの控えで描いてから最新に差し替える（app.js の prime。DESIGN.md 35章）。鮮度は常にネットワークを優先する。
   控えの鍵は「?より前」だけ。以前は data/latest.json?t=時刻 をそのまま鍵にしていたので、開くたびに数MB の控えが増え続け、
   圏外ではいちばん古い控えが出ていた。v2 で古い控えを捨てる。 */
const CACHE = 'market-v2';        // app.js の SW_CACHE と同じ名前

self.addEventListener('install', (e) => { self.skipWaiting(); });
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;       // GitHub API などは触らない
  const key = url.origin + url.pathname;
  e.respondWith(
    fetch(req).then((res) => {
      if (res.ok) {
        const copy = res.clone();
        e.waitUntil(caches.open(CACHE).then((c) => c.put(key, copy)).catch(() => {}));
      }
      return res;
    }).catch(() => caches.open(CACHE).then((c) => c.match(key)).then((hit) => {
      if (!hit) return Response.error();
      const headers = new Headers(hit.headers);
      headers.set('X-Md-Offline', '1');                   // アプリが「圏外・前回の表示」と書けるように
      return new Response(hit.body, { status: hit.status, statusText: hit.statusText, headers });
    }))
  );
});
