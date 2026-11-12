'use strict';
// The only thing the console page gets from the shell: show/position/hide a real browser view
// per pane, and its navigation state. No Node, no file system, no arbitrary IPC.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('brnestrmShell', Object.freeze({
  version: 1,
  show: (pane, url, rect) => ipcRenderer.send('shell:show', { pane, url, rect }),
  setBounds: (pane, rect) => ipcRenderer.send('shell:bounds', { pane, rect }),
  hide: pane => ipcRenderer.send('shell:hide', { pane }),
  back: pane => ipcRenderer.send('shell:nav', { pane, action: 'back' }),
  forward: pane => ipcRenderer.send('shell:nav', { pane, action: 'forward' }),
  reload: pane => ipcRenderer.send('shell:nav', { pane, action: 'reload' }),
  onState: callback => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('shell:state', listener);
    return () => ipcRenderer.removeListener('shell:state', listener);
  },
}));
