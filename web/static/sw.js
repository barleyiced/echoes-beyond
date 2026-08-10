/* Service worker for the hosted build. A template: data/site.py substitutes
 * __BUILD_ID__ and __PRECACHE__ and writes the result to site/sw.js, at the
 * site root so its scope covers index.html.
 *
 * Why the build id matters more than the caching does: a service worker that
 * serves cache-first will happily keep showing last month's dataset after a
 * `du update`, with no error and no visible symptom — the numbers are simply
 * wrong. That is the single failure mode this project cares most about, so the
 * cache name is derived from the pinned game-data revision *and* the built
 * payload. Any change to either means a new cache and a clean sweep of the old.
 */

const BUILD = '__BUILD_ID__';
const CACHE = 'du-' + BUILD;
const PRECACHE = __PRECACHE__;

// Files whose contents change from build to build under an unchanging name, so
// a stale copy would be wrong rather than merely old. python.zip is the dataset
// itself, which makes it the one that matters most.
//
// Everything else — the Pyodide runtime and the icons, ~17 MB of it — only
// changes when the vendored runtime or the icon set does, and either of those
// changes the build id anyway. Forcing freshness across the board doubled
// first-visit transfer to about 33 MB for no benefit.
const SHELL_FILE = /(index\.html|app\.js|app\.css|worker\.js|python\.zip)$/;

/** Is this one of the small files that must never be a build behind? */
const isShell = (p) => p === './' || p === '/' || SHELL_FILE.test(p);

/** Which strategy a request gets. Pure, so it can be tested without a browser.
 *
 * Navigations are identified by mode rather than by path: the URL of one is
 * `/` or `/?foo`, which no filename pattern should have to know about.
 */
function routeFor(mode, pathname) {
  if (mode === 'navigate') return 'network-first';
  return isShell(pathname) ? 'network-first' : 'cache-first';
}

// How long to wait for the network before falling back to cache. Long enough
// for a slow connection to win, short enough that a dead one does not hold the
// app hostage — the point of the cache is that it is there for exactly this.
const NETWORK_TIMEOUT = 3500;

self.addEventListener('install', (ev) => {
  ev.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    // Individually rather than addAll(): one 404 would otherwise reject the
    // whole install and leave the site with no worker at all.
    const failedShell = [];
    await Promise.all(PRECACHE.map(async (p) => {
      const essential = isShell(p);
      try {
        await cache.add(essential ? new Request(p, { cache: 'reload' }) : new Request(p));
      } catch (e) {
        if (essential) failedShell.push(p);
        console.warn('[sw] could not precache', p, e);
      }
    }));

    // If the shell itself did not cache, fail the install rather than reporting
    // success with a half-empty cache. A "successful" install is never retried,
    // so swallowing this would leave the site permanently unable to work
    // offline with nothing to indicate why.
    if (failedShell.length) {
      throw new Error('[sw] shell precache failed: ' + failedShell.join(', '));
    }
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (ev) => {
  ev.waitUntil((async () => {
    for (const name of await caches.keys()) {
      // Everything from an older build goes. This is what makes `du update`
      // actually reach people instead of being masked by their cache.
      if (name.startsWith('du-') && name !== CACHE) await caches.delete(name);
    }
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (ev) => {
  const req = ev.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Never cache the Cloudflare Access endpoints, or a stale redirect could
  // strand somebody at a login they have already completed.
  if (url.pathname.startsWith('/cdn-cgi/')) return;

  ev.respondWith(
    routeFor(req.mode, url.pathname) === 'network-first'
      ? networkFirst(req)
      : cacheFirst(req));
});

/** Offline fallback for a navigation: the shell, however it was keyed. */
async function shellFallback(req) {
  if (req.mode !== 'navigate') return null;
  return (await caches.match('./', { cacheName: CACHE }))
      || (await caches.match('index.html', { cacheName: CACHE }))
      || null;
}

/** Cache-first. For the 17 MB of runtime and icons that only move with the
 *  build id, which sweeps the whole cache anyway. */
async function cacheFirst(req) {
  const cached = await caches.match(req, { cacheName: CACHE });
  if (cached) return cached;
  try {
    const fresh = await fetch(req);
    if (fresh.ok && fresh.type === 'basic') {
      (await caches.open(CACHE)).put(req, fresh.clone());
    }
    return fresh;
  } catch (e) {
    const shell = await shellFallback(req);
    if (shell) return shell;
    throw e;
  }
}

/** Network-first, with the cache as the offline answer.
 *
 * This is what makes a `du publish` reach an open tab on the *first* refresh.
 * Cache-first could not: the installed worker served the old shell for the very
 * navigation on which the new worker installed, so reload 1 showed the previous
 * build and only reload 2 showed the new one. Correct cache-busting, one visit
 * too late — and the whole point of the build id is that nobody is ever quietly
 * a version behind.
 *
 * Affordable only because the shell is small: index.html, app.js, app.css,
 * worker.js and python.zip come to roughly half a megabyte together. The
 * runtime and icons stay cache-first, which is where the weight is.
 */
async function networkFirst(req) {
  const cache = await caches.open(CACHE);
  try {
    const fresh = await Promise.race([
      fetch(req),
      new Promise((_, rej) =>
        setTimeout(() => rej(new Error('network timeout')), NETWORK_TIMEOUT)),
    ]);
    if (fresh.ok && fresh.type === 'basic') {
      // Keep the newest thing actually seen, so the offline copy is the last
      // build that reached this browser rather than the one it installed with.
      cache.put(req, fresh.clone());
    }
    return fresh;
  } catch (e) {
    const cached = await cache.match(req);
    if (cached) return cached;
    const shell = await shellFallback(req);
    if (shell) return shell;
    throw e;
  }
}
