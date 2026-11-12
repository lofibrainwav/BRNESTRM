'use strict';
// Flip Electron's fuses on the local binary before first use (run by run.sh; idempotent):
// cookies (the sign-ins) are encrypted on disk with a Keychain key, and the binary cannot be
// turned into a plain Node runtime or a debug target through env vars or CLI flags.
const path = require('node:path');
const { flipFuses, getCurrentFuseWire, FuseVersion, FuseV1Options, FuseState } = require('@electron/fuses');

const electronApp = path.join(__dirname, 'node_modules', 'electron', 'dist', 'Electron.app');
const wanted = {
  [FuseV1Options.EnableCookieEncryption]: true,
  [FuseV1Options.RunAsNode]: false,
  [FuseV1Options.EnableNodeOptionsEnvironmentVariable]: false,
  [FuseV1Options.EnableNodeCliInspectArguments]: false,
};

(async () => {
  const wire = await getCurrentFuseWire(electronApp);
  const done = Object.entries(wanted).every(([opt, on]) => (wire[opt] === FuseState.ENABLE) === on);
  if (done) { console.log('electron fuses already set'); return; }
  await flipFuses(electronApp, { version: FuseVersion.V1, resetAdHocDarwinSignature: process.platform === 'darwin', ...wanted });
  console.log('electron fuses set: cookie encryption on; RunAsNode, NODE_OPTIONS and --inspect off');
})().catch(e => { console.error('could not set electron fuses:', e.message); process.exit(1); });
