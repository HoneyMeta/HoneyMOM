// 蜂元智能 MOM 手机端离线外壳（Service Worker）。
// 页面（导航请求）：网络优先，断网时用缓存的 /m 外壳；静态资源（/assets、/brand、/vendor、图标）：缓存优先、后台更新；
// 接口 /api/*：一律走网络，不缓存（业务数据必须实时，报工等写操作由页面自己处理离线提示和重试）。
const VER = 'hm-m-v1';
const SHELL = ['/m', '/manifest.webmanifest', '/brand/mark.svg', '/brand/icon-192.png', '/brand/icon-512.png', '/vendor/zxing-library-0.21.3.min.js'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(VER).then(async (c) => {
    await c.addAll(SHELL);
    // 外壳页引用的打包文件（/assets/index-*.js、*.css）一起缓存
    const html = await (await fetch('/m')).text();
    const assets = [...new Set(html.match(/\/assets\/[^"' )]+/g) || [])];
    await c.addAll(assets).catch(() => undefined);
  }).then(() => self.skipWaiting()));
});

// 页面把已经加载的按需分包（手机端页面、组件）告诉 SW，一起缓存，断网也能打开
self.addEventListener('message', (e) => {
  const d = e.data || {};
  if (d.type === 'cache' && Array.isArray(d.urls)) {
    const ok = d.urls.filter((u) => typeof u === 'string' && /^\/(assets|brand|vendor|art)\//.test(new URL(u, location.origin).pathname));
    caches.open(VER).then((c) => c.addAll(ok)).catch(() => undefined);
  }
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== VER).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  if (req.mode === 'navigate') {
    e.respondWith(fetch(req).then((r) => {
      if (r.ok && url.pathname === '/m') caches.open(VER).then((c) => c.put('/m', r.clone()));
      return r;
    }).catch(() => caches.match('/m')));
    return;
  }
  if (/^\/(assets|brand|vendor|art)\//.test(url.pathname) || url.pathname.endsWith('.webmanifest')) {
    e.respondWith(caches.open(VER).then(async (c) => {
      const hit = await c.match(req);
      const net = fetch(req).then((r) => { if (r.ok) c.put(req, r.clone()); return r; }).catch(() => hit);
      return hit || net;
    }));
  }
});
