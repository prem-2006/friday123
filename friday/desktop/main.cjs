// Friday's desktop notch: a thin black pill just above the taskbar, like
// Wispr Flow. Hold Caps Lock anywhere to talk (friday/services' keyboard hook
// tells the notch); Friday answers out loud.
//
// The notch is a thin shell around the project folder: it shows Friday's
// #/notch page from the folder's dev server, so any change to the folder shows
// up at once, and this file restarts the shell when it changes itself. No
// reinstall is ever needed. If the app or services are not running, the shell
// starts them (logs go to data/desktop-*.log), checks every 30 seconds and
// starts again whatever stopped. It starts with Windows unless the owner turns
// that off in the tray menu.
const {
  app,
  BrowserWindow,
  Menu,
  Tray,
  ipcMain,
  nativeImage,
  screen,
  session,
  shell,
} = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const REPO = path.resolve(__dirname, '..', '..');
const WEB = process.env.FRIDAY_WEB_URL || 'http://localhost:5173';
const API = process.env.FRIDAY_APP_URL || 'http://127.0.0.1:4310';
const SERVICES = process.env.FRIDAY_SERVICES_URL || 'http://127.0.0.1:8765';
// Tall enough for a message card above the pill; the window itself is
// see-through and ignores the mouse, so the extra height covers nothing.
const NOTCH = { width: 480, height: 280, gap: 6 };
// The live task toast: bottom left, above the taskbar, about 3 cm wide.
const TOAST = { width: 340, height: 96, gap: 8 };

// Why the notch started or stopped, for when it is not there.
function notchLog(line) {
  try {
    fs.appendFileSync(
      path.join(REPO, 'data', 'desktop-notch.log'),
      `${new Date().toISOString()} [${process.pid}] ${line}\n`,
    );
  } catch {
    // no data folder yet
  }
}
notchLog('notch starting');
process.on('exit', (code) => notchLog(`notch exited (${code})`));
process.on('uncaughtException', (error) =>
  notchLog(`notch error: ${error?.stack || error}`),
);

if (!app.requestSingleInstanceLock()) {
  notchLog('another notch is already running; leaving it');
  app.exit(0);
}
// Started again while running: show the notch if it was hidden.
app.on('second-instance', () => {
  if (liveNotch() && !notch.isVisible()) notch.showInactive();
});

// Friday speaks without anyone clicking first.
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');
// Lets the notch page be inspected from this PC (127.0.0.1 only) when
// something goes wrong.
app.commandLine.appendSwitch('remote-debugging-port', '9334');

const children = new Map(); // name -> the process this shell started
let notch = null;
let tray = null;

async function reachable(url) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(1500) });
    return response.status < 500;
  } catch {
    return false;
  }
}

const running = (child) => child && child.exitCode === null && !child.killed;

function start(name, command, args, cwd) {
  // Still starting up from last time: give it time.
  if (running(children.get(name))) return;
  const log = fs.openSync(path.join(REPO, 'data', `desktop-${name}.log`), 'a');
  const child = spawn(command, args, {
    cwd,
    // Python writes its log lines at once instead of holding them back.
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    stdio: ['ignore', log, log],
    // Its own hidden console: closing the Start Friday window must not take
    // the app and the services down with it.
    detached: true,
    windowsHide: true,
    shell: process.platform === 'win32',
  });
  children.set(name, child);
}

// Starts whatever is not running: the app (web page and API) and the services
// (tools, live voice, Caps Lock). Something the owner runs in a terminal counts.
async function keepFridayRunning() {
  fs.mkdirSync(path.join(REPO, 'data'), { recursive: true });
  if (!(await reachable(`${API}/api/workspace`)) || !(await reachable(WEB)))
    start('app', 'npm', ['run', 'dev'], REPO);
  const services = path.join(REPO, 'friday', 'services');
  // The environment `uv sync` made, when there is one: no uv needed to start.
  const venvPython = path.join(services, '.venv', 'Scripts', 'python.exe');
  if (!(await reachable(`${SERVICES}/health`)))
    start(
      'services',
      fs.existsSync(venvPython) ? `"${venvPython}"` : 'python',
      fs.existsSync(venvPython)
        ? ['-m', 'friday_services']
        : ['-m', 'uv', 'run', 'python', '-m', 'friday_services'],
      services,
    );
}

async function ensureFriday() {
  await keepFridayRunning();
  for (let i = 0; i < 120 && !(await reachable(WEB)); i++)
    await new Promise((resolve) => setTimeout(resolve, 1000));
}

// Exactly bottom centre: centred on the whole screen (not the work area, which
// a side taskbar shifts), resting just above the taskbar.
function place(window) {
  const { bounds, workArea } = screen.getPrimaryDisplay();
  window.setBounds({
    x: Math.round(bounds.x + (bounds.width - NOTCH.width) / 2),
    y: Math.round(workArea.y + workArea.height - NOTCH.height - NOTCH.gap),
    width: NOTCH.width,
    height: NOTCH.height,
  });
}

let toast = null;
const liveToast = () => (toast && !toast.isDestroyed() ? toast : null);

// The current task's step, for a few seconds, then nothing (#/toast). It
// never takes the mouse or the keyboard.
function createToast() {
  const { workArea } = screen.getPrimaryDisplay();
  toast = new BrowserWindow({
    x: workArea.x + TOAST.gap,
    y: workArea.y + workArea.height - TOAST.height - TOAST.gap,
    width: TOAST.width,
    height: TOAST.height,
    frame: false,
    transparent: true,
    resizable: false,
    movable: false,
    focusable: false,
    skipTaskbar: true,
    hasShadow: false,
    alwaysOnTop: true,
    show: false,
    backgroundColor: '#00000000',
    webPreferences: { backgroundThrottling: false },
  });
  toast.on('closed', () => {
    toast = null;
  });
  toast.setAlwaysOnTop(true, 'screen-saver');
  toast.setIgnoreMouseEvents(true);
  toast.once('ready-to-show', () => liveToast()?.showInactive());
  // As with the notch: after every load (a reload too) it is on screen. Its
  // page's errors and feed connection go to the notch log, for when it shows
  // nothing.
  toast.webContents.on('did-finish-load', () => {
    notchLog(`toast page loaded at ${JSON.stringify(liveToast()?.getBounds())}`);
    liveToast()?.showInactive();
  });
  toast.webContents.on('console-message', (event) => {
    if (event.level === 'error' || event.message.startsWith('[toast]'))
      notchLog(`toast page: ${event.message.slice(0, 300)}`);
  });
  void toast.loadURL(`${WEB}/#/toast`);
  const reload = () =>
    setTimeout(() => void liveToast()?.loadURL(`${WEB}/#/toast`), 3000);
  toast.webContents.on('did-fail-load', reload);
  toast.webContents.on('render-process-gone', reload);
}

function openMain(hash = '#/home') {
  void shell.openExternal(`${WEB}/${hash}`);
}

// The notch window, or null once it is closed: a message from its page can
// still arrive while the window is going away, and a closed window throws.
const liveNotch = () => (notch && !notch.isDestroyed() ? notch : null);

function createNotch() {
  notch = new BrowserWindow({
    width: NOTCH.width,
    height: NOTCH.height,
    frame: false,
    transparent: true,
    resizable: false,
    movable: false,
    focusable: false,
    skipTaskbar: true,
    hasShadow: false,
    alwaysOnTop: true,
    show: false,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      backgroundThrottling: false,
    },
  });
  notch.on('closed', () => {
    notch = null;
  });
  notch.setAlwaysOnTop(true, 'screen-saver');
  // The notch never touches the mouse: clicks, hovering and the pointer all
  // belong to whatever is underneath. Only an approval takes clicks, while it
  // is on screen (the page asks for it).
  notch.setIgnoreMouseEvents(true);
  place(notch);
  notch.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: 'deny' };
  });
  // Shown once at start; after that, hiding with Ctrl + Caps Lock sticks.
  let shown = false;
  const show = () => {
    if (liveNotch() && !shown) {
      shown = true;
      notch.showInactive();
    }
  };
  notch.once('ready-to-show', show);
  notch.webContents.on('did-finish-load', show);
  void notch.loadURL(`${WEB}/#/notch`);
  const reload = () =>
    setTimeout(() => void liveNotch()?.loadURL(`${WEB}/#/notch`), 2000);
  // If the dev server restarts, or the page crashes or hangs, load it again.
  notch.webContents.on('did-fail-load', reload);
  notch.webContents.on('render-process-gone', reload);
  notch.on('unresponsive', reload);
}

function trayIcon() {
  return nativeImage.createFromPath(path.join(__dirname, 'icon.png'));
}

function createTray() {
  tray = new Tray(trayIcon());
  tray.setToolTip('Friday: hold Caps Lock to talk');
  const startsWithWindows = () =>
    app.getLoginItemSettings(loginItem()).openAtLogin;
  const menu = () =>
    Menu.buildFromTemplate([
      { label: 'Open Friday', click: () => openMain('#/home') },
      { label: 'Voice screen', click: () => openMain('#/friday') },
      { type: 'separator' },
      {
        label: 'Start with Windows',
        type: 'checkbox',
        checked: startsWithWindows(),
        click: (item) => setStartWithWindows(item.checked),
      },
      {
        label: 'Hide or show the notch (Ctrl + Caps Lock)',
        click: toggleNotch,
      },
      { label: 'Reload', click: () => liveNotch()?.reload() },
      { type: 'separator' },
      { label: 'Quit Friday', click: () => app.quit() },
    ]);
  tray.setContextMenu(menu());
  tray.on('click', () => openMain('#/home'));
}

// Starting with Windows runs this same folder through Electron.
function loginItem() {
  return { name: 'Friday', path: process.execPath, args: [__dirname] };
}

// Off unless the owner turns it on in the tray menu: Friday starts only when
// the owner starts it. Only that choice is remembered (a new file name, since
// older versions wrote "on" by themselves).
const choiceFile = () =>
  path.join(app.getPath('userData'), 'start-with-windows-choice.json');
function setStartWithWindows(on) {
  app.setLoginItemSettings({ ...loginItem(), openAtLogin: on });
  fs.mkdirSync(path.dirname(choiceFile()), { recursive: true });
  fs.writeFileSync(choiceFile(), JSON.stringify({ on }));
}
function startWithWindowsOnlyIfChosen() {
  let chosen = false;
  try {
    chosen = JSON.parse(fs.readFileSync(choiceFile(), 'utf8')).on === true;
  } catch {
    // No choice made: off.
  }
  if (app.getLoginItemSettings(loginItem()).openAtLogin !== chosen)
    app.setLoginItemSettings({ ...loginItem(), openAtLogin: chosen });
}

// The notch must always be connected for Caps Lock to work: if its page is
// stuck offline, did not render, or lost the stream that brings Caps Lock
// (the app restarted under it, say), reload it.
function watchNotch() {
  let strikes = 0;
  setInterval(async () => {
    if (!liveNotch()) return;
    let healthy = false;
    try {
      healthy = await notch.webContents.executeJavaScript(
        "!!document.querySelector('.friday-notch:not(.offline):not(.unlinked)')",
      );
    } catch {
      healthy = false;
    }
    strikes = healthy ? 0 : strikes + 1;
    if (strikes >= 2) {
      strikes = 0;
      void liveNotch()?.loadURL(`${WEB}/#/notch`);
    }
  }, 20_000);
}

// The shell itself updates by restarting when its own files change.
function watchSelf() {
  let timer;
  for (const file of ['main.cjs', 'preload.cjs'])
    fs.watch(path.join(__dirname, file), () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        app.relaunch();
        app.exit(0);
      }, 500);
    });
}

ipcMain.on('friday:interactive', (_event, on) =>
  liveNotch()?.setIgnoreMouseEvents(!on),
);
// Ctrl + Caps Lock or the tray: hide the notch, or bring it back. Voice
// keeps working while it is hidden.
function toggleNotch() {
  if (!liveNotch()) return;
  if (notch.isVisible()) notch.hide();
  else notch.showInactive();
}
ipcMain.on('friday:toggle', toggleNotch);
ipcMain.on('friday:open', (_event, hash) =>
  openMain(typeof hash === 'string' && hash.startsWith('#/') ? hash : '#/home'),
);

app.whenReady().then(async () => {
  // The mic is for Friday's own pages only.
  const local = (url) => {
    try {
      return ['localhost', '127.0.0.1'].includes(new URL(url).hostname);
    } catch {
      return false;
    }
  };
  session.defaultSession.setPermissionRequestHandler(
    (contents, permission, allow) =>
      allow(permission === 'media' && local(contents.getURL())),
  );
  session.defaultSession.setPermissionCheckHandler(
    (_contents, permission, origin) => permission === 'media' && local(origin),
  );
  startWithWindowsOnlyIfChosen();
  createTray();
  await ensureFriday();
  createNotch();
  createToast();
  watchSelf();
  watchNotch();
  setInterval(() => void keepFridayRunning(), 30_000);
  screen.on('display-metrics-changed', () => {
    if (liveNotch()) place(notch);
    const { workArea } = screen.getPrimaryDisplay();
    liveToast()?.setBounds({
      x: workArea.x + TOAST.gap,
      y: workArea.y + workArea.height - TOAST.height - TOAST.gap,
      width: TOAST.width,
      height: TOAST.height,
    });
  });
});

app.on('window-all-closed', () => {
  // The notch lives in the tray; closing windows does not quit it.
});

app.on('before-quit', () => {
  // Stops what the shell started, with everything those started in turn.
  for (const child of children.values())
    if (running(child) && child.pid)
      spawn('taskkill', ['/pid', String(child.pid), '/T', '/F'], {
        windowsHide: true,
      });
});
