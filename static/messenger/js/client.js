(() => {
  'use strict';

  const API = '/api/messenger/v1';
  const STICKER_BASE = '/static/messenger/stickers';
  const REACTION_BASE = '/static/messenger/reactions';
  /** Same threshold as the scroll-down button — user is "at bottom" within this range. */
  const CHAT_BOTTOM_THRESHOLD = 140;
  const EMOJI = ['😀','😃','😄','😁','😂','😊','😍','🥰','😎','🤔','😢','😭','😡','👍','👎','👏','🙏','🎉','🔥','❤️','💯','✅','👀','🤝','👌','🚀','⭐','☕','📌','💡'];
  /** Preferred quick bar order — clean e* from stickers/ (not effect r*). */
  const QUICK_REACTION_IDS = ['e077', 'e075', 'e071', 'e021', 'e053', 'e051', 'e069', 'e047'];
  /** Preferred quick bar when manifest.json is loaded (Telegram order). */
  const QUICK_REACTION_EMOJIS = ['👍', '❤', '🔥', '🥰', '👏', '😁', '🤔', '🎉'];
  /** Legacy Unicode → canonical e* store ids (paired effect is r(N+1)). */
  const LEGACY_UNICODE_TO_E = {
    '❓': 'e001', '😭': 'e003', '😡': 'e005', '👎': 'e007', '👌': 'e009',
    '😄': 'e011', '🤔': 'e013', '🙏': 'e015', '😘': 'e017', '😍': 'e019',
    '🎉': 'e021', '😱': 'e027', '🤬': 'e029', '😐': 'e031', '🗿': 'e033',
    '😈': 'e035', '🫡': 'e037', '🙄': 'e039', '💔': 'e041', '😎': 'e043',
    '⚡': 'e045', '👏': 'e047', '🤮': 'e049', '😢': 'e051', '😲': 'e053', '😮': 'e053',
    '👀': 'e055', '🌚': 'e057', '💯': 'e059', '💅': 'e061', '😴': 'e065',
    '✅': 'e067', '🔥': 'e069', '😂': 'e071', '💩': 'e073', '❤️': 'e075', '❤': 'e075',
    '👍': 'e077', '🏆': 'e079',
  };
  let STICKER_PACK = [];
  let REACTION_PACK = [];
  let REACTION_MANIFEST = [];
  const REACTION_BY_EMOJI = new Map();
  const REACTION_BY_LEGACY = new Map();
  let QUICK_REACTIONS = QUICK_REACTION_IDS.slice();
  const state = {
    me: null, csrf: '', token: '', tokenExpiresAt: 0, chats: [], messages: new Map(),
    activeChatId: null, filter: 'all', folder: 'all', search: '', socket: null,
    socketReady: false, heartbeat: null, reconnectTimer: null, reconnectAttempt: 0,
    replyTo: null, editing: null, selected: new Set(), infoOpen: false, infoTab: 'members',
    typing: new Map(), typingTimer: null, uploadCount: 0, pinned: new Map(),
    presence: new Map(),
    myStatus: null,
    groupCall: null,
    activeCallSessions: new Map(),
    emojiTab: 'emoji', emojiSearch: '', pendingReactionAnim: null, pendingStickerAnim: null,
    stickerAnimLock: false, stickerAnimRemaining: 0,
    reactionAnimLock: false, queuedRender: null,
    memberReadCursors: new Map(),
    subscribedChats: new Set(),
    directoryResults: [], directoryTimer: null,
    departments: null, departmentsLoadedAt: 0, departmentsLoading: false,
  };

  const byId = id => document.getElementById(id);
  const all = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  function iconEl(name, cls) {
    if (typeof createIcon === 'function') return createIcon(name, cls);
    return node('span', 'ic');
  }
  function fillIcon(element, name, cls) {
    if (!element) return;
    if (typeof setIcon === 'function') {
      setIcon(element, name, cls);
      return;
    }
    element.replaceChildren(iconEl(name, cls));
  }
  function mountStaticIcons() {
    all('[data-icon]').forEach(element => fillIcon(element, element.dataset.icon));
    all('[data-icon-btn]').forEach(element => fillIcon(element, element.dataset.iconBtn));
  }
  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }
  function button(className, text, title) {
    const element = node('button', className, text);
    element.type = 'button';
    if (title) element.title = title;
    return element;
  }
  function iconButton(className, iconName, title) {
    const element = button(className, undefined, title);
    element.appendChild(iconEl(iconName));
    return element;
  }
  function clear(element) {
    if (!element) return;
    element.querySelectorAll('[data-tgs="1"], [data-lottie="1"]').forEach(host => {
      if (window.MessengerStickers) window.MessengerStickers.destroy(host);
      if (host._lottie) {
        try { host._lottie.destroy(); } catch (_) { /* ignore */ }
        host._lottie = null;
      }
    });
    element.replaceChildren();
  }
  function stickerUrl(sticker) {
    const base = sticker?.base || STICKER_BASE;
    return `${base}/${encodeURIComponent(sticker.file)}`;
  }
  function isLottieSticker(sticker) {
    if (!sticker) return false;
    if (sticker.kind === 'lottie' || sticker.kind === 'tgs') return true;
    const file = String(sticker.file || '').toLowerCase();
    return file.endsWith('.json') || file.endsWith('.tgs');
  }
  function isRasterSticker(sticker) {
    if (!sticker) return false;
    const kind = String(sticker.kind || '').toLowerCase();
    if (kind === 'webp' || kind === 'png' || kind === 'gif' || kind === 'svg') return true;
    const file = String(sticker.file || '').toLowerCase();
    return file.endsWith('.webp') || file.endsWith('.png') || file.endsWith('.gif') || file.endsWith('.svg');
  }
  function normalizeReactionEmoji(value) {
    return String(value || '').normalize('NFC').replace(/\uFE0F/g, '');
  }
  function buildReactionManifestMaps(manifest) {
    REACTION_MANIFEST = Array.isArray(manifest) ? manifest : [];
    REACTION_BY_EMOJI.clear();
    REACTION_BY_LEGACY.clear();
    REACTION_MANIFEST.forEach(entry => {
      if (!entry?.emoji) return;
      REACTION_BY_EMOJI.set(entry.emoji, entry);
      REACTION_BY_EMOJI.set(normalizeReactionEmoji(entry.emoji), entry);
    });
    Object.entries(LEGACY_UNICODE_TO_E).forEach(([unicode, legacyId]) => {
      const entry = REACTION_BY_EMOJI.get(normalizeReactionEmoji(unicode))
        || REACTION_BY_EMOJI.get(unicode);
      if (entry) REACTION_BY_LEGACY.set(legacyId, entry);
    });
    REACTION_MANIFEST.forEach(entry => {
      Object.entries(LEGACY_UNICODE_TO_E).forEach(([unicode, legacyId]) => {
        if (normalizeReactionEmoji(unicode) === normalizeReactionEmoji(entry.emoji)) {
          REACTION_BY_LEGACY.set(legacyId, entry);
        }
      });
    });
  }
  function reactionManifestEntry(id) {
    if (!REACTION_MANIFEST.length) return null;
    const key = String(id || '');
    if (REACTION_BY_EMOJI.has(key)) return REACTION_BY_EMOJI.get(key);
    const norm = normalizeReactionEmoji(key);
    if (REACTION_BY_EMOJI.has(norm)) return REACTION_BY_EMOJI.get(norm);
    const legacy = reactionStoreIdLegacy(key);
    if (REACTION_BY_LEGACY.has(legacy)) return REACTION_BY_LEGACY.get(legacy);
    const parsed = parsePackNum(legacy);
    if (parsed?.kind === 'e' && REACTION_BY_LEGACY.has(legacy)) return REACTION_BY_LEGACY.get(legacy);
    return null;
  }
  function reactionAssetFromManifest(entry, fileKey, fallbackKind) {
    const rel = entry?.files?.[fileKey];
    if (!rel) return null;
    const file = String(rel).replace(/\\/g, '/');
    const lower = file.toLowerCase();
    let kind = fallbackKind;
    if (lower.endsWith('.webp')) kind = 'webp';
    else if (lower.endsWith('.png')) kind = 'png';
    else if (lower.endsWith('.json')) kind = 'lottie';
    else if (lower.endsWith('.tgs')) kind = 'tgs';
    return {
      id: `${entry.emoji}-${fileKey}`,
      file,
      kind,
      title: entry.title || entry.emoji,
      base: REACTION_BASE,
    };
  }
  function reactionStaticImg(emojiId, sizePx) {
    const size = Number(sizePx) || REACTION_CHIP_SIZE;
    const asset = reactionDisplayAsset(emojiId);
    if (!isRasterSticker(asset)) return null;
    const img = document.createElement('img');
    img.src = stickerUrl(asset);
    img.alt = asset.title || '';
    img.draggable = false;
    img.width = size;
    img.height = size;
    img.style.width = '100%';
    img.style.height = '100%';
    img.style.display = 'block';
    img.style.objectFit = 'contain';
    img.style.pointerEvents = 'none';
    return img;
  }
  function whenStickersReady() {
    if (window.MessengerStickers) return Promise.resolve(window.MessengerStickers);
    return new Promise(resolve => {
      window.addEventListener('messenger-stickers-ready', () => resolve(window.MessengerStickers), { once: true });
      window.setTimeout(() => resolve(window.MessengerStickers || null), 2000);
    });
  }
  function guardMessageStickerHost(host) {
    if (!host?.classList?.contains('msg-sticker')) return;
    host.addEventListener('selectstart', event => event.preventDefault());
    host.addEventListener('dragstart', event => event.preventDefault());
  }
  function mountSticker(host, sticker, options = {}) {
    const loop = Boolean(options.loop);
    const autoplay = options.autoplay !== false;
    const size = Number(options.size) || 168;
    host.dataset.tgs = '1';
    host._mountPromise = whenStickersReady().then(api => {
      if (!api || !host.isConnected) return null;
      return api.mount(host, stickerUrl(sticker), {
        loop,
        autoplay,
        size,
        preferMidFrame: Boolean(options.preferMidFrame),
        staticFrame: options.staticFrame,
        onComplete: options.onComplete,
        onReady: options.onReady,
      });
    }).catch(error => {
      console.warn('Sticker render failed', error);
      return null;
    });
    host.addEventListener('click', event => {
      // Only message stickers replay on click. Picker thumbs must let the
      // parent button handle insert-into-composer.
      if (!host.classList.contains('msg-sticker')) return;
      event.stopPropagation();
      host._stickerApi?.replay?.();
    });
    guardMessageStickerHost(host);
    return host;
  }
  function stickerMedia(sticker, className, size, options = {}) {
    if (!isLottieSticker(sticker)) {
      const image = node('img', className || 'sticker-img');
      image.src = stickerUrl(sticker);
      image.alt = sticker.title || t('sticker_label');
      image.draggable = false;
      image.loading = 'lazy';
      if (size) { image.width = size; image.height = size; }
      if ((className || '').includes('msg-sticker')) guardMessageStickerHost(image);
      return image;
    }
    const host = node('div', className || 'sticker-img');
    host.setAttribute('role', 'img');
    host.setAttribute('aria-label', sticker.title || t('sticker_label'));
    if (size) {
      host.style.width = `${size}px`;
      host.style.height = `${size}px`;
    }
    mountSticker(host, sticker, { ...options, size });
    return host;
  }
  function stickerPerfTier() {
    if (window.__stickerPerfTier) return window.__stickerPerfTier;
    const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    const saveData = Boolean(conn?.saveData);
    const slowNet = conn?.effectiveType === '2g' || conn?.effectiveType === 'slow-2g';
    const lowMem = Number(navigator.deviceMemory) > 0 && navigator.deviceMemory <= 4;
    const lowCpu = Number(navigator.hardwareConcurrency) > 0 && navigator.hardwareConcurrency <= 4;
    let tier = 'high';
    if (saveData || slowNet) tier = 'low';
    else if (lowMem || lowCpu) tier = 'medium';
    window.__stickerPerfTier = tier;
    return tier;
  }
  function stickerThumbSize() {
    const tier = stickerPerfTier();
    if (tier === 'low') return 26;
    if (tier === 'medium') return 28;
    return 30;
  }
  function stickerGridCols() {
    const width = byId('emojiPanel')?.getBoundingClientRect().width || 360;
    if (width < 260) return 5;
    if (width < 300) return 6;
    if (width < 340) return 7;
    return 8;
  }
  function mountStickerThumbPreview(item, sticker) {
    if (item.dataset.mounted) return;
    item.dataset.mounted = '1';
    const img = node('img', 'sticker-thumb');
    img.alt = sticker.title || t('sticker_label');
    img.draggable = false;
    img.decoding = 'async';
    item.appendChild(img);
    whenStickersReady().then(api => {
      if (!api?.preview || !item.isConnected) return null;
      return api.preview(stickerUrl(sticker), stickerThumbSize());
    }).then(src => {
      if (src && img.isConnected) img.src = src;
    }).catch(() => {
      delete item.dataset.mounted;
    });
  }
  let stickerVirtualGrid = null;
  let stickerSearchTimer = 0;
  function destroyStickerVirtualGrid() {
    if (!stickerVirtualGrid) return;
    stickerVirtualGrid.destroy();
    stickerVirtualGrid = null;
  }
  function filteredStickerPack() {
    const query = (state.emojiSearch || '').toLowerCase();
    return STICKER_PACK.filter(sticker => {
      if (!String(sticker.id || '').startsWith('s')) return false;
      if (!query) return true;
      const hay = `${sticker.title || ''} ${sticker.id || ''}`.toLowerCase();
      return hay.includes(query);
    });
  }
  function renderStickerVirtualGrid(body) {
    const stickers = filteredStickerPack();
    destroyStickerVirtualGrid();
    if (!stickers.length) {
      const empty = node('div', 'sticker-empty', (state.emojiSearch || '') ? 'Ничего не найдено' : 'Стикеры пока не загружены');
      body.appendChild(empty);
      return;
    }
    const rowHeight = 42;
    const grid = node('div', 'sticker-grid sticker-grid-virtual');
    body.appendChild(grid);
    const mounted = new Map();
    let scrollRaf = 0;
    let cols = stickerGridCols();

    function render() {
      cols = stickerGridCols();
      const totalRows = Math.ceil(stickers.length / cols);
      const scrollTop = body.scrollTop;
      const viewHeight = body.clientHeight || 320;
      const startRow = Math.max(0, Math.floor(scrollTop / rowHeight) - 1);
      const endRow = Math.min(totalRows - 1, Math.ceil((scrollTop + viewHeight) / rowHeight) + 1);
      grid.style.paddingTop = `${startRow * rowHeight}px`;
      grid.style.paddingBottom = `${Math.max(0, totalRows * rowHeight - (endRow + 1) * rowHeight)}px`;
      grid.style.minHeight = `${totalRows * rowHeight}px`;
      grid.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;

      const needed = new Set();
      for (let row = startRow; row <= endRow; row += 1) {
        for (let col = 0; col < cols; col += 1) {
          const index = row * cols + col;
          if (index < stickers.length) needed.add(index);
        }
      }

      for (const [index, item] of [...mounted.entries()]) {
        if (!needed.has(index)) {
          item.remove();
          mounted.delete(index);
        }
      }

      [...needed].sort((a, b) => a - b).forEach(index => {
        let item = mounted.get(index);
        if (!item) {
          const sticker = stickers[index];
          item = button('sticker-btn');
          item.dataset.stickerId = sticker.id;
          item.title = sticker.title || sticker.id;
          item.addEventListener('click', event => {
            event.preventDefault();
            event.stopPropagation();
            insertComposerSticker(sticker.id);
          });
          mounted.set(index, item);
          mountStickerThumbPreview(item, sticker);
        }
        grid.appendChild(item);
      });
    }

    function onScroll() {
      if (scrollRaf) return;
      scrollRaf = requestAnimationFrame(() => {
        scrollRaf = 0;
        render();
      });
    }

    body.addEventListener('scroll', onScroll, { passive: true });
    render();
    stickerVirtualGrid = {
      destroy() {
        body.removeEventListener('scroll', onScroll);
        if (scrollRaf) cancelAnimationFrame(scrollRaf);
        mounted.clear();
        grid.remove();
      },
    };
  }
  function updateStickerGridContent() {
    const body = byId('emojiPanelBody');
    if (!body) return;
    body.scrollTop = 0;
    clear(body);
    renderStickerVirtualGrid(body);
  }
  function scheduleStickerGridUpdate() {
    clearTimeout(stickerSearchTimer);
    stickerSearchTimer = window.setTimeout(updateStickerGridContent, 180);
  }
  /** Settled chip under message — compact Telegram-like pill. */
  const REACTION_CHIP_SIZE = 18;
  // In-place burst — morphs into chip (no fade-out).
  const REACTION_ANIM_SIZE = 40;
  const REACTION_ANIM_MARGIN = -Math.round((REACTION_ANIM_SIZE - REACTION_CHIP_SIZE) / 2);
  // r* comps are 512x512 with a small centered face — zoom canvas so art fills the box.
  const REACTION_ANIM_ART_SCALE = 2.2;
  // Scale/size ease-out from anim box into settled chip.
  const REACTION_SETTLE_MS = 380;
  const REACTION_SETTLE_HOLD_MS = 70;
  /** Quick bar / full-grid thumb — snug, art fully visible. */
  const REACTION_PICKER_SIZE = 24;
  function isAnimLocked() {
    return Boolean(state.stickerAnimLock || state.reactionAnimLock);
  }
  function flushQueuedRenderIfUnlocked() {
    if (isAnimLocked()) return;
    const queued = state.queuedRender;
    state.queuedRender = null;
    if (!queued) return;
    // Reaction-only sync: patch one message — never remount the chat list after settle.
    if (queued.reactionsOnly && queued.messageId != null) {
      const message = findMessage(queued.messageId);
      if (message && patchMessageReactions(message, { preserveAnimating: false })) return;
    }
    renderMessages(queued.keepPosition, {
      force: true,
      scrollToBottom: queued.scrollToBottom,
    });
  }
  function parsePackNum(id) {
    const match = String(id || '').match(/^([er])(\d+)$/i);
    if (!match) return null;
    return { kind: match[1].toLowerCase(), n: Number(match[2]) };
  }
  function padPackId(prefix, n) {
    return `${prefix}${String(n).padStart(3, '0')}`;
  }
  /** Canonical reaction id. With manifest: emoji char; legacy: e* / Unicode map. */
  function reactionStoreIdLegacy(id) {
    const key = String(id || '');
    const parsed = parsePackNum(key);
    if (parsed) {
      if (parsed.kind === 'e') return key;
      if (parsed.kind === 'r') return padPackId('e', Math.max(1, parsed.n - 1));
      return key;
    }
    return LEGACY_UNICODE_TO_E[key] || key;
  }
  function reactionStoreId(id) {
    const entry = reactionManifestEntry(id);
    if (entry) return entry.emoji;
    return reactionStoreIdLegacy(id);
  }
  /** Paired effect id for in-chip play: eN → r(N+1). */
  function reactionEffectId(emojiId) {
    const storeId = reactionStoreId(emojiId);
    const parsed = parsePackNum(storeId);
    if (parsed?.kind === 'e') return padPackId('r', parsed.n + 1);
    const asR = parsePackNum(String(emojiId || ''));
    if (asR?.kind === 'r') return String(emojiId);
    return storeId;
  }
  /** Clean static art — manifest static_icon.webp or legacy e* lottie. */
  function reactionDisplayAsset(emojiId) {
    const entry = reactionManifestEntry(emojiId);
    if (entry) {
      return reactionAssetFromManifest(entry, 'static_icon', 'webp')
        || reactionAssetFromManifest(entry, 'center_icon', 'lottie');
    }
    const eid = reactionStoreIdLegacy(emojiId);
    return stickerById(eid) || {
      id: eid,
      file: `${eid}.json`,
      kind: 'lottie',
      title: eid,
      base: STICKER_BASE,
    };
  }
  /** Telegram-like burst on select — manifest select_animation.tgs. */
  function reactionSelectAsset(emojiId) {
    const entry = reactionManifestEntry(emojiId);
    if (entry) {
      return reactionAssetFromManifest(entry, 'select_animation', 'lottie')
        || reactionAssetFromManifest(entry, 'effect_animation', 'lottie');
    }
    return reactionEffectAsset(emojiId);
  }
  /** Legacy paired effect r* — fallback when manifest has no select_animation. */
  function reactionEffectAsset(emojiId) {
    const rid = reactionEffectId(emojiId);
    return reactionById(rid) || {
      id: rid,
      file: `${rid}.json`,
      kind: 'lottie',
      title: rid,
      base: REACTION_BASE,
    };
  }
  function findReaction(message, emojiId) {
    const storeId = reactionStoreId(emojiId);
    return (message.reactions || []).find(reaction => reactionStoreId(reaction.emoji) === storeId) || null;
  }
  function applyReactionHostBox(host, sizePx, marginPx) {
    const size = Number(sizePx) || REACTION_CHIP_SIZE;
    host.style.width = `${size}px`;
    host.style.height = `${size}px`;
    host.style.minWidth = `${size}px`;
    host.style.minHeight = `${size}px`;
    host.style.flex = `0 0 ${size}px`;
    host.style.flexShrink = '0';
    host.style.aspectRatio = '1';
    if (marginPx === undefined || marginPx === null) host.style.margin = '';
    else host.style.margin = `${marginPx}px`;
    host.style.opacity = '1';
  }
  /**
   * After r* or e* plays: morph the same host 56→chip with opacity ~1,
   * reveal static e* underneath, then freeze — no fade-out vanish.
   */
  function settleReactionChip(host, emojiId, animGen) {
    if (!host?.isConnected) {
      // Host was remounted (e.g. reaction switch) — do not unlock a newer anim.
      releaseReactionAnim(animGen);
      return;
    }
    const settleSize = REACTION_CHIP_SIZE;
    const animating = host.classList.contains('react-sticker-anim');
    if (host.dataset.reactSettling === '1') return;
    host.dataset.reactSettling = '1';

    const mountSettledFrom = (frozenImg) => {
      if (!host?.isConnected) {
        releaseReactionAnim(animGen);
        return;
      }
      const under = host.querySelector('.react-sticker-settle-under');
      if (under) under.remove();
      try { window.MessengerStickers?.destroy(host); } catch (_) { /* ignore */ }
      if (host._lottie) {
        try { host._lottie.destroy(); } catch (_) { /* ignore */ }
        host._lottie = null;
      }
      host.replaceChildren();
      host.removeAttribute('data-tgs');
      host._stickerApi = null;
      host.classList.remove('react-sticker-anim', 'react-sticker-settling');
      host.classList.add('react-sticker');
      applyReactionHostBox(host, settleSize, null);
      host.style.removeProperty('--react-art-scale');
      delete host.dataset.reactSettling;

      if (frozenImg && frozenImg.tagName === 'IMG' && frozenImg.src) {
        frozenImg.alt = '';
        frozenImg.draggable = false;
        frozenImg.width = settleSize;
        frozenImg.height = settleSize;
        frozenImg.style.width = '100%';
        frozenImg.style.height = '100%';
        frozenImg.style.display = 'block';
        frozenImg.style.pointerEvents = 'none';
        host.appendChild(frozenImg);
        releaseReactionAnim(animGen);
        return;
      }

      const staticImg = reactionStaticImg(emojiId, settleSize);
      if (staticImg) {
        host.appendChild(staticImg);
        releaseReactionAnim(animGen);
        return;
      }

      const asset = reactionDisplayAsset(emojiId);
      // Static = frame 0 upright pose (never mid-frame tilt).
      mountSticker(host, asset, {
        loop: false,
        autoplay: false,
        size: settleSize,
      });
      freezeStickerHost(host, settleSize).finally(() => {
        releaseReactionAnim(animGen);
      });
    };

    if (!animating) {
      mountSettledFrom(null);
      return;
    }

    // Preload settled static art under the playing layer so the chip never blanks.
    let underlay = host.querySelector('.react-sticker-settle-under');
    let underlayReady = Promise.resolve();
    if (!underlay) {
      underlay = node('div', 'react-sticker-settle-under');
      underlay.style.width = `${settleSize}px`;
      underlay.style.height = `${settleSize}px`;
      host.insertBefore(underlay, host.firstChild);
      const staticImg = reactionStaticImg(emojiId, settleSize);
      if (staticImg) {
        underlay.appendChild(staticImg);
        underlayReady = staticImg.complete
          ? Promise.resolve()
          : new Promise(resolve => {
            staticImg.onload = resolve;
            staticImg.onerror = resolve;
          });
      } else {
        const asset = reactionDisplayAsset(emojiId);
        mountSticker(underlay, asset, {
          loop: false,
          autoplay: false,
          size: settleSize,
        });
        underlayReady = freezeStickerHost(underlay, settleSize);
      }
    }

    let finished = false;
    let morphDone = false;
    let fadeDone = false;
    let freezeDone = false;
    const canvas = host.querySelector('canvas');
    const tryFinishMorph = () => {
      if (finished || !morphDone || !fadeDone || !freezeDone) return;
      finished = true;
      host.removeEventListener('transitionend', onTransitionEnd);
      if (canvas) canvas.removeEventListener('transitionend', onCanvasTransitionEnd);
      window.clearTimeout(host._reactSettleTimer);
      host._reactSettleTimer = null;
      const img = underlay?.querySelector('img');
      const keep = img ? img.cloneNode(true) : null;
      if (underlay) {
        try { window.MessengerStickers?.destroy(underlay); } catch (_) { /* ignore */ }
        underlay.remove();
        underlay = null;
      }
      // One frame after fade so the swap is invisible.
      window.requestAnimationFrame(() => mountSettledFrom(keep));
    };
    const onTransitionEnd = (event) => {
      if (event.target !== host) return;
      if (event.propertyName !== 'width' && event.propertyName !== 'height') return;
      morphDone = true;
      tryFinishMorph();
    };
    const onCanvasTransitionEnd = (event) => {
      if (event.target !== canvas) return;
      if (event.propertyName !== 'opacity') return;
      fadeDone = true;
      tryFinishMorph();
    };
    host.addEventListener('transitionend', onTransitionEnd);
    if (canvas) {
      canvas.addEventListener('transitionend', onCanvasTransitionEnd);
    } else {
      fadeDone = true;
    }
    underlayReady.finally(() => {
      freezeDone = true;
      tryFinishMorph();
    });

    const beginMorph = () => {
      applyReactionHostBox(host, REACTION_ANIM_SIZE, REACTION_ANIM_MARGIN);
      host.style.opacity = '1';
      void host.offsetWidth;
      host.classList.add('react-sticker-settling');
      applyReactionHostBox(host, settleSize, 0);
      host.style.setProperty('--react-art-scale', '1');
    };

    host._reactSettleTimer = window.setTimeout(() => {
      morphDone = true;
      fadeDone = true;
      freezeDone = true;
      tryFinishMorph();
    }, REACTION_SETTLE_MS + REACTION_SETTLE_HOLD_MS + 120);

    window.setTimeout(beginMorph, REACTION_SETTLE_HOLD_MS);
  }
  function reactionGlyph(emoji, className, options = {}) {
    const size = Number(options.size) || REACTION_CHIP_SIZE;
    const cls = className || 'react-sticker';
    const storeId = reactionStoreId(emoji);
    const freeze = options.freeze !== false;

    // In-place at chip slot: play select_animation once, then settle to static_icon.
    if (options.animate) {
      const effect = reactionSelectAsset(storeId);
      const playAsset = isLottieSticker(effect) ? effect : reactionDisplayAsset(storeId);
      if (!isLottieSticker(playAsset) && !isRasterSticker(playAsset)) return node('span', 'emo', storeId);
      const animGen = state.pendingReactionAnim?.gen;
      const animSize = REACTION_ANIM_SIZE;
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        settleReactionChip(host, storeId, animGen);
      };
      // Play in a larger chip-anchored box; CSS zooms internal r* art to fill (not a speck).
      // Keep the flex item square: flex-basis must match animSize (not chip settle size).
      const host = stickerMedia(playAsset, `${cls} react-sticker-anim`, animSize, {
        loop: false,
        autoplay: true,
        onComplete: finish,
      });
      host.style.width = `${animSize}px`;
      host.style.height = `${animSize}px`;
      host.style.minWidth = `${animSize}px`;
      host.style.minHeight = `${animSize}px`;
      host.style.flex = `0 0 ${animSize}px`;
      host.style.flexShrink = '0';
      host.style.aspectRatio = '1';
      host.style.setProperty('--react-art-scale', String(REACTION_ANIM_ART_SCALE));
      window.setTimeout(finish, 4000);
      return host;
    }

    // Picker (live) / settled chips: static_icon webp or legacy e* frame 0.
    const asset = reactionDisplayAsset(storeId);
    if (isRasterSticker(asset)) {
      const host = node('div', cls);
      host.setAttribute('role', 'img');
      host.setAttribute('aria-label', asset.title || storeId);
      host.style.width = `${size}px`;
      host.style.height = `${size}px`;
      host.appendChild(reactionStaticImg(storeId, size));
      return host;
    }
    if (!isLottieSticker(asset)) return node('span', 'emo', storeId);
    const host = stickerMedia(asset, cls, size, {
      loop: false,
      autoplay: false,
    });
    if (freeze) freezeStickerHost(host, size);
    return host;
  }
  function countStickersInText(text) {
    const only = parseStickerIds(text);
    if (only.length) return only.length;
    return parseMessageSegments(text).filter(segment => segment.type === 'sticker').length;
  }
  function beginStickerAnim(clientId) {
    const id = String(clientId);
    state.pendingStickerAnim = {
      clientId: id,
      animate: true,
      didAnimate: false,
      settleTimer: null,
    };
    state.stickerAnimLock = true;
    state.stickerAnimRemaining = 1;
    state.pendingStickerAnim.settleTimer = window.setTimeout(() => {
      if (state.pendingStickerAnim?.clientId !== id) return;
      completeStickerSend(id);
    }, 4500);
  }
  function releaseStickerAnimSlot(options = {}) {
    const clientId = state.pendingStickerAnim?.clientId;
    if (!state.stickerAnimLock) return;
    state.stickerAnimRemaining = Math.max(0, state.stickerAnimRemaining - 1);
    if (state.stickerAnimRemaining > 0) return;
    completeStickerSend(clientId, options);
  }
  let reactionAnimGen = 0;
  function beginReactionAnim(messageId, emoji) {
    reactionAnimGen += 1;
    state.pendingReactionAnim = {
      messageId,
      emoji,
      animate: true,
      gen: reactionAnimGen,
    };
    state.reactionAnimLock = true;
  }
  function releaseReactionAnim(gen) {
    // Ignore stale settles from a previous reaction after an instant switch.
    if (gen != null && state.pendingReactionAnim && state.pendingReactionAnim.gen !== gen) return;
    state.reactionAnimLock = false;
    state.pendingReactionAnim = null;
    flushQueuedRenderIfUnlocked();
  }
  function shouldAnimateReaction(message, emoji) {
    const pending = state.pendingReactionAnim;
    if (!pending || !pending.animate) return false;
    if (String(pending.messageId) !== String(message.id)) return false;
    if (reactionStoreId(pending.emoji) !== reactionStoreId(emoji)) return false;
    pending.animate = false;
    return true;
  }
  function ensureOptimisticReaction(message, emoji) {
    if (!Array.isArray(message.reactions)) message.reactions = [];
    const storeId = reactionStoreId(emoji);
    // Enforce one-mine-per-message in optimistic UI before the new chip mounts.
    clearMyOtherReactions(message, storeId);
    const existing = findReaction(message, storeId);
    if (existing) {
      if (!existing.mine) {
        existing.mine = true;
        existing.reacted_by_me = true;
        existing.count = (existing.count || 1) + 1;
      }
      return;
    }
    message.reactions.push({ emoji: storeId, count: 1, mine: true, reacted_by_me: true });
  }
  function removeOptimisticReaction(message, emoji) {
    if (!Array.isArray(message.reactions)) return;
    const existing = findReaction(message, emoji);
    if (!existing) return;
    const index = message.reactions.indexOf(existing);
    if ((existing.count || 1) <= 1) message.reactions.splice(index, 1);
    else {
      existing.count = (existing.count || 1) - 1;
      existing.mine = false;
      existing.reacted_by_me = false;
    }
  }
  /** One reaction per user (matches backend): drop every other mine chip instantly. */
  function clearMyOtherReactions(message, keepEmoji) {
    if (!Array.isArray(message.reactions)) return;
    const keep = reactionStoreId(keepEmoji);
    const others = message.reactions.filter(
      reaction => reaction.mine && reactionStoreId(reaction.emoji) !== keep,
    );
    others.forEach(reaction => removeOptimisticReaction(message, reaction.emoji));
  }
  function findMyReaction(message) {
    return (message.reactions || []).find(reaction => reaction.mine) || null;
  }
  function shouldAnimateMessageStickers(message) {
    const pending = state.pendingStickerAnim;
    if (!pending || !pending.animate) return false;
    if (pending.clientId && message.client_id && String(pending.clientId) === String(message.client_id)) {
      pending.animate = false;
      pending.didAnimate = true;
      return true;
    }
    if (pending.messageId && String(pending.messageId) === String(message.id)) {
      pending.animate = false;
      pending.didAnimate = true;
      return true;
    }
    return false;
  }
  function completeStickerSend(clientId, options = {}) {
    const id = String(clientId || state.pendingStickerAnim?.clientId || '');
    if (!id) return;
    const pending = state.pendingStickerAnim;
    if (pending?.settleTimer) window.clearTimeout(pending.settleTimer);
    state.stickerAnimLock = false;
    state.stickerAnimRemaining = 0;
    state.pendingStickerAnim = null;
    state.queuedRender = null;
    const message = activeMessages().find(item => String(item.client_id) === id);
    if (message) patchMessageDelivered(message, id);
    patchPendingDeliveredRows();
    // After a natural play-once finish, keep the live TGS host (stop at frame 0) so
    // click replay works like older messages. Full re-render only on timeout/fallback.
    if (options.rerender !== false) {
      renderMessages(false, { force: true });
      renderChats();
    }
    flushQueuedRenderIfUnlocked();
  }
  function afterStickerSendRender(clientId) {
    window.requestAnimationFrame(() => {
      const pending = state.pendingStickerAnim;
      if (!pending || String(pending.clientId) !== String(clientId)) return;
      if (!pending.didAnimate) completeStickerSend(clientId);
    });
  }
  function remountMessageBody(message) {
    if (!message) return false;
    const row = messageRowEl(message.id)
      || (message.client_id && document.querySelector(`.msg-row[data-message-id="pending-${message.client_id}"]`));
    if (!row) return false;
    row.dataset.messageId = message.id;
    row.classList.remove('optimistic');
    const bubble = row.querySelector('.bubble');
    if (!bubble) return false;
    bubble.querySelectorAll('[data-tgs="1"]').forEach(host => {
      try { window.MessengerStickers?.destroy(host); } catch (_) { /* ignore */ }
    });
    bubble.querySelectorAll('.sticker-row, .msg-text, .msg-rich-text').forEach(el => el.remove());
    bubble.classList.remove('sticker-only', 'sticker-cluster', 'emoji-only-bubble');
    appendMessageBody(bubble, message);
    return true;
  }
  function mountMessageSticker(sticker, size, animate) {
    if (!animate || !isLottieSticker(sticker)) {
      if (animate) releaseStickerAnimSlot();
      return stickerMedia(sticker, 'msg-sticker', size, { loop: false, autoplay: false });
    }
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      host._stickerApi?.stop?.(0);
      releaseStickerAnimSlot({ rerender: false });
    };
    const host = stickerMedia(sticker, 'msg-sticker', size, {
      loop: false,
      autoplay: true,
      onComplete: finish,
    });
    window.setTimeout(() => {
      if (!settled) finish();
    }, 5000);
    return host;
  }
  function initials(value) {
    return String(value || '?').trim().split(/\s+/).slice(0, 2)
      .map(part => part.charAt(0).toUpperCase()).join('') || '?';
  }
  function avatarUrlForUsername(username) {
    if (!username) return null;
    if (username === state.me?.username) return state.me?.avatar_url || null;
    for (const chat of state.chats) {
      const member = (chat.members || []).find(item => item.username === username);
      if (member?.avatar_url) return member.avatar_url;
    }
    return null;
  }
  function chatPeer(chat) {
    if (!chat || chat.kind === 'group') return null;
    return (chat.members || []).find(member => member.username !== state.me?.username) || null;
  }
  const ACTIVITY_IDLE_MS = 5 * 60 * 1000;
  const ACTIVITY_HIDDEN_MS = 2 * 60 * 1000;
  let lastActivityAt = Date.now();
  let hiddenSince = document.hidden ? Date.now() : null;
  ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart'].forEach(type => {
    window.addEventListener(type, () => { lastActivityAt = Date.now(); }, { passive: true });
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { hiddenSince = Date.now(); }
    else { hiddenSince = null; lastActivityAt = Date.now(); }
  });
  function currentActivity() {
    const now = Date.now();
    if (hiddenSince && now - hiddenSince > ACTIVITY_HIDDEN_MS) return 'idle';
    if (!hiddenSince && now - lastActivityAt > ACTIVITY_IDLE_MS) return 'idle';
    return 'active';
  }
  function userPresence(username) {
    if (!username) return 'offline';
    if (username === state.me?.username) return state.myStatus?.status || 'offline';
    return state.presence.get(username)?.status || 'offline';
  }
  function userStatusText(username) {
    if (!username) return null;
    const entry = username === state.me?.username ? state.myStatus : state.presence.get(username);
    return entry?.status_text || null;
  }
  function presenceStatusLabel(status) {
    if (status === 'dnd') return t('status_dnd');
    if (status === 'online') return t('status_online');
    if (status === 'away') return t('status_away');
    return t('status_offline');
  }
  function presenceDotClass(status) {
    return (status === 'online' || status === 'away' || status === 'dnd') ? status : '';
  }
  function presenceLabelWithText(username) {
    const label = presenceStatusLabel(userPresence(username));
    const text = userStatusText(username);
    return text ? `${label} — ${text}` : label;
  }
  function chatPresenceSubtitle(chat) {
    if (!chat) return 'Безопасная внутренняя переписка';
    if (chat.kind === 'group') return `${(chat.members || []).length} участников`;
    const peer = chatPeer(chat);
    if (!peer) return 'ваши заметки';
    return presenceLabelWithText(peer.username);
  }
  function renderChatHeaderSubtitle(chat) {
    if (state.typing.get(state.activeChatId)) return state.typing.get(state.activeChatId);
    return chatPresenceSubtitle(chat);
  }
  function collectPresenceUsernames() {
    const users = new Set();
    state.chats.forEach(chat => {
      (chat.members || []).forEach(member => {
        if (member.username && member.username !== state.me?.username) users.add(member.username);
      });
    });
    return [...users];
  }
  async function loadPresence(usernames) {
    const unique = [...new Set((usernames || []).filter(Boolean))];
    if (!unique.length) return;
    try {
      const rows = await api(`${API}/presence?users=${unique.map(item => encodeURIComponent(item)).join(',')}`);
      (rows || []).forEach(row => {
        if (row?.username) state.presence.set(row.username, {
          status: row.status || 'offline',
          status_text: row.status_text || null,
          status_until: row.status_until || null,
        });
      });
    } catch (error) {
      console.warn('Presence snapshot failed', error);
    }
  }
  async function loadMyStatus() {
    try {
      const row = await api(`${API}/presence/me`);
      state.myStatus = {
        status: row.status || 'offline',
        status_text: row.status_text || null,
        status_until: row.status_until || null,
        manual_status: row.manual_status || null,
      };
    } catch (error) {
      console.warn('Presence/me failed', error);
    }
  }
  function avatar(name, size = 46, options = {}) {
    const username = options.username || null;
    const url = options.url || (username ? avatarUrlForUsername(username) : null);
    const el = node('div', `avatar messenger-avatar avatar-size-${size}`);
    if (url) {
      const img = document.createElement('img');
      img.alt = '';
      img.decoding = 'async';
      img.loading = 'lazy';
      img.src = url;
      img.addEventListener('error', () => {
        img.remove();
        if (!el.textContent) el.textContent = initials(name);
      }, { once: true });
      el.appendChild(img);
    } else {
      el.textContent = initials(name);
    }
    if (options.dot !== false) {
      const presence = options.presence ?? (username ? userPresence(username) : null);
      const dotClass = presenceDotClass(presence);
      if (dotClass) {
        el.appendChild(node('span', `online-dot ${dotClass}`));
      }
    }
    return el;
  }
  async function uploadAvatar(file) {
    const form = new FormData();
    form.append('file', file);
    const payload = await jsonResponse(await fetch('/api/me/avatar', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRF-Token': state.csrf },
      body: form,
    }));
    state.me.avatar_url = payload.avatar_url || null;
    await loadChats();
    renderChats();
    if (state.activeChatId) renderMessages(true);
  }
  async function removeAvatar() {
    const payload = await jsonResponse(await fetch('/api/me/avatar', {
      method: 'DELETE',
      credentials: 'same-origin',
      headers: { 'X-CSRF-Token': state.csrf, Accept: 'application/json' },
    }));
    state.me.avatar_url = payload.avatar_url || null;
    await loadChats();
    renderChats();
    if (state.activeChatId) renderMessages(true);
  }
  function formatSize(bytes) {
    const value = Number(bytes || 0);
    if (value < 1024) return `${value} B`;
    if (value < 1048576) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / 1048576).toFixed(1)} MB`;
  }
  function fileExtension(name) {
    const parts = String(name || '').trim().split('.');
    return parts.length > 1 ? parts.pop().toLowerCase() : '';
  }
  function fileIconKind(ext) {
    const key = String(ext || '').toLowerCase();
    if (['doc', 'docx', 'rtf', 'odt'].includes(key)) return 'kind-docx';
    if (key === 'pdf') return 'kind-pdf';
    if (['xls', 'xlsx', 'csv', 'ods'].includes(key)) return 'kind-xls';
    if (['ppt', 'pptx'].includes(key)) return 'kind-ppt';
    if (['zip', 'rar', '7z', 'tar', 'gz'].includes(key)) return 'kind-zip';
    return 'kind-default';
  }
  function fileIconLabel(ext) {
    const key = String(ext || '').toLowerCase();
    if (key === 'docx') return 'docx';
    if (key === 'xlsx') return 'xlsx';
    if (key === 'pptx') return 'pptx';
    return key.slice(0, 4) || 'file';
  }
  function buildFileIcon(ext, className = 'att-file-ic') {
    const icon = node('span', `${className} ${fileIconKind(ext)}`);
    icon.appendChild(node('span', 'ext', fileIconLabel(ext)));
    return icon;
  }
  function formatFileDate(iso) {
    if (!iso) return '';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';
    const now = new Date();
    const sameYear = date.getFullYear() === now.getFullYear();
    const datePart = date.toLocaleDateString('ru', {
      month: 'short',
      day: 'numeric',
      ...(sameYear ? {} : { year: 'numeric' }),
    }).replace('.', '');
    const timePart = date.toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' });
    return `${datePart} в ${timePart}`;
  }
  function fileMonthLabel(iso) {
    if (!iso) return 'Без даты';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return 'Без даты';
    const label = date.toLocaleDateString('ru', { month: 'long' });
    return label.charAt(0).toUpperCase() + label.slice(1);
  }
  function monthKey(iso) {
    const date = new Date(iso || 0);
    if (Number.isNaN(date.getTime())) return 'unknown';
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
  }
  function messageTime(message) {
    return new Date(message.created_at).toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' });
  }
  function toast(message, actionText, action) {
    const item = node('div', 'toast');
    item.appendChild(node('span', '', message));
    if (actionText && action) {
      const undo = button('undo', actionText);
      undo.addEventListener('click', () => { action(); item.remove(); });
      item.appendChild(undo);
    }
    byId('toastHost').appendChild(item);
    window.setTimeout(() => {
      item.classList.add('toast-out');
      window.setTimeout(() => item.remove(), 250);
    }, 3600);
  }
  function later(feature) { toast(`${feature} — будет добавлено позже`); }
  function setStatus(text, connected = false) {
    const status = byId('connectionStatus');
    if (!status) return;
    if (connected || !text) {
      status.hidden = true;
      status.textContent = '';
      status.classList.remove('connected');
      return;
    }
    status.hidden = false;
    status.textContent = text;
    status.classList.remove('connected');
  }

  async function jsonResponse(response) {
    let body = {};
    try { body = await response.json(); } catch (_) { body = {}; }
    if (!response.ok) {
      const error = new Error(body.error || body.detail || `HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return body;
  }
  async function loadIdentity() {
    const identity = await jsonResponse(await fetch('/api/me', {
      credentials: 'same-origin', headers: { Accept: 'application/json' },
    }));
    if (!identity.logged_in) {
      window.location.assign('/login?next=/messenger');
      throw new Error('Требуется авторизация');
    }
    state.me = identity;
    state.csrf = identity.csrf_token;
  }
  async function refreshToken() {
    const body = await jsonResponse(await fetch('/api/auth/messenger-token', {
      method: 'POST', credentials: 'same-origin',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf },
      body: '{}',
    }));
    state.token = body.access_token;
    state.tokenExpiresAt = Date.now() + (body.expires_in * 1000);
    return body;
  }
  async function ensureToken() {
    if (!state.token || Date.now() > state.tokenExpiresAt - 30000) await refreshToken();
  }
  async function api(path, options = {}, retry = true) {
    await ensureToken();
    const headers = new Headers(options.headers || {});
    headers.set('Accept', 'application/json');
    headers.set('Authorization', `Bearer ${state.token}`);
    if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
    const response = await fetch(path, { ...options, headers, credentials: 'same-origin' });
    if (response.status === 401 && retry) {
      state.token = '';
      await ensureToken();
      return api(path, options, false);
    }
    return jsonResponse(response);
  }
  function featureError(error, label) {
    if (error.status === 404 || error.status === 405 || error.status === 422) later(label);
    else toast(error.message);
  }

  function chatTitle(chat) {
    if (!chat) return 'Выберите чат';
    if (chat.kind === 'group') return chat.title;
    const peer = (chat.members || []).find(member => member.username !== state.me.username);
    return peer ? peer.display_name : 'Личный чат';
  }
  function chatSubtitle(chat) {
    if (!chat) return 'Безопасная внутренняя переписка';
    if (chat.kind === 'group') return `${(chat.members || []).length} участников`;
    const peer = (chat.members || []).find(member => member.username !== state.me.username);
    return peer ? `@${peer.username}` : 'ваши заметки';
  }
  function activeChat() { return state.chats.find(chat => chat.id === state.activeChatId) || null; }
  function allMessages(chatId = state.activeChatId) {
    return state.messages.get(chatId) || [];
  }
  function activeMessages() {
    return allMessages().filter(message => !message.deleted_at);
  }
  function findMessage(id) {
    return allMessages().find(message => String(message.id) === String(id));
  }
  function removeMessageFromState(messageOrId, chatId = state.activeChatId) {
    const id = typeof messageOrId === 'object' ? messageOrId.id : messageOrId;
    const targetChatId = typeof messageOrId === 'object' ? messageOrId.chat_id : chatId;
    const messages = state.messages.get(targetChatId) || [];
    state.messages.set(
      targetChatId,
      messages.filter(item => String(item.id) !== String(id)),
    );
  }
  function refreshChatPreview(chatId) {
    const chat = state.chats.find(item => chatIdEq(item.id, chatId));
    if (!chat) return;
    const last = allMessages(chatId).filter(message => !message.deleted_at).at(-1);
    if (last) {
      chat.last_message_preview = messagePreviewText(last);
      chat.last_sequence = last.sequence;
      chat.updated_at = last.created_at;
    } else {
      chat.last_message_preview = '';
      chat.last_sequence = 0;
    }
    renderChatList();
  }
  function renderChatList() {
    renderFolders();
    renderChats();
  }
  function bumpChatToTop(chat) {
    const index = state.chats.indexOf(chat);
    if (index > 0) {
      state.chats.splice(index, 1);
      state.chats.unshift(chat);
    }
  }
  function touchChatFromMessage(message, options = {}) {
    const chat = state.chats.find(item => chatIdEq(item.id, message.chat_id));
    if (!chat || message.deleted_at) return null;
    const sequence = Number(message.sequence) || 0;
    const preview = messagePreviewText(message);
    const shouldReplaceLast = !sequence
      || sequence >= Number(chat.last_sequence || 0)
      || !chat.last_message_preview;
    if (shouldReplaceLast) {
      if (sequence) chat.last_sequence = Math.max(Number(chat.last_sequence) || 0, sequence);
      if (preview || !chat.last_message_preview) chat.last_message_preview = preview;
      chat.updated_at = message.created_at || new Date().toISOString();
      bumpChatToTop(chat);
    }
    const isActive = chatIdEq(message.chat_id, state.activeChatId);
    const isIncoming = message.sender_username !== state.me?.username;
    if (options.bumpUnread && !isActive && isIncoming) {
      chat.unread_count = (Number(chat.unread_count) || 0) + 1;
    }
    return chat;
  }
  function updateChatFromMessage(message, options = {}) {
    const chat = touchChatFromMessage(message, options);
    if (chat && options.renderList !== false) renderChatList();
    return chat;
  }
  function updateChatPreviewOptimistic(message) {
    const chat = state.chats.find(item => chatIdEq(item.id, message.chat_id));
    if (!chat) return;
    const preview = messagePreviewText(message);
    if (preview) chat.last_message_preview = preview;
    chat.updated_at = message.created_at || new Date().toISOString();
    bumpChatToTop(chat);
    renderChatList();
  }
  function normalizeMessage(message) {
    const chatId = message.chat_id || state.activeChatId;
    return {
      reactions: [], attachments: [], pinned: false,
      ...message,
      reactions: Array.isArray(message.reactions)
        ? message.reactions.map(reaction => ({
          ...reaction,
          // Keep server emoji key for DELETE; UI resolves via reactionStoreId → e*.
          mine: Boolean(reaction.mine ?? reaction.reacted_by_me),
        }))
        : Object.entries(message.reactions || {}).map(([emoji, users]) => ({
          emoji, count: Array.isArray(users) ? users.length : Number(users.count || users),
          mine: Array.isArray(users) ? users.includes(state.me.username) : Boolean(users.mine),
        })),
      attachments: (message.attachments || []).map(attachment => ({
        ...attachment,
        chat_id: attachment.chat_id || chatId,
        content_type: attachment.content_type || attachment.media_type || 'application/octet-stream',
      })),
    };
  }
  function chatIdEq(left, right) {
    return String(left || '') === String(right || '');
  }
  function syncChatSubscriptions() {
    if (!state.socketReady) return;
    const ids = new Set(state.chats.map(chat => String(chat.id)));
    ids.forEach(id => {
      if (state.subscribedChats.has(id)) return;
      socketSend({ type: 'subscribe', chat_id: id });
      state.subscribedChats.add(id);
    });
    [...state.subscribedChats].forEach(id => {
      if (ids.has(id)) return;
      socketSend({ type: 'unsubscribe', chat_id: id });
      state.subscribedChats.delete(id);
    });
  }
  let liveSyncTimer = null;
  function scheduleLiveSync() {
    window.clearTimeout(liveSyncTimer);
    liveSyncTimer = window.setTimeout(() => {
      syncLiveState().catch(() => {});
    }, 350);
  }
  async function syncLiveState() {
    await loadChats({ skipAutoOpen: true });
    syncChatSubscriptions();
    if (state.activeChatId) await refreshActiveChat();
  }
  async function refreshActiveChat() {
    const chatId = state.activeChatId;
    if (!chatId) return;
    const batch = await api(`${API}/chats/${encodeURIComponent(chatId)}/messages?limit=100`);
    if (!Array.isArray(batch)) return;
    batch.forEach(raw => mergeMessage(raw));
    await loadReadState(chatId);
    if (!chatIdEq(chatId, state.activeChatId)) return;
    renderMessages(true);
    renderPinned();
    const last = batch.filter(message => !message.deleted_at).at(-1);
    if (last) await markRead(chatId, last.sequence);
  }
  function chatReadCursors(chatId) {
    if (!state.memberReadCursors.has(chatId)) state.memberReadCursors.set(chatId, new Map());
    return state.memberReadCursors.get(chatId);
  }
  function recomputeOutgoingRead(chatId) {
    const cursors = chatReadCursors(chatId);
    const others = [...cursors.entries()]
      .filter(([username]) => username !== state.me?.username)
      .map(([, sequence]) => Number(sequence) || 0);
    const minRead = others.length ? Math.min(...others) : 0;
    (state.messages.get(chatId) || []).forEach(message => {
      if (message.sender_username !== state.me?.username || message.optimistic) return;
      message.read = others.length > 0 && Number(message.sequence) <= minRead;
    });
  }
  function patchOutgoingCheckmarks() {
    all('.msg-row.out').forEach(row => {
      const message = findMessage(row.dataset.messageId);
      if (!message || message.optimistic) return;
      const check = row.querySelector('.check');
      if (!check) return;
      const isRead = message.read === true;
      check.classList.toggle('read', isRead);
      check.classList.toggle('sent', !isRead);
      fillIcon(check, isRead ? 'checks' : 'check');
    });
  }
  async function loadReadState(chatId) {
    try {
      const data = await api(`${API}/chats/${encodeURIComponent(chatId)}/read-state`);
      const map = new Map();
      (data.members || []).forEach(member => {
        map.set(member.username, Number(member.sequence) || 0);
      });
      state.memberReadCursors.set(chatId, map);
      recomputeOutgoingRead(chatId);
    } catch (_) { /* Read state is optional for rendering. */ }
  }
  function mergeMessage(raw) {
    const message = normalizeMessage(raw);
    if (message.client_id) message.client_id = String(message.client_id);
    if (message.id && !String(message.id).startsWith('pending-')) {
      message.optimistic = false;
    }
    if (message.sender_username === state.me?.username && !message.optimistic && message.read !== true) {
      message.read = false;
    }
    const messages = state.messages.get(message.chat_id) || [];
    const index = messages.findIndex(item => String(item.id) === String(message.id)
      || (item.client_id && message.client_id && String(item.client_id) === String(message.client_id))
      || (message.client_id && String(item.id) === `pending-${message.client_id}`));
    if (index >= 0) messages[index] = message; else messages.push(message);
    messages.sort((a, b) => Number(a.sequence || Number.MAX_SAFE_INTEGER)
      - Number(b.sequence || Number.MAX_SAFE_INTEGER));
    state.messages.set(message.chat_id, messages);
    return message;
  }
  function parseStickerId(text) {
    const ids = parseStickerIds(text);
    return ids.length === 1 ? ids[0] : null;
  }
  function parseMessageSegments(text) {
    const raw = String(text || '');
    if (!raw) return [];
    const re = /\[\[sticker:([a-zA-Z0-9_-]{1,24})\]\]/g;
    const segments = [];
    let cursor = 0;
    let match;
    while ((match = re.exec(raw))) {
      if (match.index > cursor) {
        segments.push({ type: 'text', value: raw.slice(cursor, match.index) });
      }
      segments.push({ type: 'sticker', id: match[1] });
      cursor = match.index + match[0].length;
    }
    if (cursor < raw.length) segments.push({ type: 'text', value: raw.slice(cursor) });
    return segments;
  }
  function parseStickerIds(text) {
    const segments = parseMessageSegments(String(text || '').trim());
    if (!segments.length) return [];
    if (segments.some(segment => segment.type === 'text' && segment.value.replace(/\s+/g, '') !== '')) {
      return [];
    }
    return segments.filter(segment => segment.type === 'sticker').map(segment => segment.id);
  }
  const COMPOSER_STICKER_SIZE = 22;
  function stickerClusterSize(count) {
    // Telegram-like jumbo sizes for sticker-only messages (after send).
    if (count <= 1) return 128;
    if (count === 2) return 96;
    if (count === 3) return 80;
    if (count === 4) return 68;
    if (count === 5) return 60;
    if (count <= 7) return 52;
    return 40;
  }
  function isEmojiOnlyMessage(text) {
    const raw = String(text || '').trim();
    if (!raw || parseStickerIds(raw).length) return false;
    try {
      const withoutEmoji = raw
        .replace(/\p{Extended_Pictographic}(?:\uFE0F|\u200D\p{Extended_Pictographic})*/gu, '')
        .replace(/\s+/g, '');
      return withoutEmoji.length === 0;
    } catch (_) {
      return false;
    }
  }
  function countEmojiGlyphs(text) {
    try {
      const matches = String(text || '').match(
        /\p{Extended_Pictographic}(?:\uFE0F|\u200D\p{Extended_Pictographic})*/gu
      );
      return matches ? matches.length : 0;
    } catch (_) {
      return 0;
    }
  }
  function emojiOnlyClass(count) {
    if (count <= 1) return 'emoji-only e1';
    if (count === 2) return 'emoji-only e2';
    if (count === 3) return 'emoji-only e3';
    if (count <= 5) return 'emoji-only e5';
    if (count <= 8) return 'emoji-only e8';
    return 'emoji-only';
  }
  function resolveSticker(stickerId) {
    return stickerById(stickerId) || reactionById(stickerId) || {
      id: stickerId,
      file: `${stickerId}.json`,
      kind: 'lottie',
      title: stickerId,
      base: String(stickerId).startsWith('r') ? REACTION_BASE : STICKER_BASE,
    };
  }
  function stickerPayload(id) { return `[[sticker:${id}]]`; }
  function stickerById(id) { return STICKER_PACK.find(item => item.id === id) || null; }
  function reactionById(id) {
    return REACTION_PACK.find(item => item.id === id) || null;
  }
  function reactionChip(className, emoji, size = REACTION_PICKER_SIZE) {
    const storeId = reactionStoreId(emoji);
    const item = button(className);
    const host = reactionGlyph(storeId, 'react-sticker', { size, freeze: false });
    item.appendChild(host);
    item.title = reactionDisplayAsset(storeId).title || storeId;
    item.dataset.emoji = storeId;
    return item;
  }
  function messagePreviewText(message) {
    if (!message || message.deleted_at) return '';
    const stickerOnlyIds = parseStickerIds(message.text);
    if (stickerOnlyIds.length === 1) return t('sticker_label');
    if (stickerOnlyIds.length > 1) return `Стикеры (${stickerOnlyIds.length})`;
    const segments = parseMessageSegments(message.text || '');
    if (segments.some(segment => segment.type === 'sticker')) {
      const plain = segments
        .filter(segment => segment.type === 'text')
        .map(segment => segment.value)
        .join('')
        .replace(/\s+/g, ' ')
        .trim();
      return plain ? plain.slice(0, 160) : t('sticker_label');
    }
    if (message.text) return String(message.text).replace(/\n/g, ' ').slice(0, 160);
    if (message.attachments && message.attachments.length) return 'Вложение';
    return '';
  }
  function formatChatPreview(preview) {
    const raw = String(preview || '').trim();
    if (!raw) return '';
    const stickerOnlyIds = parseStickerIds(raw);
    if (stickerOnlyIds.length === 1) return t('sticker_label');
    if (stickerOnlyIds.length > 1) return `Стикеры (${stickerOnlyIds.length})`;
    const segments = parseMessageSegments(raw);
    if (segments.some(segment => segment.type === 'sticker')) {
      const plain = segments
        .filter(segment => segment.type === 'text')
        .map(segment => segment.value)
        .join('')
        .replace(/\s+/g, ' ')
        .trim();
      return (plain || t('sticker_label')).slice(0, 160);
    }
    return raw.replace(/\n/g, ' ').slice(0, 160);
  }
  async function loadAssetPack(url, base) {
    const response = await fetch(url, { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const pack = await response.json();
    return (pack.stickers || []).map(item => ({
      id: String(item.id || '').trim(),
      file: String(item.file || '').trim(),
      kind: String(item.kind || 'svg'),
      title: String(item.title || item.id || t('sticker_label')),
      base,
    })).filter(item => item.id && item.file && !/[\\/]/.test(item.file));
  }
  async function loadReactionManifest() {
    try {
      const response = await fetch(`${REACTION_BASE}/manifest.json`, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) return false;
      const manifest = await response.json();
      if (!Array.isArray(manifest) || !manifest.length) return false;
      buildReactionManifestMaps(manifest);
      return true;
    } catch (_) {
      return false;
    }
  }
  async function loadStickerPack() {
    try {
      const hasManifest = await loadReactionManifest();
      const [emojiPack, reactionPack] = await Promise.all([
        loadAssetPack(`${STICKER_BASE}/pack.json`, STICKER_BASE).catch(() => []),
        hasManifest ? Promise.resolve([]) : loadAssetPack(`${REACTION_BASE}/pack.json`, REACTION_BASE).catch(() => []),
      ]);
      STICKER_PACK = emojiPack.filter(item => String(item.id || '').startsWith('s'));
      REACTION_PACK = reactionPack;
      if (REACTION_MANIFEST.length) {
        const preferred = QUICK_REACTION_EMOJIS.filter(emoji => reactionManifestEntry(emoji));
        QUICK_REACTIONS = preferred.length
          ? preferred
          : REACTION_MANIFEST.slice(0, 8).map(entry => entry.emoji);
      } else if (STICKER_PACK.length) {
        const preferred = QUICK_REACTION_IDS.filter(id => STICKER_PACK.some(item => item.id === id));
        QUICK_REACTIONS = preferred.length
          ? preferred
          : STICKER_PACK.slice(0, 8).map(item => item.id);
      } else {
        QUICK_REACTIONS = QUICK_REACTION_IDS.slice();
      }
    } catch (_) {
      STICKER_PACK = [];
      REACTION_PACK = [];
      REACTION_MANIFEST = [];
      REACTION_BY_EMOJI.clear();
      REACTION_BY_LEGACY.clear();
      QUICK_REACTIONS = QUICK_REACTION_IDS.slice();
    }
  }

  function unreadMessageTotal(chats = state.chats) {
    return chats.reduce((sum, chat) => sum + Number(chat.unread_count || 0), 0);
  }
  function renderFolders() {
    const wrap = byId('folders');
    clear(wrap);
    const unreadTotal = unreadMessageTotal();
    [
      ['all', t('folder_all'), unreadTotal],
      ['unread', t('folder_unread'), unreadTotal],
      ['direct', t('folder_direct'), 0],
      ['group', t('folder_group'), 0],
    ].forEach(([key, label, count]) => {
      const tab = button(`folder-tab${state.folder === key ? ' active' : ''}`);
      tab.appendChild(node('span', '', label));
      if (count) tab.appendChild(node('span', 'badge', String(count)));
      tab.addEventListener('click', () => { state.folder = key; renderFolders(); renderChats(); });
      wrap.appendChild(tab);
    });
  }
  function renderChats() {
    const list = byId('chatList');
    clear(list);
    if (state.filter === 'people') { renderPeople(list); return; }
    const query = state.search.trim().toLocaleLowerCase('ru');
    const chats = state.chats.filter(chat => {
      if (state.filter === 'group' && chat.kind !== 'group') return false;
      if (state.filter === 'channel') return false;
      if (state.folder === 'unread' && !(Number(chat.unread_count) > 0)) return false;
      if (state.folder === 'direct' && chat.kind !== 'direct') return false;
      if (state.folder === 'group' && chat.kind !== 'group') return false;
      return !query || chatTitle(chat).toLocaleLowerCase('ru').includes(query)
        || chatSubtitle(chat).toLocaleLowerCase('ru').includes(query)
        || chatPresenceSubtitle(chat).toLocaleLowerCase('ru').includes(query)
        || String(chat.last_message_preview || '').toLocaleLowerCase('ru').includes(query);
    });
    // Always re-derive order from last-message time instead of trusting
    // accumulated array mutations (bumpChatToTop on live messages vs. a
    // fresh server fetch replacing state.chats wholesale) — those two
    // sources can briefly disagree, which read as the list "jumping
    // around on its own" instead of staying pinned to last-message order.
    chats.sort((a, b) => new Date(b.updated_at || 0) - new Date(a.updated_at || 0));
    if (!chats.length) {
      list.appendChild(node('div', 'messenger-empty', 'Диалоги не найдены'));
    }
    chats.forEach(chat => {
      const item = button(`chat-item${chatIdEq(chat.id, state.activeChatId) ? ' active' : ''}`);
      item.dataset.chatId = chat.id;
      item.appendChild(avatar(chatTitle(chat), 54, {
        username: chatPeer(chat)?.username,
        url: chatPeer(chat)?.avatar_url,
      }));
      const body = node('span', 'ci-body');
      const first = node('span', 'ci-row');
      first.appendChild(node('span', 'ci-name', chatTitle(chat)));
      first.appendChild(node('span', 'ci-time', chat.last_sequence && chat.updated_at
        ? new Date(chat.updated_at).toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' }) : ''));
      const preview = node('span', 'ci-preview');
      const typers = state.typing.get(chat.id);
      const previewText = typers
        || formatChatPreview(chat.last_message_preview)
        || (chat.kind === 'direct' ? chatPresenceSubtitle(chat) : chatSubtitle(chat));
      preview.appendChild(node('span', typers ? 'ci-text typing-text' : 'ci-text', previewText));
      if (Number(chat.unread_count) > 0) preview.appendChild(node('span', 'badge', String(chat.unread_count)));
      body.append(first, preview);
      item.appendChild(body);
      item.addEventListener('click', () => openChat(chat.id));
      list.appendChild(item);
    });
    renderDirectoryResults(list, new Set(state.chats.map(chat => chatPeer(chat)?.username).filter(Boolean)));
  }

  function scheduleDirectorySearch(query) {
    window.clearTimeout(state.directoryTimer);
    const q = query.trim();
    if (q.length < 2) {
      if (state.directoryResults.length) { state.directoryResults = []; renderChats(); }
      return;
    }
    state.directoryTimer = window.setTimeout(async () => {
      try {
        state.directoryResults = await api(`${API}/directory/search?q=${encodeURIComponent(q)}`);
      } catch (error) {
        state.directoryResults = [];
      }
      renderChats();
    }, 300);
  }

  function renderDirectoryResults(list, existingPeers) {
    if (!state.search.trim() || !state.directoryResults.length) return;
    const fresh = state.directoryResults.filter(entry => !existingPeers.has(entry.username));
    if (!fresh.length) return;
    list.appendChild(node('div', 'directory-results-label', 'Найти по логину'));
    fresh.forEach(entry => {
      const item = button('chat-item directory-result');
      item.appendChild(avatar(entry.display_name, 54, { username: entry.username, url: entry.avatar_url }));
      const body = node('span', 'ci-body');
      const first = node('span', 'ci-row');
      first.appendChild(node('span', 'ci-name', entry.display_name));
      body.appendChild(first);
      body.appendChild(node('span', 'ci-preview', personSubtitle(entry)));
      item.appendChild(body);
      item.addEventListener('click', () => startDirectChat(entry.username));
      list.appendChild(item);
    });
  }

  function personSubtitle(entry) {
    return [entry.title, entry.department].filter(Boolean).join(' · ') || `@${entry.username}`;
  }

  const DEPARTMENTS_TTL_MS = 5 * 60 * 1000;
  async function loadDepartments(force = false) {
    if (state.departmentsLoading) return;
    if (!force && state.departments && Date.now() - state.departmentsLoadedAt < DEPARTMENTS_TTL_MS) return;
    state.departmentsLoading = true;
    try {
      state.departments = await api(`${API}/directory/departments`);
      state.departmentsLoadedAt = Date.now();
    } catch (error) {
      toast(error.message);
      state.departments = state.departments || [];
    } finally {
      state.departmentsLoading = false;
    }
    if (state.filter === 'people') renderChats();
  }

  function renderPeople(list) {
    if (!state.departments) {
      list.appendChild(node('div', 'messenger-empty', 'Загрузка сотрудников…'));
      loadDepartments();
      return;
    }
    const query = state.search.trim().toLocaleLowerCase('ru');
    const matches = entry => !query || [entry.display_name, entry.username, entry.title, entry.department]
      .some(value => String(value || '').toLocaleLowerCase('ru').includes(query));
    let shown = 0;
    state.departments.forEach(department => {
      const members = department.members.filter(matches);
      if (!members.length) return;
      shown += members.length;
      list.appendChild(node('div', 'directory-results-label department-label',
        `${department.name || 'Без отдела'} · ${members.length}`));
      members.forEach(entry => {
        const item = button('chat-item person-item');
        item.appendChild(avatar(entry.display_name, 46, { username: entry.username, url: entry.avatar_url }));
        const body = node('span', 'ci-body');
        const first = node('span', 'ci-row');
        first.appendChild(node('span', 'ci-name', entry.display_name));
        body.appendChild(first);
        body.appendChild(node('span', 'ci-preview', entry.title || `@${entry.username}`));
        item.appendChild(body);
        item.addEventListener('click', () => {
          setRailFilter('all');
          startDirectChat(entry.username);
        });
        list.appendChild(item);
      });
    });
    if (!shown) list.appendChild(node('div', 'messenger-empty', query ? 'Сотрудники не найдены' : 'Справочник пуст'));
  }

  function setRailFilter(filter) {
    state.filter = filter;
    all('.rail-filter').forEach(item => item.classList.toggle('active', item.dataset.filter === filter));
    const folders = document.querySelector('.folders-wrap');
    if (folders) folders.hidden = filter === 'people';
    if (filter === 'people') loadDepartments();
  }

  async function startDirectChat(username) {
    try {
      const chat = await api(`${API}/chats`, {
        method: 'POST', body: JSON.stringify({ kind: 'direct', members: [username] }),
      });
      state.search = '';
      const input = byId('searchInput');
      if (input) input.value = '';
      state.directoryResults = [];
      await loadChats({ skipAutoOpen: true });
      await openChat(chat.id);
    } catch (error) { toast(error.message); }
  }

  function renderHeader() {
    const chat = activeChat();
    byId('chatTitle').textContent = chatTitle(chat);
    const subtitle = renderChatHeaderSubtitle(chat);
    const subtitleEl = byId('chatSubtitle');
    subtitleEl.textContent = subtitle;
    const isTyping = state.typing.has(state.activeChatId);
    subtitleEl.classList.toggle('typing', isTyping);
    const peer = chatPeer(chat);
    const peerStatus = peer ? userPresence(peer.username) : null;
    subtitleEl.classList.toggle('presence-online', !isTyping && peerStatus === 'online');
    subtitleEl.classList.toggle('presence-offline', !isTyping && peerStatus === 'offline');
    byId('callButton').hidden = !chat;
    byId('messageSearchButton').hidden = !chat;
    byId('chatMenuButton').hidden = !chat;
    renderCallSessionBar();
    if (window.MessengerNotify) window.MessengerNotify.applyWindowTitle(chat, unreadMessageTotal());
  }
  function dayLabel(dateValue) {
    const date = new Date(dateValue);
    const today = new Date();
    const yesterday = new Date(today);
    yesterday.setDate(today.getDate() - 1);
    if (date.toDateString() === today.toDateString()) return 'Сегодня';
    if (date.toDateString() === yesterday.toDateString()) return 'Вчера';
    return date.toLocaleDateString('ru', { day: 'numeric', month: 'long' });
  }
  function appendReply(bubble, message) {
    if (!message.reply_to_id) return;
    const replied = findMessage(message.reply_to_id);
    const preview = message.reply_preview || {};
    const quote = node('button', 'reply-quote');
    quote.type = 'button';
    const body = node('span', 'rq-body');
    const name = replied
      ? (replied.sender_username === state.me.username ? 'Вы' : replied.sender_display_name)
      : (preview.sender_username === state.me.username ? 'Вы' : (preview.sender_username || 'Ответ'));
    const text = replied
      ? messagePreviewText(replied) || 'Вложение'
      : (preview.deleted_at ? ''
        : (parseStickerIds(preview.text).length ? t('sticker_label') : (preview.text || t('reply_original_message'))));
    body.appendChild(node('span', 'rq-name', name));
    body.appendChild(node('span', 'rq-text', text));
    quote.appendChild(body);
    quote.addEventListener('click', () => scrollToMessage(message.reply_to_id));
    bubble.appendChild(quote);
  }
  function appendForward(bubble, message) {
    if (!message.forwarded_from_message_id) return;
    const label = node('div', 'fwd-label');
    label.appendChild(iconEl('forward'));
    label.appendChild(document.createTextNode(` Переслано от ${message.forwarded_sender_username || 'участника'}`));
    bubble.appendChild(label);
  }
  function attachmentUrl(attachment) {
    const chatId = attachment.chat_id || state.activeChatId;
    return `${API}/chats/${encodeURIComponent(chatId)}/attachments/${encodeURIComponent(attachment.id)}`;
  }
  async function attachmentBlob(attachment, inline = false, retry = true) {
    await ensureToken();
    const response = await fetch(`${attachmentUrl(attachment)}?inline=${inline ? 'true' : 'false'}`, {
      credentials: 'same-origin',
      headers: { Authorization: `Bearer ${state.token}` },
    });
    if (response.status === 401 && retry) {
      state.token = '';
      return attachmentBlob(attachment, inline, false);
    }
    if (!response.ok) throw new Error(`Не удалось получить файл: HTTP ${response.status}`);
    return response.blob();
  }
  async function showAttachmentImage(image, attachment) {
    try {
      const blob = await attachmentBlob(attachment, true);
      const url = URL.createObjectURL(blob);
      image.src = url;
      image.addEventListener('load', () => URL.revokeObjectURL(url), { once: true });
    } catch (_) {
      image.alt = 'Изображение недоступно';
    }
  }
  async function downloadAttachment(attachment) {
    try {
      const blob = await attachmentBlob(attachment);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = attachment.original_name || attachment.name || 'attachment';
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      toast(error.message);
    }
  }
  function appendAttachment(bubble, attachment, chat) {
    const type = attachment.content_type || attachment.mime_type || '';
    if (type.startsWith('image/')) {
      const wrap = node('button', 'att-image real-image');
      wrap.type = 'button';
      const image = document.createElement('img');
      image.alt = attachment.original_name || attachment.name || 'Изображение';
      image.loading = 'lazy';
      wrap.appendChild(image);
      const autoload = !window.MessengerNotify || window.MessengerNotify.shouldAutoloadMedia(chat);
      if (autoload) {
        showAttachmentImage(image, attachment);
        wrap.addEventListener('click', () => downloadAttachment(attachment));
      } else {
        wrap.classList.add('att-image-deferred');
        wrap.appendChild(node('span', 'att-image-load-hint', 'Нажмите, чтобы загрузить'));
        wrap.addEventListener('click', () => {
          wrap.classList.remove('att-image-deferred');
          wrap.replaceChildren(image);
          showAttachmentImage(image, attachment);
          wrap.addEventListener('click', () => downloadAttachment(attachment));
        }, { once: true });
      }
      bubble.appendChild(wrap);
      return;
    }
    const card = button('att-file');
    const name = attachment.original_name || attachment.name || 'Файл';
    const ext = fileExtension(name);
    const iconWrap = buildFileIcon(ext, 'att-file-ic');
    card.appendChild(iconWrap);
    const body = node('span', 'att-file-body');
    body.appendChild(node('span', 'att-file-name', name));
    body.appendChild(node('span', 'att-file-size', formatSize(attachment.size || attachment.size_bytes)));
    card.appendChild(body);
    card.addEventListener('click', () => downloadAttachment(attachment));
    bubble.appendChild(card);
  }
  function appendInfoFileRow(container, attachment, createdAt) {
    const name = attachment.original_name || attachment.name || 'Файл';
    const ext = fileExtension(name);
    const row = button('file-row');
    const icon = buildFileIcon(ext, 'file-row-ic');
    row.appendChild(icon);
    const body = node('span', 'file-row-body');
    body.appendChild(node('span', 'file-row-name', name));
    body.appendChild(node('span', 'file-row-size', formatSize(attachment.size || attachment.size_bytes)));
    body.appendChild(node('span', 'file-row-date', formatFileDate(createdAt)));
    row.appendChild(body);
    row.addEventListener('click', () => downloadAttachment(attachment));
    container.appendChild(row);
  }
  function renderInfoFiles(tabBody) {
    const items = [];
    activeMessages().forEach(message => {
      (message.attachments || []).forEach(attachment => {
        if ((attachment.content_type || '').startsWith('image/')) return;
        items.push({ attachment, createdAt: message.created_at });
      });
    });
    items.sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));
    if (!items.length) {
      tabBody.appendChild(node('div', 'messenger-empty', 'Здесь пока ничего нет'));
      return;
    }
    const list = node('div', 'file-list');
    let currentMonth = '';
    items.forEach(item => {
      const key = monthKey(item.createdAt);
      if (key !== currentMonth) {
        currentMonth = key;
        list.appendChild(node('div', 'file-month', fileMonthLabel(item.createdAt)));
      }
      appendInfoFileRow(list, item.attachment, item.createdAt);
    });
    tabBody.appendChild(list);
  }
  function messageRowEl(messageId) {
    return document.querySelector(`.msg-row[data-message-id="${CSS.escape(String(messageId))}"]`);
  }
  function buildMessageMeta(message, outgoing) {
    const meta = node('span', 'msg-meta');
    if (message.optimistic) {
      meta.appendChild(node('span', '', 'отправка…'));
      return meta;
    }
    if (message.edited_at) meta.appendChild(node('span', 'edited', 'изменено'));
    if (message.pinned) meta.appendChild(iconEl('pin'));
    meta.appendChild(node('span', '', messageTime(message)));
    if (outgoing) {
      const isRead = message.read === true;
      const check = node('span', isRead ? 'check read' : 'check sent');
      check.appendChild(iconEl(isRead ? 'checks' : 'check'));
      meta.appendChild(check);
    }
    return meta;
  }
  function appendMessageHoverActions(bubble, message) {
    if (message.deleted_at || message.optimistic || bubble.querySelector('.msg-hover-actions')) return;
    const hover = node('div', 'msg-hover-actions');
    [['smile', 'Реакция', () => openReactionPicker(message, bubble)],
      ['reply', 'Ответить', () => startReply(message)],
      ['more', 'Ещё', event => openMessageMenu(message, event.currentTarget)]].forEach(([iconName, title, action]) => {
      const control = iconButton('mha-btn', iconName, title);
      control.addEventListener('click', event => { event.stopPropagation(); action(event); });
      hover.appendChild(control);
    });
    bubble.appendChild(hover);
  }
  /** Patch delivered state on an optimistic row without remounting sticker animation. */
  function patchMessageDelivered(message, clientId) {
    if (!message) return false;
    const resolved = { ...message, optimistic: false };
    if (resolved.client_id) resolved.client_id = String(resolved.client_id);
    const row = (clientId && document.querySelector(`.msg-row[data-message-id="pending-${clientId}"]`))
      || (resolved.client_id && document.querySelector(`.msg-row[data-message-id="pending-${resolved.client_id}"]`))
      || messageRowEl(resolved.id);
    if (!row) return false;
    row.dataset.messageId = resolved.id;
    row.classList.remove('optimistic');
    const bubble = row.querySelector('.bubble');
    if (!bubble) return false;
    const oldMeta = bubble.querySelector('.msg-meta');
    if (!oldMeta) return false;
    const outgoing = resolved.sender_username === state.me.username;
    oldMeta.replaceWith(buildMessageMeta(resolved, outgoing));
    appendMessageHoverActions(bubble, resolved);
    return true;
  }
  function syncOutgoingMessageStatus(rawMessage, clientId) {
    const merged = mergeMessage(rawMessage);
    if (merged.sender_username !== state.me.username) return merged;
    updateChatFromMessage(merged);
    if (state.pendingStickerAnim?.clientId === String(clientId || merged.client_id || '')) {
      state.pendingStickerAnim.messageId = merged.id;
    }
    patchMessageDelivered(merged, clientId || merged.client_id);
    patchPendingDeliveredRows();
    if (!isAnimLocked()) renderMessages(false, { force: true });
    return merged;
  }
  function patchPendingDeliveredRows() {
    activeMessages().forEach(message => {
      if (!message.client_id || message.optimistic) return;
      const pendingId = `pending-${message.client_id}`;
      const row = document.querySelector(`.msg-row[data-message-id="${pendingId}"]`)
        || document.querySelector(`.msg-row.optimistic[data-message-id="${CSS.escape(String(message.id))}"]`);
      if (row?.classList.contains('optimistic')) patchMessageDelivered(message, message.client_id);
    });
    document.querySelectorAll('.msg-row.optimistic.out').forEach(row => {
      const message = findMessage(row.dataset.messageId)
        || activeMessages().find(item => `pending-${item.client_id}` === row.dataset.messageId);
      if (message && !message.optimistic) patchMessageDelivered(message, message.client_id);
    });
  }
  // The server already groups by a normalized emoji, but this stays as a
  // defensive merge for any reaction rows that predate that normalization
  // (or arrive from a stale cache) — same-emoji entries would otherwise
  // render as separate chips instead of one summed count.
  function mergedReactions(message) {
    const byStoreId = new Map();
    (message.reactions || []).forEach(reaction => {
      const storeId = reactionStoreId(reaction.emoji);
      const existing = byStoreId.get(storeId);
      if (existing) {
        existing.count += Number(reaction.count) || 1;
        existing.mine = existing.mine || Boolean(reaction.mine);
      } else {
        byStoreId.set(storeId, { ...reaction, count: Number(reaction.count) || 1 });
      }
    });
    return Array.from(byStoreId.values());
  }
  function buildReactionsRow(message) {
    if (!message.reactions?.length) return null;
    const row = node('div', 'reactions');
    mergedReactions(message).forEach(reaction => {
      const item = button(`reaction${reaction.mine ? ' mine' : ''}`);
      item.dataset.emoji = reaction.emoji;
      const animate = shouldAnimateReaction(message, reaction.emoji);
      item.appendChild(reactionGlyph(reaction.emoji, undefined, {
        size: REACTION_CHIP_SIZE,
        animate,
      }));
      item.appendChild(node('span', 'reaction-count', String(reaction.count || 1)));
      item.addEventListener('click', () => toggleReaction(message, reaction.emoji, reaction.mine));
      row.appendChild(item);
    });
    return row;
  }
  function appendReactions(bubble, message) {
    const row = buildReactionsRow(message);
    if (row) bubble.appendChild(row);
  }
  function ensureBubbleFooter(bubble, message) {
    const hasReactions = Boolean(message.reactions?.length);
    bubble.classList.toggle('has-footer', hasReactions);
    if (!hasReactions) return null;

    let footer = bubble.querySelector(':scope > .bubble-footer');
    if (!footer) {
      footer = node('div', 'bubble-footer');
      const hover = bubble.querySelector('.msg-hover-actions');
      if (hover) bubble.insertBefore(footer, hover);
      else bubble.appendChild(footer);
    }

    const meta = bubble.querySelector('.msg-meta');
    if (meta && meta.parentElement !== footer) footer.appendChild(meta);

    const orphan = bubble.querySelector(':scope > .reactions');
    if (orphan && orphan.parentElement !== footer) {
      if (!orphan.querySelector('.react-sticker-anim, .react-sticker-settling')) {
        footer.insertBefore(orphan, footer.querySelector('.msg-meta'));
      }
    }
    return footer;
  }
  function teardownBubbleFooter(bubble) {
    const footer = bubble.querySelector(':scope > .bubble-footer');
    if (!footer) {
      bubble.classList.remove('has-footer');
      return;
    }
    const meta = footer.querySelector('.msg-meta');
    const textWrap = bubble.querySelector('.bubble-text-wrap');
    if (meta) {
      if (textWrap && !textWrap.querySelector('.msg-meta')) textWrap.appendChild(meta);
      else if (!textWrap) {
        const hover = bubble.querySelector('.msg-hover-actions');
        if (hover) bubble.insertBefore(meta, hover);
        else bubble.appendChild(meta);
      }
    }
    footer.remove();
    bubble.classList.remove('has-footer');
  }
  function reactionsHost(bubble) {
    return bubble.querySelector(':scope > .bubble-footer .reactions')
      || bubble.querySelector(':scope > .reactions')
      || bubble.querySelector('.reactions');
  }
  /**
   * Patch one message's .reactions row in place — never remount the message list.
   * preserveAnimating: keep the playing/settling chip host mounted (API/WS mid-anim).
   */
  function patchMessageReactions(message, options = {}) {
    const msgRow = messageRowEl(message.id);
    if (!msgRow) return false;
    const bubble = msgRow.querySelector('.bubble');
    if (!bubble) return false;

    const pending = state.pendingReactionAnim;
    const animatingThis = Boolean(
      state.reactionAnimLock
      && pending
      && String(pending.messageId) === String(message.id),
    );
    const preserve = Boolean(options.preserveAnimating) && animatingThis;
    const footer = message.reactions?.length ? ensureBubbleFooter(bubble, message) : null;
    let reactionsEl = reactionsHost(bubble);

    if (preserve && reactionsEl) {
      const animStoreId = reactionStoreId(pending.emoji);
      const wanted = new Map(
        (message.reactions || []).map(reaction => [reactionStoreId(reaction.emoji), reaction]),
      );
      Array.from(reactionsEl.querySelectorAll('.reaction')).forEach(el => {
        const storeId = reactionStoreId(el.dataset.emoji);
        const data = wanted.get(storeId);
        if (!data) {
          if (
            storeId === animStoreId
            && el.querySelector('.react-sticker-anim, .react-sticker-settling')
          ) return;
          el.remove();
          return;
        }
        wanted.delete(storeId);
        el.classList.toggle('mine', Boolean(data.mine));
        el.dataset.emoji = data.emoji;
        const countEl = el.querySelector('.reaction-count');
        if (countEl) countEl.textContent = String(data.count || 1);
      });
      wanted.forEach(reaction => {
        const item = button(`reaction${reaction.mine ? ' mine' : ''}`);
        item.dataset.emoji = reaction.emoji;
        item.appendChild(reactionGlyph(reaction.emoji, undefined, {
          size: REACTION_CHIP_SIZE,
          animate: false,
        }));
        item.appendChild(node('span', 'reaction-count', String(reaction.count || 1)));
        item.addEventListener('click', () => toggleReaction(message, reaction.emoji, reaction.mine));
        reactionsEl.appendChild(item);
      });
      if (
        !(message.reactions || []).length
        && !reactionsEl.querySelector('.react-sticker-anim, .react-sticker-settling')
      ) {
        reactionsEl.remove();
      }
      return true;
    }

    const next = buildReactionsRow(message);
    if (!next) {
      if (reactionsEl) reactionsEl.remove();
      teardownBubbleFooter(bubble);
      return true;
    }
    if (reactionsEl) reactionsEl.replaceWith(next);
    else if (footer) footer.insertBefore(next, footer.querySelector('.msg-meta'));
    else {
      const hover = bubble.querySelector('.msg-hover-actions');
      if (hover) bubble.insertBefore(next, hover);
      else bubble.appendChild(next);
      ensureBubbleFooter(bubble, message);
    }
    return true;
  }
  /** Prefer in-place reaction patch; only full-render if the message row is missing. */
  function syncMessageReactions(message, options = {}) {
    if (!message) return false;
    if (patchMessageReactions(message, options)) return true;
    if (isAnimLocked() && !options.force) {
      // Don't downgrade an already-queued full remount to reactions-only.
      if (!state.queuedRender || state.queuedRender.reactionsOnly) {
        state.queuedRender = {
          keepPosition: true,
          reactionsOnly: true,
          messageId: message.id,
        };
      }
      return false;
    }
    renderMessages(true, { force: Boolean(options.force) });
    return true;
  }
  function appendMessageBody(bubble, message) {
    if (message.deleted_at) return;
    const raw = String(message.text || '');
    if (!raw) return;

    const stickerOnlyIds = parseStickerIds(raw);
    if (stickerOnlyIds.length) {
      const size = stickerClusterSize(stickerOnlyIds.length);
      const animate = shouldAnimateMessageStickers(message);
      bubble.classList.add('sticker-only');
      if (stickerOnlyIds.length > 1) bubble.classList.add('sticker-cluster');
      const row = node('div', `sticker-row count-${Math.min(stickerOnlyIds.length, 8)}`);
      stickerOnlyIds.forEach(stickerId => {
        row.appendChild(mountMessageSticker(resolveSticker(stickerId), size, animate));
      });
      bubble.appendChild(row);
      return;
    }

    const segments = parseMessageSegments(raw);
    const hasInlineStickers = segments.some(segment => segment.type === 'sticker');
    if (hasInlineStickers) {
      const animate = shouldAnimateMessageStickers(message);
      const wrap = node('span', 'msg-text msg-rich-text');
      segments.forEach(segment => {
        if (segment.type === 'sticker') {
          const chip = node('span', 'inline-sticker');
          chip.contentEditable = 'false';
          chip.setAttribute('role', 'img');
          if (animate) {
            let settled = false;
            const finish = () => {
              if (settled) return;
              settled = true;
              releaseStickerAnimSlot();
            };
            const host = stickerMedia(resolveSticker(segment.id), 'sticker-thumb', 28, {
              loop: false,
              autoplay: true,
              onComplete: finish,
            });
            window.setTimeout(() => { if (!settled) finish(); }, 5000);
            chip.appendChild(host);
          } else {
            chip.appendChild(stickerMedia(resolveSticker(segment.id), 'sticker-thumb', 28, {
              loop: false,
              autoplay: false,
            }));
          }
          wrap.appendChild(chip);
          return;
        }
        if (segment.value) wrap.appendChild(document.createTextNode(segment.value));
      });
      bubble.appendChild(wrap);
      return;
    }

    const text = node('span', 'msg-text', raw);
    if (isEmojiOnlyMessage(raw)) {
      bubble.classList.add('emoji-only-bubble');
      text.className = `msg-text ${emojiOnlyClass(countEmojiGlyphs(raw))}`;
    }
    bubble.appendChild(text);
  }
  function messageRow(chat, message, previous, next) {
    const outgoing = message.sender_username === state.me.username;
    const first = !previous || previous.sender_username !== message.sender_username;
    const last = !next || next.sender_username !== message.sender_username;
    const rowClasses = ['msg-row'];
    if (outgoing) rowClasses.push('out');
    if (first) rowClasses.push('first');
    if (last) rowClasses.push('last');
    if (first && last) rowClasses.push('solo');
    if (message.optimistic) rowClasses.push('optimistic');
    const row = node('div', rowClasses.join(' '));
    row.dataset.messageId = message.id;
    if (chat.kind !== 'channel') {
      const holder = node('div', 'msg-avatar');
      if (last) {
        const name = outgoing ? state.me.display_name : message.sender_display_name;
        holder.appendChild(avatar(name, 34, { username: message.sender_username, dot: false }));
        row.classList.add('show-avatar');
      }
      row.appendChild(holder);
    }
    const bubble = node('div', `bubble${state.selected.has(String(message.id)) ? ' selected' : ''}`);
    if (!outgoing && chat.kind === 'group' && first) bubble.appendChild(node('div', 'sender-name', message.sender_display_name));
    appendForward(bubble, message);
    appendReply(bubble, message);
    (message.attachments || []).forEach(attachment => appendAttachment(bubble, attachment, chat));
    const meta = buildMessageMeta(message, outgoing);
    const raw = String(message.text || '');
    const stickerOnly = Boolean(raw && parseStickerIds(raw).length);
    const hasReactions = Boolean(message.reactions?.length);
    if (message.deleted_at || (raw && !stickerOnly)) {
      const wrap = node('div', 'bubble-text-wrap');
      appendMessageBody(wrap, message);
      if (!hasReactions) wrap.appendChild(meta);
      bubble.appendChild(wrap);
    } else {
      appendMessageBody(bubble, message);
      if (!hasReactions) bubble.appendChild(meta);
    }
    if (hasReactions) {
      const footer = node('div', 'bubble-footer');
      const reactionsRow = buildReactionsRow(message);
      if (reactionsRow) footer.appendChild(reactionsRow);
      footer.appendChild(meta);
      bubble.classList.add('has-footer');
      bubble.appendChild(footer);
    } else {
      appendReactions(bubble, message);
    }
    if (!message.deleted_at && !message.optimistic) {
      appendMessageHoverActions(bubble, message);
    }
    row.appendChild(bubble);
    const selecting = byId('chatHeader').classList.contains('selecting');
    if (selecting && !message.deleted_at && !message.optimistic) {
      const picked = state.selected.has(String(message.id));
      const check = document.createElement('button');
      check.type = 'button';
      check.className = `msg-select-check${picked ? ' on' : ''}`;
      check.setAttribute('aria-label', picked ? 'Снять выделение' : 'Выделить');
      check.setAttribute('aria-pressed', picked ? 'true' : 'false');
      check.addEventListener('click', event => {
        event.stopPropagation();
        toggleSelected(String(message.id));
      });
      row.appendChild(check);
    }
    row.addEventListener('click', () => {
      if (!byId('chatHeader').classList.contains('selecting')) return;
      toggleSelected(String(message.id));
    });
    row.addEventListener('contextmenu', event => {
      if (message.optimistic || message.deleted_at) return;
      event.preventDefault();
      openMessageMenu(message, null, event.clientX, event.clientY);
    });
    return row;
  }
  function isChatNearBottom(scroller = byId('messages'), threshold = CHAT_BOTTOM_THRESHOLD) {
    if (!scroller) return true;
    return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <= threshold;
  }
  function renderMessages(keepPosition = false, options = {}) {
    if (isAnimLocked() && !options.force) {
      state.queuedRender = {
        keepPosition: Boolean(keepPosition || options.preserveScroll || state.queuedRender?.keepPosition),
        scrollToBottom: options.scrollToBottom === true || state.queuedRender?.scrollToBottom,
      };
      return;
    }
    const list = byId('messageList');
    const scroller = byId('messages');
    const oldBottom = scroller.scrollHeight - scroller.scrollTop;
    clear(list);
    const chat = activeChat();
    if (!chat) {
      list.appendChild(node('div', 'messenger-empty', 'Выберите диалог слева'));
      return;
    }
    const messages = activeMessages();
    if (!messages.length) {
      const empty = node('div', 'empty-chat');
      empty.appendChild(node('div', 'empty-pill', 'Нет сообщений. Напишите первым 👋'));
      list.appendChild(empty);
      return;
    }
    let shownDay = '';
    let previous = null;
    let daySection = null;
    messages.forEach((message, index) => {
      const day = dayLabel(message.created_at);
      if (day !== shownDay) {
        daySection = node('div', 'day-section');
        const separator = node('div', 'day-sep');
        separator.appendChild(node('div', 'day-chip', day));
        daySection.appendChild(separator);
        list.appendChild(daySection);
        shownDay = day;
        previous = null;
      }
      let next = null;
      for (let i = index + 1; i < messages.length; i += 1) {
        if (dayLabel(messages[i].created_at) !== day) break;
        next = messages[i];
        break;
      }
      daySection.appendChild(messageRow(chat, message, previous, next));
      previous = message;
    });
    const shouldPreserve = keepPosition
      || options.preserveScroll
      || (options.scrollToBottom !== true && !isChatNearBottom(scroller));
    if (shouldPreserve) scroller.scrollTop = Math.max(0, scroller.scrollHeight - oldBottom);
    else scroller.scrollTop = scroller.scrollHeight;
  }
  function renderPinned() {
    const bar = byId('pinnedBar');
    const messages = activeMessages().filter(message => message.pinned);
    if (!messages.length) { bar.hidden = true; clear(bar); return; }
    const message = messages[messages.length - 1];
    bar.hidden = false;
    clear(bar);
    bar.appendChild(node('div', 'pinned-accent'));
    const body = node('div', 'pinned-body');
    body.appendChild(node('div', 'pinned-label', `Закреплённое сообщение${messages.length > 1 ? ` · ${messages.length}` : ''}`));
    body.appendChild(node('div', 'pinned-text', messagePreviewText(message) || 'Вложение'));
    bar.appendChild(body);
    bar.appendChild(iconEl('pin'));
    bar.onclick = () => scrollToMessage(message.id);
  }
  function renderAll() { renderFolders(); renderChats(); renderHeader(); renderMessages(); renderPinned(); if (state.infoOpen) renderInfo(); }

  async function loadChats(options = {}) {
    state.chats = await api(`${API}/chats`);
    if (state.activeChatId && !state.chats.some(chat => chatIdEq(chat.id, state.activeChatId))) {
      // removed from the group, left it, or it was deleted
      state.activeChatId = null;
      document.body.classList.remove('in-chat');
      renderAll();
    }
    await loadPresence(collectPresenceUsernames());
    renderFolders();
    renderChats();
    syncChatSubscriptions();
    if (!options.skipAutoOpen && !state.activeChatId && state.chats.length) {
      const recent = state.chats.find(chat => Number(chat.last_sequence || 0) > 0);
      if (recent) await openChat(recent.id);
    }
  }
  async function loadHistory(chatId) {
    const pageSize = 200;
    let before = null;
    const all = [];
    for (;;) {
      const params = new URLSearchParams({ limit: String(pageSize) });
      if (before != null) params.set('before', String(before));
      const batch = await api(`${API}/chats/${encodeURIComponent(chatId)}/messages?${params}`);
      if (!Array.isArray(batch) || !batch.length) break;
      all.unshift(...batch.map(normalizeMessage).filter(item => !item.deleted_at));
      if (batch.length < pageSize) break;
      const oldest = batch[0]?.sequence;
      if (!oldest || oldest === before) break;
      before = oldest;
      if (chatId !== state.activeChatId) return;
    }
    if (chatId !== state.activeChatId) return;
    state.messages.set(chatId, all);
    await loadReadState(chatId);
    if (chatId !== state.activeChatId) return;
    if (chatId === state.activeChatId) {
      renderMessages(false, { scrollToBottom: true });
      renderPinned();
      if (state.infoOpen) renderInfo();
    }
    const last = all.at(-1);
    if (last) await markRead(chatId, last.sequence);
  }
  async function markRead(chatId, sequence) {
    try {
      const receipt = await api(`${API}/chats/${encodeURIComponent(chatId)}/read`, {
        method: 'PUT', body: JSON.stringify({ sequence }),
      });
      if (receipt?.username) {
        chatReadCursors(chatId).set(receipt.username, Number(receipt.sequence) || 0);
      }
      const chat = state.chats.find(item => chatIdEq(item.id, chatId));
      if (chat) chat.unread_count = 0;
      renderChatList();
    } catch (_) { /* Reading must remain uninterrupted. */ }
  }
  async function openChat(chatId) {
    state.activeChatId = chatId;
    state.replyTo = null; state.editing = null; state.selected.clear();
    setSelectionMode(false);
    document.body.classList.add('in-chat');
    closeComposeContext();
    closePopups();
    const chat = state.chats.find(item => chatIdEq(item.id, chatId));
    if (chat) chat.unread_count = 0;
    renderChatList();
    renderHeader(); renderMessages(); renderPinned();
    if (chat && chat.kind === 'group' && !state.activeCallSessions.has(String(chatId))) {
      loadActiveCallSession(chatId).catch(() => {});
    }
    await loadHistory(chatId);
    byId('composerInput').focus();
  }

  function showComposeContext(kind, message) {
    state.replyTo = kind === 'reply' ? message : null;
    state.editing = kind === 'edit' ? message : null;
    fillIcon(byId('contextIcon'), kind === 'edit' ? 'edit' : 'reply');
    byId('contextTitle').textContent = kind === 'edit'
      ? 'Редактирование'
      : `Ответ ${message.sender_username === state.me.username ? 'себе' : message.sender_display_name}`;
    byId('contextText').textContent = messagePreviewText(message) || 'Вложение';
    byId('composeContext').classList.add('show');
  }
  function closeComposeContext() {
    const wasEditing = Boolean(state.editing);
    state.replyTo = null; state.editing = null;
    byId('composeContext').classList.remove('show');
    if (wasEditing) clearComposer();
  }
  function startReply(message) { showComposeContext('reply', message); byId('composerInput').focus(); }
  function startEdit(message) {
    showComposeContext('edit', message);
    fillComposerFromText(message.text || '');
    byId('composerInput').focus();
  }
  function updateComposerLayout() {
    const input = byId('composerInput');
    const composer = byId('composer');
    if (!input || !composer) return;
    const multiline = input.scrollHeight > Math.ceil(parseFloat(getComputedStyle(input).lineHeight) || 40) + 2
      || /\n/.test(input.innerText.replace(/\r/g, ''));
    composer.classList.toggle('is-multiline', multiline);
  }
  function updateSendBtn() {
    const hasContent = composerHasContent();
    const control = byId('sendButton');
    control.classList.toggle('is-mic', !hasContent);
    control.setAttribute('aria-label', hasContent ? 'Отправить' : 'Голосовое сообщение');
    fillIcon(control, hasContent ? 'send' : 'mic');
    updateComposerLayout();
  }
  function composerHasContent() {
    const input = byId('composerInput');
    if (!input) return false;
    if (input.querySelector('.composer-sticker[data-sticker-id]')) return true;
    return Boolean(input.innerText.replace(/\r/g, '').trim());
  }
  function serializeComposer() {
    const input = byId('composerInput');
    const parts = [];
    let textBuf = '';
    const flushText = () => {
      if (!textBuf) return;
      parts.push({ type: 'text', value: textBuf.replace(/\r/g, '') });
      textBuf = '';
    };
    const walk = (node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        textBuf += node.nodeValue || '';
        return;
      }
      if (node.nodeType !== Node.ELEMENT_NODE) return;
      if (node.classList.contains('composer-sticker')) {
        flushText();
        const id = node.getAttribute('data-sticker-id');
        if (id) parts.push({ type: 'sticker', id });
        return;
      }
      if (node.tagName === 'BR') {
        textBuf += '\n';
        return;
      }
      Array.from(node.childNodes).forEach(walk);
      if (node.tagName === 'DIV' || node.tagName === 'P') textBuf += '\n';
    };
    Array.from(input.childNodes).forEach(walk);
    flushText();
    return parts;
  }
  function composerToPayload() {
    return serializeComposer()
      .map(part => (part.type === 'sticker' ? stickerPayload(part.id) : part.value))
      .join('')
      .replace(/^\n+|\n+$/g, '');
  }
  function clearComposer() {
    const input = byId('composerInput');
    input.querySelectorAll('.composer-sticker').forEach(chip => {
      const host = chip.querySelector('.sticker-thumb, [data-tgs="1"]');
      if (!host) return;
      try { window.MessengerStickers?.destroy(host); } catch (_) { /* ignore */ }
    });
    input.replaceChildren();
    updateSendBtn();
  }
  function composerHasTypedText() {
    const input = byId('composerInput');
    let text = '';
    const walk = (node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        text += node.nodeValue || '';
        return;
      }
      if (node.nodeType !== Node.ELEMENT_NODE) return;
      if (node.classList.contains('composer-sticker')) return;
      if (node.tagName === 'BR') { text += '\n'; return; }
      Array.from(node.childNodes).forEach(walk);
    };
    Array.from(input.childNodes).forEach(walk);
    return Boolean(text.replace(/\s+/g, ''));
  }
  function applyComposerChipSize(chip, size = COMPOSER_STICKER_SIZE) {
    chip.style.width = `${size}px`;
    chip.style.height = `${size}px`;
    const media = chip.querySelector('img, canvas, .sticker-thumb, [data-tgs="1"]');
    if (media) {
      media.style.width = `${size}px`;
      media.style.height = `${size}px`;
      if (media.tagName === 'IMG') {
        media.width = size;
        media.height = size;
      }
    }
  }
  function syncComposerStickerSizes() {
    // Composer stickers stay fixed-size; jumbo sizing applies only after send.
    const input = byId('composerInput');
    if (!input) return;
    input.querySelectorAll('.composer-sticker').forEach(chip => {
      applyComposerChipSize(chip, COMPOSER_STICKER_SIZE);
    });
  }
  function syncComposerSelectionHighlight() {
    const input = byId('composerInput');
    if (!input) return;
    const selection = window.getSelection();
    const chips = input.querySelectorAll('.composer-sticker');
    chips.forEach(chip => chip.classList.remove('is-selected'));
    if (!selection || !selection.rangeCount || selection.isCollapsed) return;
    const anchor = selection.anchorNode;
    const focus = selection.focusNode;
    if (!input.contains(anchor) && !input.contains(focus) && anchor !== input && focus !== input) return;
    chips.forEach(chip => {
      try {
        if (selection.containsNode(chip, true)) chip.classList.add('is-selected');
      } catch (_) { /* ignore */ }
    });
  }
  function serializeComposerSelection() {
    const input = byId('composerInput');
    const selection = window.getSelection();
    if (!selection || !selection.rangeCount || selection.isCollapsed) return null;
    const range = selection.getRangeAt(0);
    if (!input.contains(range.commonAncestorContainer) && range.commonAncestorContainer !== input) return null;
    const fragment = range.cloneContents();
    let out = '';
    const walk = (node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        out += node.nodeValue || '';
        return;
      }
      if (node.nodeType !== Node.ELEMENT_NODE) return;
      if (node.classList && node.classList.contains('composer-sticker')) {
        const id = node.getAttribute('data-sticker-id');
        if (id) out += stickerPayload(id);
        return;
      }
      if (node.tagName === 'BR') {
        out += '\n';
        return;
      }
      Array.from(node.childNodes).forEach(walk);
    };
    Array.from(fragment.childNodes).forEach(walk);
    return out;
  }
  function deleteComposerSelection() {
    const selection = window.getSelection();
    if (!selection || !selection.rangeCount || selection.isCollapsed) return;
    selection.deleteContents();
    syncComposerStickerSizes();
    syncComposerSelectionHighlight();
    updateSendBtn();
  }
  function canvasHasInk(canvas) {
    try {
      const ctx = canvas.getContext('2d', { willReadFrequently: true });
      const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
      for (let i = 3; i < data.length; i += 32) {
        if (data[i] > 8) return true;
      }
    } catch (_) { /* tainted or empty */ }
    return false;
  }
  async function freezeStickerHost(host, size) {
    try {
      await (host._mountPromise || whenStickersReady());
    } catch (_) {
      return;
    }
    let canvas = null;
    for (let attempt = 0; attempt < 40; attempt += 1) {
      if (!host.isConnected) return;
      canvas = host.querySelector('canvas');
      if (canvas && canvas.width > 0 && canvasHasInk(canvas)) break;
      await new Promise(resolve => setTimeout(resolve, 30));
    }
    if (!host.isConnected || !canvas || !canvasHasInk(canvas)) {
      // Keep live canvas instead of a broken <img>.
      return;
    }
    const img = document.createElement('img');
    img.alt = '';
    img.draggable = false;
    img.width = size;
    img.height = size;
    img.style.width = `${size}px`;
    img.style.height = `${size}px`;
    img.style.display = 'block';
    img.style.pointerEvents = 'none';
    try {
      img.src = canvas.toDataURL('image/png');
    } catch (_) {
      return;
    }
    if (!img.src || img.src === 'data:,') return;
    window.MessengerStickers?.destroy(host);
    host.replaceChildren(img);
    host.removeAttribute('data-tgs');
    host._stickerApi = null;
  }
  function createComposerStickerChip(stickerId) {
    const sticker = resolveSticker(stickerId);
    if (!sticker?.file) return null;
    const size = COMPOSER_STICKER_SIZE;
    const chip = node('span', 'composer-sticker');
    chip.contentEditable = 'false';
    chip.dataset.stickerId = stickerId;
    chip.setAttribute('role', 'img');
    chip.setAttribute('aria-label', sticker.title || stickerId);
    applyComposerChipSize(chip, size);
    const host = stickerMedia(sticker, 'sticker-thumb', size, { loop: false, autoplay: false });
    host.style.pointerEvents = 'none';
    chip.appendChild(host);
    freezeStickerHost(host, size);
    return chip;
  }
  function fillComposerFromText(text) {
    clearComposer();
    const input = byId('composerInput');
    const raw = String(text || '');
    const segments = parseMessageSegments(raw);
    if (!segments.length) {
      if (raw) input.appendChild(document.createTextNode(raw));
      updateSendBtn();
      return;
    }
    segments.forEach(segment => {
      if (segment.type === 'sticker') {
        const chip = createComposerStickerChip(segment.id);
        if (chip) input.appendChild(chip);
        else input.appendChild(document.createTextNode(stickerPayload(segment.id)));
        return;
      }
      const value = segment.value || '';
      if (!value) return;
      const lines = value.split('\n');
      lines.forEach((line, index) => {
        if (line) input.appendChild(document.createTextNode(line));
        if (index < lines.length - 1) input.appendChild(document.createElement('br'));
      });
    });
    updateSendBtn();
  }
  function insertComposerSticker(stickerId) {
    if (!stickerById(stickerId)) return;
    const chip = createComposerStickerChip(stickerId);
    if (!chip) return;
    const input = byId('composerInput');
    input.focus();
    const selection = window.getSelection();
    let inserted = false;
    if (selection && selection.rangeCount) {
      const range = selection.getRangeAt(0);
      if (input === range.commonAncestorContainer || input.contains(range.commonAncestorContainer)) {
        range.deleteContents();
        range.insertNode(chip);
        const after = document.createRange();
        after.setStartAfter(chip);
        after.collapse(true);
        selection.removeAllRanges();
        selection.addRange(after);
        inserted = true;
      }
    }
    if (!inserted) input.appendChild(chip);
    updateSendBtn();
  }
  async function sendPayloadText(chat, text, replyId) {
    const clientId = crypto.randomUUID();
    const stickerCount = countStickersInText(text);
    if (stickerCount) beginStickerAnim(clientId);
    const optimistic = normalizeMessage({
      id: `pending-${clientId}`, client_id: clientId, chat_id: chat.id, sequence: null,
      sender_username: state.me.username, sender_display_name: state.me.display_name,
      text, reply_to_id: replyId, created_at: new Date().toISOString(),
      edited_at: null, deleted_at: null, optimistic: true, read: false,
    });
    mergeMessage(optimistic);
    updateChatPreviewOptimistic(optimistic);
    renderMessages(false, { force: true, scrollToBottom: true });
    if (stickerCount) afterStickerSendRender(clientId);
    try {
      const message = await api(`${API}/chats/${encodeURIComponent(chat.id)}/messages`, {
        method: 'POST', body: JSON.stringify({ text, client_id: clientId, reply_to_id: replyId }),
      });
      syncOutgoingMessageStatus(message, clientId);
      const pendingRow = document.querySelector(`.msg-row[data-message-id="${CSS.escape(String(message.id))}"]`);
      if (!pendingRow && !isAnimLocked()) renderMessages();
      return true;
    } catch (error) {
      state.stickerAnimLock = false;
      state.stickerAnimRemaining = 0;
      state.pendingStickerAnim = null;
      state.queuedRender = null;
      state.messages.set(chat.id, activeMessages().filter(item => item.client_id !== clientId));
      renderMessages(false, { force: true });
      toast(`Не удалось отправить: ${error.message}`);
      return false;
    }
  }
  async function sendCurrentMessage() {
    const chat = activeChat();
    if (!chat) return;
    if (state.editing) {
      const text = composerToPayload().trim();
      if (!text) return;
      if (text.length > 8000) { toast('Сообщение превышает 8000 символов'); return; }
      const editing = state.editing;
      try {
        const updated = await api(`${API}/chats/${encodeURIComponent(chat.id)}/messages/${encodeURIComponent(editing.id)}`, {
          method: 'PATCH', body: JSON.stringify({ text }),
        });
        mergeMessage(updated);
        updateChatFromMessage(updated);
        closeComposeContext();
        renderMessages(true);
      } catch (error) { toast(error.message); }
      return;
    }
    const text = composerToPayload().trim();
    if (!text) { later('Голосовые сообщения'); return; }
    if (text.length > 8000) { toast('Сообщение превышает 8000 символов'); return; }
    const replyId = state.replyTo ? state.replyTo.id : null;
    clearComposer();
    closeComposeContext();
    sendTyping(false);
    await sendPayloadText(chat, text, replyId);
  }
  async function deleteMessage(message) {
    openDeleteConfirmModal([message]);
  }
  function deleteSelectedMessages() {
    const messages = Array.from(state.selected)
      .map(id => findMessage(id))
      .filter(message => message && !message.optimistic && !message.deleted_at);
    if (!messages.length) return;
    openDeleteConfirmModal(messages);
  }
  function pluralRu(count, one, few, many) {
    const mod10 = count % 10;
    const mod100 = count % 100;
    if (mod10 === 1 && mod100 !== 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
    return many;
  }
  function applyMessageDeletion(message, deleted, forEveryone) {
    if (forEveryone) {
      removeMessageFromState(deleted);
    } else {
      const list = state.messages.get(state.activeChatId) || [];
      state.messages.set(
        state.activeChatId,
        list.filter(item => item.id !== message.id && item.id !== deleted.id),
      );
    }
  }
  async function performMessageDelete(message, forEveryone) {
    const deleted = await api(
      `${API}/chats/${encodeURIComponent(state.activeChatId)}/messages/${encodeURIComponent(message.id)}?for_everyone=${forEveryone ? 'true' : 'false'}`,
      { method: 'DELETE' },
    );
    applyMessageDeletion(message, deleted, forEveryone);
    return deleted;
  }
  function deleteTargetLabel(chat) {
    if (!chat) return 'собеседника';
    if (chat.kind === 'group') return 'всех';
    const peer = (chat.members || []).find(member => member.username !== state.me.username);
    return peer?.display_name || peer?.username || 'собеседника';
  }
  function openDeleteConfirmModal(messages) {
    const list = (Array.isArray(messages) ? messages : [messages])
      .filter(message => message && !message.optimistic && !message.deleted_at);
    if (!list.length) return;
    closePopups();
    const chat = activeChat();
    const count = list.length;
    const ownMessages = list.filter(message => message.sender_username === state.me.username);
    const ownCount = ownMessages.length;
    const bulk = count > 1;
    const layer = byId('modalLayer');
    clear(layer);
    layer.classList.add('show');
    const modal = node('div', 'modal sm delete-confirm');
    const body = node('div', 'delete-confirm-body');
    const title = bulk
      ? `Удалить ${count} ${pluralRu(count, 'сообщение', 'сообщения', 'сообщений')}?`
      : 'Вы хотите удалить это сообщение?';
    body.appendChild(node('div', 'delete-confirm-title', title));

    let forEveryoneBox = null;
    if (ownCount) {
      const option = node('label', 'delete-confirm-option');
      forEveryoneBox = document.createElement('input');
      forEveryoneBox.type = 'checkbox';
      forEveryoneBox.checked = true;
      const label = bulk && ownCount < count
        ? `Также удалить мои сообщения для ${deleteTargetLabel(chat)}`
        : `Также удалить для ${deleteTargetLabel(chat)}`;
      option.append(forEveryoneBox, node('span', '', label));
      body.appendChild(option);
      if (bulk && ownCount < count) {
        body.appendChild(node(
          'div',
          'delete-confirm-note',
          `Чужие сообщения (${count - ownCount} ${pluralRu(count - ownCount, 'сообщение', 'сообщения', 'сообщений')}) будут удалены только у вас.`,
        ));
      }
    } else if (bulk) {
      body.appendChild(node('div', 'delete-confirm-note', 'Сообщения будут удалены только у вас.'));
    }

    const foot = node('div', 'modal-foot delete-confirm-foot');
    const cancel = button('btn ghost', 'Отмена');
    cancel.addEventListener('click', closeModal);
    const yes = button('btn ghost delete-action', 'Удалить');
    yes.addEventListener('click', async () => {
      const forEveryoneForMine = Boolean(ownCount && forEveryoneBox?.checked);
      closeModal();
      const deletingIds = new Set(list.map(message => String(message.id)));
      deletingIds.forEach(id => state.selected.delete(id));
      try {
        const results = await Promise.allSettled(list.map(message => {
          const mine = message.sender_username === state.me.username;
          return performMessageDelete(message, Boolean(mine && forEveryoneForMine));
        }));
        const failed = results.filter(result => result.status === 'rejected').length;
        refreshChatPreview(state.activeChatId);
        if (!state.selected.size) exitSelection();
        else {
          renderSelectionBar();
          renderMessages(true);
        }
        renderPinned();
        renderChats();
        if (failed) {
          toast(`Не удалось удалить ${failed} из ${count}`);
        } else if (bulk) {
          toast(`Удалено ${count} ${pluralRu(count, 'сообщение', 'сообщения', 'сообщений')}`);
        }
      } catch (error) { toast(error.message); }
    });
    foot.append(cancel, yes);
    modal.append(body, foot);
    layer.appendChild(modal);
  }
  function reactionApiPath(messageId, emojiKey) {
    return `${API}/chats/${encodeURIComponent(state.activeChatId)}/messages/${encodeURIComponent(messageId)}/reactions/${encodeURIComponent(emojiKey)}`;
  }
  async function toggleReaction(message, emoji, mine) {
    const existing = findReaction(message, emoji);
    // Prefer server key when removing; store new reactions as e*.
    const storeId = reactionStoreId(emoji);
    const apiKey = mine && existing ? existing.emoji : storeId;
    const snapshot = message.reactions.map(reaction => ({ ...reaction }));
    try {
      if (mine) {
        removeOptimisticReaction(message, apiKey);
        syncMessageReactions(message, { force: true });
        const result = await api(reactionApiPath(message.id, apiKey), { method: 'DELETE' });
        const current = findMessage(result.message_id) || message;
        if (current) current.reactions = normalizeMessage({ reactions: result.reactions }).reactions;
        syncMessageReactions(current, {
          preserveAnimating: Boolean(state.reactionAnimLock),
        });
        return;
      }

      // One reaction per user: drop previous mine chip instantly (before new anim).
      const previousMine = findMyReaction(message);
      const previousKey = previousMine && reactionStoreId(previousMine.emoji) !== storeId
        ? previousMine.emoji
        : null;
      clearMyOtherReactions(message, storeId);
      beginReactionAnim(message.id, storeId);
      ensureOptimisticReaction(message, storeId);
      // Rebuild only this message's reactions row so the new chip can play, then settle.
      syncMessageReactions(message, { force: true });

      // DELETE previous mine (if any), then PUT the new one. Backend PUT also replaces.
      if (previousKey) {
        try {
          await api(reactionApiPath(message.id, previousKey), { method: 'DELETE' });
        } catch (_) { /* PUT replace still enforces one-mine */ }
      }
      const result = await api(reactionApiPath(message.id, storeId), { method: 'PUT' });
      const current = findMessage(result.message_id) || message;
      if (current) {
        current.reactions = normalizeMessage({ reactions: result.reactions }).reactions;
        // Safety: never keep more than one mine after server sync.
        const mines = (current.reactions || []).filter(reaction => reaction.mine);
        if (mines.length > 1) {
          mines.slice(1).forEach(reaction => {
            reaction.mine = false;
            reaction.reacted_by_me = false;
          });
        }
      }
      // Mid-anim: patch counts/mine only — do NOT queue a full list remount for settle.
      syncMessageReactions(current, {
        preserveAnimating: Boolean(state.reactionAnimLock),
      });
    } catch (error) {
      message.reactions = snapshot;
      if (state.reactionAnimLock) {
        state.reactionAnimLock = false;
        state.pendingReactionAnim = null;
        if (state.queuedRender?.reactionsOnly) state.queuedRender = null;
      }
      syncMessageReactions(message, { force: true });
      featureError(error, 'Реакции');
    }
  }
  async function togglePin(message) {
    const path = `${API}/chats/${encodeURIComponent(state.activeChatId)}/pins/${encodeURIComponent(message.id)}`;
    try {
      const result = await api(path, { method: message.pinned ? 'DELETE' : 'PUT' });
      const current = findMessage(result.message_id);
      if (current) current.pinned = Boolean(result.pinned);
      renderMessages(true); renderPinned();
      toast(result.pinned ? 'Сообщение закреплено' : 'Сообщение откреплено');
    } catch (error) { featureError(error, 'Закрепление сообщений'); }
  }
  async function forwardMessage(message, targetChatId) {
    try {
      const result = await api(`${API}/chats/${encodeURIComponent(targetChatId)}/messages`, {
        method: 'POST',
        body: JSON.stringify({
          text: message.text || '', client_id: crypto.randomUUID(),
          reply_to_id: null, forward_message_id: message.id,
        }),
      });
      mergeMessage(result);
      closeModal();
      toast(`Переслано в «${chatTitle(state.chats.find(chat => chat.id === targetChatId))}»`);
    } catch (error) { featureError(error, 'Пересылка сообщений'); }
  }

  function closePopups() {
    clear(byId('popupLayer'));
    byId('popupLayer').classList.remove('show');
    hideEmojiPanel();
    byId('attachMenu').classList.remove('show');
  }
  function positionPopup(element, anchor, x, y, offset = 0) {
    document.body.offsetHeight;
    const rect = anchor ? anchor.getBoundingClientRect() : null;
    let left = rect ? rect.left : x;
    let top = rect ? rect.bottom + 6 + offset : y + offset;
    if (left + element.offsetWidth > window.innerWidth - 10) left = window.innerWidth - element.offsetWidth - 10;
    if (top + element.offsetHeight > window.innerHeight - 10) top = Math.max(10, (rect ? rect.top : y) - element.offsetHeight - 6);
    element.style.left = `${Math.max(10, left)}px`;
    element.style.top = `${Math.max(10, top)}px`;
  }
  function reactionPoolIds() {
    if (REACTION_MANIFEST.length) return REACTION_MANIFEST.map(entry => entry.emoji);
    return STICKER_PACK.length
      ? STICKER_PACK.map(item => item.id)
      : QUICK_REACTIONS.slice();
  }
  function bindReactionPick(item, message, emoji) {
    item.addEventListener('click', () => {
      const existing = findReaction(message, emoji);
      toggleReaction(message, existing ? existing.emoji : reactionStoreId(emoji), Boolean(existing?.mine));
      closePopups();
    });
    return item;
  }
  function mountReactionGrid(message) {
    const bar = node('div', 'react-bar react-grid');
    reactionPoolIds().forEach(emoji => {
      bar.appendChild(bindReactionPick(reactionChip('react-emo', emoji, REACTION_PICKER_SIZE), message, emoji));
    });
    return bar;
  }
  function mountQuickReactionBar(message, onExpand) {
    const bar = node('div', 'react-bar');
    QUICK_REACTIONS.slice(0, 8).forEach(emoji => {
      bar.appendChild(bindReactionPick(reactionChip('react-emo', emoji, REACTION_PICKER_SIZE), message, emoji));
    });
    const expand = button('react-expand', undefined, 'Все реакции');
    expand.setAttribute('aria-label', 'Все реакции');
    expand.appendChild(iconEl('chevronDown'));
    expand.addEventListener('click', event => {
      event.stopPropagation();
      onExpand?.(bar);
    });
    bar.appendChild(expand);
    return bar;
  }
  function openReactionPicker(message, anchor, x, y) {
    closePopups();
    const layer = byId('popupLayer');
    layer.classList.add('show');
    const bar = mountReactionGrid(message);
    layer.appendChild(bar);
    positionPopup(bar, anchor, x, y);
  }
  function menuItem(label, iconName, action, danger = false) {
    const item = button(`ctx-item${danger ? ' danger' : ''}`);
    item.appendChild(iconEl(iconName));
    item.appendChild(node('span', '', label));
    item.addEventListener('click', () => { closePopups(); action(); });
    return item;
  }
  function openMessageMenu(message, anchor, x, y) {
    closePopups();
    const layer = byId('popupLayer');
    layer.classList.add('show');
    const menu = node('div', 'ctx-menu');
    menu.appendChild(menuItem(t('msg_reply'), 'reply', () => startReply(message)));
    menu.appendChild(menuItem(t('msg_copy'), 'copy', async () => {
      try { await navigator.clipboard.writeText(message.text || ''); toast(t('copied_toast')); }
      catch (_) { toast(t('copy_failed_toast')); }
    }));
    if (window.MessengerNotify?.translateButtonEnabled() && message.text && !parseStickerId(message.text)) {
      menu.appendChild(menuItem(t('msg_translate'), 'globe', () => window.MessengerNotify.translateMessage(message)));
    }
    menu.appendChild(menuItem(t('msg_forward'), 'forward', () => openForwardModal(message)));
    menu.appendChild(menuItem(message.pinned ? t('msg_unpin') : t('msg_pin'), 'pin', () => togglePin(message)));
    if (message.sender_username === state.me.username && message.text && !parseStickerId(message.text)) {
      menu.appendChild(menuItem(t('msg_edit'), 'edit', () => startEdit(message)));
    }
    menu.appendChild(menuItem(t('msg_select'), 'select', () => enterSelection(message)));
    menu.appendChild(node('div', 'ctx-sep'));
    menu.appendChild(menuItem(t('msg_delete'), 'trash', () => deleteMessage(message), true));
    layer.appendChild(menu);
    positionPopup(menu, anchor, x, y);
    const placeBar = (bar, heightHint = 48) => {
      const menuTop = Number.parseFloat(menu.style.top) || 0;
      const left = Number.parseFloat(menu.style.left) || 10;
      bar.style.left = `${left}px`;
      bar.style.top = `${Math.max(10, menuTop - heightHint)}px`;
      document.body.offsetHeight;
      const rect = bar.getBoundingClientRect();
      if (rect.right > window.innerWidth - 10) {
        bar.style.left = `${Math.max(10, window.innerWidth - rect.width - 10)}px`;
      }
      if (rect.top < 10) bar.style.top = '10px';
    };
    const bar = mountQuickReactionBar(message, quickBar => {
      const left = quickBar.style.left;
      const top = quickBar.style.top;
      // Destroy mounted e* thumbs before swapping to full grid.
      quickBar.querySelectorAll('[data-tgs="1"], [data-lottie="1"]').forEach(host => {
        try { window.MessengerStickers?.destroy(host); } catch (_) { /* ignore */ }
      });
      quickBar.remove();
      const grid = mountReactionGrid(message);
      layer.appendChild(grid);
      grid.style.left = left;
      grid.style.top = top;
      document.body.offsetHeight;
      const rect = grid.getBoundingClientRect();
      const menuTop = Number.parseFloat(menu.style.top) || 0;
      if (rect.bottom > menuTop - 6) {
        grid.style.top = `${Math.max(10, menuTop - rect.height - 8)}px`;
      }
      if (rect.right > window.innerWidth - 10) {
        grid.style.left = `${Math.max(10, window.innerWidth - rect.width - 10)}px`;
      }
    });
    layer.appendChild(bar);
    placeBar(bar, 50);
  }
  function openChatMenu(anchor) {
    if (!activeChat()) return;
    closePopups();
    const layer = byId('popupLayer');
    layer.classList.add('show');
    const menu = node('div', 'ctx-menu');
    menu.appendChild(menuItem('Информация', 'info', () => toggleInfo(true)));
    menu.appendChild(menuItem('Поиск', 'search', openMessageSearch));
    menu.appendChild(menuItem('Выбрать сообщения', 'select', () => enterSelection()));
    menu.appendChild(menuItem('Отключить уведомления', 'bellOff', () => later('Настройки уведомлений')));
    const chat = activeChat();
    if (chat.kind === 'group') {
      const mine = (chat.members || []).find(member => member.username === state.me?.username);
      if (mine?.role === 'owner') {
        menu.appendChild(menuItem('Переименовать группу', 'edit', () => openRenameGroup(chat)));
      }
      menu.appendChild(menuItem('Выйти из группы', 'logout', () => confirmModal(
        'Выйти из группы', mine?.role === 'owner'
          ? 'Вы владелец группы: права владельца перейдут участнику, который в группе дольше всех.'
          : 'Вы перестанете получать сообщения этой группы.',
        'Выйти', () => groupAction(`${API}/chats/${chat.id}/leave`, 'POST', 'Вы вышли из группы'),
      ), true));
      if (mine?.role === 'owner') {
        menu.appendChild(menuItem('Удалить группу', 'trash', () => confirmModal(
          'Удалить группу', 'Группа исчезнет у всех участников. Это действие нельзя отменить.',
          'Удалить', () => groupAction(`${API}/chats/${chat.id}`, 'DELETE', 'Группа удалена'),
        ), true));
      }
    }
    layer.appendChild(menu);
    positionPopup(menu, anchor);
  }

  async function groupAction(url, method, message) {
    try {
      await api(url, { method });
      toast(message);
      await loadChats({ skipAutoOpen: true });
    } catch (error) { toast(error.message); }
  }

  function openRenameGroup(chat) {
    const { modal, body } = modalShell('Переименовать группу', true);
    body.classList.add('pad');
    const input = document.createElement('input');
    input.className = 'modal-search';
    input.maxLength = 120;
    input.value = chat.title || '';
    body.appendChild(input);
    const foot = node('div', 'modal-foot');
    const cancel = button('btn ghost', 'Отмена');
    cancel.addEventListener('click', closeModal);
    const save = button('btn', 'Сохранить');
    const submit = async () => {
      const title = input.value.trim();
      if (!title) return;
      try {
        await api(`${API}/chats/${chat.id}`, { method: 'PATCH', body: JSON.stringify({ title }) });
        closeModal();
        await loadChats({ skipAutoOpen: true });
      } catch (error) { toast(error.message); }
    };
    save.addEventListener('click', submit);
    input.addEventListener('keydown', event => { if (event.key === 'Enter') submit(); });
    foot.append(cancel, save);
    modal.appendChild(foot);
    input.focus();
  }

  function setSelectionMode(active) {
    byId('chatHeader').classList.toggle('selecting', active);
    byId('messages').classList.toggle('selecting', active);
    byId('chatMain').classList.toggle('selecting', active);
  }
  function enterSelection(message) {
    closePopups();
    setSelectionMode(true);
    if (message) state.selected.add(String(message.id));
    renderSelectionBar();
    renderMessages(true);
  }
  function exitSelection() {
    state.selected.clear();
    setSelectionMode(false);
    clear(byId('selectionBar'));
    renderMessages(true);
  }
  function syncSelectionRow(messageId) {
    const row = messageRowEl(messageId);
    if (!row) return;
    const picked = state.selected.has(String(messageId));
    const check = row.querySelector('.msg-select-check');
    if (check) {
      check.classList.toggle('on', picked);
      check.setAttribute('aria-label', picked ? 'Снять выделение' : 'Выделить');
      check.setAttribute('aria-pressed', picked ? 'true' : 'false');
    }
    row.querySelector('.bubble')?.classList.toggle('selected', picked);
  }
  function toggleSelected(id) {
    if (state.selected.has(id)) state.selected.delete(id); else state.selected.add(id);
    if (!state.selected.size) exitSelection();
    else {
      renderSelectionBar();
      syncSelectionRow(id);
    }
  }
  function renderSelectionBar() {
    const bar = byId('selectionBar');
    const count = state.selected.size;
    clear(bar);
    const forward = button('sel-action', '');
    forward.append('ПЕРЕСЛАТЬ ', node('span', 'sel-action-count', String(count)));
    forward.addEventListener('click', () => {
      if (state.selected.size !== 1) later('Групповая пересылка');
      else openForwardModal(findMessage(Array.from(state.selected)[0]));
    });
    const remove = button('sel-action', '');
    remove.append('УДАЛИТЬ ', node('span', 'sel-action-count', String(count)));
    remove.addEventListener('click', () => deleteSelectedMessages());
    bar.append(forward, remove);
  }

  function modalShell(title, small = false, onBack = null) {
    const layer = byId('modalLayer');
    clear(layer);
    layer.classList.add('show');
    const modal = node('div', `modal${small ? ' sm' : ''}`);
    const head = node('div', 'modal-head');
    const close = iconButton('icon-btn', 'close', t('close_aria'));
    close.addEventListener('click', closeModal);
    if (onBack) {
      const back = iconButton('icon-btn', 'back', t('back_aria'));
      back.addEventListener('click', onBack);
      head.append(back, node('span', 'title', title), close);
    } else {
      head.append(close, node('span', 'title', title));
    }
    const body = node('div', 'modal-body');
    modal.append(head, body);
    layer.appendChild(modal);
    return { layer, modal, body, close: closeModal };
  }
  function closeModal() { byId('modalLayer').classList.remove('show'); clear(byId('modalLayer')); }
  function confirmModal(title, text, yesLabel, onYes) {
    const { modal, body } = modalShell(title, true);
    body.classList.add('pad');
    body.appendChild(node('div', 'confirm-text', text));
    const foot = node('div', 'modal-foot');
    const cancel = button('btn ghost', 'Отмена');
    cancel.addEventListener('click', closeModal);
    const yes = button('btn danger', yesLabel);
    yes.addEventListener('click', () => { closeModal(); onYes(); });
    foot.append(cancel, yes);
    modal.appendChild(foot);
  }
  function openForwardModal(message) {
    const { body } = modalShell('Переслать сообщение');
    const list = node('div', 'modal-chat-list');
    state.chats.filter(chat => chat.id !== state.activeChatId).forEach(chat => {
      const item = button('chat-item');
      item.appendChild(avatar(chatTitle(chat), 46, {
        username: chatPeer(chat)?.username,
        url: chatPeer(chat)?.avatar_url,
      }));
      const info = node('span', 'ci-body');
      info.append(node('span', 'ci-name', chatTitle(chat)), node('span', 'ci-text', chatSubtitle(chat)));
      item.appendChild(info);
      item.addEventListener('click', () => forwardMessage(message, chat.id));
      list.appendChild(item);
    });
    if (!list.childElementCount) list.appendChild(node('div', 'messenger-empty', 'Других чатов пока нет'));
    body.appendChild(list);
  }
  function openMessageSearch() {
    const chat = activeChat();
    if (!chat) return;
    const { body } = modalShell('Поиск в чате');
    const field = document.createElement('input');
    field.className = 'modal-search';
    field.type = 'search';
    field.placeholder = 'Введите запрос';
    const results = node('div', 'search-results');
    body.append(field, results);
    let timer;
    field.addEventListener('input', () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(async () => {
        clear(results);
        const query = field.value.trim();
        if (!query) return;
        try {
          const found = await api(`${API}/chats/${encodeURIComponent(chat.id)}/messages/search?q=${encodeURIComponent(query)}&limit=50`);
          (found.items || found).forEach(message => {
            const item = button('search-result');
            item.append(node('strong', '', message.sender_display_name), node('span', '', messagePreviewText(message) || 'Вложение'));
            item.addEventListener('click', () => { closeModal(); scrollToMessage(message.id); });
            results.appendChild(item);
          });
          if (!results.childElementCount) results.appendChild(node('div', 'messenger-empty', 'Ничего не найдено'));
        } catch (error) { featureError(error, 'Поиск по сообщениям'); closeModal(); }
      }, 300);
    });
    field.focus();
  }
  function field(label, name, value = '') {
    const wrap = node('label', 'field');
    wrap.appendChild(node('span', '', label));
    const input = document.createElement('input');
    input.name = name; input.value = value;
    wrap.appendChild(input);
    return { wrap, input };
  }
  function openCreateChat() {
    const { modal, body } = modalShell('Новый чат');
    body.classList.add('pad-form');
    const kind = document.createElement('select');
    kind.className = 'modal-select';
    [['direct', 'Личный чат'], ['group', 'Группа']].forEach(([value, label]) => {
      const option = document.createElement('option'); option.value = value; option.textContent = label; kind.appendChild(option);
    });
    const kindWrap = node('label', 'field');
    kindWrap.append(node('span', '', 'Тип чата'), kind);
    const members = field('Логины участников через запятую', 'members');
    const title = field('Название группы', 'title');
    title.wrap.hidden = true;
    kind.addEventListener('change', () => {
      title.wrap.hidden = kind.value !== 'group';
      members.wrap.firstElementChild.textContent = kind.value === 'group' ? 'Логины участников через запятую' : 'Логин сотрудника';
    });
    body.append(kindWrap, title.wrap, members.wrap);
    const foot = node('div', 'modal-foot');
    const create = button('btn primary', 'Создать');
    create.addEventListener('click', async () => {
      const usernames = members.input.value.split(',').map(value => value.trim().toLowerCase()).filter(Boolean);
      if (!usernames.length || (kind.value === 'group' && !title.input.value.trim())) {
        toast('Заполните обязательные поля'); return;
      }
      try {
        const chat = await api(`${API}/chats`, {
          method: 'POST',
          body: JSON.stringify({ kind: kind.value, title: title.input.value.trim(), members: usernames }),
        });
        const index = state.chats.findIndex(item => item.id === chat.id);
        if (index >= 0) state.chats[index] = chat; else state.chats.unshift(chat);
        syncChatSubscriptions();
        closeModal(); await openChat(chat.id);
      } catch (error) { toast(error.message); }
    });
    foot.appendChild(create); modal.appendChild(foot);
    members.input.focus();
  }

  function infoAction(label, iconName, action, danger = false) {
    const control = button(`info-action${danger ? ' danger' : ''}`);
    control.append(iconEl(iconName), node('span', '', label));
    control.addEventListener('click', action);
    return control;
  }
  function renderInfo() {
    const chat = activeChat();
    const content = byId('infoContent');
    clear(content);
    if (!chat) return;
    const hero = node('div', 'info-hero');
    const peer = chatPeer(chat);
    hero.append(
      avatar(chatTitle(chat), 54, { username: peer?.username, url: peer?.avatar_url }),
      node('div', 'name', chatTitle(chat)),
      node('div', `sub${chat?.kind === 'direct' && peer ? ` presence-${userPresence(peer.username)}` : ''}`, chatPresenceSubtitle(chat)),
    );
    const actions = node('div', 'info-actions');
    actions.append(
      infoAction('Уведомл.', 'bell', () => later('Настройки уведомлений')),
      infoAction(chat.kind === 'group' ? 'Добавить' : 'Звонок', chat.kind === 'group' ? 'plus' : 'phone',
        () => chat.kind === 'group' ? openAddMember() : callFromChat(chat)),
      infoAction('Поиск', 'search', openMessageSearch),
      infoAction(chat.kind === 'group' ? 'Покинуть' : 'Удалить', chat.kind === 'group' ? 'logout' : 'trash',
        () => later('Удаление чата'), true),
    );
    if (chat.kind === 'group') {
      actions.appendChild(infoAction('Звонок', 'phone', () => groupCallFromChat(chat)));
    }
    content.append(hero, actions);
    const tabs = node('div', 'info-tabs');
    const tabKeys = chat.kind === 'group'
      ? [['members', 'Участники'], ['media', 'Медиа'], ['files', 'Файлы'], ['links', 'Ссылки']]
      : [['media', 'Медиа'], ['files', 'Файлы'], ['links', 'Ссылки']];
    if (chat.kind !== 'group' && state.infoTab === 'members') state.infoTab = 'media';
    tabKeys.forEach(([key, label]) => {
      const tab = button(`info-tab${state.infoTab === key ? ' active' : ''}`, label);
      tab.addEventListener('click', () => { state.infoTab = key; renderInfo(); });
      tabs.appendChild(tab);
    });
    const section = node('div', 'info-section info-tab-section');
    section.appendChild(tabs);
    const tabBody = node('div', 'info-tab-body');
    if (state.infoTab === 'members') {
      (chat.members || []).forEach(member => {
        const row = node('div', 'member-row');
        row.appendChild(avatar(member.display_name, 46, {
          username: member.username,
          url: member.avatar_url,
        }));
        const body = node('div', 'member-body');
        const memberLabel = member.username === state.me.username ? 'Вы' : member.display_name;
        body.append(node('div', 'member-name', memberLabel));
        body.append(node('div', `member-sub presence-${userPresence(member.username)}`,
          presenceLabelWithText(member.username)));
        row.appendChild(body);
        if (member.role === 'owner') row.appendChild(node('span', 'member-role', 'владелец'));
        if (member.username !== state.me.username && chat.kind === 'group') {
          const remove = iconButton('member-remove', 'close', 'Удалить участника');
          remove.addEventListener('click', () => removeMember(member));
          row.appendChild(remove);
        }
        tabBody.appendChild(row);
      });
    } else if (state.infoTab === 'files') {
      renderInfoFiles(tabBody);
    } else if (state.infoTab === 'media') {
      const grid = node('div', 'media-grid');
      activeMessages().flatMap(message => message.attachments || [])
        .filter(attachment => (attachment.content_type || '').startsWith('image/'))
        .forEach(attachment => {
          const cell = button('media-cell');
          const image = document.createElement('img');
          image.alt = attachment.original_name || 'Медиа';
          showAttachmentImage(image, attachment);
          cell.appendChild(image);
          cell.addEventListener('click', () => downloadAttachment(attachment));
          grid.appendChild(cell);
        });
      tabBody.appendChild(grid);
    } else {
      const links = [];
      activeMessages().forEach(message => {
        String(message.text || '').match(/https?:\/\/[^\s]+/g)?.forEach(link => links.push(link));
      });
      links.forEach(url => {
        const link = document.createElement('a');
        link.className = 'info-link'; link.href = url; link.target = '_blank'; link.rel = 'noopener'; link.textContent = url;
        tabBody.appendChild(link);
      });
    }
    if (!tabBody.childElementCount || (state.infoTab === 'media' && !tabBody.querySelector('img'))) {
      if (state.infoTab !== 'files') {
        tabBody.appendChild(node('div', 'messenger-empty', 'Здесь пока ничего нет'));
      }
    }
    section.appendChild(tabBody);
    content.appendChild(section);
  }
  function closeInfoPanel() {
    state.infoOpen = false;
    byId('infoPanel').classList.remove('open');
  }
  function toggleInfo(open) {
    state.infoOpen = open === undefined ? !state.infoOpen : open;
    if (state.infoOpen) {
      hideEmojiPanel();
      byId('infoPanel').classList.add('open');
      renderInfo();
    } else {
      closeInfoPanel();
    }
  }
  function openAddMember() {
    const { modal, body } = modalShell('Добавить участника', true);
    const username = field('Логин сотрудника', 'username');
    body.appendChild(username.wrap);
    const foot = node('div', 'modal-foot');
    const add = button('btn primary', 'Добавить');
    add.addEventListener('click', async () => {
      const value = username.input.value.trim().toLowerCase();
      if (!value) return;
      try {
        await api(`${API}/chats/${encodeURIComponent(state.activeChatId)}/members`, {
          method: 'POST', body: JSON.stringify({ username: value }),
        });
        closeModal(); await loadChats(); renderInfo(); toast('Участник добавлен');
      } catch (error) { toast(error.message); }
    });
    foot.appendChild(add); modal.appendChild(foot); username.input.focus();
  }
  function removeMember(member) {
    confirmModal('Удалить участника?', member.display_name, 'Удалить', async () => {
      try {
        await api(`${API}/chats/${encodeURIComponent(state.activeChatId)}/members/${encodeURIComponent(member.username)}`, { method: 'DELETE' });
        await loadChats(); renderInfo(); toast('Участник удалён');
      } catch (error) { toast(error.message); }
    });
  }

  const ZOOM_STEPS = [100, 110, 125, 150, 175, 200, 225, 250, 275, 300];
  function applyZoom(level, on) {
    // Scoped to .app only — modal-layer/popup-layer/call-layer/toast-host
    // are separate siblings under <body>, so overlays stay at native 100%
    // scale. Zooming document.documentElement instead visually enlarges
    // the whole page including modals, which pushes them past the fixed
    // 88vh/100vh sizing everything else in this app assumes and leaves
    // their bottom content (including this very settings panel) impossible
    // to scroll to — the app already blocks native browser zoom
    // (bindZoomLock) for exactly this kind of fixed-viewport breakage.
    const root = document.querySelector('.app');
    if (root) root.style.zoom = on ? String(level / 100) : '';
  }
  function initZoom() {
    const on = localStorage.getItem('messenger-zoom-on') !== '0';
    const level = Number(localStorage.getItem('messenger-zoom')) || 100;
    applyZoom(level, on);
  }
  // Message text only (not the whole UI, unlike applyZoom above) — CSS
  // custom properties, so every open bubble repaints live with no re-render.
  const MSG_FONT_STEPS = [12, 13, 14, 15, 16, 17, 18, 19, 20];
  function applyMsgFontSize(size) {
    document.documentElement.style.setProperty('--msg-font-size', `${size}px`);
    document.documentElement.style.setProperty('--msg-meta-font-size', `${Math.max(11, size - 1)}px`);
    localStorage.setItem('messenger-msg-font-size', String(size));
  }
  function initMsgFontSize() {
    const saved = Number(localStorage.getItem('messenger-msg-font-size'));
    applyMsgFontSize(MSG_FONT_STEPS.includes(saved) ? saved : 13);
  }
  const SETTINGS_ROWS = () => [
    { key: 'account', icon: 'user', label: t('row_account') },
    { key: 'notif', icon: 'bell', label: t('row_notif') },
    { key: 'chats', icon: 'chats', label: t('row_chats') },
    { key: 'advanced', icon: 'menu', label: t('row_advanced') },
    { key: 'media', icon: 'camera', label: t('row_media') },
    { key: 'lang', icon: 'globe', label: t('row_lang'), value: t(`lang_${window.MessengerI18n.getLang()}`) },
    { key: 'info', icon: 'info', label: t('row_info'), value: t('app_base_title') },
  ];
  function settingsRowValue(row) {
    return row.value || '';
  }
  function settingsRowAction(key) {
    if (key === 'chats') return openChatSettings;
    if (key === 'admin') return openAdminPanel;
    if (key === 'info') return () => toast(t('about_toast'));
    // notif/media/advanced/lang live in a separate file (settings-extra.js)
    // so client.js doesn't keep growing — fall back to the "coming soon"
    // toast only if that file somehow failed to load.
    const extra = { notif: 'openNotifications', media: 'openSoundCamera', advanced: 'openAdvanced', lang: 'openLanguage' }[key];
    if (extra && window.MessengerSettingsExtra?.[extra]) return window.MessengerSettingsExtra[extra];
    const later_ = {
      account: t('row_account'),
    }[key] || key;
    return () => later(later_);
  }

  function openSettings() {
    const layer = byId('modalLayer');
    clear(layer);
    layer.classList.add('show');
    const modal = node('div', 'modal settings');
    layer.appendChild(modal);

    const view = { search: '', searchOpen: false, menuOpen: false };

    function renderHead() {
      const head = node('div', 'modal-head');
      head.appendChild(node('span', 'title', t('settings_title')));
      const searchBtn = iconButton(`icon-btn${view.searchOpen ? ' active' : ''}`, 'search', t('search_placeholder'));
      searchBtn.addEventListener('click', () => {
        view.searchOpen = !view.searchOpen;
        view.search = '';
        view.menuOpen = false;
        render();
      });
      const menuBtn = iconButton(`icon-btn${view.menuOpen ? ' active' : ''}`, 'more', t('more_title'));
      menuBtn.addEventListener('click', event => {
        event.stopPropagation();
        view.menuOpen = !view.menuOpen;
        render();
      });
      const close = iconButton('icon-btn', 'close', t('close_aria'));
      close.addEventListener('click', closeModal);
      head.append(searchBtn, menuBtn, close);
      return head;
    }

    function renderMenu() {
      const menu = node('div', 'admin-menu');
      menu.style.position = 'absolute';
      menu.style.right = '12px';
      menu.style.top = '52px';
      menu.style.zIndex = '10';
      const add = (label, danger, onClick) => {
        const item = button(`admin-menu-item${danger ? ' danger' : ''}`);
        item.appendChild(node('span', 'mi-label', label));
        item.addEventListener('click', () => { view.menuOpen = false; onClick(); });
        menu.appendChild(item);
      };
      add(t('menu_change_avatar'), false, () => byId('settingsAvatarInput')?.click());
      add(t('menu_change_name'), false, () => later(t('menu_change_name')));
      add(t('menu_logout'), true, () => {
        confirmModal(t('logout_title'), t('logout_text'), t('logout_yes'), async () => {
          try {
            const response = await fetch('/api/auth/logout', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf },
            });
            if (!response.ok) throw new Error('Не удалось выйти');
            const result = await response.json();
            window.location.assign(result.redirect_url || '/login');
          } catch (error) {
            toast(error.message || 'Не удалось выйти');
          }
        });
      });
      return menu;
    }

    function renderProfile() {
      const row = node('div', 'settings-profile');
      const avatarWrap = avatar(state.me.display_name, 70, {
        username: state.me.username,
        url: state.me.avatar_url,
      });
      avatarWrap.classList.add('avatar-editable');
      avatarWrap.title = t('menu_change_avatar');
      const fileInput = document.createElement('input');
      fileInput.type = 'file';
      fileInput.id = 'settingsAvatarInput';
      fileInput.accept = 'image/jpeg,image/png,image/webp,image/gif';
      fileInput.className = 'visually-hidden';
      fileInput.addEventListener('change', async () => {
        const file = fileInput.files?.[0];
        fileInput.value = '';
        if (!file) return;
        try {
          await uploadAvatar(file);
          toast('Аватар обновлён');
          openSettings();
        } catch (error) {
          toast(error.message);
        }
      });
      avatarWrap.addEventListener('click', () => fileInput.click());
      const info = node('div', 'settings-profile-body');
      info.append(
        node('div', 'settings-profile-name', state.me.display_name),
        node('div', 'settings-profile-sub', `@${state.me.username}`),
      );
      row.append(avatarWrap, info, fileInput);
      return row;
    }

    function renderZoom() {
      const wrap = node('div', 'settings-zoom-wrap');
      let on = localStorage.getItem('messenger-zoom-on') !== '0';
      let level = Number(localStorage.getItem('messenger-zoom')) || 100;

      const toggleRow = node('div', 'settings-zoom-row');
      toggleRow.append(iconEl('eye'), node('span', 'label', t('interface_zoom')));
      const track = node('div', `switch${on ? ' on' : ''}`);
      toggleRow.appendChild(track);
      const sliderRow = node('div', 'settings-zoom-slider-row');
      const slider = node('div', `settings-slider${on ? '' : ' off'}`);
      const fill = node('div', 'settings-slider-fill');
      const thumb = node('div', 'settings-slider-thumb');
      slider.append(node('div', 'settings-slider-track'), fill, thumb);
      const label = node('div', 'settings-zoom-label', `${level}%`);

      function paint() {
        const pos = ZOOM_STEPS.indexOf(level) / (ZOOM_STEPS.length - 1);
        const pct = Math.max(0, pos) * 100;
        fill.style.width = `${pct}%`;
        thumb.style.left = `${pct}%`;
        label.textContent = `${level}%`;
        track.classList.toggle('on', on);
        slider.classList.toggle('off', !on);
      }
      function persist() {
        localStorage.setItem('messenger-zoom', String(level));
        localStorage.setItem('messenger-zoom-on', on ? '1' : '0');
        applyZoom(level, on);
      }
      function setFromEvent(event) {
        const rect = slider.getBoundingClientRect();
        const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
        level = ZOOM_STEPS[Math.round(ratio * (ZOOM_STEPS.length - 1))];
        on = true;
        paint();
        persist();
      }
      const flip = () => { on = !on; paint(); persist(); };
      toggleRow.addEventListener('click', flip);
      track.addEventListener('click', event => { event.stopPropagation(); flip(); });
      let moveHandler = null;
      let upHandler = null;
      slider.addEventListener('pointerdown', event => {
        setFromEvent(event);
        moveHandler = ev => setFromEvent(ev);
        upHandler = () => {
          window.removeEventListener('pointermove', moveHandler);
          window.removeEventListener('pointerup', upHandler);
        };
        window.addEventListener('pointermove', moveHandler);
        window.addEventListener('pointerup', upHandler);
      });
      sliderRow.append(slider, label);
      wrap.append(toggleRow, sliderRow);
      paint();
      return wrap;
    }

    function renderRows(body) {
      const query = view.search.trim().toLowerCase();
      const rows = SETTINGS_ROWS()
        .concat(state.me?.admin ? [{ key: 'admin', icon: 'shield', label: t('row_admin'), value: t('admin_row_hint') }] : [])
        .filter(row => !query || row.label.toLowerCase().includes(query));
      const list = node('div', 'set-list');
      if (!rows.length) {
        list.appendChild(node('div', 'admin-empty', t('settings_not_found')));
      } else {
        rows.forEach(row => {
          const item = button('set-item');
          item.append(iconEl(row.icon), node('span', 'si-label', row.label), node('span', 'si-value', settingsRowValue(row)));
          item.addEventListener('click', settingsRowAction(row.key));
          list.appendChild(item);
        });
      }
      body.appendChild(list);
      if (!query) body.appendChild(renderZoom());
    }

    function render() {
      clear(modal);
      modal.style.position = 'relative';
      modal.appendChild(renderHead());
      if (view.searchOpen) {
        const searchWrap = node('div', 'settings-search-wrap');
        const input = document.createElement('input');
        input.type = 'text';
        input.placeholder = t('settings_search_placeholder');
        input.value = view.search;
        input.addEventListener('input', () => { view.search = input.value; render(); });
        searchWrap.appendChild(input);
        modal.appendChild(searchWrap);
      }
      const body = node('div', 'modal-body');
      if (!view.search.trim()) body.appendChild(renderProfile());
      renderRows(body);
      modal.appendChild(body);
      if (view.menuOpen) modal.appendChild(renderMenu());
      if (view.searchOpen) modal.querySelector('input')?.focus();
    }
    render();
  }
  function darkenHex(hex, amount) {
    const n = parseInt(hex.slice(1), 16);
    const clamp = value => Math.max(0, Math.min(255, value));
    const r = clamp(((n >> 16) & 255) - amount);
    const g = clamp(((n >> 8) & 255) - amount);
    const b = clamp((n & 255) - amount);
    return `#${((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1)}`;
  }
  function applyAccent(accent) {
    document.documentElement.dataset.accent = accent;
    document.documentElement.style.removeProperty('--accent');
    document.documentElement.style.removeProperty('--accent-strong');
    localStorage.setItem('messenger-accent', accent);
    localStorage.removeItem('messenger-accent-custom');
  }
  function applyCustomAccent(hex) {
    document.documentElement.dataset.accent = 'custom';
    document.documentElement.style.setProperty('--accent', hex);
    document.documentElement.style.setProperty('--accent-strong', darkenHex(hex, 24));
    localStorage.setItem('messenger-accent', 'custom');
    localStorage.setItem('messenger-accent-custom', hex);
  }
  function initAccent() {
    const saved = localStorage.getItem('messenger-accent') || 'blue';
    if (saved === 'custom') applyCustomAccent(localStorage.getItem('messenger-accent-custom') || '#3390ec');
    else applyAccent(saved);
  }

  const WALLPAPER_IDS = Array.from({ length: 17 }, (_, i) => `wall${i + 1}`);
  let wallpaperPhotoUrl = null;
  async function wallpaperPhotoBlob(retry = true) {
    await ensureToken();
    const response = await fetch(`${API}/wallpapers/me`, {
      credentials: 'same-origin',
      headers: { Authorization: `Bearer ${state.token}` },
    });
    if (response.status === 401 && retry) {
      state.token = '';
      return wallpaperPhotoBlob(false);
    }
    if (!response.ok) return null;
    return response.blob();
  }
  // Recolors automatically: 'pattern' mode masks a flat --accent fill with
  // the chosen pattern, so switching theme/accent repaints it with no JS work.
  // Uses pre-rasterized .png masks, not the source .svg files: those doodle
  // patterns run 350-950 <path> elements each, and re-rasterizing that as a
  // live CSS mask (repeated via mask-repeat, redone on every scroll/resize)
  // visibly stutters. A flat PNG mask just gets alpha-composited — cheap
  // regardless of how complex the original vector art was.
  async function applyWallpaper() {
    const bg = document.querySelector('.chat-bg');
    if (!bg) return;
    const selection = localStorage.getItem('messenger-wallpaper') || 'wall1';
    bg.classList.remove('pattern', 'photo', 'off');
    if (selection === 'off') {
      bg.classList.add('off');
      return;
    }
    if (selection === 'custom') {
      if (!wallpaperPhotoUrl) {
        const blob = await wallpaperPhotoBlob();
        if (!blob) {
          localStorage.setItem('messenger-wallpaper', 'wall1');
          return applyWallpaper();
        }
        wallpaperPhotoUrl = URL.createObjectURL(blob);
      }
      bg.style.setProperty('--wallpaper-photo', `url("${wallpaperPhotoUrl}")`);
      bg.classList.add('photo');
      return;
    }
    bg.style.setProperty('--wallpaper-mask', `url("/static/messenger/wallpapers/${selection}.png")`);
    bg.classList.add('pattern');
  }

  function openChatSettings() {
    const { body } = modalShell(t('chat_settings_title'), false, openSettings);
    body.appendChild(node('div', 'set-group-label', t('theme_label')));
    const cards = node('div', 'theme-cards');
    [['light', t('theme_classic')], ['day', t('theme_day')], ['dark', t('theme_dark')], ['night', t('theme_night')]].forEach(([theme, label]) => {
      const card = button(`theme-card${document.documentElement.dataset.theme === theme ? ' active' : ''}`);
      const preview = node('span', `prev ${theme}`);
      preview.append(node('i', 'b'), node('i', 'b r'), node('i', 'b'));
      card.append(preview, node('span', 'label', label));
      card.addEventListener('click', () => {
        document.documentElement.dataset.theme = theme;
        localStorage.setItem('messenger-theme', theme);
        all('.theme-card').forEach(item => item.classList.toggle('active', item === card));
        if (state.activeChatId) renderMessages(true);
      });
      cards.appendChild(card);
    });
    body.appendChild(cards);

    body.appendChild(node('div', 'set-group-label', t('accent_label')));
    const accents = node('div', 'accent-row');
    const currentAccent = document.documentElement.dataset.accent || localStorage.getItem('messenger-accent') || 'blue';
    [['blue', '#3390ec'], ['teal', '#14b8a6'], ['green', '#22c55e'], ['orange', '#f59e0b'], ['rose', '#e11d48'], ['violet', '#8b5cf6']].forEach(([name, color]) => {
      const dot = button(`accent-dot${currentAccent === name ? ' active' : ''}`);
      dot.style.setProperty('--accent-preview', color);
      dot.title = name;
      dot.addEventListener('click', () => {
        applyAccent(name);
        all('.accent-dot').forEach(item => item.classList.toggle('active', item === dot));
      });
      accents.appendChild(dot);
    });
    const customDot = button(`accent-dot accent-dot-custom${currentAccent === 'custom' ? ' active' : ''}`);
    customDot.title = t('custom_color_title');
    const customInput = document.createElement('input');
    customInput.type = 'color';
    customInput.value = localStorage.getItem('messenger-accent-custom') || '#3390ec';
    customInput.addEventListener('input', () => {
      applyCustomAccent(customInput.value);
      all('.accent-dot').forEach(item => item.classList.toggle('active', item === customDot));
    });
    customDot.appendChild(customInput);
    accents.appendChild(customDot);
    body.appendChild(accents);

    body.appendChild(node('div', 'set-group-label', t('text_size_label')));
    const textBlock = node('div', 'text-size-block');
    const preview = node('div', 'text-size-preview');
    const previewBubble = node('div', 'text-size-preview-bubble', t('text_size_preview'));
    preview.appendChild(previewBubble);
    textBlock.appendChild(preview);

    const textRow = node('div', 'text-size-row');
    let fontSize = MSG_FONT_STEPS.includes(Number(localStorage.getItem('messenger-msg-font-size')))
      ? Number(localStorage.getItem('messenger-msg-font-size')) : 13;
    const textSlider = node('div', 'settings-slider');
    const textFill = node('div', 'settings-slider-fill');
    const textThumb = node('div', 'settings-slider-thumb');
    textSlider.append(node('div', 'settings-slider-track'), textFill, textThumb);
    function paintTextSlider() {
      const pos = MSG_FONT_STEPS.indexOf(fontSize) / (MSG_FONT_STEPS.length - 1);
      const pct = Math.max(0, pos) * 100;
      textFill.style.width = `${pct}%`;
      textThumb.style.left = `${pct}%`;
    }
    function setTextSizeFromEvent(event) {
      const rect = textSlider.getBoundingClientRect();
      const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
      fontSize = MSG_FONT_STEPS[Math.round(ratio * (MSG_FONT_STEPS.length - 1))];
      paintTextSlider();
      applyMsgFontSize(fontSize);
    }
    textSlider.addEventListener('pointerdown', event => {
      setTextSizeFromEvent(event);
      const moveHandler = ev => setTextSizeFromEvent(ev);
      const upHandler = () => {
        window.removeEventListener('pointermove', moveHandler);
        window.removeEventListener('pointerup', upHandler);
      };
      window.addEventListener('pointermove', moveHandler);
      window.addEventListener('pointerup', upHandler);
    });
    textRow.append(node('span', 'text-size-a sm', 'A'), textSlider, node('span', 'text-size-a lg', 'A'));
    textBlock.appendChild(textRow);
    paintTextSlider();
    body.appendChild(textBlock);

    body.appendChild(node('div', 'set-group-label', t('wallpaper_label')));
    const grid = node('div', 'wallpaper-grid');
    const currentWallpaper = localStorage.getItem('messenger-wallpaper') || 'wall1';
    const markActive = tile => all('.wallpaper-tile', grid).forEach(item => item.classList.toggle('active', item === tile));

    const offTile = button(`wallpaper-tile wallpaper-tile-off${currentWallpaper === 'off' ? ' active' : ''}`);
    offTile.appendChild(iconEl('close'));
    offTile.title = t('wallpaper_off_title');
    offTile.addEventListener('click', () => {
      localStorage.setItem('messenger-wallpaper', 'off');
      applyWallpaper();
      markActive(offTile);
    });
    grid.appendChild(offTile);

    WALLPAPER_IDS.forEach(id => {
      const tile = button(`wallpaper-tile${currentWallpaper === id ? ' active' : ''}`);
      const pattern = node('span', 'wallpaper-tile-pattern');
      const url = `url("/static/messenger/wallpapers/${id}.png")`;
      pattern.style.setProperty('mask-image', url);
      pattern.style.setProperty('-webkit-mask-image', url);
      tile.appendChild(pattern);
      tile.addEventListener('click', () => {
        localStorage.setItem('messenger-wallpaper', id);
        applyWallpaper();
        markActive(tile);
      });
      grid.appendChild(tile);
    });

    const uploadTile = button(`wallpaper-tile wallpaper-tile-upload${currentWallpaper === 'custom' ? ' active' : ''}`);
    if (currentWallpaper === 'custom' && wallpaperPhotoUrl) {
      uploadTile.style.backgroundImage = `url("${wallpaperPhotoUrl}")`;
    } else {
      uploadTile.appendChild(iconEl('image'));
    }
    uploadTile.title = t('wallpaper_upload_title');
    const uploadInput = document.createElement('input');
    uploadInput.type = 'file';
    uploadInput.accept = 'image/jpeg,image/png,image/webp,image/gif';
    uploadInput.className = 'visually-hidden';
    uploadInput.addEventListener('change', async () => {
      const file = uploadInput.files?.[0];
      uploadInput.value = '';
      if (!file) return;
      try {
        const form = new FormData();
        form.append('file', file);
        await api(`${API}/wallpapers/me`, { method: 'PUT', body: form });
        if (wallpaperPhotoUrl) { URL.revokeObjectURL(wallpaperPhotoUrl); wallpaperPhotoUrl = null; }
        localStorage.setItem('messenger-wallpaper', 'custom');
        await applyWallpaper();
        uploadTile.style.backgroundImage = `url("${wallpaperPhotoUrl}")`;
        markActive(uploadTile);
        toast(t('wallpaper_updated'));
      } catch (error) {
        toast(error.message);
      }
    });
    uploadTile.appendChild(uploadInput);
    uploadTile.addEventListener('click', () => uploadInput.click());
    grid.appendChild(uploadTile);
    body.appendChild(grid);
  }

  // ---- Admin panel ----
  const ADMIN_TABS = [
    { key: 'general', icon: 'settings', label: 'Общее', title: 'Общее', sub: 'Звонки и передача файлов в организации' },
    { key: 'users', icon: 'users', label: 'Пользователи', title: 'Пользователи', sub: 'Доступ, тихий режим и отзыв сессий' },
    { key: 'audit', icon: 'file', label: 'Журнал аудита', title: 'Журнал аудита', sub: 'Действия админов и системы' },
    { key: 'retention', icon: 'trash', label: 'Хранение данных', title: 'Хранение данных', sub: 'Сколько держим сообщения, файлы, журнал и бэкапы' },
    { key: 'limits', icon: 'pause', label: 'Лимиты', title: 'Лимиты', sub: 'Rate-лимиты и максимальный размер группы' },
    { key: 'auditors', icon: 'shield', label: 'Аудиторы', title: 'Аудиторы безопасности', sub: 'Роль security_auditor — без правки env' },
  ];
  const ADMIN_SEV = { info: 'var(--accent)', warn: '#f0b429', danger: '#e0483d', ok: '#4dcd5e' };
  const ADMIN_SEV_LABEL = { info: 'настройки', warn: 'доступ', danger: 'критично', ok: 'вход' };
  /** action → [human title, severity]; unknown actions fall back to the raw action name. */
  const ADMIN_AUDIT_ACTIONS = {
    'admin.config_updated': ['Изменена конфигурация', 'info'],
    'admin.user_calls_updated': ['Изменён доступ к звонкам', 'info'],
    'admin.user_mute_updated': ['Изменён тихий режим', 'warn'],
    'admin.user_auditor_updated': ['Изменена роль аудитора', 'info'],
    'admin.user_disabled': ['Пользователь отключён', 'danger'],
    'admin.user_enabled': ['Пользователь включён', 'ok'],
    'admin.sessions_revoked': ['Отозваны сессии пользователя', 'danger'],
    'admin.sessions_revoked_all': ['Массовый отзыв сессий', 'danger'],
    'presence.status_updated': ['Изменён статус', 'info'],
    'token.issue': ['Вход в мессенджер', 'ok'],
    'token.revoke_all': ['Отозваны все токены', 'danger'],
    'connection.open': ['Подключение', 'ok'],
    'connection.close': ['Отключение', 'ok'],
    'attachment.upload': ['Загружен файл', 'ok'],
    'chat.create': ['Создан чат', 'info'],
    'membership.add': ['Добавлен участник', 'info'],
    'membership.remove': ['Удалён участник', 'warn'],
  };
  const ADMIN_RETENTION_DEFS = [
    { key: 'retention_messages_days', label: 'Сообщения', hint: 'После срока текст сообщений стирается безвозвратно', opts: [90, 180, 365, 730, 0] },
    { key: 'retention_files_days', label: 'Файлы и медиа', hint: 'Вложения чистятся раньше текста — экономит хранилище', opts: [30, 90, 180, 365, 0] },
    { key: 'retention_audit_days', label: 'Журнал аудита', hint: 'Модель безопасности рекомендует не меньше 2 лет', opts: [180, 365, 730, 1095, 0] },
    { key: 'retention_backups_days', label: 'Резервные копии', hint: 'Срок хранения шифрованных снимков (для операторов)', opts: [7, 14, 30, 90, 0] },
  ];
  const ADMIN_LIMIT_DEFS = [
    { key: 'rate_messages_per_minute', label: 'Сообщений в минуту на пользователя', hint: 'Защита от флуда и ботов', unit: 'msg', step: 10, min: 10, max: 600 },
    { key: 'rate_logins_per_hour', label: 'Попыток входа в час с одного IP', hint: 'Применяется платформой при следующем входе', unit: 'try', step: 5, min: 5, max: 100 },
    { key: 'group_max_members', label: 'Максимум участников группы', hint: 'Больше 500 заметно нагружает рассылку', unit: 'чел', step: 50, min: 50, max: 1000 },
    { key: 'invites_per_day', label: 'Добавлений в группы в день', hint: 'Ограничивает массовый онбординг одним админом', unit: 'инв', step: 5, min: 5, max: 200 },
  ];

  function adminDays(value) {
    const n = Number(value) || 0;
    if (!n) return 'бессрочно';
    if (n < 365) return `${n} дн.`;
    return `${Math.round(n / 365 * 10) / 10} г.`;
  }
  function adminBytesToMb(bytes) {
    return bytes ? String(Math.round(bytes / (1024 * 1024))) : '';
  }
  function adminAuditEntry(row) {
    const known = ADMIN_AUDIT_ACTIONS[row.action];
    const kind = row.result && row.result !== 'success' ? 'danger' : (known ? known[1] : 'info');
    const meta = row.metadata && typeof row.metadata === 'object'
      ? Object.entries(row.metadata).map(([k, v]) => `${k}: ${v}`).join(', ')
      : '';
    // Connection events carry an internal Redis slot key as target_id — that
    // is plumbing, not something an admin should have to read.
    const internalTarget = row.action.startsWith('connection.');
    const target = !internalTarget && row.target_id && row.target_id !== '*' ? row.target_id : '';
    return {
      id: row.id,
      kind,
      title: known ? known[0] : row.action,
      detail: [target, meta].filter(Boolean).join(' · ') || '—',
      who: row.actor_username || 'система',
      at: row.created_at ? new Date(row.created_at).toLocaleString('ru-RU') : '',
      raw: row,
    };
  }

  function adminConfirm(modal, options) {
    const overlay = node('div', 'admin-confirm');
    const card = node('div', 'admin-confirm-card');
    card.appendChild(node('div', 'admin-confirm-title', options.title));
    card.appendChild(node('div', 'admin-confirm-text', options.text));
    const foot = node('div', 'admin-confirm-foot');
    const cancel = button('btn ghost', 'Отмена');
    cancel.addEventListener('click', () => overlay.remove());
    const confirm = button('btn danger', options.cta);
    confirm.addEventListener('click', async () => {
      overlay.remove();
      await options.run();
    });
    foot.append(cancel, confirm);
    card.appendChild(foot);
    overlay.appendChild(card);
    overlay.addEventListener('click', event => { if (event.target === overlay) overlay.remove(); });
    modal.appendChild(overlay);
  }

  async function openAdminPanel() {
    const layer = byId('modalLayer');
    clear(layer);
    layer.classList.add('show');
    const modal = node('div', 'modal admin');
    const side = node('div', 'admin-side');
    const main = node('div', 'admin-main');
    modal.append(side, main);
    layer.appendChild(modal);

    const view = {
      tab: 'general',
      config: null,
      users: [],
      presence: new Map(),
      log: [],
      search: '',
      filter: 'all',
      menu: null,
      logQuery: '',
      logFilter: 'all',
      fileLimit: '',
      draft: {},
    };

    try {
      view.config = await api(`${API}/admin/config`);
    } catch (error) {
      toast(error.message);
      closeModal();
      return;
    }
    view.fileLimit = adminBytesToMb(view.config.max_attachment_bytes);
    ADMIN_RETENTION_DEFS.concat(ADMIN_LIMIT_DEFS).forEach(def => {
      view.draft[def.key] = view.config[def.key];
    });

    async function loadUsers() {
      view.users = await api(`${API}/admin/users`);
      const names = view.users.map(u => u.username).join(',');
      if (names) {
        try {
          const rows = await api(`${API}/presence?users=${encodeURIComponent(names)}`);
          view.presence = new Map(rows.map(r => [r.username, r.status]));
        } catch (_) { /* presence is decorative here — the list still renders */ }
      }
    }
    async function loadLog() {
      const rows = await api(`${API}/audit?limit=100`);
      view.log = rows.map(adminAuditEntry);
    }
    async function patchConfig(changes) {
      view.config = await api(`${API}/admin/config`, { method: 'PUT', body: JSON.stringify(changes) });
      return view.config;
    }

    function isDirty() {
      if (view.tab === 'general') {
        return view.fileLimit.trim() !== adminBytesToMb(view.config.max_attachment_bytes);
      }
      const defs = view.tab === 'retention' ? ADMIN_RETENTION_DEFS : view.tab === 'limits' ? ADMIN_LIMIT_DEFS : [];
      return defs.some(d => view.draft[d.key] !== view.config[d.key]);
    }

    function renderSide() {
      clear(side);
      const head = node('div', 'admin-side-head');
      const close = iconButton('icon-btn', 'close', 'Закрыть');
      close.addEventListener('click', closeModal);
      const titles = node('div', '');
      titles.appendChild(node('div', 'admin-side-title', 'Админ-панель'));
      const online = view.users.filter(u => view.presence.get(u.username) === 'online').length;
      titles.appendChild(node('div', 'admin-side-sub', `${view.users.length} участников · ${online} в сети`));
      head.append(close, titles);
      side.appendChild(head);

      const nav = node('div', 'admin-nav');
      ADMIN_TABS.forEach(meta => {
        const item = button(`admin-nav-item${view.tab === meta.key ? ' active' : ''}`);
        item.appendChild(iconEl(meta.icon));
        item.appendChild(node('span', 'an-label', meta.label));
        let badge = '';
        if (meta.key === 'users') badge = String(view.users.filter(u => u.is_active).length);
        if (meta.key === 'audit') badge = String(view.log.filter(e => e.kind === 'danger').length);
        if (meta.key === 'auditors') badge = String(view.users.filter(u => u.auditor).length);
        if (badge && badge !== '0') item.appendChild(node('span', 'admin-nav-badge', badge));
        item.addEventListener('click', () => { view.tab = meta.key; view.menu = null; render(); });
        nav.appendChild(item);
      });
      side.appendChild(nav);
      side.appendChild(node('div', 'admin-side-foot', 'Модель безопасности\nmessenger-security-model.md'));
    }

    function renderShell() {
      clear(main);
      const meta = ADMIN_TABS.find(t => t.key === view.tab);
      const head = node('div', 'admin-head');
      const titles = node('div', '');
      titles.appendChild(node('div', 'admin-head-title', meta.title));
      titles.appendChild(node('div', 'admin-head-sub', meta.sub));
      head.appendChild(titles);
      if (isDirty()) head.appendChild(node('div', 'admin-dirty', 'не сохранено'));
      const body = node('div', 'admin-body');
      const foot = node('div', 'admin-foot');
      const limitMb = adminBytesToMb(view.config.max_attachment_bytes);
      foot.appendChild(node('div', 'admin-foot-text',
        `Файл ≤ ${limitMb || 'по умолчанию'}${limitMb ? ' МБ' : ''} · звонки ${view.config.calls_enabled ? 'вкл' : 'выкл'}`
        + ` · сообщения ${adminDays(view.config.retention_messages_days)} · ${view.config.rate_messages_per_minute} msg/мин`));
      const done = button('btn ghost', 'Готово');
      done.addEventListener('click', closeModal);
      foot.appendChild(done);
      main.append(head, body, foot);
      return body;
    }

    function toggleTrack(on, onClick) {
      const track = node('div', `switch${on ? ' on' : ''}`);
      if (onClick) track.addEventListener('click', event => { event.stopPropagation(); onClick(); });
      return track;
    }

    function renderGeneral(body) {
      body.appendChild(node('div', 'admin-label', 'Звонки'));
      const row = node('div', 'admin-toggle-row');
      const icon = node('div', 'admin-toggle-icon');
      icon.appendChild(iconEl('phone'));
      const info = node('div', 'admin-toggle-body');
      info.appendChild(node('div', 'admin-toggle-title', 'Звонки разрешены в организации'));
      info.appendChild(node('div', 'admin-toggle-hint',
        view.config.calls_enabled ? 'Аудио и видео доступны всем участникам' : 'Звонки выключены для всех'));
      const flip = async () => {
        try {
          await patchConfig({ calls_enabled: !view.config.calls_enabled });
          toast(view.config.calls_enabled ? 'Звонки включены' : 'Звонки выключены');
          render();
        } catch (error) { toast(error.message); }
      };
      row.append(icon, info, toggleTrack(view.config.calls_enabled, flip));
      row.addEventListener('click', flip);
      body.appendChild(row);

      body.appendChild(node('div', 'admin-sep'));
      body.appendChild(node('div', 'admin-label', 'Максимальный размер файла'));
      const inputRow = node('div', 'admin-input-row');
      const wrap = node('div', 'admin-input-wrap');
      const input = document.createElement('input');
      input.type = 'text';
      input.inputMode = 'numeric';
      input.value = view.fileLimit;
      input.placeholder = 'по умолчанию';
      input.addEventListener('input', () => {
        view.fileLimit = input.value.replace(/[^\d]/g, '').slice(0, 6);
        input.value = view.fileLimit;
        const head = main.querySelector('.admin-head');
        const existing = head.querySelector('.admin-dirty');
        if (isDirty() && !existing) head.appendChild(node('div', 'admin-dirty', 'не сохранено'));
        if (!isDirty() && existing) existing.remove();
      });
      wrap.append(input, node('span', 'admin-input-unit', 'МБ'));
      const save = button('btn primary', 'Сохранить');
      save.addEventListener('click', async () => {
        const mb = view.fileLimit.trim();
        try {
          await patchConfig(mb
            ? { max_attachment_bytes: Math.round(Number(mb) * 1024 * 1024) }
            : { max_attachment_bytes_set: true });
          toast(mb ? `Лимит файлов — ${mb} МБ` : 'Лимит файлов — по умолчанию');
          render();
        } catch (error) { toast(error.message); }
      });
      inputRow.append(wrap, save);
      body.appendChild(inputRow);

      const chips = node('div', 'admin-chips');
      [['50 МБ', '50'], ['100 МБ', '100'], ['500 МБ', '500'], ['1 ГБ', '1024']].forEach(([label, value]) => {
        const chip = button(`admin-chip${view.fileLimit.trim() === value ? ' soft' : ''}`, label);
        chip.addEventListener('click', () => { view.fileLimit = value; render(); });
        chips.appendChild(chip);
      });
      body.appendChild(chips);
    }

    function userStatusLine(user) {
      if (!user.is_active) return 'Доступ отключён администратором';
      const parts = [];
      const manual = user.dnd ? 'не беспокоить (вручную)' : user.manual_away ? 'отошёл (вручную)' : null;
      parts.push(manual || presenceStatusLabel(view.presence.get(user.username) || 'offline'));
      parts.push(`${user.sessions} активных сессий`);
      if (user.muted) parts.push('тихий режим');
      return parts.join(' · ');
    }

    function renderUsers(body) {
      const searchRow = node('div', 'admin-search-row');
      const search = node('div', 'admin-search');
      search.appendChild(iconEl('search'));
      const input = document.createElement('input');
      input.type = 'text';
      input.placeholder = 'Поиск по имени';
      input.value = view.search;
      input.addEventListener('input', () => { view.search = input.value; renderUserList(); });
      search.appendChild(input);
      const revokeAll = button('admin-pill danger', 'Отозвать все сессии');
      revokeAll.addEventListener('click', () => {
        const total = view.users.reduce((sum, u) => sum + (u.sessions || 0), 0);
        adminConfirm(modal, {
          title: 'Отозвать сессии всех пользователей?',
          text: `Все ${total} активных сессий будут аннулированы — каждый войдёт заново. Обычно применяется при подозрении на утечку токена.`,
          cta: 'Отозвать все',
          run: async () => {
            try {
              const result = await api(`${API}/admin/revoke-all-sessions`, { method: 'POST' });
              toast(`Сессии отозваны: ${result.affected}`);
              await loadUsers();
              render();
            } catch (error) { toast(error.message); }
          },
        });
      });
      searchRow.append(search, revokeAll);
      body.appendChild(searchRow);

      const filters = node('div', 'admin-chips');
      filters.style.marginBottom = '10px';
      const counts = {
        all: view.users.length,
        online: view.users.filter(u => view.presence.get(u.username) === 'online').length,
        off: view.users.filter(u => !u.is_active).length,
        auditors: view.users.filter(u => u.auditor).length,
      };
      [['all', 'Все'], ['online', 'В сети'], ['off', 'Отключены'], ['auditors', 'Аудиторы']].forEach(([key, label]) => {
        const chip = button(`admin-chip${view.filter === key ? ' active' : ''}`, `${label} · ${counts[key]}`);
        chip.addEventListener('click', () => { view.filter = key; view.menu = null; render(); });
        filters.appendChild(chip);
      });
      body.appendChild(filters);

      const list = node('div', '');
      list.id = 'adminUserList';
      body.appendChild(list);
      renderUserList();
    }

    function renderUserList() {
      const list = byId('adminUserList');
      if (!list) return;
      clear(list);
      const query = view.search.trim().toLowerCase();
      const visible = view.users.filter(u => {
        if (query && !(u.display_name || '').toLowerCase().includes(query)
          && !u.username.toLowerCase().includes(query)) return false;
        if (view.filter === 'online') return view.presence.get(u.username) === 'online';
        if (view.filter === 'off') return !u.is_active;
        if (view.filter === 'auditors') return u.auditor;
        return true;
      });
      if (!visible.length) {
        list.appendChild(node('div', 'admin-empty', 'Никого не нашлось'));
        return;
      }
      visible.forEach(user => list.appendChild(renderUserRow(user)));
    }

    async function userAction(fn, message) {
      try {
        await fn();
        if (message) toast(message);
        await loadUsers();
        render();
      } catch (error) { toast(error.message); }
    }

    function renderUserRow(user) {
      const row = node('div', `admin-user-row${user.is_active ? '' : ' off'}`);
      const avatarWrap = node('div', `admin-user-avatar${user.is_active ? '' : ' off'}`);
      avatarWrap.appendChild(avatar(user.display_name || user.username, 46, { username: user.username }));
      const status = user.dnd ? 'dnd' : user.manual_away ? 'away' : (view.presence.get(user.username) || 'offline');
      avatarWrap.appendChild(node('div', `admin-user-dot ${user.is_active ? presenceDotClass(status) : ''}`));
      row.appendChild(avatarWrap);

      const bodyCol = node('div', 'admin-user-body');
      const nameRow = node('div', 'admin-user-name');
      nameRow.appendChild(node('span', 'nm', user.display_name || user.username));
      if (user.is_admin) nameRow.appendChild(node('span', 'admin-tag admin', 'админ'));
      if (user.auditor) nameRow.appendChild(node('span', 'admin-tag auditor', 'аудитор'));
      if (user.muted) nameRow.appendChild(node('span', 'admin-tag muted', 'тихий'));
      bodyCol.appendChild(nameRow);
      bodyCol.appendChild(node('div', 'admin-user-sub', userStatusLine(user)));
      row.appendChild(bodyCol);

      const muteBtn = iconButton(`admin-icon-btn${user.muted ? ' on' : ''}`, 'bellOff',
        user.muted ? 'Снять тихий режим' : 'Тихий режим: запретить отправку сообщений');
      muteBtn.addEventListener('click', () => userAction(
        () => api(`${API}/admin/users/${encodeURIComponent(user.username)}/muted`,
          { method: 'PUT', body: JSON.stringify({ value: !user.muted }) }),
        user.muted ? `Тихий режим снят: ${user.display_name}` : `Тихий режим: ${user.display_name}`,
      ));
      row.appendChild(muteBtn);

      const menuBtn = iconButton(`admin-icon-btn${view.menu === user.username ? ' on' : ''}`, 'more', 'Ещё');
      menuBtn.addEventListener('click', () => {
        view.menu = view.menu === user.username ? null : user.username;
        renderUserList();
      });
      row.appendChild(menuBtn);

      row.appendChild(toggleTrack(user.is_active, () => {
        if (user.is_active) {
          adminConfirm(modal, {
            title: `Отключить ${user.display_name}?`,
            text: 'Доступ к рабочему пространству закроется, активные сессии будут сброшены. Историю переписок это не удаляет.',
            cta: 'Отключить',
            run: () => userAction(
              () => api(`${API}/admin/users/${encodeURIComponent(user.username)}/active`,
                { method: 'PUT', body: JSON.stringify({ value: false }) }),
              `Отключён: ${user.display_name}`,
            ),
          });
          return;
        }
        userAction(
          () => api(`${API}/admin/users/${encodeURIComponent(user.username)}/active`,
            { method: 'PUT', body: JSON.stringify({ value: true }) }),
          `Доступ открыт: ${user.display_name}`,
        );
      }));

      if (view.menu === user.username) row.appendChild(renderUserMenu(user));
      return row;
    }

    function renderUserMenu(user) {
      const menu = node('div', 'admin-menu');
      const add = (label, hint, danger, onClick) => {
        const item = button(`admin-menu-item${danger ? ' danger' : ''}`);
        item.appendChild(node('span', 'mi-label', label));
        if (hint) item.appendChild(node('span', 'mi-hint', hint));
        item.addEventListener('click', () => { view.menu = null; onClick(); });
        menu.appendChild(item);
      };
      add('Отозвать все сессии', `${user.sessions} шт.`, true, () => adminConfirm(modal, {
        title: `Отозвать сессии ${user.display_name}?`,
        text: `Пользователь выйдет из ${user.sessions} устройств и должен будет войти заново. Токены станут недействительны сразу.`,
        cta: 'Отозвать',
        run: () => userAction(
          () => api(`${API}/admin/users/${encodeURIComponent(user.username)}/revoke-sessions`, { method: 'POST' }),
          `Сессии отозваны: ${user.display_name}`,
        ),
      }));
      add('Задать статус вручную', '', false, () => openAdminUserStatusPanel(user));
      add(user.calls_enabled ? 'Запретить звонки' : 'Разрешить звонки', '', false, () => userAction(
        () => api(`${API}/admin/users/${encodeURIComponent(user.username)}/calls`,
          { method: 'PUT', body: JSON.stringify({ calls_enabled: !user.calls_enabled }) }),
        user.calls_enabled ? `Звонки запрещены: ${user.display_name}` : `Звонки разрешены: ${user.display_name}`,
      ));
      add(user.auditor ? 'Снять роль аудитора' : 'Назначить аудитором', 'security_auditor', false, () => userAction(
        () => api(`${API}/admin/users/${encodeURIComponent(user.username)}/auditor`,
          { method: 'PUT', body: JSON.stringify({ value: !user.auditor }) }),
        user.auditor ? `Роль снята: ${user.display_name}` : `Аудитор: ${user.display_name}`,
      ));
      add('Показать в журнале', '', false, () => {
        view.tab = 'audit';
        view.logQuery = user.username;
        view.logFilter = 'all';
        render();
      });
      return menu;
    }

    function renderAudit(body) {
      const searchRow = node('div', 'admin-search-row');
      const search = node('div', 'admin-search');
      search.appendChild(iconEl('search'));
      const input = document.createElement('input');
      input.type = 'text';
      input.placeholder = 'Поиск: действие, пользователь';
      input.value = view.logQuery;
      input.addEventListener('input', () => { view.logQuery = input.value; renderLogList(); });
      search.appendChild(input);
      const exportBtn = button('admin-pill accent', 'Экспорт CSV');
      exportBtn.addEventListener('click', () => exportAuditCsv());
      searchRow.append(search, exportBtn);
      body.appendChild(searchRow);

      const filters = node('div', 'admin-chips');
      filters.style.marginBottom = '12px';
      [['all', 'Все события'], ['danger', 'Опасные'], ['warn', 'Доступ'], ['info', 'Настройки'], ['ok', 'Входы']].forEach(([key, label]) => {
        const chip = button(`admin-chip${view.logFilter === key ? ' active' : ''}`, label);
        chip.addEventListener('click', () => { view.logFilter = key; render(); });
        filters.appendChild(chip);
      });
      body.appendChild(filters);

      const list = node('div', '');
      list.id = 'adminLogList';
      body.appendChild(list);
      renderLogList();
    }

    function visibleLog() {
      const query = view.logQuery.trim().toLowerCase();
      return view.log.filter(entry => {
        if (view.logFilter !== 'all' && entry.kind !== view.logFilter) return false;
        if (!query) return true;
        return `${entry.title} ${entry.detail} ${entry.who}`.toLowerCase().includes(query);
      });
    }

    function renderLogList() {
      const list = byId('adminLogList');
      if (!list) return;
      clear(list);
      const rows = visibleLog();
      if (!rows.length) {
        list.appendChild(node('div', 'admin-empty', 'Событий не найдено'));
        return;
      }
      rows.forEach((entry, index) => {
        const item = node('div', 'admin-log-item');
        const rail = node('div', 'admin-log-rail');
        const dot = node('div', 'admin-log-dot');
        dot.style.background = ADMIN_SEV[entry.kind];
        rail.appendChild(dot);
        if (index !== rows.length - 1) rail.appendChild(node('div', 'admin-log-line'));
        item.appendChild(rail);
        const bodyCol = node('div', 'admin-log-body');
        const title = node('div', 'admin-log-title');
        title.appendChild(node('span', '', entry.title));
        const tag = node('span', 'admin-log-tag', ADMIN_SEV_LABEL[entry.kind]);
        tag.style.color = ADMIN_SEV[entry.kind];
        tag.style.background = `color-mix(in srgb, ${ADMIN_SEV[entry.kind]} 14%, transparent)`;
        title.appendChild(tag);
        bodyCol.appendChild(title);
        bodyCol.appendChild(node('div', 'admin-log-detail', entry.detail));
        bodyCol.appendChild(node('div', 'admin-log-meta', `${entry.who} · ${entry.at}`));
        item.appendChild(bodyCol);
        list.appendChild(item);
      });
      list.appendChild(node('div', 'admin-note',
        `${rows.length} из ${view.log.length} событий · журнал хранится ${adminDays(view.config.retention_audit_days)}`));
    }

    function exportAuditCsv() {
      const rows = visibleLog();
      const escape = value => `"${String(value == null ? '' : value).replace(/"/g, '""')}"`;
      const csv = [['time', 'actor', 'action', 'target', 'result'].join(',')]
        .concat(rows.map(entry => [
          entry.raw.created_at, entry.raw.actor_username, entry.raw.action,
          entry.raw.target_id, entry.raw.result,
        ].map(escape).join(',')))
        .join('\r\n');
      const blob = new Blob([`﻿${csv}`], { type: 'text/csv;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `audit-${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      toast(`CSV сформирован: ${rows.length} записей`);
    }

    function renderRetention(body) {
      ADMIN_RETENTION_DEFS.forEach(def => {
        const card = node('div', 'admin-card');
        const head = node('div', 'admin-card-head');
        head.appendChild(node('div', 'admin-card-title', def.label));
        const dirty = view.draft[def.key] !== view.config[def.key];
        head.appendChild(node('div', `admin-card-value${dirty ? ' dirty' : ''}`, adminDays(view.draft[def.key])));
        card.appendChild(head);
        card.appendChild(node('div', 'admin-card-hint', def.hint));
        const chips = node('div', 'admin-chips');
        chips.style.marginTop = '0';
        def.opts.forEach(value => {
          const chip = button(`admin-chip${view.draft[def.key] === value ? ' soft' : ''}`, adminDays(value));
          chip.addEventListener('click', () => { view.draft[def.key] = value; render(); });
          chips.appendChild(chip);
        });
        card.appendChild(chips);
        body.appendChild(card);
      });

      const purge = node('div', 'admin-card row');
      const purgeBody = node('div', '');
      purgeBody.style.flex = '1';
      purgeBody.appendChild(node('div', 'admin-card-title', 'Ночная задача очистки'));
      purgeBody.appendChild(node('div', 'admin-card-hint', view.config.auto_purge_enabled
        ? 'Раз в сутки удаляет данные вне политики хранения'
        : 'Выключена — данные накапливаются без ограничений'));
      const flipPurge = async () => {
        try {
          await patchConfig({ auto_purge_enabled: !view.config.auto_purge_enabled });
          toast(view.config.auto_purge_enabled ? 'Ночная очистка включена' : 'Ночная очистка выключена');
          render();
        } catch (error) { toast(error.message); }
      };
      purge.append(purgeBody, toggleTrack(view.config.auto_purge_enabled, flipPurge));
      purge.addEventListener('click', flipPurge);
      body.appendChild(purge);

      const actions = node('div', 'admin-actions end');
      const save = button('btn primary', 'Применить политику');
      save.addEventListener('click', async () => {
        const changes = {};
        ADMIN_RETENTION_DEFS.forEach(def => { changes[def.key] = view.draft[def.key]; });
        try {
          await patchConfig(changes);
          toast('Политика хранения применена');
          render();
        } catch (error) { toast(error.message); }
      });
      actions.appendChild(save);
      body.appendChild(actions);
    }

    function renderLimits(body) {
      ADMIN_LIMIT_DEFS.forEach(def => {
        const card = node('div', 'admin-card row');
        const info = node('div', '');
        info.style.flex = '1';
        info.style.minWidth = '0';
        info.appendChild(node('div', 'admin-card-title', def.label));
        info.appendChild(node('div', 'admin-card-hint', def.hint));
        const stepper = node('div', 'admin-stepper');
        const value = view.draft[def.key];
        const bump = delta => () => {
          view.draft[def.key] = Math.min(def.max, Math.max(def.min, view.draft[def.key] + delta));
          render();
        };
        const minus = button(`admin-step${value <= def.min ? ' disabled' : ''}`, '−');
        minus.addEventListener('click', bump(-def.step));
        const plus = button(`admin-step${value >= def.max ? ' disabled' : ''}`, '+');
        plus.addEventListener('click', bump(def.step));
        stepper.append(minus, node('div', 'admin-step-value', String(value)), plus);
        card.append(info, stepper, node('div', 'admin-step-unit', def.unit));
        body.appendChild(card);
      });

      const actions = node('div', 'admin-actions');
      const dirty = ADMIN_LIMIT_DEFS.some(d => view.draft[d.key] !== view.config[d.key]);
      actions.appendChild(node('div', 'admin-note', dirty
        ? 'Применяется без перезапуска — env больше не нужен'
        : `Сохранено: ${view.config.rate_messages_per_minute} msg/мин · группа до ${view.config.group_max_members}`));
      const save = button('btn primary', 'Применить лимиты');
      save.addEventListener('click', async () => {
        const changes = {};
        ADMIN_LIMIT_DEFS.forEach(def => { changes[def.key] = view.draft[def.key]; });
        try {
          await patchConfig(changes);
          toast('Лимиты применены');
          render();
        } catch (error) { toast(error.message); }
      });
      actions.appendChild(save);
      body.appendChild(actions);
    }

    function renderAuditors(body) {
      const hero = node('div', 'admin-hero');
      const heroIcon = node('div', 'admin-hero-icon');
      heroIcon.appendChild(iconEl('shield'));
      hero.append(heroIcon, node('div', 'admin-hero-text',
        'Аудиторы читают журнал и метаданные, но не содержимое переписок. '
        + 'Раньше список задавался переменной окружения — теперь им можно управлять здесь.'));
      body.appendChild(hero);

      const auditors = view.users.filter(u => u.auditor);
      if (!auditors.length) {
        body.appendChild(node('div', 'admin-empty', 'Аудиторы не назначены'));
      } else {
        auditors.forEach(user => {
          const row = node('div', 'admin-user-row');
          row.appendChild(avatar(user.display_name || user.username, 42, { username: user.username }));
          const info = node('div', 'admin-user-body');
          info.appendChild(node('div', 'admin-user-name', user.display_name || user.username));
          info.appendChild(node('div', 'admin-user-sub',
            user.is_admin ? 'администратор · полный доступ к журналу' : 'читает журнал и метаданные'));
          row.appendChild(info);
          const remove = button('admin-pill danger', 'Снять');
          remove.style.height = '34px';
          remove.addEventListener('click', () => userAction(
            () => api(`${API}/admin/users/${encodeURIComponent(user.username)}/auditor`,
              { method: 'PUT', body: JSON.stringify({ value: false }) }),
            `Роль снята: ${user.display_name}`,
          ));
          row.appendChild(remove);
          body.appendChild(row);
        });
      }

      body.appendChild(node('div', 'admin-sep'));
      body.appendChild(node('div', 'admin-label', 'Назначить аудитора'));
      const chips = node('div', 'admin-chips');
      view.users.filter(u => !u.auditor).forEach(user => {
        const chip = button('admin-cand');
        chip.appendChild(avatar(user.display_name || user.username, 32, { username: user.username }));
        chip.appendChild(node('span', 'cd-name', user.display_name || user.username));
        chip.appendChild(node('span', 'cd-plus', '+'));
        chip.addEventListener('click', () => userAction(
          () => api(`${API}/admin/users/${encodeURIComponent(user.username)}/auditor`,
            { method: 'PUT', body: JSON.stringify({ value: true }) }),
          `Аудитор: ${user.display_name}`,
        ));
        chips.appendChild(chip);
      });
      body.appendChild(chips);
    }

    function openAdminUserStatusPanel(user) {
      let manualStatus = user.dnd ? 'dnd' : user.manual_away ? 'away' : null;
      const overlay = node('div', 'admin-confirm');
      const card = node('div', 'admin-confirm-card');
      card.appendChild(node('div', 'admin-confirm-title', `Статус: ${user.display_name}`));
      const options = [
        [null, 'user', 'В сети'],
        ['away', 'pause', 'Отошёл'],
        ['dnd', 'moon', 'Не беспокоить'],
      ];
      const toggles = options.map(([value, icon, label]) => {
        const opt = button(`status-toggle${manualStatus === value ? ' active' : ''}`);
        opt.style.margin = '0 0 6px';
        opt.append(iconEl(icon), node('span', 'label', label));
        opt.addEventListener('click', () => {
          manualStatus = value;
          toggles.forEach((el, i) => el.classList.toggle('active', options[i][0] === value));
        });
        card.appendChild(opt);
        return opt;
      });
      const foot = node('div', 'admin-confirm-foot');
      const cancel = button('btn ghost', 'Отмена');
      cancel.addEventListener('click', () => overlay.remove());
      const save = button('btn primary', 'Сохранить');
      save.addEventListener('click', async () => {
        overlay.remove();
        await userAction(
          () => api(`${API}/admin/users/${encodeURIComponent(user.username)}/status`, {
            method: 'PUT',
            body: JSON.stringify({
              dnd: manualStatus === 'dnd', manual_away: manualStatus === 'away',
              status_text: '', expires_in_minutes: null,
            }),
          }),
          `Статус обновлён: ${user.display_name}`,
        );
      });
      foot.append(cancel, save);
      card.appendChild(foot);
      overlay.appendChild(card);
      overlay.addEventListener('click', event => { if (event.target === overlay) overlay.remove(); });
      modal.appendChild(overlay);
    }

    function render() {
      renderSide();
      const body = renderShell();
      if (view.tab === 'general') renderGeneral(body);
      else if (view.tab === 'users') renderUsers(body);
      else if (view.tab === 'audit') renderAudit(body);
      else if (view.tab === 'retention') renderRetention(body);
      else if (view.tab === 'limits') renderLimits(body);
      else if (view.tab === 'auditors') renderAuditors(body);
    }

    render();
    try {
      await loadUsers();
      await loadLog();
    } catch (error) {
      toast(error.message);
    }
    render();
  }

  async function sendSticker(stickerId) {
    const chat = activeChat();
    if (!chat || !stickerById(stickerId)) return;
    const text = stickerPayload(stickerId);
    const clientId = crypto.randomUUID();
    const replyId = state.replyTo ? state.replyTo.id : null;
    beginStickerAnim(clientId);
    const optimistic = normalizeMessage({
      id: `pending-${clientId}`, client_id: clientId, chat_id: chat.id, sequence: null,
      sender_username: state.me.username, sender_display_name: state.me.display_name,
      text, reply_to_id: replyId, created_at: new Date().toISOString(),
      edited_at: null, deleted_at: null, optimistic: true, read: false,
    });
    mergeMessage(optimistic);
    updateChatPreviewOptimistic(optimistic);
    hideEmojiPanel();
    closeComposeContext();
    renderMessages(false, { force: true, scrollToBottom: true });
    afterStickerSendRender(clientId);
    try {
      const message = await api(`${API}/chats/${encodeURIComponent(chat.id)}/messages`, {
        method: 'POST', body: JSON.stringify({ text, client_id: clientId, reply_to_id: replyId }),
      });
      syncOutgoingMessageStatus(message, clientId);
      if (!messageRowEl(message.id) && !isAnimLocked()) renderMessages();
    } catch (error) {
      state.stickerAnimLock = false;
      state.stickerAnimRemaining = 0;
      state.pendingStickerAnim = null;
      state.queuedRender = null;
      state.messages.set(chat.id, activeMessages().filter(item => item.client_id !== clientId));
      renderMessages(false, { force: true });
      toast(`Не удалось отправить стикер: ${error.message}`);
    }
  }
  function hideEmojiPanel() {
    destroyStickerVirtualGrid();
    clearTimeout(stickerSearchTimer);
    const panel = byId('emojiPanel');
    panel.classList.remove('show');
    byId('emojiButton')?.classList.remove('active');
    state.emojiSearch = '';
    clear(panel);
    delete panel.dataset.shellReady;
  }
  function showEmojiPanel() {
    closeInfoPanel();
    ensureEmojiPanelShell();
    updateStickerGridContent();
    byId('emojiPanel').classList.add('show');
    byId('emojiButton')?.classList.add('active');
  }
  function ensureEmojiPanelShell() {
    const panel = byId('emojiPanel');
    if (panel.dataset.shellReady) return;
    clear(panel);
    const head = node('div', 'emoji-panel-head');
    const closeBtn = iconButton('icon-btn', 'close', 'Закрыть');
    closeBtn.addEventListener('click', () => hideEmojiPanel());
    head.appendChild(closeBtn);
    head.appendChild(node('span', 'emoji-panel-title', 'Стикеры'));
    panel.appendChild(head);

    const searchWrap = node('div', 'emoji-panel-search');
    searchWrap.appendChild(iconEl('search'));
    const searchInput = document.createElement('input');
    searchInput.type = 'search';
    searchInput.placeholder = 'Поиск';
    searchInput.value = state.emojiSearch || '';
    searchInput.addEventListener('input', () => {
      state.emojiSearch = searchInput.value.trim().toLowerCase();
      scheduleStickerGridUpdate();
    });
    searchWrap.appendChild(searchInput);
    panel.appendChild(searchWrap);

    const body = node('div', 'emoji-panel-body');
    body.id = 'emojiPanelBody';
    panel.appendChild(body);
    panel.dataset.shellReady = '1';
  }
  function buildEmojiPanel() {
    ensureEmojiPanelShell();
    updateStickerGridContent();
  }
  function buildAttachMenu() {
    const menu = byId('attachMenu');
    menu.appendChild(menuItem('Фото или видео', 'image', () => byId('mediaInput').click()));
    menu.appendChild(menuItem('Документ', 'file', () => byId('fileInput').click()));
    menu.appendChild(menuItem('Опрос', 'select', () => later('Опросы')));
  }
  function insertAtCaret(element, text) {
    element.focus();
    const selection = window.getSelection();
    if (!selection.rangeCount) { element.textContent += text; return; }
    const range = selection.getRangeAt(0);
    range.deleteContents();
    range.insertNode(document.createTextNode(text));
    range.collapse(false);
    selection.removeAllRanges();
    selection.addRange(range);
  }
  async function uploadFiles(files) {
    const chat = activeChat();
    if (!chat) return;
    for (const file of files) {
      const card = node('div', 'upload-card');
      const label = node('div', 'upload-name', file.name);
      const track = node('div', 'upload-track');
      const fill = node('div', 'upload-fill');
      track.appendChild(fill);
      card.append(label, track, node('div', 'upload-status', 'Подготовка…'));
      byId('uploadQueue').appendChild(card);
      state.uploadCount += 1;
      try {
        const result = await uploadFile(chat.id, file, progress => {
          fill.style.width = `${progress}%`;
          card.lastElementChild.textContent = `Загрузка ${progress}%`;
        });
        const message = await api(`${API}/chats/${encodeURIComponent(chat.id)}/messages`, {
          method: 'POST',
          body: JSON.stringify({
            text: '',
            client_id: crypto.randomUUID(),
            reply_to_id: state.replyTo ? state.replyTo.id : null,
            attachment_ids: [result.id],
          }),
        });
        mergeMessage(message);
        updateChatFromMessage(message);
        closeComposeContext();
        renderMessages(false, { scrollToBottom: true });
        card.lastElementChild.textContent = 'Загружено';
        window.setTimeout(() => card.remove(), 900);
      } catch (error) {
        card.classList.add('failed');
        card.lastElementChild.textContent = error.status === 404 || error.status === 405
          ? 'Загрузка будет добавлена позже' : error.message;
        window.setTimeout(() => card.remove(), 4000);
      } finally { state.uploadCount -= 1; }
    }
  }
  async function uploadFile(chatId, file, onProgress, retry = true) {
    await ensureToken();
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', `${API}/chats/${encodeURIComponent(chatId)}/attachments`);
      xhr.responseType = 'json';
      xhr.setRequestHeader('Accept', 'application/json');
      xhr.setRequestHeader('Authorization', `Bearer ${state.token}`);
      xhr.upload.addEventListener('progress', event => {
        if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
      });
      xhr.addEventListener('load', async () => {
        if (xhr.status === 401 && retry) {
          state.token = '';
          try { resolve(await uploadFile(chatId, file, onProgress, false)); } catch (error) { reject(error); }
          return;
        }
        if (xhr.status >= 200 && xhr.status < 300) { resolve(xhr.response || {}); return; }
        const error = new Error((xhr.response && (xhr.response.error || xhr.response.detail)) || `HTTP ${xhr.status}`);
        error.status = xhr.status; reject(error);
      });
      xhr.addEventListener('error', () => reject(new Error('Ошибка сети при загрузке')));
      const form = new FormData();
      form.append('file', file, file.name);
      xhr.send(form);
    });
  }
  function sendTyping(typing) {
    window.clearTimeout(state.typingTimer);
    if (!state.activeChatId) return;
    socketSend({ type: 'typing', chat_id: state.activeChatId, active: typing });
    if (typing) state.typingTimer = window.setTimeout(() => sendTyping(false), 2400);
  }

  function scrollToMessage(id) {
    const row = all('.msg-row').find(item => item.dataset.messageId === String(id));
    if (!row) { toast('Сообщение не загружено'); return; }
    row.scrollIntoView({ behavior: 'auto', block: 'center' });
  }
  function socketSend(payload) {
    if (state.socket && state.socket.readyState === WebSocket.OPEN) state.socket.send(JSON.stringify(payload));
  }
  function handleSocketEvent(event) {
    if (event.type === 'ready') {
      state.socketReady = true; state.reconnectAttempt = 0;
      setStatus('', true);
      state.subscribedChats.clear();
      syncChatSubscriptions();
      window.clearInterval(state.heartbeat);
      state.heartbeat = window.setInterval(() => socketSend({ type: 'ping', activity: currentActivity() }), 25000);
      loadMyStatus().then(() => { renderHeader(); renderChats(); if (state.infoOpen) renderInfo(); });
      return;
    }
    if (event.type === 'call') { handleCallEvent(event); return; }
    if (event.type === 'call_session') { handleCallSessionEvent(event); return; }
    if (event.type === 'subscribed') return;
    if (event.type === 'error') return;
    if (event.type === 'pong') return;
    if (event.type === 'membership.updated' || event.type === 'chat.updated' || event.type === 'chat.deleted') {
      loadChats({ skipAutoOpen: true }).catch(error => toast(error.message));
      return;
    }
    if (event.type === 'typing.updated') {
      if (event.username === state.me.username) return;
      if (event.active) state.typing.set(event.chat_id, `${event.display_name || event.username} печатает…`);
      else state.typing.delete(event.chat_id);
      renderHeader(); renderChats();
      return;
    }
    if (event.type === 'presence.updated') {
      if (!event.username) return;
      const entry = {
        status: event.status || 'offline',
        status_text: event.status_text || null,
        status_until: event.status_until || null,
        manual_status: event.manual_status || null,
      };
      state.presence.set(event.username, entry);
      if (event.username === state.me?.username) state.myStatus = entry;
      renderChats();
      renderHeader();
      if (state.infoOpen) renderInfo();
      return;
    }
    if (event.type === 'receipt.updated') {
      const chatId = event.receipt?.chat_id;
      if (chatId && event.receipt?.username) {
        chatReadCursors(chatId).set(event.receipt.username, Number(event.receipt.sequence) || 0);
      }
      if (chatIdEq(chatId, state.activeChatId)) {
        recomputeOutgoingRead(chatId);
        if (isAnimLocked()) {
          patchOutgoingCheckmarks();
          state.queuedRender = state.queuedRender || { keepPosition: false };
        } else {
          renderMessages();
        }
      }
      return;
    }
    if (event.type === 'message.deleted' && event.message) {
      removeMessageFromState(event.message);
      refreshChatPreview(event.message.chat_id);
      if (chatIdEq(event.message.chat_id, state.activeChatId)) {
        renderMessages(true);
        renderPinned();
      }
      return;
    }
    if (event.type && event.type.startsWith('message.') && event.message) {
      const messageChatId = event.message.chat_id;
      if (!state.chats.some(chat => chatIdEq(chat.id, messageChatId))) {
        loadChats().catch(() => {});
      }
      if (event.message.deleted_at) {
        removeMessageFromState(event.message);
        refreshChatPreview(messageChatId);
        if (chatIdEq(messageChatId, state.activeChatId)) {
          renderMessages(true);
          renderPinned();
        }
        renderChatList();
        return;
      }
      const isOwn = event.message.sender_username === state.me.username;
      const isActive = chatIdEq(messageChatId, state.activeChatId);
      if (isOwn && isActive) {
        syncOutgoingMessageStatus(event.message, event.message.client_id);
      } else {
        // Sticker "burst" animation was only ever armed on the sender's own
        // optimistic send path (beginStickerAnim in sendPayloadText) — a
        // sticker landing here, on the recipient's side, never got one,
        // so it always rendered as a static frame. Arm the same one-shot
        // animation slot here too, keyed by the message's own client_id,
        // so shouldAnimateMessageStickers() picks it up when this message
        // renders below.
        if (isActive && !state.stickerAnimLock && countStickersInText(event.message.text)) {
          beginStickerAnim(event.message.client_id || event.message.id);
        }
        mergeMessage(event.message);
        updateChatFromMessage(event.message, { bumpUnread: true });
        if (window.MessengerNotify) {
          window.MessengerNotify.onMessage(state.chats.find(c => chatIdEq(c.id, messageChatId)), event.message);
        }
      }
      if (isActive) {
        const pending = state.pendingStickerAnim;
        const keepAnim = Boolean(state.stickerAnimLock && pending && (
          (pending.clientId && String(event.message.client_id) === String(pending.clientId))
          || (pending.messageId && String(event.message.id) === String(pending.messageId))
          || (pending.clientId && String(event.message.id) === `pending-${pending.clientId}`)
        ));
        if (keepAnim || isAnimLocked()) {
          if (keepAnim && pending) {
            pending.messageId = event.message.id;
          } else if (!isOwn) {
            state.queuedRender = { keepPosition: false };
          }
          renderPinned();
          flushQueuedRenderIfUnlocked();
        } else {
          renderMessages(false, { scrollToBottom: !isOwn });
          renderPinned();
        }
        markRead(messageChatId, event.message.sequence);
      }
      renderChatList();
      return;
    }
    if (event.type === 'reaction.updated') {
      const message = findMessage(event.message_id);
      if (message) {
        const previousMine = new Map(
          message.reactions.map(reaction => [reactionStoreId(reaction.emoji), reaction.mine]),
        );
        message.reactions = (event.reactions || []).map(reaction => {
          const storeId = reactionStoreId(reaction.emoji);
          return {
            ...reaction,
            mine: event.actor_username === state.me.username
              ? Boolean(reaction.reacted_by_me)
              : Boolean(previousMine.get(storeId)),
          };
        });
        // Active chat: patch this message's .reactions only — never remount the list.
        const animatingThis = Boolean(
          state.reactionAnimLock
          && state.pendingReactionAnim
          && String(state.pendingReactionAnim.messageId) === String(event.message_id),
        );
        syncMessageReactions(message, { preserveAnimating: animatingThis });
        if (window.MessengerNotify && event.actor_username !== state.me.username
          && message.sender_username === state.me.username) {
          window.MessengerNotify.onReaction(state.chats.find(c => chatIdEq(c.id, message.chat_id)), message, event);
        }
      }
      return;
    }
    if (event.type === 'pin.updated') {
      const message = findMessage(event.message_id);
      if (message) message.pinned = Boolean(event.pinned);
      if (window.MessengerNotify && event.pinned && event.actor_username !== state.me.username) {
        window.MessengerNotify.onPinned(state.chats.find(c => chatIdEq(c.id, event.chat_id)), message);
      }
      if (isAnimLocked()) {
        state.queuedRender = { keepPosition: true };
        renderPinned();
      } else {
        renderMessages(true); renderPinned();
      }
    }
  }

  // ---- Calls (1:1 ringing/control plane + LiveKit media, same SFU as group calls) ----
  function callFromChat(chat) {
    if (!chat || chat.kind !== 'direct') return;
    const peer = chatPeer(chat);
    if (!peer) { toast('Собеседник не найден'); return; }
    startCall(peer.username, peer.display_name, chat.id, true);
  }
  function sendCallSignal(callId, payload, to) {
    const command = { type: 'call', call_id: callId, payload };
    if (to) command.to = to;
    socketSend(command);
  }
  function playRingtone() {
    if (!state.call) return;
    stopRingtone();
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const ctx = new Ctx();
      state.call.audioCtx = ctx;
      const beep = () => {
        if (!state.call || state.call.audioCtx !== ctx) return;
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.frequency.value = 880;
        osc.connect(gain).connect(ctx.destination);
        const now = ctx.currentTime;
        gain.gain.setValueAtTime(0.0001, now);
        gain.gain.exponentialRampToValueAtTime(0.15, now + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.35);
        osc.start(now);
        osc.stop(now + 0.4);
      };
      beep();
      state.call.ringToneTimer = window.setInterval(beep, 1500);
    } catch (_) { /* WebAudio unavailable — silent ringing is acceptable */ }
  }
  function stopRingtone() {
    if (!state.call) return;
    if (state.call.ringToneTimer) window.clearInterval(state.call.ringToneTimer);
    if (state.call.audioCtx) { try { state.call.audioCtx.close(); } catch (_) {} }
    state.call.ringToneTimer = null;
    state.call.audioCtx = null;
  }
  function callDurationText(totalSeconds) {
    const m = Math.floor(totalSeconds / 60);
    const s = totalSeconds % 60;
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }
  function callStatusLabel(call) {
    if (call.status === 'ringing') return call.role === 'caller' ? 'Вызов…' : 'Входящий звонок';
    if (call.status === 'accepting') return 'Соединение…';
    return '';
  }
  function callSupported() {
    return Boolean(window.LivekitClient && navigator.mediaDevices?.getUserMedia);
  }
  async function startCall(peerUsername, peerDisplayName, chatId, withVideo) {
    if (state.call) { toast('Звонок уже выполняется'); return; }
    if (!callSupported()) { toast('Звонки не поддерживаются в этом браузере'); return; }
    const callId = crypto.randomUUID();
    state.call = {
      callId, role: 'caller', peerUsername, peerDisplayName, chatId,
      status: 'ringing', media: { audio: true, video: withVideo },
      room: null, micOn: true, camOn: withVideo, screenSharing: false,
      ringTimer: null, durationTimer: null, ringToneTimer: null, audioCtx: null, answeredAt: null,
    };
    sendCallSignal(callId, { type: 'invite', chat_id: chatId, media: { audio: true, video: withVideo } }, peerUsername);
    renderCallOverlay();
    state.call.ringTimer = window.setTimeout(() => {
      if (state.call?.status === 'ringing') {
        sendCallSignal(callId, { type: 'cancel', reason: 'timeout' });
        endCallLocal('missed');
      }
    }, 45000);
  }
  function handleIncomingInvite(payload) {
    if (state.call) {
      sendCallSignal(payload.call_id, { type: 'decline' });
      return;
    }
    if (!callSupported()) {
      sendCallSignal(payload.call_id, { type: 'decline' });
      return;
    }
    if (window.MessengerNotify && !window.MessengerNotify.shouldAcceptCallOnThisDevice()) {
      sendCallSignal(payload.call_id, { type: 'decline' });
      return;
    }
    const media = payload.media || { audio: true, video: false };
    state.call = {
      callId: payload.call_id, role: 'callee', peerUsername: payload.from,
      peerDisplayName: payload.from_display_name || payload.from, chatId: payload.chat_id,
      status: 'ringing', media,
      room: null, micOn: true, camOn: Boolean(media.video), screenSharing: false,
      ringTimer: null, durationTimer: null, ringToneTimer: null, audioCtx: null, answeredAt: null,
    };
    renderCallOverlay();
    playRingtone();
    state.call.ringTimer = window.setTimeout(() => {
      if (state.call?.status === 'ringing') {
        sendCallSignal(payload.call_id, { type: 'cancel', reason: 'timeout' });
        endCallLocal('missed');
      }
    }, 45000);
  }
  function acceptCall() {
    if (!state.call || state.call.role !== 'callee') return;
    stopRingtone();
    window.clearTimeout(state.call.ringTimer);
    state.call.status = 'accepting';
    sendCallSignal(state.call.callId, { type: 'accept' });
    renderCallOverlay();
  }
  function declineCall() {
    if (!state.call) return;
    sendCallSignal(state.call.callId, { type: 'decline' });
    endCallLocal('declined');
  }
  function hangupCall() {
    if (!state.call) return;
    const stillRinging = state.call.status === 'ringing' || state.call.status === 'accepting';
    sendCallSignal(state.call.callId, {
      type: stillRinging ? (state.call.role === 'caller' ? 'cancel' : 'decline') : 'hangup',
    });
    endCallLocal('ended');
  }
  // Best-effort: applies the mic/speaker/camera chosen in Settings > Звук и
  // камера. Each device is independently optional — a missing/unplugged
  // pick should never block the call, just fall back to the room default.
  async function applyPreferredCallDevices(room) {
    if (!window.MessengerNotify?.useDeviceInCalls?.()) return;
    for (const [kind, pref] of [['audiooutput', 'speaker'], ['audioinput', 'mic'], ['videoinput', 'camera']]) {
      const id = window.MessengerNotify.preferredDeviceId(pref);
      if (id) { try { await room.switchActiveDevice(kind, id); } catch (_) { /* device gone, keep default */ } }
    }
  }
  function attachDirectCallTrack(participant, track) {
    if (!track) return;
    const el = byId(participant?.isLocal ? 'callLocalVideo' : 'callRemoteVideo');
    if (!el) return;
    if (track.kind === 'audio' && participant?.isLocal) return; // never play back our own mic
    track.attach(el);
  }
  function detachDirectCallTrack(participant, track) {
    if (!track) return;
    const el = byId(participant?.isLocal ? 'callLocalVideo' : 'callRemoteVideo');
    if (el) { try { track.detach(el); } catch (_) {} }
  }
  async function joinDirectCallRoom() {
    const call = state.call;
    if (!call) return;
    let tokenData;
    try {
      tokenData = await api(`${API}/calls/${encodeURIComponent(call.callId)}/token`, { method: 'POST' });
    } catch (error) {
      toast('Не удалось подключиться к звонку'); hangupCall(); return;
    }
    if (!state.call || state.call.callId !== call.callId) return;
    const { Room, RoomEvent } = LivekitClient;
    const room = new Room();
    state.call.room = room;
    room.on(RoomEvent.TrackSubscribed, (track, _pub, participant) => attachDirectCallTrack(participant, track));
    room.on(RoomEvent.TrackUnsubscribed, (track, _pub, participant) => detachDirectCallTrack(participant, track));
    room.on(RoomEvent.LocalTrackPublished, publication => attachDirectCallTrack(room.localParticipant, publication.track));
    room.on(RoomEvent.LocalTrackUnpublished, publication => detachDirectCallTrack(room.localParticipant, publication.track));
    room.on(RoomEvent.Disconnected, () => { if (state.call?.room === room) endCallLocal(); });
    try {
      await room.connect(tokenData.livekit_url, tokenData.access_token);
    } catch (error) {
      toast(`Не удалось подключиться к звонку: ${error.name || error.message || 'ошибка'}`);
      hangupCall();
      return;
    }
    if (!state.call || state.call.room !== room) { try { await room.disconnect(); } catch (_) {} return; }
    state.call.status = 'active';
    state.call.answeredAt = Date.now();
    renderCallOverlay();
    state.call.durationTimer = window.setInterval(() => {
      const bar = byId('callDuration');
      if (bar && state.call?.answeredAt) bar.textContent = callDurationText(Math.floor((Date.now() - state.call.answeredAt) / 1000));
    }, 1000);
    // Mic/camera are best-effort: a device grab failure shouldn't tear down a
    // call whose room connection already succeeded — degrade to one-sided.
    try {
      await room.localParticipant.setMicrophoneEnabled(true);
      if (state.call) state.call.micOn = true;
    } catch (error) {
      if (state.call) state.call.micOn = false;
      toast('Микрофон недоступен — вы в звонке без звука с вашей стороны');
    }
    if (call.media.video) {
      try {
        await room.localParticipant.setCameraEnabled(true);
        if (state.call) state.call.camOn = true;
      } catch (error) {
        if (state.call) state.call.camOn = false;
        toast('Камера недоступна (возможно, занята другой вкладкой/приложением) — вы в звонке без видео с вашей стороны');
      }
    }
    await applyPreferredCallDevices(room);
    renderCallControls();
  }
  async function handleCallEvent(event) {
    const payload = event.payload || {};
    if (payload.type === 'invite') {
      handleIncomingInvite(payload);
      return;
    }
    if (!state.call || String(state.call.callId) !== String(payload.call_id || event.call_id)) return;
    if (payload.type === 'busy') {
      toast('Занято'); endCallLocal('busy'); return;
    }
    if (payload.type === 'accept-rejected') {
      toast('Ответили с другого устройства'); endCallLocal('elsewhere'); return;
    }
    if (payload.type === 'accepted') {
      if (state.call.role === 'callee' && state.call.status !== 'accepting') {
        toast('Вы ответили с другого устройства'); endCallLocal('elsewhere'); return;
      }
      stopRingtone();
      window.clearTimeout(state.call.ringTimer);
      try { await joinDirectCallRoom(); } catch (error) { toast(error.message || 'Не удалось подключиться к звонку'); hangupCall(); }
      return;
    }
    if (['declined', 'cancelled', 'missed', 'ended', 'error'].includes(payload.type)) {
      const labels = {
        declined: 'Звонок отклонён', cancelled: 'Звонок отменён', missed: 'Пропущенный звонок',
        ended: 'Звонок завершён', error: 'Не удалось совершить звонок',
      };
      toast(labels[payload.type] || 'Звонок завершён');
      endCallLocal(payload.type);
    }
  }
  function toggleMic() {
    const local = state.call?.room?.localParticipant;
    if (!local) return;
    const next = !state.call.micOn;
    local.setMicrophoneEnabled(next).then(() => {
      if (state.call) { state.call.micOn = next; renderCallControls(); }
    }).catch(() => toast('Не удалось переключить микрофон'));
  }
  function toggleCamera() {
    const local = state.call?.room?.localParticipant;
    if (!local) return;
    const next = !state.call.camOn;
    local.setCameraEnabled(next).then(() => {
      if (state.call) { state.call.camOn = next; renderCallControls(); }
    }).catch(() => toast('Не удалось переключить камеру'));
  }
  async function toggleScreenShare() {
    const local = state.call?.room?.localParticipant;
    if (!local) return;
    const next = !state.call.screenSharing;
    try {
      await local.setScreenShareEnabled(next);
      state.call.screenSharing = next;
    } catch (error) {
      toast('Не удалось начать показ экрана');
    }
    renderCallControls();
  }
  function endCallLocal() {
    if (!state.call) return;
    window.clearTimeout(state.call.ringTimer);
    window.clearInterval(state.call.durationTimer);
    stopRingtone();
    const room = state.call.room;
    state.call = null;
    closeCallOverlay();
    if (room) { room.disconnect().catch(() => {}); }
  }
  function closeCallOverlay() {
    const layer = byId('callLayer');
    if (!layer) return;
    layer.classList.remove('show');
    clear(layer);
  }
  function renderCallControls() {
    const controls = byId('callActiveControls');
    const call = state.call;
    if (!controls || !call) return;
    clear(controls);
    const micBtn = iconButton(`icon-btn call-toggle${call.micOn ? '' : ' off'}`, 'mic', call.micOn ? 'Выключить микрофон' : 'Включить микрофон');
    micBtn.addEventListener('click', toggleMic);
    const camBtn = iconButton(`icon-btn call-toggle${call.camOn ? '' : ' off'}`, 'camera', call.camOn ? 'Выключить камеру' : 'Включить камеру');
    camBtn.addEventListener('click', toggleCamera);
    const screenBtn = iconButton(`icon-btn call-toggle${call.screenSharing ? ' active' : ''}`, 'image', call.screenSharing ? 'Остановить показ экрана' : 'Показать экран');
    screenBtn.addEventListener('click', toggleScreenShare);
    const hangupBtn = iconButton('icon-btn call-hangup', 'phone', 'Завершить звонок');
    hangupBtn.addEventListener('click', hangupCall);
    controls.append(micBtn, camBtn, screenBtn, hangupBtn);
  }
  function renderCallOverlay() {
    const layer = byId('callLayer');
    if (!layer || !state.call) { closeCallOverlay(); return; }
    const call = state.call;
    if (call.status !== 'active') {
      clear(layer);
      layer.classList.add('show');
      renderRingingCard(layer, call);
      return;
    }
    layer.classList.add('show');
    if (!byId('callStage')) {
      // Built once per call: LiveKit attaches tracks to these specific
      // elements, so recreating them on every render (e.g. a mic toggle)
      // would strand already-subscribed tracks on now-detached nodes.
      clear(layer);
      const stage = node('div', 'call-stage');
      stage.id = 'callStage';
      const remoteVideo = document.createElement('video');
      remoteVideo.id = 'callRemoteVideo';
      remoteVideo.autoplay = true; remoteVideo.playsInline = true;
      const localVideo = document.createElement('video');
      localVideo.id = 'callLocalVideo';
      localVideo.autoplay = true; localVideo.playsInline = true; localVideo.muted = true;
      stage.append(remoteVideo, localVideo);
      const top = node('div', 'call-top-bar');
      const durationEl = node('div', 'call-duration', call.answeredAt ? callDurationText(Math.floor((Date.now() - call.answeredAt) / 1000)) : '00:00');
      durationEl.id = 'callDuration';
      top.append(node('div', 'call-peer-name', call.peerDisplayName || call.peerUsername), durationEl);
      const controls = node('div', 'call-controls');
      controls.id = 'callActiveControls';
      layer.append(stage, top, controls);
    }
    renderCallControls();
  }
  function renderRingingCard(layer, call) {
    const card = node('div', 'call-card');
    card.appendChild(avatar(call.peerDisplayName || call.peerUsername, 84, { username: call.peerUsername }));
    card.appendChild(node('div', 'call-peer-name', call.peerDisplayName || call.peerUsername));
    card.appendChild(node('div', 'call-status-line', callStatusLabel(call)));
    const controls = node('div', 'call-controls');
    if (call.status === 'ringing' && call.role === 'callee') {
      const declineBtn = iconButton('icon-btn call-hangup', 'phone', 'Отклонить');
      declineBtn.addEventListener('click', declineCall);
      const acceptBtn = iconButton('icon-btn call-accept', 'phone', 'Принять');
      acceptBtn.addEventListener('click', acceptCall);
      controls.append(declineBtn, acceptBtn);
    } else {
      const cancelBtn = iconButton('icon-btn call-hangup', 'phone', 'Отменить');
      cancelBtn.addEventListener('click', hangupCall);
      controls.append(cancelBtn);
    }
    card.appendChild(controls);
    layer.appendChild(card);
  }

  // ---- Group calls (LiveKit) ----
  async function loadActiveCallSession(chatId) {
    try {
      const data = await api(`${API}/chats/${encodeURIComponent(chatId)}/calls/active`);
      state.activeCallSessions.set(String(chatId), data || null);
    } catch (error) {
      console.warn('Active call lookup failed', error);
    }
    if (chatIdEq(state.activeChatId, chatId)) renderCallSessionBar();
  }
  function renderCallSessionBar() {
    const bar = byId('callSessionBar');
    if (!bar) return;
    const chat = activeChat();
    const session = chat && chat.kind === 'group' ? state.activeCallSessions.get(String(chat.id)) : null;
    if (!session) { bar.hidden = true; clear(bar); return; }
    clear(bar);
    bar.hidden = false;
    const count = session.participants.length;
    bar.appendChild(node('div', 'call-session-bar-text', `Звонок идёт — ${count} участник${count === 1 ? '' : count < 5 ? 'а' : 'ов'}`));
    const inThisCall = state.groupCall?.sessionId === session.session_id;
    const joinBtn = button('btn primary call-session-bar-join', inThisCall ? 'Вы в звонке' : 'Присоединиться');
    if (inThisCall) joinBtn.disabled = true;
    else joinBtn.addEventListener('click', () => groupCallFromChat(chat));
    bar.appendChild(joinBtn);
  }
  function handleCallSessionEvent(event) {
    const chatIdStr = String(event.chat_id);
    if (event.action === 'ended') {
      state.activeCallSessions.set(chatIdStr, null);
    } else {
      state.activeCallSessions.set(chatIdStr, {
        session_id: event.session_id,
        participants: event.participants || [],
      });
    }
    if (chatIdEq(state.activeChatId, event.chat_id)) renderCallSessionBar();
    if (event.action === 'started' && event.by !== state.me?.username) {
      const chat = state.chats.find(item => chatIdEq(item.id, event.chat_id));
      toast(`Начался звонок${chat ? ` в «${chatTitle(chat)}»` : ''}`);
    }
  }
  async function groupCallFromChat(chat) {
    if (!chat || chat.kind !== 'group') return;
    if (state.groupCall) { toast('Групповой звонок уже идёт'); return; }
    if (!window.LivekitClient) { toast('Групповые звонки не поддерживаются в этом браузере'); return; }
    try {
      const session = await api(`${API}/chats/${encodeURIComponent(chat.id)}/calls`, { method: 'POST' });
      await enterGroupCallRoom(session, chatTitle(chat));
    } catch (error) {
      toast(error.message);
    }
  }
  function groupCallTileId(identity) {
    return `call-tile-${String(identity).replace(/[^a-zA-Z0-9_-]/g, '_')}`;
  }
  function ensureGroupCallTile(grid, participant) {
    let tile = document.getElementById(groupCallTileId(participant.identity));
    if (!tile) {
      tile = node('div', 'call-tile');
      tile.id = groupCallTileId(participant.identity);
      tile.appendChild(node('div', 'call-tile-name', participant.name || participant.identity));
      grid.appendChild(tile);
    }
    return tile;
  }
  function removeGroupCallTileEl(identity) {
    const tile = document.getElementById(groupCallTileId(identity));
    if (tile) tile.remove();
  }
  function attachGroupCallTrack(participant, track) {
    const grid = byId('callGroupGrid');
    if (!grid || !track) return;
    const tile = ensureGroupCallTile(grid, participant);
    const el = track.attach();
    if (track.kind === 'video') {
      tile.querySelectorAll('video').forEach(existing => existing.remove());
      el.className = 'call-tile-video';
      tile.insertBefore(el, tile.firstChild);
    } else {
      el.className = 'call-tile-audio';
      tile.appendChild(el);
    }
  }
  function detachGroupCallTrack(track) {
    if (!track) return;
    track.detach().forEach(el => el.remove());
  }
  function groupCallParticipants() {
    const room = state.groupCall?.room;
    if (!room) return [];
    return [room.localParticipant, ...room.remoteParticipants.values()];
  }
  function renderGroupCallShell() {
    const layer = byId('callLayer');
    if (!layer || !state.groupCall) return;
    clear(layer);
    layer.classList.add('show');
    const top = node('div', 'call-top-bar');
    top.appendChild(node('div', 'call-peer-name', state.groupCall.chatTitleText || 'Групповой звонок'));
    layer.appendChild(top);
    const grid = node('div', 'call-grid');
    grid.id = 'callGroupGrid';
    layer.appendChild(grid);
    groupCallParticipants().forEach(participant => ensureGroupCallTile(grid, participant));
    const controls = node('div', 'call-controls');
    controls.id = 'callGroupControls';
    layer.appendChild(controls);
    renderGroupCallControls();
  }
  function renderGroupCallControls() {
    const controls = byId('callGroupControls');
    const call = state.groupCall;
    if (!controls || !call) return;
    clear(controls);
    const local = call.room.localParticipant;
    const micOn = Boolean(local?.isMicrophoneEnabled);
    const camOn = Boolean(local?.isCameraEnabled);
    const micBtn = iconButton(`icon-btn call-toggle${micOn ? '' : ' off'}`, 'mic', micOn ? 'Выключить микрофон' : 'Включить микрофон');
    micBtn.addEventListener('click', toggleGroupCallMic);
    const camBtn = iconButton(`icon-btn call-toggle${camOn ? '' : ' off'}`, 'camera', camOn ? 'Выключить камеру' : 'Включить камеру');
    camBtn.addEventListener('click', toggleGroupCallCamera);
    const screenBtn = iconButton(`icon-btn call-toggle${call.screenSharing ? ' active' : ''}`, 'image', call.screenSharing ? 'Остановить показ экрана' : 'Показать экран');
    screenBtn.addEventListener('click', toggleGroupCallScreenShare);
    const leaveBtn = iconButton('icon-btn call-hangup', 'phone', 'Покинуть звонок');
    leaveBtn.addEventListener('click', leaveGroupCall);
    controls.append(micBtn, camBtn, screenBtn, leaveBtn);
  }
  async function enterGroupCallRoom(session, chatTitleText) {
    const { Room, RoomEvent } = LivekitClient;
    const room = new Room();
    state.groupCall = {
      sessionId: session.session_id,
      chatId: session.chat_id,
      chatTitleText,
      room,
      screenSharing: false,
    };
    room.on(RoomEvent.ParticipantConnected, participant => {
      const grid = byId('callGroupGrid');
      if (grid) ensureGroupCallTile(grid, participant);
    });
    room.on(RoomEvent.ParticipantDisconnected, participant => removeGroupCallTileEl(participant.identity));
    room.on(RoomEvent.TrackSubscribed, (track, _publication, participant) => attachGroupCallTrack(participant, track));
    room.on(RoomEvent.TrackUnsubscribed, track => detachGroupCallTrack(track));
    room.on(RoomEvent.LocalTrackPublished, publication => attachGroupCallTrack(room.localParticipant, publication.track));
    room.on(RoomEvent.LocalTrackUnpublished, publication => detachGroupCallTrack(publication.track));
    room.on(RoomEvent.Disconnected, () => { if (state.groupCall?.room === room) endGroupCallLocal(); });
    renderGroupCallShell();
    try {
      await room.connect(session.livekit_url, session.access_token);
    } catch (error) {
      toast(`Не удалось подключиться к звонку: ${error.name || error.message || 'ошибка'}`);
      await abandonGroupCallJoin(session, room);
      return;
    }
    // Mic/camera are best-effort and independent: on a shared machine the
    // camera may already be held by another tab/participant — that should
    // degrade to audio-only, not abort a call the room connection already
    // succeeded on.
    try {
      await room.localParticipant.setMicrophoneEnabled(true);
    } catch (error) {
      toast('Микрофон недоступен — вы в звонке без звука с вашей стороны');
    }
    try {
      await room.localParticipant.setCameraEnabled(true);
    } catch (error) {
      toast('Камера недоступна (возможно, занята другой вкладкой/приложением) — вы в звонке без видео с вашей стороны');
    }
    await applyPreferredCallDevices(room);
    renderGroupCallControls();
  }
  async function abandonGroupCallJoin(session, room) {
    try { await room.disconnect(); } catch (_) { /* already gone */ }
    try {
      await api(`${API}/chats/${encodeURIComponent(session.chat_id)}/calls/${encodeURIComponent(session.session_id)}/leave`, { method: 'POST' });
    } catch (_) { /* best-effort — reconciliation sweep covers this otherwise */ }
    if (state.groupCall?.room === room) endGroupCallLocal();
  }
  function toggleGroupCallMic() {
    const local = state.groupCall?.room?.localParticipant;
    if (!local) return;
    local.setMicrophoneEnabled(!local.isMicrophoneEnabled).then(renderGroupCallControls);
  }
  function toggleGroupCallCamera() {
    const local = state.groupCall?.room?.localParticipant;
    if (!local) return;
    local.setCameraEnabled(!local.isCameraEnabled).then(renderGroupCallControls);
  }
  async function toggleGroupCallScreenShare() {
    const call = state.groupCall;
    if (!call?.room?.localParticipant) return;
    const next = !call.screenSharing;
    try {
      await call.room.localParticipant.setScreenShareEnabled(next);
      call.screenSharing = next;
    } catch (error) {
      toast('Не удалось начать показ экрана');
    }
    renderGroupCallControls();
  }
  async function leaveGroupCall() {
    const call = state.groupCall;
    if (!call) return;
    try { await call.room.disconnect(); } catch (_) { /* already gone */ }
    try {
      await api(`${API}/chats/${encodeURIComponent(call.chatId)}/calls/${encodeURIComponent(call.sessionId)}/leave`, { method: 'POST' });
    } catch (_) { /* best-effort — webhook/sweep reconciles if this fails */ }
    endGroupCallLocal();
  }
  function endGroupCallLocal() {
    state.groupCall = null;
    closeCallOverlay();
    renderCallSessionBar();
  }

  async function connectSocket() {
    window.clearTimeout(state.reconnectTimer);
    try { await ensureToken(); } catch (error) { setStatus('Messenger недоступен'); toast(error.message); return; }
    if (state.socket) state.socket.close();
    state.socketReady = false; setStatus('Подключение…');
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(`${protocol}//${window.location.host}${API}/ws`);
    state.socket = socket;
    socket.addEventListener('open', () => socketSend({ type: 'auth', token: state.token }));
    socket.addEventListener('message', message => {
      try {
        handleSocketEvent(JSON.parse(message.data));
      } catch (error) {
        console.error('Messenger WebSocket event failed', error);
      }
    });
    socket.addEventListener('close', event => {
      if (state.socket !== socket) return;
      state.socketReady = false;
      state.myStatus = { status: 'offline', status_text: null, status_until: null, manual_status: null };
      if (state.call) endCallLocal('disconnected');
      state.subscribedChats.clear();
      window.clearInterval(state.heartbeat); setStatus('Соединение потеряно');
      renderHeader();
      renderChats();
      if (event.code === 1008) state.token = '';
      const delay = Math.min(30000, 1000 * (2 ** state.reconnectAttempt));
      state.reconnectAttempt += 1;
      state.reconnectTimer = window.setTimeout(connectSocket, delay);
    });
  }

  function listWidthBounds() {
    const min = 280;
    const max = Math.floor(window.innerWidth * 0.65);
    return { min, max: Math.max(min, max) };
  }
  function clampListWidth(width) {
    const { min, max } = listWidthBounds();
    return Math.min(max, Math.max(min, Math.round(width)));
  }
  function applyListWidth(width, persist = false) {
    const next = clampListWidth(width);
    document.documentElement.style.setProperty('--list-w', `${next}px`);
    if (persist) localStorage.setItem('messenger-list-w', String(next));
    return next;
  }
  function resetListScroll() {
    const list = byId('chatList');
    if (list) list.scrollTop = 0;
  }
  function initListWidth() {
    const saved = Number(localStorage.getItem('messenger-list-w'));
    const fallback = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--list-w')) || 340;
    applyListWidth(Number.isFinite(saved) && saved > 0 ? saved : fallback, true);
    resetListScroll();
  }
  function bindListResize() {
    const resizer = byId('listResizer');
    if (!resizer || window.matchMedia('(max-width: 760px)').matches) return;

    let dragging = false;
    const rail = () => document.querySelector('.rail')?.getBoundingClientRect().width || 0;

    const finish = () => {
      if (!dragging) return;
      dragging = false;
      resizer.classList.remove('dragging');
      document.body.classList.remove('list-resizing');
      const current = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--list-w'));
      if (Number.isFinite(current)) applyListWidth(current, true);
    };

    resizer.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      dragging = true;
      resizer.classList.add('dragging');
      document.body.classList.add('list-resizing');
      resizer.setPointerCapture(event.pointerId);
      event.preventDefault();
    });
    resizer.addEventListener('pointermove', event => {
      if (!dragging) return;
      applyListWidth(event.clientX - rail());
    });
    resizer.addEventListener('pointerup', finish);
    resizer.addEventListener('pointercancel', finish);
    window.addEventListener('resize', () => {
      const current = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--list-w'));
      if (Number.isFinite(current)) applyListWidth(current, true);
    });
  }

  function bindZoomLock() {
    document.addEventListener('wheel', event => {
      if (event.ctrlKey) event.preventDefault();
    }, { passive: false });
    ['gesturestart', 'gesturechange', 'gestureend'].forEach(type => {
      document.addEventListener(type, event => event.preventDefault(), { passive: false });
    });
  }

  function bindUi() {
    byId('searchInput').addEventListener('input', event => {
      state.search = event.target.value;
      renderChats();
      scheduleDirectorySearch(state.search);
    });
    all('.rail-filter').forEach(control => control.addEventListener('click', () => {
      setRailFilter(control.dataset.filter);
      if (state.filter === 'channel') later('Каналы');
      renderChats();
    }));
    byId('settingsButton').addEventListener('click', openSettings);
    byId('mobileMenuButton').addEventListener('click', openSettings);
    byId('backButton').addEventListener('click', () => document.body.classList.remove('in-chat'));
    byId('chatInfoButton').addEventListener('click', () => toggleInfo(true));
    byId('infoCloseButton').addEventListener('click', () => toggleInfo(false));
    byId('callButton').addEventListener('click', () => {
      const chat = activeChat();
      if (!chat) return;
      if (chat.kind === 'group') groupCallFromChat(chat);
      else callFromChat(chat);
    });
    byId('messageSearchButton').addEventListener('click', openMessageSearch);
    byId('chatMenuButton').addEventListener('click', event => openChatMenu(event.currentTarget));
    byId('sendButton').addEventListener('click', sendCurrentMessage);
    byId('contextCloseButton').addEventListener('click', closeComposeContext);
    byId('composerInput').addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); sendCurrentMessage(); }
    });
    byId('composerInput').addEventListener('input', () => {
      syncComposerStickerSizes();
      syncComposerSelectionHighlight();
      updateSendBtn();
      sendTyping(true);
    });
    byId('composerInput').addEventListener('paste', event => {
      event.preventDefault();
      insertAtCaret(event.currentTarget, event.clipboardData.getData('text/plain'));
      syncComposerStickerSizes();
      syncComposerSelectionHighlight();
      updateSendBtn();
    });
    byId('composerInput').addEventListener('copy', event => {
      const payload = serializeComposerSelection();
      if (payload == null) return;
      event.preventDefault();
      event.clipboardData.setData('text/plain', payload);
    });
    byId('composerInput').addEventListener('cut', event => {
      const payload = serializeComposerSelection();
      if (payload == null) return;
      event.preventDefault();
      event.clipboardData.setData('text/plain', payload);
      deleteComposerSelection();
    });
    document.addEventListener('selectionchange', () => {
      if (!byId('composerInput')?.contains(document.activeElement)
        && document.activeElement !== byId('composerInput')) {
        // Still update when selection is inside composer even if focus quirks.
      }
      syncComposerSelectionHighlight();
    });
    byId('emojiButton').addEventListener('click', event => {
      event.stopPropagation();
      byId('attachMenu').classList.remove('show');
      if (byId('emojiPanel').classList.contains('show')) hideEmojiPanel();
      else showEmojiPanel();
    });
    byId('attachButton').addEventListener('click', event => {
      event.stopPropagation();
      hideEmojiPanel();
      byId('attachMenu').classList.toggle('show');
    });
    byId('fileInput').addEventListener('change', event => { uploadFiles(Array.from(event.target.files)); event.target.value = ''; });
    byId('mediaInput').addEventListener('change', event => { uploadFiles(Array.from(event.target.files)); event.target.value = ''; });
    byId('scrollDown').addEventListener('click', () => byId('messages').scrollTo({ top: byId('messages').scrollHeight, behavior: 'smooth' }));
    byId('messages').addEventListener('scroll', event => {
      const target = event.currentTarget;
      byId('scrollDown').classList.toggle('show', target.scrollHeight - target.scrollTop - target.clientHeight > CHAT_BOTTOM_THRESHOLD);
    });
    byId('popupLayer').addEventListener('click', event => { if (event.target === byId('popupLayer')) closePopups(); });
    byId('modalLayer').addEventListener('click', event => { if (event.target === byId('modalLayer')) closeModal(); });
    document.addEventListener('click', event => {
      if (!event.target.closest('#attachMenu, #attachButton')) byId('attachMenu').classList.remove('show');
    });
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape') return;
      closePopups();
      if (byId('modalLayer').classList.contains('show')) closeModal();
      else if (byId('emojiPanel').classList.contains('show')) hideEmojiPanel();
      else if (state.infoOpen) toggleInfo(false);
      else if (byId('chatHeader').classList.contains('selecting')) exitSelection();
    });
    bindListResize();
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') scheduleLiveSync();
    });
    window.addEventListener('focus', scheduleLiveSync);
    window.addEventListener('pagehide', () => {
      const call = state.groupCall;
      if (!call) return;
      // Best-effort only: keepalive lets this survive page teardown, but it's
      // not guaranteed (crash/power-loss won't fire it at all) — the server
      // side reconciliation loop is the actual correctness backstop.
      try {
        fetch(`${API}/chats/${encodeURIComponent(call.chatId)}/calls/${encodeURIComponent(call.sessionId)}/leave`, {
          method: 'POST',
          credentials: 'same-origin',
          keepalive: true,
          headers: { Authorization: `Bearer ${state.token}` },
        });
      } catch (_) { /* best-effort */ }
    });
  }
  async function init() {
    document.documentElement.dataset.theme = localStorage.getItem('messenger-theme') || 'light';
    initAccent();
    initMsgFontSize();
    initZoom();
    stickerPerfTier();
    initListWidth();
    mountStaticIcons();
    await loadStickerPack();
    buildAttachMenu(); bindZoomLock(); bindUi(); updateSendBtn();
    const refreshStickers = () => {
      if (state.activeChatId) renderMessages(true);
    };
    window.addEventListener('messenger-tgs-ready', refreshStickers);
    window.addEventListener('messenger-stickers-ready', refreshStickers);
    try {
      await loadIdentity(); await refreshToken();
      // Needs a valid Bearer token when a custom photo is selected (fetches
      // it as an authenticated blob) — must run after refreshToken(), not
      // before, or the token/CSRF plumbing isn't ready yet.
      applyWallpaper();
      await loadChats(); await connectSocket();
    } catch (error) { setStatus('Messenger недоступен'); toast(error.message); }
  }
  document.addEventListener('DOMContentLoaded', init);

  // Minimal, deliberate surface for static/messenger/js/settings-extra.js
  // (the notifications/sound-camera/advanced/language screens) — keeps that
  // file from having to reimplement modals, icons, or the api() helper.
  window.MessengerCore = {
    node, button, iconButton, iconEl, all, byId, clear,
    modalShell, closeModal, confirmModal, toast, later,
    api, state, chatTitle, unreadMessageTotal, openSettings,
  };
})();
