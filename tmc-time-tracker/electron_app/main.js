// main.js - Update to use refactored API service

const { app, BrowserWindow, powerMonitor, ipcMain, Tray, Menu, nativeImage, dialog, Notification, net, powerSaveBlocker, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const log = require('electron-log');
const Store = require('electron-store');
const { exec } = require('child_process');
const macaddress = require('node-macaddress');
const { utcToZonedTime } = require('date-fns-tz');

// --- CRITICAL FIX: LOAD CONFIG FIRST ---
const config = require('./config');

// --- Modules ---
const ApiService = require('./apiService');
const { authenticateWithMicrosoft, signOutMicrosoft } = require('./auth');
const ActivityTracker = require('./services/tracker');
const SessionManager = require('./services/session');
const { version } = require('./package.json');

// --- FIX 1: Set App ID for Windows Notifications ---
if (process.platform === 'win32') {
    app.setAppUserModelId('com.tmc.timetracker');
}

// --- Config ---
const appConfigStore = new Store({ name: 'app-config' });
const isPackaged = app.isPackaged;

// Use Config for API URL
let FLASK_API_BASE_URL = config.FLASK_API_BASE_URL;

// --- FIX 2: Expanded Localization ---
const appTranslations = {
    en: {
        clockOutDetectedTitle: "Clock-Out Detected",
        clockOutDetectedBody: "The server has automatically clocked you out.",
        autoClockOutTitle: "Auto Clock-Out",
        autoClockOutBody: "You have been automatically clocked out.",
        longBreakBody: "You have been clocked out due to an extended break.",
        reminderTitle: "Clock-In Reminder",
        reminderBody: "It's working hours. Don't forget to clock in!",
        reminderBtn: "Clock In",
        startupErrorTitle: "Startup Error",
        clockInFailedTitle: "Clock In Failed",
        clockOutFailedTitle: "Clock Out Failed",
        outsideWorkingHoursTitle: "Outside Working Hours",
        outsideWorkingHoursBody: "You can only clock in between {start} and {end}.",
        networkErrorTitle: "Connection Error",
        networkErrorBody: "Could not sync with server. Please try again.",
        showApp: "Show App",
        switchAccount: "Switch account...",
        signOut: "Sign out",
        signInDifferent: "Sign in with another account...",
        quitApp: "Quit",
        accountChangeBlockedTitle: "Clock out first",
        accountChangeBlockedBody: "Please clock out before signing out or switching accounts."
    },
    de: {
        clockOutDetectedTitle: "Ausstempeln erkannt",
        clockOutDetectedBody: "Der Server hat Sie automatisch ausgeloggt.",
        autoClockOutTitle: "Automatisches Ausstempeln",
        autoClockOutBody: "Sie wurden automatisch ausageloggt.",
        longBreakBody: "Sie wurden aufgrund einer verlängerten Pause ausgeloggt.",
        reminderTitle: "Erinnerung: Einstempeln",
        reminderBody: "Es ist Arbeitszeit. Vergessen Sie nicht, sich einzustempeln!",
        reminderBtn: "Einstempeln",
        startupErrorTitle: "Startfehler",
        clockInFailedTitle: "Einstempeln fehlgeschlagen",
        clockOutFailedTitle: "Ausstempeln fehlgeschlagen",
        outsideWorkingHoursTitle: "Außerhalb der Arbeitszeit",
        outsideWorkingHoursBody: "Sie können sich nur zwischen {start} und {end} einstempeln.",
        networkErrorTitle: "Verbindungsfehler",
        networkErrorBody: "Synchronisation mit Server fehlgeschlagen. Bitte versuchen Sie es erneut.",
        showApp: "App anzeigen",
        switchAccount: "Benutzer wechseln...",
        signOut: "Abmelden",
        signInDifferent: "Mit anderem Benutzer anmelden...",
        quitApp: "Beenden",
        accountChangeBlockedTitle: "Zuerst ausstempeln",
        accountChangeBlockedBody: "Bitte stempeln Sie sich aus, bevor Sie sich abmelden oder den Benutzer wechseln."
    }
};

// --- Single Instance Lock ---
if (!app.requestSingleInstanceLock()) {
    app.quit();
}

// --- Helper: Device Fingerprint ---
function getWindowsMachineGuid() {
    return new Promise((resolve) => {
        if (process.platform !== 'win32') return resolve(null);
        exec('reg query HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Cryptography /v MachineGuid', (error, stdout) => {
            if (error || !stdout) return resolve(null);
            const match = stdout.match(/MachineGuid\s+REG_SZ\s+([\w-]+)/);
            resolve(match ? match[1] : null);
        });
    });
}

// --- Main Controller ---
class AppController {
    constructor() {
        // Initialize API Service
        this.apiService = new ApiService(FLASK_API_BASE_URL);

        // Initialize Timer
        this.startTimeBigInt = process.hrtime.bigint();

        this.mainWindow = null;
        this.tray = null;
        this.userProfile = null;
        this.currentLanguage = appConfigStore.get('language', 'de');
        
        // State
        this.isClockedIn = false;
        this.isBreakActive = false;
        this.isIdle = false;
        this.isOnline = net.isOnline();
        this.isShuttingDown = false;
        this.isAuthenticating = false;
        this.activeIdleEntryId = null;
        this.currentBreakStartTime = null;
        this.automationConfig = null;
        
        // Initialize Services
        this.tracker = new ActivityTracker(this); 
        this.sessionManager = new SessionManager(
            this.apiService, 
            () => this.handleClockOut('auto_expiry')
        );
        
        // Timers
        this.dashboardPollingInterval = null;
        this.heartbeatInterval = null;
        this.activityCheckInterval = null;
        this.clockInReminderInterval = null;
        
        this.lastUserActivityMonotonic = this.getMonotonicNow();
    }

    getMonotonicNow() {
        if (!this.startTimeBigInt) this.startTimeBigInt = process.hrtime.bigint();
        const now = process.hrtime.bigint();
        const diff = now - this.startTimeBigInt;
        return Number(diff / 1000000n);
    }

    getTranslation(key, params = {}) {
        const lang = this.currentLanguage || 'en';
        let text = appTranslations[lang]?.[key] || appTranslations['en'][key] || key;
        
        Object.keys(params).forEach(pKey => {
            text = text.replace(`{${pKey}}`, params[pKey]);
        });
        return text;
    }

    // Device Identification Logic
    async getOrGenerateDeviceId() {
        let id = null;

        // A. Try Windows Machine GUID
        try {
            if (process.platform === 'win32') {
                id = await getWindowsMachineGuid(); 
            }
        } catch (e) {
            log.warn('[DeviceID] Windows GUID fetch failed:', e);
        }

        // B. Fallback: MAC Address
        if (!id) {
            try {
                id = await new Promise(resolve => macaddress.one((e, mac) => resolve(mac || null)));
            } catch (e) {
                log.warn('[DeviceID] MAC fetch failed:', e);
            }
        }

        // C. Fallback: Persistent Random ID
        if (!id) {
            const store = new Store();
            id = store.get('fallback_device_id');
            if (!id) {
                id = `fallback-${Date.now()}-${Math.floor(Math.random() * 10000)}`;
                store.set('fallback_device_id', id);
            }
        }
        return id;
    }

    // Initialization Sequence
    async init() {
        await app.whenReady();
        
        // Setup Device ID
        try {
            this.deviceId = await this.getOrGenerateDeviceId();
            this.apiService.setDeviceId(this.deviceId); 
            log.info(`[Init] Device ID initialized: ${this.deviceId}`);
        } catch (err) {
            log.error('[Init] Critical Device ID failure:', err);
        }

        // Standard Startup
        this.createWindow();
        this.createTray();
        this.setupIpc();
        this.setupPowerMonitor();
        this.setupNativeHooks();
        this.setupAutoLaunch();

        setInterval(() => { this.isOnline = net.isOnline(); }, 30000);

        try {
            await this.login({ selectAccount: false });
        } catch (err) {
            log.error('[Init] Startup failed:', err);
            dialog.showErrorBox(this.getTranslation('startupErrorTitle'), err.message);
        }
    }

    setupAutoLaunch() {
        if (!app.isPackaged) return;
        try {
            app.setLoginItemSettings({
                openAtLogin: true,
                path: process.execPath,
                args: [],
                name: app.getName(),
            });
        } catch (error) { log.error('[AutoLaunch] Error:', error); }
    }

    createWindow() {
        const iconPath = isPackaged ? path.join(process.resourcesPath, 'icon.ico') : path.join(__dirname, 'icon.ico');
        this.mainWindow = new BrowserWindow({
            width: 300, height: 120,
            frame: false, resizable: false,
            alwaysOnTop: true, transparent: true, skipTaskbar: true,
            icon: iconPath,
            webPreferences: { preload: path.join(__dirname, 'preload.js'), nodeIntegration: false, contextIsolation: true }
        });
        this.mainWindow.loadFile('index.html');
        this.mainWindow.on('close', (e) => {
            if (!this.isShuttingDown) { e.preventDefault(); this.mainWindow.hide(); }
        });
    }

    createTray() {
        const iconPath = isPackaged ? path.join(process.resourcesPath, 'icon.png') : path.join(__dirname, 'icon.png');
        this.tray = new Tray(nativeImage.createFromPath(iconPath));
        this.tray.setToolTip('Time Tracker');
        this.updateTrayMenu();
        this.tray.on('double-click', () => this.mainWindow.show());
    }

    updateTrayMenu() {
        if (!this.tray) return;
        const template = [
            { label: this.getTranslation('showApp'), click: () => this.mainWindow.show() },
            { type: 'separator' }
        ];

        if (this.userProfile) {
            template.push(
                { label: this.getTranslation('switchAccount'), click: () => this.switchAccount() },
                { label: this.getTranslation('signOut'), click: () => this.logout() },
                { type: 'separator' }
            );
        } else {
            template.push(
                { label: this.getTranslation('signInDifferent'), click: () => this.login({ selectAccount: true }) },
                { type: 'separator' }
            );
        }

        template.push({
            label: this.getTranslation('quitApp'),
            click: () => { this.isShuttingDown = true; app.quit(); }
        });
        this.tray.setContextMenu(Menu.buildFromTemplate(template));
    }

    // --- Auth & Session ---

    async checkAndAuthenticate({ selectAccount = false } = {}) {
        const sessionPath = path.join(app.getPath('userData'), 'session.json');

        try {
            if (fs.existsSync(sessionPath)) {
                try {
                    fs.unlinkSync(sessionPath);
                } catch (err) {
                    log.warn(`[Auth] Could not remove legacy session file: ${err.message}`);
                }
            }

            const authResult = await authenticateWithMicrosoft({
                parentWindow: this.mainWindow,
                selectAccount
            });
            const profile = authResult.userProfile;

            // Get Device ID
            let deviceId = await getWindowsMachineGuid();

            if (!deviceId) {
                deviceId = await new Promise((resolve) => {
                    macaddress.one((err, mac) => {
                        if (err || !mac) {
                            log.warn('[Auth] Failed to retrieve MAC address:', err);
                            resolve(null);
                        } else {
                            resolve(mac);
                        }
                    });
                });
            }

            if (!deviceId) {
                log.warn('[Auth] Critical: No Device ID found. Using fallback.');
                deviceId = `fallback-${profile.id}-${Date.now()}`;
            }

            log.info(`[Auth] Linking user with Device ID: ${deviceId}`);

            // Link User
            const linkResult = await this.apiService.linkUser(
                profile.id, 
                profile.mail || profile.userPrincipalName, 
                profile.displayName, 
                deviceId
            );
            
            if (linkResult) {
                profile.serverUserId = linkResult.user_id || linkResult.id || linkResult.user?.id;
            }
            
            this.userProfile = profile;
            return true;

        } catch (e) {
            log.error('[Auth] Login Error:', e);
            if (e.response && e.response.data) {
                log.error('[Auth] Server Response:', e.response.data);
            }
            return false;
        }
    }

    async login({ selectAccount = false } = {}) {
        if (this.isAuthenticating) return false;
        this.isAuthenticating = true;
        this.sendAuthState();

        try {
            const isAuthenticated = await this.checkAndAuthenticate({ selectAccount });
            if (!isAuthenticated) {
                this.userProfile = null;
                this.updateTrayMenu();
                return false;
            }

            await this.sessionManager.fetchAndStoreConfig(this.userProfile.serverUserId);
            this.startDashboardPolling();
            this.mainWindow?.show();
            await this.refreshDashboard();

            if (!this.isClockedIn) this.startClockInReminderChecks();
            else this.startMonitoring();

            this.updateTrayMenu();
            return true;
        } finally {
            this.isAuthenticating = false;
            this.sendAuthState();
        }
    }

    stopAuthenticatedRuntime() {
        this.stopMonitoring();
        this.stopClockInReminderChecks();
        if (this.dashboardPollingInterval) clearInterval(this.dashboardPollingInterval);
        this.dashboardPollingInterval = null;
        this.isClockedIn = false;
        this.isBreakActive = false;
        this.isIdle = false;
        this.activeIdleEntryId = null;
        this.currentBreakStartTime = null;
    }

    sendAuthState() {
        if (!this.mainWindow || this.mainWindow.isDestroyed()) return;
        const email = this.userProfile?.mail || this.userProfile?.userPrincipalName || '';
        this.mainWindow.webContents.send('auth-state-changed', {
            status: this.isAuthenticating ? 'authenticating' : (this.userProfile ? 'authenticated' : 'signed-out'),
            displayName: this.userProfile?.displayName || '',
            email
        });
    }

    async logout({ switchAccount = false } = {}) {
        if (this.isClockedIn) {
            await dialog.showMessageBox(this.mainWindow, {
                type: 'warning',
                title: this.getTranslation('accountChangeBlockedTitle'),
                message: this.getTranslation('accountChangeBlockedBody')
            });
            return false;
        }

        this.stopAuthenticatedRuntime();
        await signOutMicrosoft();
        this.userProfile = null;
        this.updateTrayMenu();
        this.sendAuthState();
        this.mainWindow?.show();

        if (switchAccount) return this.login({ selectAccount: true });
        return true;
    }

    async switchAccount() {
        return this.logout({ switchAccount: true });
    }

    showAccountMenu() {
        if (!this.mainWindow || this.mainWindow.isDestroyed()) return;
        if (!this.userProfile) {
            this.login({ selectAccount: true });
            return;
        }

        const email = this.userProfile.mail || this.userProfile.userPrincipalName || '';
        const menu = Menu.buildFromTemplate([
            { label: this.userProfile.displayName || email, enabled: false },
            ...(email && email !== this.userProfile.displayName ? [{ label: email, enabled: false }] : []),
            { type: 'separator' },
            { label: this.getTranslation('switchAccount'), click: () => this.switchAccount() },
            { label: this.getTranslation('signOut'), click: () => this.logout() }
        ]);
        menu.popup({ window: this.mainWindow });
    }

    // --- State Management ---
    async refreshDashboard() {
        if (!this.userProfile || !this.isOnline) return;
        try {
            const data = await this.apiService.getDashboardData(this.userProfile.serverUserId, this.currentLanguage);
            this.syncLocalState(data);
            if (this.mainWindow && !this.mainWindow.isDestroyed()) {
                this.mainWindow.webContents.send('dashboard-data-update', data);
            }
        } catch (e) { 
            if (e.message.includes('Network') || e.message.includes('connect')) {
                log.warn('[Dashboard] Network unreachable. Skipping refresh.');
            } else {
                log.error('[Dashboard] Refresh error', e);
            }
        }
    }

    syncLocalState(data) {
        if (!data || data.error) return;
        
        const wasClockedIn = this.isClockedIn;
        this.isClockedIn = !!data.is_clocked_in;
        this.isBreakActive = !!data.is_break_active;
        this.isIdle = !!data.is_idle_active;
        this.activeIdleEntryId = data.active_idle_entry_id;
        this.automationConfig = data.automation_config;
        this.currentBreakStartTime = data.current_ongoing_break_start_time || null;

        if (!wasClockedIn && this.isClockedIn) {
            this.stopClockInReminderChecks();
            this.startMonitoring();
        }
        if (wasClockedIn && !this.isClockedIn) {
            this.stopMonitoring();
            this.startClockInReminderChecks();
            new Notification({
                title: this.getTranslation('clockOutDetectedTitle'),
                body: this.getTranslation('clockOutDetectedBody')
            }).show();
        }
    }

    startDashboardPolling() {
        if (this.dashboardPollingInterval) clearInterval(this.dashboardPollingInterval);
        this.dashboardPollingInterval = setInterval(() => this.refreshDashboard(), 15000);
    }

    // --- Monitoring & Logic ---
    startMonitoring() {
        if (!this.tracker) return;
        this.tracker.start();
        
        if (this.heartbeatInterval) clearInterval(this.heartbeatInterval);
        this.heartbeatInterval = setInterval(() => {
            if (this.isOnline) {
                this.apiService.sendHeartbeatPing(this.userProfile.id).catch(() => {});
            }
        }, 120000);
        
        if (this.activityCheckInterval) clearInterval(this.activityCheckInterval);
        this.activityCheckInterval = setInterval(() => this.checkIdleStateLoop(), 5000);
    }

    stopMonitoring() {
        if (this.tracker) this.tracker.stop();
        if (this.heartbeatInterval) clearInterval(this.heartbeatInterval);
        if (this.activityCheckInterval) clearInterval(this.activityCheckInterval);
        this.sessionManager.cancelAutoClockOut();
    }

    async checkIdleStateLoop() {
        if (!this.isClockedIn || !this.isOnline) return;

        if (this.isBreakActive && this.automationConfig?.auto_clock_out_after_break_minutes && this.currentBreakStartTime) {
            const breakMins = (Date.now() - new Date(this.currentBreakStartTime).getTime()) / 60000;
            if (breakMins > this.automationConfig.auto_clock_out_after_break_minutes) {
                log.info(`[AutoClockOut] Break duration ${Math.round(breakMins)}m exceeded limit.`);
                await this.handleClockOut('long_break');
                return;
            }
        }

        const idleMins = (this.getMonotonicNow() - this.lastUserActivityMonotonic) / 60000;
        const maxIdleMins = this.automationConfig?.max_idle_minutes || 1;
        const idleToBreakMins = this.automationConfig?.idle_to_break_minutes || 5;

        if (idleMins >= maxIdleMins && !this.isIdle && !this.isBreakActive) {
            await this.apiService.startIdle(this.userProfile.id);
            this.refreshDashboard();
        }
        else if (idleMins >= idleToBreakMins && this.isIdle && !this.isBreakActive) {
            await this.apiService.startBreak(this.userProfile.id);
            this.refreshDashboard();
        }
        else if (idleMins < 0.1 && (this.isIdle || this.isBreakActive)) {
            if (this.isBreakActive) {
                 const res = await this.apiService.endBreak(this.userProfile.id);
                 if (res?.prompt_for_reason && this.mainWindow) {
                     this.mainWindow.show();
                     this.mainWindow.webContents.send('prompt-break-reason', { breakId: res.break_id });
                 }
            }
            if (this.isIdle) await this.apiService.endIdle(this.activeIdleEntryId);
            this.refreshDashboard();
        }
    }

    async handleClockInCheck() {
        const config = this.sessionManager.config;
        if (config && config.startTime && config.endTime) {
            const nowTz = utcToZonedTime(new Date(), 'Europe/Berlin');
            const nowSeconds = nowTz.getHours() * 3600 + nowTz.getMinutes() * 60;
            
            const [sH, sM] = config.startTime.split(':').map(Number);
            const [eH, eM] = config.endTime.split(':').map(Number);
            const startSeconds = sH * 3600 + sM * 60;
            const endSeconds = eH * 3600 + eM * 60;

            if (nowSeconds < startSeconds || nowSeconds > endSeconds) {
                dialog.showErrorBox(
                    this.getTranslation('outsideWorkingHoursTitle'), 
                    this.getTranslation('outsideWorkingHoursBody', { start: config.startTime, end: config.endTime })
                );
                return;
            }
        }

        try {
            const loc = await this.apiService.getGeolocation();
            await this.apiService.clockIn(this.userProfile.id, loc, app.getVersion());
            this.startMonitoring();
            this.refreshDashboard();
        } catch(e) {
            dialog.showErrorBox(this.getTranslation('clockInFailedTitle'), e.message);
        }
    }

    async handleClockOut(reason) {
        const payload = { source: reason || 'manual', timestamp: new Date().toISOString() };
        try {
            await this.apiService.clockOut(this.userProfile.id, payload);
            this.stopMonitoring();
            this.refreshDashboard();
            if (reason === 'long_break') {
                new Notification({
                    title: this.getTranslation('autoClockOutTitle'),
                    body: this.getTranslation('longBreakBody')
                }).show();
            }
        } catch (error) {
            log.error(`[ClockOut] Failed: ${error.message}`);
            if (this.isOnline) {
                dialog.showErrorBox(
                    this.getTranslation('clockOutFailedTitle'), 
                    this.getTranslation('networkErrorBody')
                );
            }
        }
    }

    // --- Reminders ---
    startClockInReminderChecks() {
        if (this.clockInReminderInterval) clearInterval(this.clockInReminderInterval);
        
        this.clockInReminderInterval = setInterval(() => {
             const config = this.sessionManager.config;
             if (config && !this.isClockedIn && config.startTime && config.endTime) {
                 const nowTz = utcToZonedTime(new Date(), 'Europe/Berlin');
                 const [sH, sM] = config.startTime.split(':').map(Number);
                 const [eH, eM] = config.endTime.split(':').map(Number);
                 const nowH = nowTz.getHours();
                 
                 if (nowH >= sH && nowH < eH) {
                     const n = new Notification({
                         title: this.getTranslation('reminderTitle'),
                         body: this.getTranslation('reminderBody')
                     });
                     n.on('click', () => this.mainWindow.show());
                     n.show();
                 }
             }
        }, 900000); 
    }

    stopClockInReminderChecks() {
        if (this.clockInReminderInterval) clearInterval(this.clockInReminderInterval);
    }

    // --- Hardware Hooks ---
    setupNativeHooks() {
        powerSaveBlocker.start('prevent-app-suspension');
        try {
            const { uIOhook } = require('uiohook-napi');
            const handler = () => { this.lastUserActivityMonotonic = this.getMonotonicNow(); };
            uIOhook.on('mousemove', handler);
            uIOhook.on('keydown', handler);
            uIOhook.start();
        } catch (e) { log.error('IOHook failed', e); }
    }

    setupPowerMonitor() {
        powerMonitor.on('lock-screen', async () => {
            if (this.isClockedIn && !this.isBreakActive) {
                try {
                    const response = await this.apiService.startIdle(this.userProfile.id);
                    if (response && response.id) {
                        this.activeIdleEntryId = response.id;
                        log.info(`[Idle] Started idle entry: ${this.activeIdleEntryId}`);
                    }
                    this.refreshDashboard();
                } catch (err) {
                    log.error('[Idle] Failed to start:', err.message);
                }
            }
        });

        powerMonitor.on('unlock-screen', async () => {
            if (this.activeIdleEntryId) {
                try {
                    await this.apiService.endIdle(this.activeIdleEntryId);
                    log.info(`[Idle] Ended idle entry: ${this.activeIdleEntryId}`);
                    this.activeIdleEntryId = null;
                    this.refreshDashboard();
                } catch (err) {
                    log.error('[Idle] Failed to end:', err.message);
                }
             }
        });
    }

    // --- IPC ---
    setupIpc() {
        ipcMain.on('renderer-ready', () => {
            this.sendAuthState();
            this.refreshDashboard();
        });
        ipcMain.on('minimize-app', () => this.mainWindow.hide());
        ipcMain.on('quit-app', () => { this.isShuttingDown = true; app.quit(); });
        ipcMain.on('retry-startup', () => this.login({ selectAccount: false }));
        ipcMain.on('show-account-menu', () => this.showAccountMenu());
        ipcMain.on('sign-in-with-other-account', () => this.login({ selectAccount: true }));
        
        ipcMain.on('clock-action', async (event, action) => {
            if (action === 'clock_in') await this.handleClockInCheck();
            if (action === 'clock_out') await this.handleClockOut('manual');
        });

        ipcMain.on('submit-break-reason', async (event, { breakId, reason }) => {
            try {
                // Use the unified endpoint - both work with the same handler now
                await this.apiService.submitBreakReason(breakId, reason);
                this.refreshDashboard(); 
            } catch (err) {
                log.error('[BreakReason] Failed to submit:', err);
            }
        });

        ipcMain.on('set-language', (e, { lang }) => {
            this.currentLanguage = lang;
            appConfigStore.set('language', lang);
            this.updateTrayMenu();
            this.refreshDashboard();
        });

        ipcMain.on('open-dashboard-window', () => this.openDashboardWindow());
        
        ipcMain.handle('get-app-version', () => app.getVersion());
    }

    async openDashboardWindow() {
        if (!this.userProfile || !this.userProfile.serverUserId) return;
        try {
            const response = await this.apiService.generateWebLoginToken(this.userProfile.serverUserId);
            if (response.token) {
                const baseUrl = FLASK_API_BASE_URL.replace(/\/$/, '');
                const dashboardUrl = `${baseUrl}/login-with-token?token=${response.token}`;
                await shell.openExternal(dashboardUrl);
            }
        } catch (error) {
            log.error(`[Dashboard] Failed to open: ${error.message}`);
        }
    }
}

// --- Bootstrap ---
const appController = new AppController();
app.on('second-instance', () => {
    if (appController.mainWindow) {
        if (appController.mainWindow.isMinimized()) appController.mainWindow.restore();
        appController.mainWindow.show();
        appController.mainWindow.focus();
    }
});

app.on('ready', () => appController.init());
