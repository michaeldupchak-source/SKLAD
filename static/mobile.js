/* ═══════════════════════════════════════════════════════
   SKLAD — mobile.js  (task #01 — PWA / Mobile)
   ═══════════════════════════════════════════════════════ */
(function () {
  'use strict';

  // ── Service Worker ──────────────────────────────────────
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker
        .register('/static/sw.js', { scope: '/' })
        .then(reg => {
          reg.addEventListener('updatefound', () => {
            const nw = reg.installing;
            nw.addEventListener('statechange', () => {
              if (nw.state === 'installed' && navigator.serviceWorker.controller) {
                showUpdateToast();
              }
            });
          });
        })
        .catch(err => console.warn('[PWA] SW failed:', err));
    });
  }

  // ── Install Prompt ──────────────────────────────────────
  let deferredPrompt = null;
  window.addEventListener('beforeinstallprompt', e => {
    e.preventDefault();
    deferredPrompt = e;
    setTimeout(showInstallBanner, 4000);
  });

  function showInstallBanner() {
    if (localStorage.getItem('pwa_dismissed')) return;
    const banner = document.getElementById('install-banner');
    if (banner) banner.classList.add('show');
  }

  window.installPWA = async function () {
    if (!deferredPrompt) return;
    deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;
    deferredPrompt = null;
    const banner = document.getElementById('install-banner');
    if (banner) banner.classList.remove('show');
  };

  window.dismissInstallBanner = function () {
    const banner = document.getElementById('install-banner');
    if (banner) banner.classList.remove('show');
    localStorage.setItem('pwa_dismissed', '1');
  };

  // ── Active nav link ─────────────────────────────────────
  function setActiveNavLink() {
    const path = window.location.pathname;
    document.querySelectorAll('.mobile-nav__link').forEach(link => {
      const href = link.getAttribute('href');
      if (!href) return;
      const active = href === '/' ? path === '/' : path.startsWith(href);
      link.classList.toggle('active', active);
    });
  }

  // ── vh fix (mobile address bar) ─────────────────────────
  function setVH() {
    document.documentElement.style.setProperty('--vh', window.innerHeight * 0.01 + 'px');
  }

  // ── Update Toast ────────────────────────────────────────
  function showUpdateToast() {
    const t = document.createElement('div');
    t.style.cssText = [
      'position:fixed;bottom:80px;left:50%;transform:translateX(-50%)',
      'background:var(--surface2);border:1px solid var(--accent)',
      'border-radius:8px;padding:12px 20px;z-index:9999',
      'font-family:var(--mono);font-size:.72rem;color:var(--text)',
      'display:flex;align-items:center;gap:12px',
      'box-shadow:0 4px 20px rgba(0,0,0,.5)',
      'animation:mobileSlideUp .3s ease',
    ].join(';');
    t.innerHTML = `<span>📦 Обновление доступно</span>
      <button onclick="location.reload()" style="padding:6px 12px;background:var(--accent);
        color:#0d0f12;border:none;border-radius:4px;font-family:inherit;
        font-size:.7rem;font-weight:700;cursor:pointer">Обновить</button>`;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 8000);
  }

  // ── Init ────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', () => {
    setActiveNavLink();
    setVH();
  });
  window.addEventListener('resize', setVH);
  window.addEventListener('orientationchange', () => { setTimeout(setVH, 100); setActiveNavLink(); });

})();
