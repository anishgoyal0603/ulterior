/**
 * Ulterior extension -- the one place that knows which browser it is in.
 *
 * Chrome and Edge expose the extension API as `chrome`; Firefox exposes the
 * same API as `browser` (and a partial `chrome`). Everything else in the
 * extension goes through `ext` so the same files run in all three.
 *
 * Session memory (the price you picked, countdown readings, pop-ups you
 * closed) is kept by the background script, not read directly by the page
 * scripts. The background only hands a page the keys that belong to that
 * page's own site, so a shop's page can never read what you picked on
 * another shop -- and it means no browser-specific access setting is needed.
 */
(function (root) {
  "use strict";
  const ext = (root.browser && root.browser.runtime) ? root.browser : root.chrome;

  async function send(msg) {
    try { return await ext.runtime.sendMessage(msg); } catch (e) { return null; }
  }

  const Session = {
    async get(key) {
      const r = await send({ type: "SESSION_GET", key });
      return r && r.ok ? r.value : null;
    },
    set(key, value) { return send({ type: "SESSION_SET", key, value }); },
  };

  root.UlteriorPlatform = { ext, send, Session };
})(globalThis);
