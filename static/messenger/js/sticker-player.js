/**
 * Sticker API used by client.js.
 * Delegates to Telegram-accurate rlottie-wasm (window.MessengerTgs).
 * lottie-web canvas/svg both break these TGS files (pink faces or matte circles).
 */
(function () {
  'use strict';

  function waitForTgs(timeoutMs = 8000) {
    if (window.MessengerTgs) return Promise.resolve(window.MessengerTgs);
    return new Promise((resolve) => {
      const done = () => resolve(window.MessengerTgs || null);
      window.addEventListener('messenger-tgs-ready', done, { once: true });
      window.setTimeout(done, timeoutMs);
    });
  }

  window.MessengerStickers = {
    async mount(host, url, options = {}) {
      const tgs = await waitForTgs();
      if (!tgs) throw new Error('rlottie sticker player unavailable');
      const api = await tgs.mount(host, url, options);
      host._stickerApi = api;
      return api;
    },
    async preview(url, displaySize = 40) {
      const tgs = await waitForTgs();
      if (!tgs?.preview) throw new Error('rlottie sticker preview unavailable');
      return tgs.preview(url, displaySize);
    },
    destroy(host) {
      if (window.MessengerTgs) window.MessengerTgs.destroy(host);
      host._stickerApi = null;
    },
  };

  window.dispatchEvent(new Event('messenger-stickers-ready'));
})();
