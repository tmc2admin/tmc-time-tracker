// preload.js - Updated for modularized API
const { contextBridge, ipcRenderer } = require('electron');

console.log('[Preload] Script initialized.');

contextBridge.exposeInMainWorld('electronAPI', {
  // --- Window & System Actions ---
  minimizeApp: () => ipcRenderer.send('minimize-app'),
  quitApp: () => ipcRenderer.send('quit-app'),
  getAppVersion: () => ipcRenderer.invoke('get-app-version'),
  
  // --- App Flow Actions ---
  rendererReady: () => {
      console.log('[Preload] Sending renderer-ready signal...'); 
      ipcRenderer.send('renderer-ready');
  },
  retryStartup: () => ipcRenderer.send('retry-startup'),
  setLanguage: (lang) => ipcRenderer.send('set-language', { lang }),
  openDashboardWindow: () => ipcRenderer.send('open-dashboard-window'),
  showAccountMenu: () => ipcRenderer.send('show-account-menu'),
  signInWithOtherAccount: () => ipcRenderer.send('sign-in-with-other-account'),
  
  // --- Core Logic (Clock, Breaks, Meetings) ---
  sendClockAction: (action, payload) => ipcRenderer.send('clock-action', action, payload),
  submitBreakReason: (breakId, reason) => ipcRenderer.send('submit-break-reason', { breakId, reason }),
  
  // --- Meeting Actions ---
  startMeeting: () => ipcRenderer.send('meeting-action', 'start'),
  endMeeting: () => ipcRenderer.send('meeting-action', 'end'),

  // --- Data Requests ---
  fetchDashboardData: (lang) => ipcRenderer.invoke('fetch-dashboard-data', lang),
  
  // --- Event Listeners (Main -> Renderer) ---
  onDashboardDataUpdate: (callback) => ipcRenderer.on('dashboard-data-update', (event, ...args) => callback(...args)),
  onInitialDataReady: (callback) => ipcRenderer.on('initial-data-ready', (event, ...args) => callback(...args)),
  onStartupError: (callback) => ipcRenderer.on('startup-error', (event, ...args) => callback(...args)),
  onAuthStateChanged: (callback) => ipcRenderer.on('auth-state-changed', (event, ...args) => callback(...args)),
  onPromptBreakReason: (callback) => ipcRenderer.on('prompt-break-reason', (event, ...args) => callback(...args)),
  onClockInError: (callback) => ipcRenderer.on('clock-in-error', (event, ...args) => callback(...args)),
});
