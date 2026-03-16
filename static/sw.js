// SKLAD Service Worker — v1.0
// Стратегия: Cache-First для статики, Network-First для API/данных

const STATIC_CACHE = 'sklad-static-v1';
const DATA_CACHE   = 'sklad-data-v1';

const STATIC_ASSETS = [
  '/',
  '/stock',
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

// ── Activate ──────────────────────────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(k => k !== STATIC_CACHE && k !== DATA_CACHE)
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

  // API и мутирующие роуты — всегда сеть
  if (isDataRoute(url.pathname)) {
    event.respondWith(networkFirst(event.request));
    return;
  }
  // Статика — кэш
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(cacheFirst(event.request));
    return;
  }
  // Страницы — stale-while-revalidate
  event.respondWith(staleWhileRevalidate(event.request));
});

function isDataRoute(p) {
  return ['/api/', '/operations', '/products/add', '/products/edit', '/login', '/logout']
    .some(r => p.startsWith(r));
}

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
    if (res.ok) (await caches.open(DATA_CACHE)).put(req, res.clone());
    return res;
  } catch {
    return (await caches.match(req)) || offlinePage();
  }
}

async function staleWhileRevalidate(req) {
  const cache  = await caches.open(DATA_CACHE);
  const cached = await cache.match(req);
  const fetchP = fetch(req).then(res => { if (res.ok) cache.put(req, res.clone()); return res; }).catch(() => null);
  return cached || await fetchP || offlinePage();
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
