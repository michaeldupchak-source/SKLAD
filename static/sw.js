// SKLAD Service Worker — v1.1
// Стратегия: Cache-First для статики, Network-First для всех HTML-страниц.
// Складское приложение требует актуальных данных — staleWhileRevalidate
// намеренно убрана: она возвращала кэш после POST-редиректа и страница
// не обновлялась без физического F5.

const STATIC_CACHE = 'sklad-static-v2';

const STATIC_ASSETS = [
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

// ── Install ───────────────────────────────────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then(cache => cache.addAll(STATIC_ASSETS).catch(err => {
        console.warn('[SW] Some assets failed to cache:', err);
      }))
      .then(() => self.skipWaiting())
  );
});

// ── Activate — удаляем все старые кэши ───────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(k => k !== STATIC_CACHE)
          .map(k => { console.log('[SW] Deleting old cache:', k); return caches.delete(k); })
      )
    ).then(() => self.clients.claim())
  );
});

// ── Fetch ─────────────────────────────────────────────────
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET') return;
  if (!url.protocol.startsWith('http')) return;

  // Статика — cache-first (иконки, CSS, JS, манифест)
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(cacheFirst(event.request));
    return;
  }

  // Все HTML-страницы — network-first (всегда свежие данные)
  event.respondWith(networkFirst(event.request));
});

async function cacheFirst(req) {
  const cached = await caches.match(req);
  if (cached) return cached;
  try {
    const res = await fetch(req);
    if (res.ok) (await caches.open(STATIC_CACHE)).put(req, res.clone());
    return res;
  } catch { return offlinePage(); }
}

async function networkFirst(req) {
  try {
    const res = await fetch(req);
    return res;
  } catch {
    const cached = await caches.match(req);
    return cached || offlinePage();
  }
}

function offlinePage() {
  return new Response(`<!DOCTYPE html>
<html lang="ru"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SKLAD — Офлайн</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Space Mono',monospace;background:#0d0f12;color:#dde2f0;
  min-height:100vh;display:flex;align-items:center;justify-content:center;
  flex-direction:column;gap:16px;text-align:center;padding:24px}
.icon{font-size:3rem}.title{font-size:1.1rem;color:#e8c44a}
p{font-size:.8rem;color:#6b7492;max-width:280px;line-height:1.6}
button{margin-top:8px;padding:12px 28px;background:#e8c44a;color:#0d0f12;
  border:none;border-radius:8px;font-family:inherit;font-size:.8rem;font-weight:700;cursor:pointer}
</style></head>
<body>
<div class="icon">📦</div>
<div class="title">SKLAD офлайн</div>
<p>Нет подключения к сети. Некоторые данные могут быть недоступны.</p>
<button onclick="location.reload()">Повторить</button>
</body></html>`, {
    status: 503,
    headers: { 'Content-Type': 'text/html; charset=utf-8' }
  });
}
