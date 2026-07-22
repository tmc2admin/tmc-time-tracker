// renderer.js - Fixed Break Reason Logic

const AppState = {
    isClockedIn: false,
    isBreakActive: false,
    currentBreakId: null, // This tracks the active break ID
    isIdleActive: false,
    clockInTimestamp: null,
    currentLanguage: 'de',
    durationInterval: null,
    timeOffset: 0,
    startOfDayUtcTimestamp: null,
    isFirstLoad: true
};

let ui = {};

const translations = {
    en: { 
        workTracker: "Time-Tracker",
        welcome: "Welcome,", 
        activityTimeline: "Activity Timeline", 
        status: "Status:", 
        date: "Date:", 
        clockIn: "Clock In", 
        clockOut: "Clock Out", 
        status_Active: "Active", 
        status_OnBreak: "On Break", 
        status_Idle: "Idle", 
        status_Inactive: "Inactive", 
        liveDashboard: "Live Dashboard",
        error_E_CLOCK_IN_OUTSIDE_HOURS: "Clock-in is only allowed between {{startTime}} and {{endTime}}.",
        error_E_SESSION_NOT_READY: "Session manager is not ready. Please restart the application.",
        error_E_UNKNOWN: "An unknown error occurred. Please try again.",
        error_E_DEVICE_LOCKED: "This account is locked to a different device. Please contact your administrator.",
        breakModalTitle: "Break Justification",
        breakModalMessage: "Your break exceeded the configured limit. Please provide a reason.",
        breakModalSubmit: "Submit",
        breakModalCancel: "Cancel",
        breakModalPlaceholder: "e.g., Extended lunch...",
        signedOut: "Signed out",
        signInDifferent: "Sign in with another account",
        accountMenu: "Account"
    },
    de: { 
        workTracker: "Zeiterfassung",
        welcome: "Willkommen,", 
        activityTimeline: "Aktivitäts-Zeitleiste", 
        status: "Status:", 
        date: "Datum:", 
        clockIn: "Einstempeln", 
        clockOut: "Ausstempeln", 
        status_Active: "Aktiv", 
        status_OnBreak: "In Pause", 
        status_Idle: "Inaktiv", 
        status_Inactive: "Inaktiv", 
        liveDashboard: "Live-Dashboard",
        error_E_CLOCK_IN_OUTSIDE_HOURS: "Einstempeln ist nur zwischen {{startTime}} und {{endTime}} erlaubt.",
        error_E_SESSION_NOT_READY: "Sitzungsmanager ist nicht bereit. Bitte starten Sie die Anwendung neu.",
        error_E_UNKNOWN: "Ein unbekannter Fehler ist aufgetreten. Bitte versuchen Sie es erneut.",
        error_E_DEVICE_LOCKED: "Dieses Konto ist an ein anderes Gerät gebunden. Bitte kontaktieren Sie Ihren Administrator.",
        breakModalTitle: "Pausenbegründung",
        breakModalMessage: "Ihre Pause hat das konfigurierte Limit überschritten. Bitte geben Sie einen Grund an.",
        breakModalSubmit: "Senden",
        breakModalCancel: "Abbrechen",
        breakModalPlaceholder: "z.B. Verlängerte Mittagspause...",
        signedOut: "Abgemeldet",
        signInDifferent: "Mit anderem Benutzer anmelden",
        accountMenu: "Konto"
    },
};

function getTranslation(key, params = {}) {
    let text = translations[AppState.currentLanguage]?.[key] || translations['en']?.[key] || key;
    if (params) {
        Object.keys(params).forEach(pKey => {
            text = text.replace(new RegExp(`{{${pKey}}}`, 'g'), params[pKey]);
        });
    }
    return text;
}

function translatePage() {
    document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = getTranslation(el.getAttribute('data-i18n')); });
    document.querySelectorAll('[data-i18n-prefix]').forEach(el => { 
        if(el.childNodes[0]) el.childNodes[0].nodeValue = `${getTranslation(el.getAttribute('data-i18n-prefix'))} `; 
    });
    if (ui.breakReasonInput) {
        ui.breakReasonInput.placeholder = getTranslation('breakModalPlaceholder');
    }
    if (ui.accountMenuBtn && !ui.accountMenuBtn.dataset.accountEmail) {
        ui.accountMenuBtn.title = getTranslation('accountMenu');
        ui.accountMenuBtn.setAttribute('aria-label', getTranslation('accountMenu'));
    }
}

function handleAuthState(authState) {
    if (!authState) return;

    const isAuthenticating = authState.status === 'authenticating';
    const isSignedOut = authState.status === 'signed-out';
    ui.signedOutState?.classList.toggle('hidden', !isSignedOut);

    if (isAuthenticating) {
        ui.appContainer?.classList.add('hidden');
        ui.loadingIndicator?.classList.remove('hidden');
        if (ui.loadingMessage) ui.loadingMessage.textContent = 'Single Sign-On...';
    } else if (isSignedOut) {
        ui.appContainer?.classList.add('hidden');
        ui.loadingIndicator?.classList.add('hidden');
    } else {
        ui.appContainer?.classList.remove('hidden');
        ui.loadingIndicator?.classList.add('hidden');
    }

    if (authState.status === 'authenticated' && ui.accountMenuBtn) {
        const accountLabel = [authState.displayName, authState.email].filter(Boolean).join(' - ');
        ui.accountMenuBtn.dataset.accountEmail = authState.email || '';
        ui.accountMenuBtn.title = accountLabel || getTranslation('accountMenu');
        ui.accountMenuBtn.setAttribute('aria-label', ui.accountMenuBtn.title);
    } else if (ui.accountMenuBtn) {
        delete ui.accountMenuBtn.dataset.accountEmail;
        ui.accountMenuBtn.title = getTranslation('accountMenu');
        ui.accountMenuBtn.setAttribute('aria-label', ui.accountMenuBtn.title);
    }
}

function formatDuration(totalSeconds) {
    totalSeconds = Math.max(0, totalSeconds || 0);
    const h = Math.floor(totalSeconds / 3600).toString().padStart(2, '0');
    const m = Math.floor((totalSeconds % 3600) / 60).toString().padStart(2, '0');
    const s = Math.floor(totalSeconds % 60).toString().padStart(2, '0');
    return `${h}:${m}:${s}`;
}

function parseISODate(isoStr) {
    return isoStr ? new Date(isoStr) : null;
}

function showMessage(message, type = 'info', duration = 3000) {
    if (!ui.messageBox) return;
    ui.messageBox.textContent = message;
    ui.messageBox.classList.remove('bg-red-800', 'bg-slate-800', 'opacity-0');
    ui.messageBox.classList.add(type === 'error' ? 'bg-red-800' : 'bg-slate-800');
    setTimeout(() => { ui.messageBox?.classList.add('opacity-0'); }, duration);
}

// ----------------------------------------------------------------------------
// Timeline & Progress Bar Logic
// ----------------------------------------------------------------------------
function updateProgressBar(segments, clockInDate) {
    const track = document.getElementById('progress-track-container');
    if (!track) return;
    
    if (!clockInDate || isNaN(clockInDate.getTime())) {
        track.innerHTML = '';
        return;
    }

    const now = new Date(new Date().getTime() - AppState.timeOffset);
    const scaleStart = new Date(clockInDate);
    scaleStart.setMinutes(0, 0, 0);

    const scaleEnd = new Date(now);
    if (scaleEnd.getMinutes() > 0 || scaleEnd.getSeconds() > 0) {
        scaleEnd.setHours(scaleEnd.getHours() + 1);
    }
    scaleEnd.setMinutes(0, 0, 0);

    if (scaleEnd <= scaleStart) scaleEnd.setTime(scaleStart.getTime() + 3600000);

    const totalScaleDuration = scaleEnd - scaleStart; 
    const baseStartPct = Math.max(0, ((clockInDate - scaleStart) / totalScaleDuration) * 100);
    const baseWidthPct = Math.min(100, ((now - clockInDate) / totalScaleDuration) * 100);

    let html = `
        <div class="absolute top-0 h-full rounded-[6px]" 
             style="left: ${baseStartPct}%; width: ${baseWidthPct}%; background: linear-gradient(to bottom, #2ecc71, #27ae60); box-shadow: 0 0 8px rgba(46, 204, 113, 0.3); z-index: 1;">
        </div>
    `;

    if (segments && segments.length > 0) {
        segments.forEach(seg => {
            if (seg.status !== 'On Break') return;
            const start = parseISODate(seg.start_time);
            const end = seg.end_time ? parseISODate(seg.end_time) : now;
            
            if (start && end) {
                const segDuration = end - start;
                const leftPercent = ((start - scaleStart) / totalScaleDuration) * 100;
                const widthPercent = (segDuration / totalScaleDuration) * 100;

                html += `
                    <div class="absolute top-0 h-full" 
                         style="left: ${leftPercent}%; width: ${widthPercent}%; background: linear-gradient(to bottom, #f39c12, #d35400); box-shadow: 0 0 5px rgba(243, 156, 18, 0.5); z-index: 2;">
                    </div>
                `;
            }
        });
    }

    html += `
        <div class="absolute top-0 left-0 w-full h-1/2 rounded-t-[6px] pointer-events-none" 
             style="background: linear-gradient(to bottom, rgba(255,255,255,0.4) 0%, rgba(255,255,255,0.1) 80%, rgba(255,255,255,0) 100%); z-index: 10;">
        </div>
    `;
    track.innerHTML = html;
}

function updateTimeLabels(clockInDate) {
    const container = document.getElementById('time-labels-container');
    if (!container) return;
    
    if (!clockInDate || isNaN(clockInDate.getTime())) {
        container.innerHTML = '';
        return;
    }

    const now = new Date(new Date().getTime() - AppState.timeOffset);
    const scaleStart = new Date(clockInDate);
    scaleStart.setMinutes(0, 0, 0);

    const scaleEnd = new Date(now);
    if (scaleEnd.getMinutes() > 0 || scaleEnd.getSeconds() > 0) scaleEnd.setHours(scaleEnd.getHours() + 1);
    scaleEnd.setMinutes(0, 0, 0);
    
    if (scaleEnd <= scaleStart) scaleEnd.setTime(scaleStart.getTime() + 3600000);

    let labels = [];
    let loopTime = new Date(scaleStart);
    
    while (loopTime <= scaleEnd) {
        labels.push(loopTime.getHours().toString());
        loopTime.setHours(loopTime.getHours() + 1);
    }
    container.innerHTML = labels.map(l => `<span>${l}</span>`).join('');
}

function updateTimers() {
    const now = new Date(new Date().getTime() - AppState.timeOffset);
    const sessionSeconds = AppState.clockInTimestamp ? (now - AppState.clockInTimestamp) / 1000 : 0;
    if (ui.digitalClockDisplay) {
        ui.digitalClockDisplay.textContent = formatDuration(sessionSeconds);
    }
}

// --- Main UI Update Logic ---
function updateUI(data) {
    if (!data || data.error) return;

    // --- Hide Loading Screen on First Data ---
    if (AppState.isFirstLoad) {
        ui.appContainer?.classList.remove('hidden');
        ui.loadingIndicator?.classList.add('hidden');
        AppState.isFirstLoad = false;
        console.log('[Renderer] First data received. UI Unlocked.');
    }

    AppState.timeOffset = new Date().getTime() - new Date(data.server_time_utc).getTime();
    AppState.startOfDayUtcTimestamp = data.start_of_day_utc_timestamp; 
    AppState.isClockedIn = data.is_clocked_in;
    AppState.isBreakActive = data.is_break_active;
    AppState.isIdleActive = data.is_idle_active;
    AppState.clockInTimestamp = parseISODate(data.clock_in_time);
    
    // --- CRITICAL FIX 1: Capture Break ID from Server Response ---
    if (data.active_break_id) {
        AppState.currentBreakId = data.active_break_id;
    } else if (data.status !== 'On Break') {
        // Clear if we are definitely not on break (status check needed to avoid clearing too early)
        AppState.currentBreakId = null;
    }

    if (ui.userDisplayName) ui.userDisplayName.textContent = data.user_display_name || 'User';

    const statusMap = { 
        'Active':   { key: 'status_Active',   colorClass: 'bg-[#2ecc71]' },
        'On Break': { key: 'status_OnBreak',  colorClass: 'bg-orange-500' },
        'Idle':     { key: 'status_Idle',     colorClass: 'bg-blue-500' },
        'Offline':  { key: 'status_Inactive', colorClass: 'bg-blue-500' }
    };
    const currentStatus = statusMap[data.status] || statusMap['Offline'];
    
    if (ui.clockStatus) ui.clockStatus.textContent = getTranslation(currentStatus.key);
    if (ui.clockStatusDot) {
        ui.clockStatusDot.className = `w-2 h-2 rounded-full mr-2 ${currentStatus.colorClass}`;
    }
    
    const baseClasses = "w-7 h-7 rounded-full flex items-center justify-center shadow-lg transition-all ease-in-out text-white";
    if (AppState.isClockedIn) {
        ui.playIcon?.classList.add('hidden');
        ui.stopIcon?.classList.remove('hidden');
        if (ui.clockToggleBtn) {
            ui.clockToggleBtn.className = `${baseClasses} bg-red-600 hover:bg-red-700`;
            ui.clockToggleBtn.disabled = false;
        }
    } else {
        ui.playIcon?.classList.remove('hidden');
        ui.stopIcon?.classList.add('hidden');
        if (ui.clockToggleBtn) {
            ui.clockToggleBtn.className = `${baseClasses} bg-blue-600 hover:bg-blue-700`;
            ui.clockToggleBtn.disabled = !data.can_clock_in;
        }
    }

    if(data.daily_summary && ui.sessionDate) {
        const localeMap = { en: 'en-US', de: 'de-DE' };
        ui.sessionDate.textContent = new Date().toLocaleDateString(localeMap[AppState.currentLanguage] || 'default');
    }

    clearInterval(AppState.durationInterval);
    updateTimers(); 
    
    let timelineStart = AppState.clockInTimestamp;
    if (data.session_segments && data.session_segments.length > 0) {
        const firstSeg = data.session_segments[0];
        if (firstSeg && firstSeg.start_time) timelineStart = parseISODate(firstSeg.start_time);
    }

    if (AppState.isClockedIn) {
        AppState.durationInterval = setInterval(updateTimers, 1000);
        updateProgressBar(data.session_segments, timelineStart);
        updateTimeLabels(timelineStart);
    } else {
        updateProgressBar([], null);
        updateTimeLabels(null);
    }
}

// --- Event Listeners ---
function setupEventListeners() {
    ui.retryBtn?.addEventListener('click', () => {
        ui.errorState?.classList.add('hidden');
        ui.loadingState?.classList.remove('hidden');
        if(ui.loadingMessage) ui.loadingMessage.textContent = 'Retrying connection...';
        window.electronAPI.retryStartup();
    });

    ui.exitBtn?.addEventListener('click', () => window.electronAPI.quitApp());
    ui.minimizeBtn?.addEventListener('click', () => window.electronAPI.minimizeApp());

    ui.clockToggleBtn?.addEventListener('click', async () => {
        const action = AppState.isClockedIn ? 'clock_out' : 'clock_in';
        let payload = {};
        if (action === 'clock_in') {
            try {
                const appVersion = await window.electronAPI.getAppVersion();
                payload.client_version = appVersion;
            } catch (error) { console.error('Version check failed', error); }
        }
        window.electronAPI.sendClockAction(action, payload);
    });
    
    ui.openDashboardBtn?.addEventListener('click', () => window.electronAPI.openDashboardWindow());
    ui.accountMenuBtn?.addEventListener('click', () => window.electronAPI.showAccountMenu());
    ui.manualSignInBtn?.addEventListener('click', () => window.electronAPI.signInWithOtherAccount());

    ui.languageToggleWrapper?.addEventListener('click', () => {
        const newLang = AppState.currentLanguage === 'de' ? 'en' : 'de';
        AppState.currentLanguage = newLang;
        window.electronAPI.setLanguage(newLang);
        
        // Immediate UI Update
        if (newLang === 'de') {
            ui.langDeBtn?.classList.remove('hidden');
            ui.langEnBtn?.classList.add('hidden');
            if(ui.langLabel) ui.langLabel.textContent = 'DE';
        } else {
            ui.langDeBtn?.classList.add('hidden');
            ui.langEnBtn?.classList.remove('hidden');
            if(ui.langLabel) ui.langLabel.textContent = 'EN';
        }
        translatePage();
    });

    ui.breakReasonForm?.addEventListener('submit', (e) => {
        e.preventDefault();
        // --- CRITICAL FIX 2: Fallback to AppState ID if hidden input is empty ---
        const breakIdToSubmit = ui.breakIdInput.value || AppState.currentBreakId;
        
        if (breakIdToSubmit) {
            window.electronAPI.submitBreakReason(breakIdToSubmit, ui.breakReasonInput.value);
            if(ui.breakReasonInput) ui.breakReasonInput.value = '';
            ui.breakReasonModal?.classList.add('hidden');
        } else {
            console.error("Cannot submit break reason: No active break ID found.");
            // Optional: Show error to user or close modal anyway
            ui.breakReasonModal?.classList.add('hidden');
        }
    });
    ui.cancelBreakReason?.addEventListener('click', () => {
        if(ui.breakReasonInput) ui.breakReasonInput.value = '';
        ui.breakReasonModal?.classList.add('hidden');
    });
}

// --- IPC Handlers ---
function setupIpcHandlers() {
    // THIS IS THE MAIN DATA PIPE
    window.electronAPI.onDashboardDataUpdate((data) => { 
        if (data && !data.error) {
            updateUI(data); 
        }
    });
    
    window.electronAPI.onInitialDataReady(() => {
        // Fallback: If this event is sent, respect it
        if (AppState.isFirstLoad) {
            ui.appContainer?.classList.remove('hidden');
            ui.loadingIndicator?.classList.add('hidden');
            AppState.isFirstLoad = false;
        }
    });

    window.electronAPI.onPromptBreakReason(({ breakId }) => {
        if(ui.breakIdInput) ui.breakIdInput.value = breakId;
        // Also sync state for safety
        AppState.currentBreakId = breakId; 
        ui.breakReasonModal?.classList.remove('hidden');
    });

    window.electronAPI.onStartupError((errorMessage) => {
        ui.loadingState?.classList.add('hidden');
        let displayMessage = errorMessage || 'An unknown error occurred.';
        if (errorMessage.includes("locked to a different device")) {
            displayMessage = getTranslation('error_E_DEVICE_LOCKED');
        }
        if(ui.errorMessage) ui.errorMessage.textContent = displayMessage;
        ui.errorState?.classList.remove('hidden');
    });

    window.electronAPI.onAuthStateChanged(handleAuthState);

    window.electronAPI.onClockInError((errorData) => {
        const translationKey = `error_${errorData.key}`;
        const message = getTranslation(translationKey, errorData.data);   
        showMessage((message === translationKey) ? getTranslation('error_E_UNKNOWN') : message, 'error', 5000);
    });
}

// --- Initialization ---
function initializeApp() {
    console.log('[Renderer] initializeApp called.');

    ui = {
        loadingIndicator: document.getElementById('loading-indicator'),
        loadingMessage: document.getElementById('loadingMessage'),
        appContainer: document.getElementById('app-container'),
        clockToggleBtn: document.getElementById('clockToggleBtn'),
        clockStatus: document.getElementById('clockStatus'),
        clockStatusDot: document.getElementById('clockStatusDot'),
        digitalClockDisplay: document.getElementById('digitalClockDisplay'),
        appVersionDisplay: document.getElementById('appVersionDisplay'),
        playIcon: document.getElementById('play-icon'),
        stopIcon: document.getElementById('stop-icon'),
        languageToggleWrapper: document.getElementById('languageToggleWrapper'),
        langDeBtn: document.getElementById('langDeBtn'),
        langEnBtn: document.getElementById('langEnBtn'),
        langLabel: document.getElementById('langLabel'),
        sessionDate: document.getElementById('sessionDate'), 
        userDisplayName: document.getElementById('userDisplayName'),
        messageBox: document.getElementById('messageBox'),
        breakReasonModal: document.getElementById('breakReasonModal'),
        breakReasonForm: document.getElementById('breakReasonForm'),
        breakIdInput: document.getElementById('breakIdInput'),
        breakReasonInput: document.getElementById('breakReasonInput'),
        cancelBreakReason: document.getElementById('cancelBreakReason'),
        submitBreakReason: document.getElementById('submitBreakReason'),
        errorState: document.getElementById('error-state'),
        loadingState: document.getElementById('loading-state'),
        errorMessage: document.getElementById('errorMessage'),
        retryBtn: document.getElementById('retry-btn'),
        exitBtn: document.getElementById('exit-btn'),
        openDashboardBtn: document.getElementById('openDashboardBtn'),
        minimizeBtn: document.getElementById('minimizeBtn'),
        accountMenuBtn: document.getElementById('accountMenuBtn'),
        signedOutState: document.getElementById('signed-out-state'),
        manualSignInBtn: document.getElementById('manual-sign-in-btn')
    };

    if (typeof window.electronAPI === 'undefined') {
        const errMsg = 'Error: Preload script failed to load electronAPI.';
        console.error(errMsg);
        if(ui.loadingMessage) ui.loadingMessage.textContent = errMsg;
        return;
    }

    try {
        ui.appContainer?.classList.add('hidden');
        ui.loadingIndicator?.classList.remove('hidden');

        if (AppState.currentLanguage === 'de') {
            ui.langDeBtn?.classList.remove('hidden');
            ui.langEnBtn?.classList.add('hidden');
            if(ui.langLabel) ui.langLabel.textContent = 'DE';
        } else {
            ui.langDeBtn?.classList.add('hidden');
            ui.langEnBtn?.classList.remove('hidden');
            if(ui.langLabel) ui.langLabel.textContent = 'EN';
        }
        
        translatePage();
        setupEventListeners();
        setupIpcHandlers();

        window.electronAPI.setLanguage(AppState.currentLanguage);

        window.electronAPI.getAppVersion()
            .then(version => {
                if (ui.appVersionDisplay) ui.appVersionDisplay.textContent = `v${version}`;
            })
            .catch(err => console.error('Could not get app version', err));

        console.log('[Renderer] Signaling renderer-ready...');
        window.electronAPI.rendererReady();

    } catch (error) {
        console.error('[Renderer] Critical Error during initialization:', error);
        alert(`Renderer Initialization Error: ${error.message}`);
    }
}

document.addEventListener('DOMContentLoaded', initializeApp);
