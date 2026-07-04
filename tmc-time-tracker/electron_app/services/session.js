const log = require('electron-log');
const { app, dialog } = require('electron');
const { utcToZonedTime } = require('date-fns-tz');

class SessionManager {
    constructor(apiService, onAutoClockOut) {
        this.apiService = apiService;
        this.userConfig = null; 
        this.autoClockOutTimer = null;
        this.onAutoClockOut = onAutoClockOut;
    }

    /**
     * Exposes the config to the Main Controller (main.js)
     * This is required for the "Working Hours Validation" check.
     */
    get config() {
        return this.userConfig;
    }

    async fetchAndStoreConfig(userId) {
        try {
            this.userConfig = await this.apiService.getUserConfig(userId);
            
            // Safety: Ensure object exists even if API fails silently
            if (!this.userConfig) this.userConfig = {};
            
            // Default to true if undefined to prevent lockout
            if (this.userConfig.isClockedIn === undefined) this.userConfig.isClockedIn = true;
            
            log.info('[Session] User configuration loaded:', this.userConfig);

            this.scheduleAutoClockOut();
            
            if (this.userConfig.isSuspended) {
                log.warn('[Session] Account suspended. Quitting.');
                dialog.showErrorBox('Account Suspended', 'Your account has been suspended. Contact administrator.');
                app.quit();
            }
        } catch (error) {
            log.error('[Session] Config fetch failed:', error.message);
        }
    }

    scheduleAutoClockOut() {
        // Always clear existing timer first
        this.cancelAutoClockOut();
        
        // Validation: Need config, an End Time, and user must be Clocked In
        if (!this.userConfig || !this.userConfig.endTime || this.userConfig.isClockedIn === false) return;
    
        const userTimeZone = 'Europe/Berlin'; 
        const now = new Date();
        const nowInUserTz = utcToZonedTime(now, userTimeZone); 
        
        // Parse "HH:MM:SS" from config
        const [hours, minutes, seconds] = this.userConfig.endTime.split(':').map(Number);
        
        // Create a Date object for Today at the Clock-Out Time
        const clockOutTimeInUserTz = new Date(nowInUserTz);
        clockOutTimeInUserTz.setHours(hours, minutes, seconds || 0, 0);
    
        // If it's already past the time, do nothing (or optionally auto-clock out immediately)
        if (nowInUserTz >= clockOutTimeInUserTz) {
            log.info('[Session] Current time is past scheduled clock-out time. Timer skipped.');
            return;
        }
    
        const delay = clockOutTimeInUserTz.getTime() - nowInUserTz.getTime();
        log.info(`[Session] Auto clock-out scheduled for ${this.userConfig.endTime} (in ~${Math.round(delay / 60000)} min).`);
    
        this.autoClockOutTimer = setTimeout(() => {
            log.info('[Session] Timer triggered. Executing auto clock-out.');
            if (this.onAutoClockOut) this.onAutoClockOut();
        }, delay);
    }
    
    cancelAutoClockOut() {
        if (this.autoClockOutTimer) {
            clearTimeout(this.autoClockOutTimer);
            this.autoClockOutTimer = null;
        }
    }
}

module.exports = SessionManager;