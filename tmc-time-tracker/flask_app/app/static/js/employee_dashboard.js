/**
 * Employee Dashboard Logic
 */

class EmployeeDashboard {
    constructor(config) {
        this.config = config;
        this.picker = null;
        this.refreshInterval = null;
        this.timerInterval = null;

        this.dom = {
            // KPIs
            status: document.getElementById('display-status'),
            dot: document.getElementById('status-dot'),
            timer: document.getElementById('timer-duration'),
            productive: document.getElementById('display-productive'),
            gross: document.getElementById('display-gross'),
            break: document.getElementById('display-break'),
            expectedEnd: document.getElementById('display-expected-end'),
            
            // Tabs
            tabs: document.querySelectorAll('.tab-link'),
            tabContents: document.querySelectorAll('.tab-content'),
            
            // Filter
            dateRange: document.getElementById('filter-date-range'),
            
            // Modals
            btnOt: document.getElementById('btn-open-ot'),
            btnLeave: document.getElementById('btn-open-leave'),
            modalOt: document.getElementById('modal-ot'),
            modalLeave: document.getElementById('modal-leave'),
            closeButtons: document.querySelectorAll('.js-close-modal'),
            formOt: document.getElementById('form-ot'),
            formLeave: document.getElementById('form-leave'),
        };

        this.init();
    }

    init() {
        this.initTabs();
        this.initDatePicker();
        this.initModals();
        this.startLiveUpdates();
        
        // Initial load of daily report for today
        const today = new Date().toISOString().split('T')[0];
        this.fetchDailyReport(today, today);
    }

    // --- Tabs ---
    initTabs() {
        this.dom.tabs.forEach(tab => {
            tab.addEventListener('click', (e) => {
                e.preventDefault();
                // Reset styling
                this.dom.tabs.forEach(t => t.className = 'tab-link border-b-2 font-medium text-sm py-3 px-1 text-gray-500 border-transparent hover:text-gray-700 hover:border-gray-300');
                this.dom.tabContents.forEach(c => c.classList.add('hidden'));
                
                // Activate
                e.target.className = 'tab-link active border-b-2 font-medium text-sm py-3 px-1 text-indigo-600 border-indigo-500';
                document.getElementById(e.target.dataset.target).classList.remove('hidden');
            });
        });
    }

    // --- Date Picker ---
    initDatePicker() {
        this.picker = new Litepicker({
            element: this.dom.dateRange,
            singleMode: false,
            format: 'DD.MM.YYYY',
            setup: (picker) => {
                picker.on('selected', (date1, date2) => {
                    const d1 = date1.format('YYYY-MM-DD');
                    const d2 = date2.format('YYYY-MM-DD');
                    this.fetchDailyReport(d1, d2);
                    // Add fetch calls for Leave and Overtime history here if needed
                });
            }
        });
    }

    // --- Real-Time Data ---
    startLiveUpdates() {
        this.updateDashboard(); // Immediate
        this.refreshInterval = setInterval(() => this.updateDashboard(), 10000); // Poll API every 10s
        this.timerInterval = setInterval(() => this.incrementTimer(), 1000); // Local ticker
    }

    async updateDashboard() {
        try {
            const res = await fetch(this.config.api.dashboardData);
            if (!res.ok) return;
            const data = await res.json();
            
            // 1. Status
            const isActive = !!data.active_entry_id;
            this.dom.status.textContent = isActive ? this.config.i18n.active : this.config.i18n.offline;
            this.dom.status.className = isActive ? 'text-2xl font-bold text-green-600' : 'text-2xl font-bold text-gray-400';
            this.dom.dot.className = isActive ? 'h-3 w-3 rounded-full bg-green-500 animate-pulse' : 'h-3 w-3 rounded-full bg-gray-300';

            // 2. Expected End
            this.dom.expectedEnd.textContent = data.expected_clock_out || '--:--';

            // 3. Stats (Convert decimals to HH:MM)
            this.dom.productive.textContent = this.formatDecimal(data.net_worked_hours);
            this.dom.gross.textContent = this.formatDuration(data.total_gross_duration_seconds);
            this.dom.break.textContent = this.formatDuration(data.total_non_productive_seconds);

            // 4. Handle Timer
            if (isActive && data.current_session_clock_in_time) {
                const startTime = new Date(data.current_session_clock_in_time);
                this.dom.timer.dataset.start = startTime.getTime();
            } else {
                delete this.dom.timer.dataset.start;
                this.dom.timer.textContent = '00:00:00';
            }

        } catch (e) { console.error("Dashboard sync error", e); }
    }

    incrementTimer() {
        const startTimestamp = this.dom.timer.dataset.start;
        if (!startTimestamp) return;
        
        const now = new Date().getTime();
        const diff = Math.floor((now - parseInt(startTimestamp)) / 1000);
        this.dom.timer.textContent = this.formatDuration(diff, true);
    }

    // --- Reports ---
    async fetchDailyReport(start, end) {
        const tbody = document.getElementById('tbody-timesheet');
        const msg = document.getElementById('msg-no-timesheet');
        tbody.innerHTML = '';
        
        try {
            const url = `${this.config.api.dailyReport}?start_date=${start}&end_date=${end}`;
            const res = await fetch(url);
            if(!res.ok) throw new Error();
            const data = await res.json();

            if (data.length === 0) {
                msg.classList.remove('hidden');
                return;
            }
            msg.classList.add('hidden');

            data.sort((a,b) => new Date(b.date.split('.').reverse().join('-')) - new Date(a.date.split('.').reverse().join('-')));

            data.forEach(row => {
                tbody.innerHTML += `
                    <tr>
                        <td class="py-3 px-3 font-mono text-gray-800">${row.date}</td>
                        <td class="py-3 px-3 text-gray-500">${row.clock_in}</td>
                        <td class="py-3 px-3 text-gray-500">${row.clock_out}</td>
                        <td class="py-3 px-3 text-gray-700">${this.formatDecimal(row.gross_session_hours)}</td>
                        <td class="py-3 px-3 font-bold text-gray-800">${this.formatDecimal(row.net_hours_worked)}</td>
                        <td class="py-3 px-3 text-gray-500">${this.formatDecimal(row.break_hours)}</td>
                    </tr>
                `;
            });

        } catch (e) { console.error("Report fetch error", e); }
    }

    // --- Modals & Forms ---
    initModals() {
        const toggle = (el, show) => show ? el.classList.remove('hidden') : el.classList.add('hidden');
        
        this.dom.btnOt.addEventListener('click', () => {
            toggle(this.dom.modalOt, true);
            document.getElementById('ot-date').valueAsDate = new Date();
        });
        
        this.dom.btnLeave.addEventListener('click', () => toggle(this.dom.modalLeave, true));
        
        this.dom.closeButtons.forEach(btn => {
            btn.addEventListener('click', () => {
                toggle(this.dom.modalOt, false);
                toggle(this.dom.modalLeave, false);
            });
        });

        // Submit Overtime (MUST BE JSON)
        this.dom.formOt.addEventListener('submit', async (e) => {
            e.preventDefault();
            const payload = {
                date: document.getElementById('ot-date').value,
                start_time: document.getElementById('ot-start').value,
                end_time: document.getElementById('ot-end').value,
                reason: document.getElementById('ot-reason').value
            };
            // Pass true for isJson
            await this.submitForm(this.config.api.requestOvertime, payload, this.dom.modalOt, true);
        });

        // Submit Leave (MUST BE FORM DATA)
        this.dom.formLeave.addEventListener('submit', async (e) => {
            e.preventDefault();
            const payload = {
                start_date: document.getElementById('leave-start').value,
                end_date: document.getElementById('leave-end').value,
                reason: document.getElementById('leave-reason').value
            };
            // Pass false for isJson
            await this.submitForm(this.config.api.requestLeave, payload, this.dom.modalLeave, false);
        });
    }

    /**
     * Handles submission for both JSON and Form Data endpoints
     */
    async submitForm(url, data, modal, isJson = true) {
        // Validation: ensure no fields are empty
        if(Object.values(data).some(v => !v)) {
            alert(this.config.i18n.fillAll);
            return;
        }

        try {
            const options = { method: 'POST' };

            if (isJson) {
                // For endpoints expecting JSON (e.g., request_overtime)
                options.headers = { 'Content-Type': 'application/json' };
                options.body = JSON.stringify(data);
            } else {
                // For endpoints expecting Form Data (e.g., request_leave)
                const formData = new URLSearchParams();
                for (const key in data) formData.append(key, data[key]);
                options.headers = { 'Content-Type': 'application/x-www-form-urlencoded' };
                options.body = formData;
            }

            const res = await fetch(url, options);

            // Handle redirection (common with Form Data endpoints)
            if (res.redirected) {
                window.location.href = res.url;
                return;
            }

            // Handle JSON responses
            const contentType = res.headers.get("content-type");
            if (contentType && contentType.indexOf("application/json") !== -1) {
                const result = await res.json();
                if (res.ok) {
                    alert(result.message || this.config.i18n.success);
                    modal.classList.add('hidden');
                    window.location.reload(); 
                } else {
                    alert(result.message || this.config.i18n.error);
                }
            } else {
                // Fallback for success without JSON response
                if (res.ok) {
                    window.location.reload();
                } else {
                    alert(this.config.i18n.error);
                }
            }

        } catch (e) {
            console.error(e);
            alert(this.config.i18n.error);
        }
    }

    // --- Helpers ---
    formatDuration(s, includeSeconds=false) {
        s = Math.max(0, s || 0);
        const h = Math.floor(s / 3600).toString().padStart(2, '0');
        const m = Math.floor((s % 3600) / 60).toString().padStart(2, '0');
        if(includeSeconds) {
            const sec = Math.floor(s % 60).toString().padStart(2, '0');
            return `${h}:${m}:${sec}`;
        }
        return `${h}:${m}`;
    }

    formatDecimal(val) {
            const num = parseFloat(val);
            if (isNaN(num)) return '00:00';

            const totalMin = Math.round(num * 60);   // ← CHANGE HERE
            const h = Math.floor(totalMin / 60).toString().padStart(2, '0');
            const m = (totalMin % 60).toString().padStart(2, '0');
            return `${h}:${m}`;
        }

}

document.addEventListener('DOMContentLoaded', () => {
    window.employeeApp = new EmployeeDashboard(window.DashboardConfig);
});