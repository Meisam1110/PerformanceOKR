/*
 * okrStore - a localStorage-shaped façade over the Python backend.
 *
 * The tracker's load() and persist() are synchronous and assume a storage that
 * answers immediately, so this shim keeps an in-memory mirror of the workspace
 * document and mirrors writes to the server in the background:
 *
 *   getItem(WORKSPACE)  -> the mirrored document text, seeded server-side into
 *                          the page so the very first load() has real data
 *   setItem(WORKSPACE, text)
 *                       -> updates the mirror, then PUTs with the revision the
 *                          mirror held beforehand (compare-and-swap)
 *   every other key     -> real localStorage (UI preferences and the legacy
 *                          single-file workspace stay per-device)
 *
 * Nothing here needs the application to cooperate. When the server rejects a
 * write, or a poll finds someone else's revision, the shim replaces the mirror
 * with the server's document and fires a `storage` event - which is exactly the
 * signal the app already listens for to flag a stale workspace, so conflicts
 * from another browser tab and from another user take the same path.
 */
(function () {
 'use strict';

 /* The server renders API endpoints, artwork URLs and the current workspace
    into a JSON data block, so the page needs no inline script and can keep a
    strict script-src. app.js reads OKR_BACKEND.art for its artwork map. */
 function bootstrap() {
  const node = document.getElementById('okrBootstrap');
  if (!node) {
   console.error('okrStore: no bootstrap data in the page');
   return {};
  }
  try {
   return JSON.parse(node.textContent || '{}') || {};
  } catch (error) {
   console.error('okrStore: unreadable bootstrap data', error);
   return {};
  }
 }

 const config = bootstrap();
 config.api = config.api || { workspace: '/api/workspace' };
 globalThis.OKR_BACKEND = config;

 const WORKSPACE = (config.keys && config.keys.workspace) || 'pole.okr.workspace.v4';
 const POLL_MS = Math.max(0, Number(config.pollSeconds) || 0) * 1000;

 /* Real localStorage, where it is still the right home. Access is wrapped
    because a browser with site data blocked throws on the property itself. */
 function local() {
  try { return globalThis.localStorage || null; } catch { return null; }
 }

 /* ---------------------------------------------------------------- mirror */

 /* null means "nothing stored yet", which is exactly what the application's
    load() treats as a first run: it builds a sample workspace and saves it. */
 let mirror = config.workspace ? JSON.stringify(config.workspace) : null;
 let mirrorRevision = revisionOf(mirror);

 /* The last state the server is known to hold. Writes move the mirror ahead of
    it optimistically; this is what the mirror falls back to when a write can be
    neither confirmed nor replaced by the server's own document. */
 let confirmed = mirror;

 function revisionOf(text) {
  try { return String(JSON.parse(text || 'null')?.revision || ''); } catch { return ''; }
 }

 /* Hand the app the same signal it already handles for cross-tab edits. */
 function announce(oldValue, newValue) {
  const event = new StorageEvent('storage', {
   key: WORKSPACE, oldValue, newValue, storageArea: null, url: location.href
  });
  window.dispatchEvent(event);
 }

 function adopt(document_) {
  const text = document_ ? JSON.stringify(document_) : null;
  confirmed = text;
  return replaceMirror(text);
 }

 function replaceMirror(text) {
  if (text === mirror) return false;
  const previous = mirror;
  mirror = text;
  mirrorRevision = revisionOf(text);
  announce(previous, text);
  return true;
 }

 /* ------------------------------------------------------------- transport */

 async function request(url, options) {
  const response = await fetch(url, {
   credentials: 'same-origin',
   headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
   ...options
  });
  let payload = null;
  try { payload = await response.json(); } catch { /* non-JSON error page */ }
  return { ok: response.ok, status: response.status, payload };
 }

 /* Writes are serialised: each PUT carries the revision the previous one
    settled on, so a burst of edits cannot race itself into a false conflict. */
 let queue = Promise.resolve();
 let pending = 0;

 function enqueue(task) {
  pending += 1;
  /* Each task swallows its own failure, so one bad write cannot leave the
     queue in a rejected state and stall or unhandled-reject every write after
     it. flush() is then always safe to await. */
  queue = queue.then(async () => {
   try {
    await task();
   } catch (error) {
    console.error('okrStore: unexpected failure while saving', error);
    notify('error');
   } finally {
    pending -= 1;
   }
  });
  return queue;
 }

 /* One retry, because a dropped connection is usually momentary and losing a
    user's edit to it would be worse than a second's delay. Retrying is safe
    even if the first attempt did reach the server: the revision it consumed
    makes the retry a conflict, which resynchronises rather than double-writes. */
 async function send(body, attempts = 2, delayMs = 1200) {
  for (let attempt = 1; ; attempt += 1) {
   try {
    return await request(config.api.workspace, { method: 'PUT', body });
   } catch (error) {
    if (attempt >= attempts) throw error;
    await new Promise(resolve => setTimeout(resolve, delayMs));
   }
  }
 }

 function push(text, baseRevision) {
  return enqueue(async () => {
   let document_;
   try {
    document_ = JSON.parse(text);
   } catch (error) {
    console.error('okrStore: refusing to send unparseable workspace', error);
    return;
   }

   const body = JSON.stringify({
    document: document_, baseRevision: baseRevision || null
   });

   let result;
   try {
    result = await send(body);
   } catch (error) {
    /* The write never reached the server, so the mirror is now ahead of it
       and every later write would be based on a revision the server has
       never seen -- they would all fail too, silently. Fall back to the
       server's document instead: this edit is lost and the app raises its own
       conflict notice, but the session keeps saving from here on. */
    console.error('okrStore: workspace write failed to reach the server', error);
    notify('offline');
    if (!await refresh()) replaceMirror(confirmed);
    return;
   }

   if (result.ok) { confirmed = text; notify('saved'); return; }

   if (result.status === 409 && result.payload && result.payload.document) {
    console.warn('okrStore: workspace changed underneath this session');
    adopt(result.payload.document);
    return;
   }

   console.error('okrStore: workspace write rejected', result.status, result.payload);
   notify(result.status === 403 ? 'readonly' : 'error', result.payload);
   /* Pull the server's truth back so the session stops diverging, falling back
      to the last confirmed state if even that read does not come through. */
   if (!await refresh()) replaceMirror(confirmed);
  });
 }

 async function refresh() {
  let result;
  try {
   result = await request(config.api.workspace, { method: 'GET' });
  } catch {
   return false;
  }
  if (!result.ok || !result.payload) return false;
  if (String(result.payload.revision || '') === mirrorRevision) return false;
  return adopt(result.payload.document);
 }

 /* ------------------------------------------------------------ status pip */

 /* The app owns the DOM inside #app, so status lives in its own fixed node. */
 let statusNode = null;
 let statusTimer = 0;

 const STATUS_TEXT = {
  saved: { text: 'Saved to server', tone: 'ok', linger: 1400 },
  offline: { text: 'Offline — changes are not saved', tone: 'warn', linger: 0 },
  readonly: { text: 'Read-only workspace — change not saved', tone: 'warn', linger: 6000 },
  error: { text: 'Server rejected the change', tone: 'warn', linger: 6000 }
 };

 function notify(kind, payload) {
  const status = STATUS_TEXT[kind];
  if (!status) return;
  if (!statusNode) {
   statusNode = document.createElement('div');
   statusNode.id = 'okrBackendStatus';
   statusNode.setAttribute('role', 'status');
   statusNode.setAttribute('aria-live', 'polite');
   document.body.appendChild(statusNode);
  }
  const detail = payload && payload.error ? `: ${payload.error}` : '';
  statusNode.textContent = status.text + detail;
  statusNode.dataset.tone = status.tone;
  statusNode.dataset.visible = 'true';
  clearTimeout(statusTimer);
  if (status.linger) {
   statusTimer = setTimeout(() => { statusNode.dataset.visible = 'false'; }, status.linger);
  }
 }

 /* ---------------------------------------------------------------- façade */

 const okrStore = {
  getItem(key) {
   if (key === WORKSPACE) return mirror;
   const store = local();
   try { return store ? store.getItem(key) : null; } catch { return null; }
  },

  setItem(key, value) {
   if (key !== WORKSPACE) {
    const store = local();
    try { if (store) store.setItem(key, value); } catch { /* quota or blocked */ }
    return;
   }
   const text = String(value);
   const baseRevision = mirrorRevision;
   mirror = text;
   mirrorRevision = revisionOf(text);
   push(text, baseRevision);
  },

  removeItem(key) {
   if (key === WORKSPACE) { mirror = null; mirrorRevision = ''; return; }
   const store = local();
   try { if (store) store.removeItem(key); } catch { /* blocked */ }
  },

  /* Escape hatches for the page and for tests. */
  get pendingWrites() { return pending; },
  refresh,
  flush() { return queue; }
 };

 Object.defineProperty(globalThis, 'okrStore', { value: okrStore, enumerable: true });

 /* ------------------------------------------------------------- lifecycle */

 if (POLL_MS) {
  let timer = 0;
  const tick = () => {
   timer = setTimeout(async () => {
    if (!document.hidden && !pending) await refresh();
    tick();
   }, POLL_MS);
  };
  tick();
  document.addEventListener('visibilitychange', () => {
   if (!document.hidden && !pending) refresh();
  });
  window.addEventListener('beforeunload', () => clearTimeout(timer));
 }

 /* Don't let the tab close on top of a write that has not reached the server. */
 window.addEventListener('beforeunload', event => {
  if (!pending) return;
  event.preventDefault();
  event.returnValue = '';
 });
})();
