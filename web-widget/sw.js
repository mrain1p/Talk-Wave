/* Service worker for the installed call page.

   This is a PHONE. Almost nothing here should be answered from a cache: the
   whole point of the app is to reach a station that is live right now, and a
   cached /live would paint a DJ who went off air three hours ago. So the
   rule is narrow and deliberately boring:

     the shell        cache-first, because its URLs carry ?v= and change when
                      the bytes do (see api/widget.asset_tag)
     the page itself  network-first, cache only as the offline fallback
     everything else  straight to the network, never cached

   What the cache buys is the one thing an installed app has to do that a tab
   does not: OPEN. Tapping a home-screen icon with no signal has to give you
   the card saying it cannot reach the station — not the browser's dinosaur.
   The card already handles an unreachable station properly; it just has to
   load first.

   Registration is in call.js and is skipped inside an iframe: an embed on
   somebody else's page has no business installing a worker for this origin.

   Bump CACHE when the precache list changes. The worker script itself is
   served no-cache, so a new one is picked up on the next navigation. */

const CACHE = 'talkwave-v1';

// The page, and the two scripts and stylesheet it cannot start without.
// Unversioned URLs on purpose: at install time we do not know the ?v= tags,
// and these are only ever the fallback. The versioned copies get cached as
// they are actually requested, which is where the real speed comes from.
const SHELL = ['/', '/style.css', '/shared.js', '/call.js', '/icon-192.png'];

self.addEventListener('install', (e) => {
  // addAll rejects the whole install if any one URL fails, which would leave
  // an app with no offline shell over a single missing icon. Each is fetched
  // on its own and a failure is allowed.
  e.waitUntil((async () => {
    const c = await caches.open(CACHE);
    await Promise.all(SHELL.map((u) => c.add(u).catch(() => {})));
    self.skipWaiting();
  })());
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n)));
    await self.clients.claim();
  })());
});

// Paths this worker must never answer for. /settings is the operator's
// surface and not part of the app (/panel, its retired old name, stays
// listed so a stale cache can never resurrect it); the rest are live state and would be actively
// wrong from a cache. Anything not GET is out too — a cached POST is not a
// thing that should ever be attempted.
const NEVER = ['/panel', '/live', '/token', '/call-ended', '/call-feedback',
               '/settings', '/logs', '/calls', '/health', '/test', '/prompt',
               '/hooks', '/avatar'];

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  // Cross-origin means the LiveKit CDN and the station's audio stream.
  // Neither is ours to cache, and a stream answered from a cache is not a
  // stream.
  if (url.origin !== self.location.origin) return;
  if (NEVER.some((p) => url.pathname === p || url.pathname.startsWith(p + '/'))) return;

  // A versioned asset is immutable by contract — the tag changes when the
  // file does — so the cached copy can be trusted without asking.
  if (url.searchParams.has('v')) {
    e.respondWith((async () => {
      const hit = await caches.match(req);
      if (hit) return hit;
      const res = await fetch(req);
      if (res.ok) (await caches.open(CACHE)).put(req, res.clone());
      return res;
    })());
    return;
  }

  // The page. Network first, so a caller always gets the current card when
  // they have signal; the cache is only what makes the app open without it.
  if (req.mode === 'navigate') {
    e.respondWith((async () => {
      try {
        const res = await fetch(req);
        if (res.ok) (await caches.open(CACHE)).put('/', res.clone());
        return res;
      } catch (err) {
        return (await caches.match('/')) || Response.error();
      }
    })());
    return;
  }

  // Everything else the shell needs: try the network, fall back to whatever
  // was cached at install.
  e.respondWith(fetch(req).catch(async () => (await caches.match(req)) || Response.error()));
});
