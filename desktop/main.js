// UniversalAI Messenger — desktop shell.
//
// This does NOT reimplement anything: it just opens the existing
// /messenger web app (the same HTML/CSS/JS served by the app+gateway
// containers, unmodified) inside a native window. Every feature — chats,
// calls, reactions, admin panel, bots — works exactly as in a browser tab,
// because it IS the same Chromium engine rendering the same page.
//
// The one thing a browser tab genuinely cannot do is report presence while
// its window is closed. This shell adds that: closing the window hides it
// instead of quitting, so the page (and its WebSocket heartbeat) keeps
// running in the background. The web app's own idle logic (see
// static/messenger/js/client.js, currentActivity()) already turns a
// hidden/backgrounded page into "away" after a couple of minutes — nothing
// about that logic changes here, we just stop it from being destroyed on
// window-close. Quitting from the tray menu (or the OS shutting down) ends
// the process for real, which is what makes the user go fully offline.
'use strict';

const { app, BrowserWindow, Tray, Menu, session, shell, dialog, ipcMain, nativeImage } = require('electron');
const path = require('path');
const fs = require('fs');

const CONFIG_PATH = path.join(app.getPath('userData'), 'config.json');
const DEFAULT_SERVER_URL = 'https://192.0.2.8';

let mainWindow = null;
let settingsWindow = null;
let tray = null;
let isQuitting = false;
let config = loadConfig();

function loadConfig() {
  try {
    return JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
  } catch (_) {
    return {};
  }
}

function saveConfig(next) {
  config = { ...config, ...next };
  fs.mkdirSync(path.dirname(CONFIG_PATH), { recursive: true });
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2), 'utf8');
}

function serverUrl() {
  return (config.serverUrl || DEFAULT_SERVER_URL).replace(/\/+$/, '');
}

function messengerUrl() {
  return `${serverUrl()}/messenger`;
}

// ---- Self-signed TLS: trust-on-first-use per host --------------------
// There's no public CA for an internal LAN gateway, so a plain browser
// asks you to click through a warning once per profile. We do the same
// thing, just without the scary page: accept a host's certificate the
// first time, remember its fingerprint, and only ever silently reuse it
// if the fingerprint hasn't changed. A changed fingerprint later (a real
// MITM risk, or the server's cert was regenerated) prompts again instead
// of failing silently or trusting blindly forever.
function setupCertificateTrust() {
  app.on('certificate-error', (event, webContents, url, error, certificate, callback) => {
    let host;
    try {
      host = new URL(url).host;
    } catch (_) {
      callback(false);
      return;
    }
    const trusted = config.trustedCertificates || {};
    if (trusted[host] === certificate.fingerprint) {
      event.preventDefault();
      callback(true);
      return;
    }
    const known = trusted[host];
    const detail = known
      ? `Сертификат сервера "${host}" изменился с последнего раза.\n` +
        `Было: ${known}\nСтало: ${certificate.fingerprint}\n\n` +
        'Если сервер недавно не переустанавливали — это может быть подмена соединения. ' +
        'Доверять новому сертификату?'
      : `Сервер "${host}" использует самоподписанный сертификат (внутренний, без публичного CA).\n` +
        `Отпечаток: ${certificate.fingerprint}\n\n` +
        'Доверять ему и запомнить для следующих запусков?';
    const choice = dialog.showMessageBoxSync({
      type: 'warning',
      buttons: ['Доверять', 'Отмена'],
      defaultId: 0,
      cancelId: 1,
      title: 'Проверка сертификата',
      message: detail,
    });
    if (choice === 0) {
      saveConfig({ trustedCertificates: { ...trusted, [host]: certificate.fingerprint } });
      event.preventDefault();
      callback(true);
    } else {
      callback(false);
    }
  });
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 720,
    minHeight: 480,
    show: false,
    icon: path.join(__dirname, 'build', 'icon.png'),
    title: 'UniversalAI Messenger',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, 'preload.js'),
      // The whole "away" status depends on the page's 25s heartbeat timer
      // (static/messenger/js/client.js) still firing on schedule while this
      // window is hidden in the tray. Electron's default background timer
      // throttling would otherwise slow that heartbeat down once hidden,
      // making the online→away transition take far longer than the app's
      // own 2-minute threshold — sometimes many minutes, since Chromium's
      // background throttling gets more aggressive the longer a page stays
      // hidden. Keeping this off is what makes "away" actually happen on
      // time.
      backgroundThrottling: false,
    },
  });

  mainWindow.once('ready-to-show', () => mainWindow.show());

  // Anything the page opens as a new window/tab (external links) goes to
  // the OS browser instead of spawning another app window.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith(serverUrl())) return { action: 'allow' };
    shell.openExternal(url);
    return { action: 'deny' };
  });

  // Minimize-to-tray: closing the window just hides it. This is the whole
  // trick that makes "away while the computer is on but the window is
  // closed" possible — the page (and its presence heartbeat) never
  // actually stops running.
  mainWindow.on('close', event => {
    if (isQuitting) return;
    event.preventDefault();
    mainWindow.hide();
  });

  mainWindow.loadURL(messengerUrl());
  return mainWindow;
}

function showMainWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) {
    createMainWindow();
    return;
  }
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

function openSettingsWindow() {
  if (settingsWindow && !settingsWindow.isDestroyed()) {
    settingsWindow.focus();
    return;
  }
  settingsWindow = new BrowserWindow({
    width: 460,
    height: 300,
    resizable: false,
    minimizable: false,
    maximizable: false,
    title: 'Настройки — UniversalAI Messenger',
    icon: path.join(__dirname, 'build', 'icon.png'),
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, 'settings', 'preload.js'),
    },
  });
  settingsWindow.setMenuBarVisibility(false);
  settingsWindow.loadFile(path.join(__dirname, 'settings', 'index.html'));
}

function rebuildTrayMenu() {
  const loginSettings = app.getLoginItemSettings();
  const template = [
    { label: 'Открыть UniversalAI Messenger', click: showMainWindow },
    { type: 'separator' },
    {
      label: 'Запускать при входе в систему',
      type: 'checkbox',
      checked: loginSettings.openAtLogin,
      click: item => app.setLoginItemSettings({ openAtLogin: item.checked, openAsHidden: true }),
    },
    { label: 'Настройки сервера…', click: openSettingsWindow },
    { type: 'separator' },
    {
      label: 'Выход',
      click: () => {
        isQuitting = true;
        app.quit();
      },
    },
  ];
  tray.setContextMenu(Menu.buildFromTemplate(template));
}

function createTray() {
  const icon = nativeImage.createFromPath(path.join(__dirname, 'build', 'tray-icon.png'));
  tray = new Tray(icon.resize({ width: 22, height: 22 }));
  tray.setToolTip('UniversalAI Messenger');
  tray.on('click', showMainWindow);
  rebuildTrayMenu();
}

// ---- IPC from the settings window -------------------------------------
ipcMain.handle('settings:get', () => ({ serverUrl: serverUrl() }));
ipcMain.handle('settings:save', (_event, next) => {
  const url = String(next?.serverUrl || '').trim().replace(/\/+$/, '');
  if (!/^https?:\/\//i.test(url)) {
    throw new Error('Укажите адрес сервера целиком, включая https://');
  }
  const changed = url !== serverUrl();
  saveConfig({ serverUrl: url });
  if (settingsWindow && !settingsWindow.isDestroyed()) settingsWindow.close();
  if (changed) {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.loadURL(messengerUrl());
    } else {
      createMainWindow();
    }
  }
  return { ok: true };
});

// ---- App lifecycle ------------------------------------------------------
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', showMainWindow);

  app.whenReady().then(() => {
    setupCertificateTrust();

    // Camera/mic (LiveKit calls) and desktop notifications — same prompts
    // a browser would show, just answered automatically since this is a
    // single trusted deployment the user chose to install.
    session.defaultSession.setPermissionRequestHandler((_wc, permission, callback) => {
      callback(['media', 'notifications'].includes(permission));
    });

    createTray();
    if (!config.serverUrl) {
      openSettingsWindow();
    } else {
      createMainWindow();
    }
  });

  app.on('before-quit', () => {
    isQuitting = true;
  });

  app.on('window-all-closed', () => {
    // Tray app: no windows open does not mean quit. Real quit only
    // happens via the tray menu / OS shutdown (before-quit above).
  });

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
    else showMainWindow();
  });
}
