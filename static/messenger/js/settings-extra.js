/* Notifications / Sound & Camera / Advanced / Language settings screens.
   Kept out of client.js on purpose — see window.MessengerCore there for the
   small shared surface (DOM helpers, modals, state, api) this file uses. */
(function () {
  const Core = window.MessengerCore;
  if (!Core) return;
  const { node, button, byId, clear, modalShell, toast, state, chatTitle, openSettings } = Core;

  function readPrefs(key, defaults) {
    try { return { ...defaults, ...JSON.parse(localStorage.getItem(key) || '{}') }; }
    catch (_) { return { ...defaults }; }
  }
  function writePrefs(key, value) { localStorage.setItem(key, JSON.stringify(value)); }

  function block(title) {
    const wrap = node('div', 'sx-block');
    if (title) wrap.appendChild(node('div', 'set-group-label', title));
    return wrap;
  }
  function toggleRow(label, sub, checked, onChange) {
    const row = node('div', 'sx-row sx-row-click');
    const info = node('div', 'sx-row-body');
    info.appendChild(node('div', 'sx-row-label', label));
    if (sub) info.appendChild(node('div', 'sx-row-sub', sub));
    const sw = node('div', `switch${checked ? ' on' : ''}`);
    row.append(info, sw);
    row.addEventListener('click', () => {
      const next = !sw.classList.contains('on');
      sw.classList.toggle('on', next);
      onChange(next);
    });
    return row;
  }
  function watchModalClosed(modal, onClosed) {
    const layer = byId('modalLayer');
    const observer = new MutationObserver(() => {
      if (!layer.contains(modal)) { onClosed(); observer.disconnect(); }
    });
    observer.observe(layer, { childList: true });
  }

  // ---------------------------------------------------------------------
  // Notifications & sounds — real: desktop Notification API, a synthesized
  // sound (no asset needed), tab-title flashing, and per-chat-kind /
  // per-event gating that actually filters what client.js dispatches here.
  // ---------------------------------------------------------------------
  const NOTIF_KEY = 'messenger-notif-prefs';
  const NOTIF_DEFAULTS = {
    desktop: false, sound: true, titleFlash: true,
    direct: true, group: true, reactions: true, pinned: true, callsOnDevice: true,
  };
  const notifPrefs = () => readPrefs(NOTIF_KEY, NOTIF_DEFAULTS);
  const setNotifPrefs = patch => { const p = { ...notifPrefs(), ...patch }; writePrefs(NOTIF_KEY, p); return p; };

  let audioCtx = null;
  function playNotifySound() {
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      if (audioCtx.state === 'suspended') audioCtx.resume();
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(880, audioCtx.currentTime);
      gain.gain.setValueAtTime(0.001, audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.16, audioCtx.currentTime + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.28);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start();
      osc.stop(audioCtx.currentTime + 0.3);
    } catch (_) { /* autoplay/audio blocked — silent no-op */ }
  }

  let flashTimer = null;
  function flashTitle(text) {
    if (flashTimer) return;
    let on = false;
    flashTimer = window.setInterval(() => {
      document.title = on ? lastTitle : text;
      on = !on;
    }, 1000);
    window.addEventListener('focus', function stop() {
      window.clearInterval(flashTimer);
      flashTimer = null;
      document.title = lastTitle;
      window.removeEventListener('focus', stop);
    }, { once: true });
  }

  function showDesktopNotification(title, bodyText) {
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    try { new Notification(title, { body: bodyText, tag: 'messenger-belnipi' }); }
    catch (_) { /* some platforms throw for unsupported combos — non-fatal */ }
  }
  function notifyEvent(titleText, bodyText) {
    const prefs = notifPrefs();
    if (prefs.sound) playNotifySound();
    if (document.hasFocus() && !document.hidden) return; // already looking at it
    if (prefs.desktop) showDesktopNotification(titleText, bodyText);
    if (prefs.titleFlash) flashTitle(`💬 ${titleText}`);
  }

  function openNotifications() {
    const { body, modal } = modalShell(t('notif_title'), false, openSettings);
    modal.classList.add('settings');
    const prefs = notifPrefs();

    const general = block(t('notif_general'));
    general.appendChild(toggleRow(t('notif_desktop'), t('notif_desktop_sub'), prefs.desktop, async on => {
      if (on && 'Notification' in window && Notification.permission === 'default') {
        const perm = await Notification.requestPermission();
        if (perm !== 'granted') { setNotifPrefs({ desktop: false }); openNotifications(); return; }
      }
      setNotifPrefs({ desktop: on });
    }));
    general.appendChild(toggleRow(t('notif_sound'), null, prefs.sound, on => setNotifPrefs({ sound: on })));
    general.appendChild(toggleRow(t('notif_flash'), t('notif_flash_sub'), prefs.titleFlash, on => setNotifPrefs({ titleFlash: on })));
    body.appendChild(general);

    const chats = block(t('notif_chats_cap'));
    chats.appendChild(toggleRow(t('notif_direct'), null, prefs.direct, on => setNotifPrefs({ direct: on })));
    chats.appendChild(toggleRow(t('notif_group'), null, prefs.group, on => setNotifPrefs({ group: on })));
    chats.appendChild(toggleRow(t('notif_reactions'), null, prefs.reactions, on => setNotifPrefs({ reactions: on })));
    chats.appendChild(toggleRow(t('notif_pinned'), null, prefs.pinned, on => setNotifPrefs({ pinned: on })));
    body.appendChild(chats);

    const calls = block(t('notif_calls_cap'));
    calls.appendChild(toggleRow(
      t('notif_calls_on_device'), t('notif_calls_on_device_sub'),
      prefs.callsOnDevice, on => setNotifPrefs({ callsOnDevice: on }),
    ));
    body.appendChild(calls);
  }

  // ---------------------------------------------------------------------
  // Sound & camera — real device enumeration, a live mic level meter and
  // camera preview via getUserMedia, applied to actual calls afterwards.
  // ---------------------------------------------------------------------
  const DEVICE_KEY = 'messenger-device-prefs';
  const DEVICE_DEFAULTS = { mic: '', speaker: '', camera: '', useForCalls: true };
  const devicePrefs = () => readPrefs(DEVICE_KEY, DEVICE_DEFAULTS);
  const setDevicePrefs = patch => { const p = { ...devicePrefs(), ...patch }; writePrefs(DEVICE_KEY, p); return p; };

  async function openSoundCamera() {
    const { body, modal } = modalShell(t('sc_title'), false, openSettings);
    modal.classList.add('settings');
    const prefs = devicePrefs();
    let micStream = null, camStream = null, meterCtx = null, meterRAF = null;

    function stopMedia() {
      if (meterRAF) cancelAnimationFrame(meterRAF);
      meterRAF = null;
      if (micStream) micStream.getTracks().forEach(t => t.stop());
      if (camStream) camStream.getTracks().forEach(t => t.stop());
      if (meterCtx) meterCtx.close().catch(() => {});
      micStream = camStream = meterCtx = null;
    }
    watchModalClosed(modal, stopMedia);

    async function deviceOptions(kind) {
      try { return (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === kind); }
      catch (_) { return []; }
    }
    function buildSelect(options, selectedId, onChange, emptyLabel) {
      const select = document.createElement('select');
      select.className = 'sx-select';
      if (!options.length) {
        const opt = document.createElement('option');
        opt.textContent = emptyLabel;
        select.appendChild(opt);
        select.disabled = true;
        return select;
      }
      const def = document.createElement('option');
      def.value = ''; def.textContent = t('sc_default');
      select.appendChild(def);
      options.forEach((d, i) => {
        const opt = document.createElement('option');
        opt.value = d.deviceId;
        opt.textContent = d.label || t('sc_device_n', { n: i + 1 });
        select.appendChild(opt);
      });
      select.value = selectedId || '';
      select.addEventListener('change', () => onChange(select.value));
      return select;
    }

    // Grabbing a stream first is what unlocks real device *labels* in most
    // browsers — enumerateDevices() returns blank labels without it.
    try { micStream = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch (_) { /* no mic / denied */ }

    const speakerBlock = block(t('sc_speakers_cap'));
    const speakerRow = node('div', 'sx-row');
    speakerRow.append(
      node('div', 'sx-row-label', t('sc_playback_device')),
      buildSelect(await deviceOptions('audiooutput'), prefs.speaker, id => setDevicePrefs({ speaker: id }), t('sc_no_output')),
    );
    speakerBlock.appendChild(speakerRow);
    body.appendChild(speakerBlock);

    const micBlock = block(t('sc_mic_cap'));
    const micRow = node('div', 'sx-row');
    const bars = [];
    function startMeter(stream) {
      if (meterRAF) cancelAnimationFrame(meterRAF);
      meterRAF = null;
      if (!stream) return;
      meterCtx = new (window.AudioContext || window.webkitAudioContext)();
      const analyser = meterCtx.createAnalyser();
      analyser.fftSize = 256;
      meterCtx.createMediaStreamSource(stream).connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);
      const tick = () => {
        analyser.getByteFrequencyData(data);
        const level = data.reduce((sum, v) => sum + v, 0) / data.length / 255;
        const active = Math.round(level * bars.length * 2.2);
        bars.forEach((bar, i) => bar.classList.toggle('on', i < active));
        meterRAF = requestAnimationFrame(tick);
      };
      tick();
    }
    async function restartMic(deviceId) {
      if (micStream) micStream.getTracks().forEach(t => t.stop());
      if (meterCtx) { meterCtx.close().catch(() => {}); meterCtx = null; }
      try {
        micStream = await navigator.mediaDevices.getUserMedia({ audio: deviceId ? { deviceId: { exact: deviceId } } : true });
        startMeter(micStream);
      } catch (_) { micStream = null; }
    }
    micRow.append(
      node('div', 'sx-row-label', t('sc_record_device')),
      buildSelect(await deviceOptions('audioinput'), prefs.mic, id => { setDevicePrefs({ mic: id }); restartMic(id); }, t('sc_no_mic')),
    );
    micBlock.appendChild(micRow);
    const meter = node('div', 'sx-meter');
    for (let i = 0; i < 32; i += 1) { const bar = node('div', 'sx-meter-bar'); bars.push(bar); meter.appendChild(bar); }
    micBlock.appendChild(meter);
    body.appendChild(micBlock);
    startMeter(micStream);

    const callsBlock = block(t('sc_calls_cap'));
    callsBlock.appendChild(toggleRow(t('sc_use_for_calls'), null, prefs.useForCalls, on => setDevicePrefs({ useForCalls: on })));
    body.appendChild(callsBlock);

    const camBlock = block(t('sc_camera_cap'));
    const camRow = node('div', 'sx-row');
    const camVideo = document.createElement('video');
    camVideo.className = 'sx-cam-preview';
    camVideo.autoplay = true; camVideo.playsInline = true; camVideo.muted = true;
    async function startCam(deviceId) {
      if (camStream) camStream.getTracks().forEach(t => t.stop());
      try {
        camStream = await navigator.mediaDevices.getUserMedia({ video: deviceId ? { deviceId: { exact: deviceId } } : true });
        camVideo.srcObject = camStream;
      } catch (_) { camStream = null; camVideo.srcObject = null; }
    }
    camRow.append(
      node('div', 'sx-row-label', t('sc_record_device')),
      buildSelect(await deviceOptions('videoinput'), prefs.camera, id => { setDevicePrefs({ camera: id }); startCam(id); }, t('sc_no_camera')),
    );
    camBlock.appendChild(camRow);
    camBlock.appendChild(camVideo);
    body.appendChild(camBlock);
    startCam(prefs.camera);
  }

  // ---------------------------------------------------------------------
  // Advanced — media autoload (real, gates image loading in the message
  // list) and the browser tab title (real, chat name + unread count).
  // Desktop-only Telegram settings (tray icon, launch at startup, window
  // frame, save-file path…) have no web equivalent and are left out rather
  // than shipped as switches that do nothing.
  // ---------------------------------------------------------------------
  const ADV_KEY = 'messenger-advanced-prefs';
  const ADV_DEFAULTS = { autoloadDirect: true, autoloadGroup: true, showChatTitle: true, showUnreadCount: true };
  const advPrefs = () => readPrefs(ADV_KEY, ADV_DEFAULTS);
  const setAdvPrefs = patch => { const p = { ...advPrefs(), ...patch }; writePrefs(ADV_KEY, p); return p; };

  let lastTitle = document.title;
  let lastChat = null, lastUnread = 0;
  function applyWindowTitle(chat, unreadTotal) {
    lastChat = chat; lastUnread = unreadTotal || 0;
    const prefs = advPrefs();
    const parts = [];
    if (prefs.showUnreadCount && lastUnread > 0) parts.push(`(${lastUnread})`);
    if (prefs.showChatTitle && chat) parts.push(chatTitle(chat));
    parts.push(t('app_base_title'));
    lastTitle = parts.join(' · ');
    if (!flashTimer) document.title = lastTitle;
  }

  function openAdvanced() {
    const { body, modal } = modalShell(t('adv_title'), false, openSettings);
    modal.classList.add('settings');
    const prefs = advPrefs();

    const media = block(t('adv_autoload_cap'));
    media.appendChild(toggleRow(t('adv_autoload_direct'), t('adv_autoload_sub'), prefs.autoloadDirect, on => setAdvPrefs({ autoloadDirect: on })));
    media.appendChild(toggleRow(t('adv_autoload_group'), t('adv_autoload_sub'), prefs.autoloadGroup, on => setAdvPrefs({ autoloadGroup: on })));
    body.appendChild(media);

    const title = block(t('adv_title_cap'));
    title.appendChild(toggleRow(t('adv_show_chat_title'), null, prefs.showChatTitle, on => { setAdvPrefs({ showChatTitle: on }); applyWindowTitle(lastChat, lastUnread); }));
    title.appendChild(toggleRow(t('adv_show_unread'), null, prefs.showUnreadCount, on => { setAdvPrefs({ showUnreadCount: on }); applyWindowTitle(lastChat, lastUnread); }));
    body.appendChild(title);
  }

  // ---------------------------------------------------------------------
  // Language — real: switching actually re-renders the app's interface in
  // that language (see static/messenger/js/i18n.js). A handful of rarer
  // dynamic strings scattered through client.js aren't wired to it yet —
  // lang_note says so plainly rather than overclaiming.
  // ---------------------------------------------------------------------
  const LANG_KEY = 'messenger-lang-prefs';
  const LANG_DEFAULTS = { code: 'ru', translateButton: false };
  const langPrefs = () => readPrefs(LANG_KEY, LANG_DEFAULTS);
  const setLangPrefs = patch => { const p = { ...langPrefs(), ...patch }; writePrefs(LANG_KEY, p); return p; };
  const LANGUAGES = [
    ['ru', 'lang_ru', 'Russian'], ['en', 'lang_en', 'English'], ['be', 'lang_be', 'Belarusian'],
  ];

  function openLanguage() {
    const { body, modal } = modalShell(t('lang_title'), false, openSettings);
    modal.classList.add('settings');

    body.appendChild(node('div', 'sx-note', t('lang_note')));
    body.appendChild(toggleRow(
      t('lang_translate_button'), t('lang_translate_button_sub'),
      langPrefs().translateButton, on => setLangPrefs({ translateButton: on }),
    ));

    const searchWrap = node('div', 'sx-search');
    const search = document.createElement('input');
    search.type = 'text'; search.placeholder = t('lang_search_placeholder');
    searchWrap.appendChild(search);
    body.appendChild(searchWrap);

    const list = node('div', 'sx-lang-list');
    body.appendChild(list);

    function renderList() {
      clear(list);
      const query = search.value.trim().toLowerCase();
      LANGUAGES
        .filter(([, nameKey, en]) => !query || t(nameKey).toLowerCase().includes(query) || en.toLowerCase().includes(query))
        .forEach(([code, nameKey, en]) => {
          const active = langPrefs().code === code;
          const item = button(`sx-lang-item${active ? ' active' : ''}`);
          const info = node('div');
          info.append(node('div', 'sx-row-label', t(nameKey)), node('div', 'sx-row-sub', en));
          item.append(node('span', 'sx-radio'), info);
          item.addEventListener('click', () => {
            if (code === langPrefs().code) return;
            setLangPrefs({ code });
            window.MessengerI18n.setLang(code);
          });
          list.appendChild(item);
        });
    }
    search.addEventListener('input', renderList);
    renderList();
  }

  window.MessengerNotify = {
    onMessage(chat, message) {
      const prefs = notifPrefs();
      if (!(chat?.kind === 'group' ? prefs.group : prefs.direct)) return;
      if (message.sender_username === state.me?.username) return;
      const preview = (message.text || (message.attachments?.length ? t('msg_attachment') : '')).slice(0, 120);
      notifyEvent(chatTitle(chat), message.sender_display_name ? `${message.sender_display_name}: ${preview}` : preview);
    },
    onReaction(chat) {
      if (!notifPrefs().reactions) return;
      notifyEvent(chatTitle(chat), t('notif_reaction_event'));
    },
    onPinned(chat) {
      if (!notifPrefs().pinned) return;
      notifyEvent(chatTitle(chat), t('notif_pinned_event'));
    },
    shouldAcceptCallOnThisDevice: () => notifPrefs().callsOnDevice,
    shouldAutoloadMedia: chat => (chat?.kind === 'group' ? advPrefs().autoloadGroup : advPrefs().autoloadDirect),
    useDeviceInCalls: () => devicePrefs().useForCalls,
    preferredDeviceId: kind => devicePrefs()[kind] || '',
    applyWindowTitle,
    translateButtonEnabled: () => langPrefs().translateButton,
    translateMessage: () => toast(t('translate_not_configured')),
  };
  window.MessengerSettingsExtra = { openNotifications, openSoundCamera, openAdvanced, openLanguage };
})();
