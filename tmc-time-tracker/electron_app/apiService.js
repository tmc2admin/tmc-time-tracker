// apiService.js - Updated for modularized API routes

const axios = require('axios');
const log = require('electron-log');
const Store = require('electron-store');
const configStore = new Store({ name: 'app-config' });

class ApiService {
    constructor(baseURL) {
        if (!baseURL || typeof baseURL !== 'string' || !baseURL.startsWith('http')) {
            log.error(`[API] CRITICAL: Invalid baseURL received: ${baseURL}`);
            this.invalidConfiguration = true;
            this.baseURL = null;
        } else {
            this.baseURL = baseURL;
            this.invalidConfiguration = false;
        }

        this.deviceId = null;

        this.axiosInstance = axios.create({
            baseURL: this.baseURL,
            timeout: 10000,
            headers: { 'Content-Type': 'application/json' }
        });

        // Response Interceptor for Logging
        this.axiosInstance.interceptors.response.use(
            (response) => response,
            (error) => {
                const method = error.config?.method?.toUpperCase() || 'UNKNOWN';
                const url = error.config?.url || 'UNKNOWN';
                
                if (error.response) {
                    if (error.response.status !== 401) {
                         log.error(`[API] ${method} ${url} failed (${error.response.status})`);
                    }
                } else if (error.request) {
                    log.warn(`[API] Network unreachable for ${method} ${url}`);
                } else {
                    log.error('[API] Request setup error:', error.message);
                }
                return Promise.reject(error);
            }
        );
    }

    setDeviceId(id) {
        this.deviceId = id;
        log.info(`[API] Device ID set to: ${id}`);
    }

    async _request(method, endpoint, data = {}, params = {}) {
        if (this.invalidConfiguration) {
            throw new Error('Server URL is not configured. Check .env file.');
        }

        const customHeaders = {};

        if (this.deviceId) {
            customHeaders['X-Device-ID'] = this.deviceId;
            if (method.toLowerCase() === 'post' || method.toLowerCase() === 'put') {
                if (data && typeof data === 'object') {
                    data.device_id = this.deviceId;
                }
            }
        }

        try {
            const response = await this.axiosInstance({
                method,
                url: endpoint,
                data,
                params,
                headers: customHeaders
            });
            return response.data;
        } catch (error) {
            if (error.code && ['ECONNREFUSED', 'ENOTFOUND', 'ETIMEDOUT'].includes(error.code) || !error.response) {
                if (error.message && error.message.includes('Invalid URL')) {
                    throw new Error('Configuration Error: The server URL is invalid.');
                }
                throw new Error('Network error: Unable to connect to server.');
            }
            throw new Error(error.response?.data?.message || error.message);
        }
    }

    // --- User & Config (api_user) ---

    getUserConfig(userId) {
        return this._request('get', `/api/v1/config/${userId}`);
    }

    linkUser(microsoftOid, email, username, deviceId) {
        return this._request('post', '/api/electron_sso_login', { 
            microsoft_oid: microsoftOid, 
            email: email, 
            username: username,
            mac_address: deviceId 
        });
    }
    
    generateWebLoginToken(userId) {
        return this._request('post', `/api/generate-web-token`, { user_id: userId });
    }

    // --- Dashboard Data (api_time) ---

    getDashboardData(userId, lang = 'en') {
        return this._request('get', `/api/dashboard_data_for_electron/${userId}?lang=${lang}`);
    }

    // --- Clock In / Out (api_time) ---

    clockIn(microsoftOid, locationData = null, clientVersion = null) {
        return this._request('post', `/api/clock_in_for_electron/${microsoftOid}`, { 
            location: locationData,
            client_version: clientVersion 
        });
    }

    clockOut(microsoftOid, payload) {
        return this._request('post', `/api/clock_out_for_electron/${microsoftOid}`, payload);
    }

    // --- Breaks & Meetings (api_breaks) ---

    startBreak(microsoftOid) {
        return this._request('post', `/api/start_break_for_electron/${microsoftOid}`);
    }

    endBreak(microsoftOid) {
        return this._request('post', `/api/end_break_for_electron/${microsoftOid}`);
    }
    
    submitBreakReason(breakId, reason) {
        // This will work with both endpoints now (unified handler)
        return this._request('post', '/api/submit_break_reason/${breakId}', { 
            reason: reason 
        });
    }

    startMeeting(microsoftOid) {
        return this._request('post', `/api/start_meeting_for_electron/${microsoftOid}`);
    }

    endMeeting(microsoftOid) {
        return this._request('post', `/api/end_meeting_for_electron/${microsoftOid}`);
    }

    // --- Idle (api_activity) ---

    startIdle(microsoftOid) {
        return this._request('post', `/api/start_idle_entry/${microsoftOid}`);
    }

    endIdle(idleEntryId) {
        if (!idleEntryId || idleEntryId === 'null' || idleEntryId === 'undefined') {
            log.warn('[API] Attempted to end idle entry with invalid ID:', idleEntryId);
            return Promise.resolve(null); 
        }

        return this._request('post', `/api/end_idle_entry/${idleEntryId}`);
    }

    finalizeIdleReason(idleEntryId, reason) {
        return this._request('post', `/api/finalize_idle_reason/${idleEntryId}`, { reason });
    }

    // --- Activity & Usage (api_activity) ---

    recordApplicationUsage(usageData) {
        return this._request('post', '/api/record_application_usage', usageData);
    }

    recordActivity(eventType, userId) {
        return this._request('post', '/api/activity', { 
            type: eventType, 
            user_id: userId, 
            timestamp: new Date().toISOString() 
        });
    }

    sendHeartbeatPing(microsoftOid) {
        return this._request('post', `/api/heartbeat_ping/${microsoftOid}`, {});
    }

    getActivitySegments(userId, date) {
        return this._request('get', `/api/get_activity_segments/${userId}`, {}, { date });
    }

    getElectronActivitySegments(userId, startDate) {
        return this._request('get', `/api/electron/user_activity_segments`, {}, { 
            user_id: userId, 
            start_date: startDate 
        });
    }

    // --- Utilities ---

    async getGeolocation() {
        try {
            const ipResponse = await axios.get('https://api.ipify.org?format=json', { timeout: 3000 });
            const publicIp = ipResponse.data.ip;
            
            if (publicIp) {
                const locationResponse = await axios.get(`https://ipinfo.io/${publicIp}/json`, { timeout: 3000 });
                const locData = locationResponse.data;
                const locationInfo = { 
                    ip: publicIp, 
                    country: locData.country, 
                    city: locData.city, 
                    region: locData.region 
                };
                
                configStore.set('lastLocation', locationInfo);
                return locationInfo;
            }
        } catch (error) {
            log.warn('[Geolocation] Failed. Using cached location.', error.message);
        }
        return configStore.get('lastLocation', null);
    }
}

module.exports = ApiService;