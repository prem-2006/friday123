// Friday desktop: adds "Friday" to the Start menu and the desktop. Both
// shortcuts run this folder through Electron, so updating the project folder
// updates the app; there is nothing to reinstall. "Start with Windows" is a
// switch in the notch's tray menu.
//
//   cd friday/desktop; npm install; npm run install-shortcuts
//   npm run uninstall-shortcuts      # removes them again
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const here = __dirname;
const electron = path.join(
  here,
  'node_modules',
  'electron',
  'dist',
  'electron.exe',
);
const places = [
  path.join(
    os.homedir(),
    'AppData',
    'Roaming',
    'Microsoft',
    'Windows',
    'Start Menu',
    'Programs',
    'Friday.lnk',
  ),
  path.join(os.homedir(), 'Desktop', 'Friday.lnk'),
];

if (process.argv.includes('--remove')) {
  for (const link of places) fs.rmSync(link, { force: true });
  console.log('Removed the Friday shortcuts.');
  process.exit(0);
}

if (!fs.existsSync(electron)) {
  // npm may skip Electron's download step; run it now.
  execFileSync(
    process.execPath,
    [path.join(here, 'node_modules', 'electron', 'install.js')],
    {
      stdio: 'inherit',
    },
  );
}

const quote = (value) => `'${value.replace(/'/g, "''")}'`;
for (const link of places) {
  fs.mkdirSync(path.dirname(link), { recursive: true });
  const script = [
    '$shell = New-Object -ComObject WScript.Shell',
    `$link = $shell.CreateShortcut(${quote(link)})`,
    `$link.TargetPath = ${quote(electron)}`,
    // --open: the main page opens as an app window (and starts Friday if needed).
    `$link.Arguments = ${quote(`"${here}" --open`)}`,
    `$link.WorkingDirectory = ${quote(here)}`,
    `$link.IconLocation = ${quote(path.join(here, 'icon.ico'))}`,
    "$link.Description = 'Friday: hold Caps Lock to talk'",
    '$link.Save()',
  ].join('; ');
  execFileSync(
    'powershell',
    ['-NoProfile', '-NonInteractive', '-Command', script],
    {
      stdio: 'inherit',
    },
  );
  console.log(`Created ${link}`);
}
console.log(
  'Open "Friday" from the desktop or the Start menu: the main page opens as an app, and the notch appears above the taskbar.',
);
