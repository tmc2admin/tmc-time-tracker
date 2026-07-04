const log = require('electron-log');

class ActivityTracker {
    constructor(appManager) {
        this.appManager = appManager;
        this.apiService = appManager.apiService;
        this.currentApp = { name: null, title: null, startTime: null };
        this.interval = null;
        this.activeWinModule = null;
        this.isActiveWinAvailable = false;

        import('active-win')
            .then(module => {
                this.activeWinModule = module;
                this.isActiveWinAvailable = true;
                log.info('[Tracker] active-win loaded successfully.');
            })
            .catch(err => log.error('[Tracker] Failed to import active-win:', err.message));
    }

    start() {
        if (this.interval) clearInterval(this.interval);
        setTimeout(() => {
            if (this.interval) clearInterval(this.interval);
            this.interval = setInterval(() => this.checkActiveWindow(), 5000);
            log.info('[Tracker] Activity tracking started. Heartbeat: 5s');
        }, 1000);
    }

    stop() {
        if (this.interval) clearInterval(this.interval);
        this.interval = null;
        this.recordCurrentAppUsage().catch(() => {});
        log.info('[Tracker] Activity tracking stopped.'); 
    }

    async checkActiveWindow() {
        try {
            if (!this.isActiveWinAvailable || !this.activeWinModule) return;
            
            // 1. Evaluate State
            if (!this.appManager.isClockedIn || this.appManager.isBreakActive || this.appManager.isIdle) {
                if (this.currentApp.name) {
                    log.info(`[Tracker] State changed. Flushing ${this.currentApp.name}...`);
                    await this.recordCurrentAppUsage();
                    this.currentApp = { name: null, title: null, startTime: null };
                }
                return;
            }

            // 2. Fetch Active Window (Version Agnostic Magic)
            let win = null;
            if (typeof this.activeWinModule.activeWindow === 'function') {
                win = await this.activeWinModule.activeWindow();
            } else if (typeof this.activeWinModule.default === 'function') {
                win = await this.activeWinModule.default();
            } else if (typeof this.activeWinModule === 'function') {
                win = await this.activeWinModule();
            }

            if (!win || !win.owner || !win.owner.name) return;

            const appName = win.owner.name;
            const appTitle = win.title;
            const appPath = win.owner.path;
            const now = Date.now();

            // 3. Change Detection OR 60-Second Periodic Flush
            const isAppChanged = appName !== this.currentApp.name || appTitle !== this.currentApp.title;
            const isTimeForPeriodicFlush = this.currentApp.startTime && (now - this.currentApp.startTime >= 60000);

            if (isAppChanged || isTimeForPeriodicFlush) {
                if (this.currentApp.name) {
                    await this.recordCurrentAppUsage(); 
                }
                
                this.currentApp = { 
                    name: appName, 
                    title: appTitle, 
                    path: appPath, 
                    startTime: now 
                }; 

                // if (isAppChanged) {
                //     log.info(`[Tracker] Switched active window to: ${appName}`);
                // }
            }
        } catch (error) {
            if (!error.message.includes('ENOENT')) {
                log.error(`[Tracker] Loop error: ${error.message}`);
            }
        }
    }

    async recordCurrentAppUsage() {
        if (!this.currentApp.name || !this.currentApp.startTime) return;

        // Ensure we grab the INTEGER User ID, not the Microsoft String UUID
        const profile = this.appManager?.userProfile || {};
        const userId = profile.serverUserId; 

        if (!userId) {
            log.warn(`[Tracker] Aborted sending ${this.currentApp.name}: Missing Integer User ID. (Check main.js linkUser mapping!)`);
            return;
        }

        const durationSeconds = Math.round((Date.now() - this.currentApp.startTime) / 1000);
        
        if (durationSeconds < 5) return; 

        try {
            const appToRecord = { ...this.currentApp };
            const endTime = new Date(); 

            log.info(`[Tracker] Sending usage data: ${appToRecord.name} for ${durationSeconds}s`);

            await this.apiService.recordApplicationUsage({
                user_id: userId,
                application_name: appToRecord.name,
                window_title: appToRecord.title,
                executable_path: appToRecord.path,
                start_time: new Date(appToRecord.startTime).toISOString(),
                end_time: endTime.toISOString(),
                duration_seconds: durationSeconds
            });
            
            // Keep tracking the same app, but reset the timer so we don't double-count
            this.currentApp.startTime = Date.now(); 
            
        } catch (error) {
            log.error(`[Tracker] Failed to record usage for ${this.currentApp.name}: ${error.message}`);
        }
    }
}

module.exports = ActivityTracker;