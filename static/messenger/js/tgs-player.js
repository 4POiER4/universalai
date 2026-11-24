/**
 * High-quality Telegram TGS/Lottie renderer via rlottie-wasm.
 * Exposes window.MessengerTgs for the classic messenger client.
 */
import createRlottieModule from './rlottie-wasm.js';

const jsonCache = new Map();
const previewCache = new Map();
let modulePromise = null;
const MAX_FPS = 60;
let previewInflight = 0;
const previewWaiters = [];

function previewConcurrencyLimit() {
  const tier = window.__stickerPerfTier || 'medium';
  if (tier === 'low') return 1;
  if (tier === 'medium') return 2;
  return 4;
}

async function acquirePreviewSlot() {
  const limit = previewConcurrencyLimit();
  if (previewInflight < limit) {
    previewInflight += 1;
    return;
  }
  await new Promise(resolve => previewWaiters.push(resolve));
  previewInflight += 1;
}

function releasePreviewSlot() {
  previewInflight = Math.max(0, previewInflight - 1);
  const next = previewWaiters.shift();
  if (next) next();
}

function getModule() {
  if (!modulePromise) {
    modulePromise = createRlottieModule({
      locateFile(path) {
        return `/static/messenger/js/${path}`;
      },
    });
  }
  return modulePromise;
}

async function loadJson(url) {
  if (jsonCache.has(url)) return jsonCache.get(url);
  const response = await fetch(url, { credentials: 'same-origin' });
  if (!response.ok) throw new Error(`Sticker HTTP ${response.status}`);
  const text = await response.text();
  JSON.parse(text);
  jsonCache.set(url, text);
  return text;
}

function pickRenderSize(display, mode = 'full') {
  const tier = window.__stickerPerfTier || 'medium';
  if (mode === 'preview') {
    if (tier === 'low') return Math.max(28, Math.round(display * 1.5));
    if (tier === 'medium') return Math.max(40, Math.round(display * 2));
    return Math.max(48, Math.round(display * 2));
  }
  if (display <= 40) {
    if (tier === 'low') return Math.max(48, Math.round(display * 2));
    return Math.max(64, Math.round(display * 3));
  }
  if (display <= 96) return tier === 'low' ? 96 : 128;
  return tier === 'low' ? 192 : 256;
}

async function renderPreview(url, displaySize = 40) {
  const size = Math.max(16, Number(displaySize) || 40);
  const renderSize = pickRenderSize(size, 'preview');
  const key = `${url}@${renderSize}`;
  if (previewCache.has(key)) return previewCache.get(key);

  const task = (async () => {
    await acquirePreviewSlot();
    try {
      const Module = await getModule();
      const json = await loadJson(url);
      const player = new Module.RlottieWasm();
      const ok = player.load(json);
      if (!ok && ok !== undefined) throw new Error('rlottie failed to load sticker preview');
      const canvas = document.createElement('canvas');
      canvas.width = renderSize;
      canvas.height = renderSize;
      const ctx = canvas.getContext('2d', { alpha: true, willReadFrequently: true });
      const bytes = player.render(0, renderSize, renderSize);
      if (bytes?.length) {
        const imageData = ctx.createImageData(renderSize, renderSize);
        imageData.data.set(bytes.subarray(0, imageData.data.length));
        ctx.putImageData(imageData, 0, 0);
      }
      try { player.delete?.(); } catch (_) { /* ignore */ }
      try {
        return canvas.toDataURL('image/webp', 0.82);
      } catch (_) {
        return canvas.toDataURL('image/png');
      }
    } finally {
      releasePreviewSlot();
    }
  })();

  previewCache.set(key, task);
  return task;
}

function createApi(host) {
  let destroyed = false;
  let raf = 0;
  let player = null;
  let frame = 0;
  let total = 0;
  let fps = MAX_FPS;
  let playing = false;
  let loop = true;
  let canvas = null;
  let ctx = null;
  let renderSize = 256;
  let lastTs = 0;
  let acc = 0;
  let imageData = null;
  let onComplete = null;

  function stopLoop() {
    if (raf) {
      cancelAnimationFrame(raf);
      raf = 0;
    }
    playing = false;
  }

  function paint(frameIndex) {
    if (!player || !ctx || destroyed) return;
    const bytes = player.render(frameIndex, renderSize, renderSize);
    if (!bytes || !bytes.length) return;
    if (!imageData || imageData.width !== renderSize) {
      imageData = ctx.createImageData(renderSize, renderSize);
    }
    imageData.data.set(bytes.subarray(0, imageData.data.length));
    ctx.putImageData(imageData, 0, 0);
  }

  function finishOnce() {
    stopLoop();
    const done = onComplete;
    onComplete = null;
    if (typeof done === 'function') {
      try { done(); } catch (_) { /* ignore */ }
    }
  }

  function tick(ts) {
    if (!playing || destroyed) return;
    if (!lastTs) lastTs = ts;
    const dt = Math.min(64, ts - lastTs);
    lastTs = ts;
    acc += dt;
    const frameMs = 1000 / Math.max(1, fps);
    let advanced = false;
    // Drop catch-up frames — only paint once per rAF.
    while (acc >= frameMs) {
      acc -= frameMs;
      frame += 1;
      advanced = true;
      if (frame >= total) {
        if (loop) frame = 0;
        else {
          frame = Math.max(0, total - 1);
          paint(frame);
          finishOnce();
          return;
        }
      }
    }
    if (advanced) paint(frame);
    raf = requestAnimationFrame(tick);
  }

  return {
    async mount(url, options = {}) {
      loop = Boolean(options.loop);
      onComplete = typeof options.onComplete === 'function' ? options.onComplete : null;
      const display = Math.max(16, Number(options.size) || 168);
      renderSize = pickRenderSize(display);

      const Module = await getModule();
      const json = await loadJson(url);
      if (destroyed) return;

      host.style.width = `${display}px`;
      host.style.height = `${display}px`;
      // Do not force overflow:hidden — reaction chips need visible/unscaled art
      // to fit Telegram-like pills; sticker CSS classes clip when needed.
      host.style.background = 'transparent';

      canvas = document.createElement('canvas');
      canvas.width = renderSize;
      canvas.height = renderSize;
      canvas.style.width = `${display}px`;
      canvas.style.height = `${display}px`;
      canvas.style.display = 'block';
      canvas.style.background = 'transparent';
      canvas.setAttribute('aria-hidden', 'true');
      host.replaceChildren(canvas);
      ctx = canvas.getContext('2d', { alpha: true, willReadFrequently: false });
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'high';

      player = new Module.RlottieWasm();
      const ok = player.load(json);
      if (!ok && ok !== undefined) {
        throw new Error('rlottie failed to load sticker');
      }
      total = Math.max(1, Number(player.frames?.() ?? player.totalFrame?.() ?? 1));
      try {
        const parsed = JSON.parse(json);
        fps = Math.min(MAX_FPS, Math.max(1, Number(parsed.fr) || MAX_FPS));
      } catch (_) {
        fps = MAX_FPS;
      }
      // Default static pose is frame 0 (upright), matching sticker thumbs.
      // preferMidFrame / staticFrame remain available for callers that need a different still.
      let startFrame = 0;
      if (typeof options.staticFrame === 'number' && Number.isFinite(options.staticFrame)) {
        const ratio = Math.min(1, Math.max(0, options.staticFrame));
        startFrame = Math.min(total - 1, Math.max(0, Math.round(ratio * (total - 1))));
      } else if (options.preferMidFrame && options.autoplay === false) {
        startFrame = Math.min(total - 1, Math.max(0, Math.round((total - 1) * 0.45)));
      }
      frame = startFrame;
      lastTs = 0;
      acc = 0;
      imageData = null;
      paint(startFrame);
      if (options.autoplay !== false) {
        playing = true;
        raf = requestAnimationFrame(tick);
      } else if (typeof options.onReady === 'function') {
        options.onReady();
      }
    },
    replay(opts = {}) {
      if (!player || destroyed) return;
      if (opts && typeof opts.loop === 'boolean') loop = opts.loop;
      frame = 0;
      lastTs = 0;
      acc = 0;
      paint(0);
      if (!playing) {
        playing = true;
        raf = requestAnimationFrame(tick);
      }
    },
    /** Stop playback and freeze at frame 0 (upright static pose). */
    stop(atFrame = 0) {
      if (!player || destroyed) return;
      stopLoop();
      const target = Math.min(total - 1, Math.max(0, Number(atFrame) || 0));
      frame = target;
      lastTs = 0;
      acc = 0;
      paint(target);
    },
    destroy() {
      destroyed = true;
      onComplete = null;
      stopLoop();
      try { player?.delete?.(); } catch (_) { /* ignore */ }
      player = null;
      canvas = null;
      ctx = null;
      imageData = null;
      host.replaceChildren();
    },
  };
}

window.MessengerTgs = {
  async mount(host, url, options = {}) {
    const api = createApi(host);
    host._tgs = api;
    await api.mount(url, options);
    return api;
  },
  async preview(url, displaySize = 40) {
    return renderPreview(url, displaySize);
  },
  destroy(host) {
    if (host?._tgs) {
      host._tgs.destroy();
      host._tgs = null;
    }
  },
};

window.dispatchEvent(new Event('messenger-tgs-ready'));
