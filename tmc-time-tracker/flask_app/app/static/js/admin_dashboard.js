/**
 * Admin Dashboard Logic
 * All JavaScript functionality from dashboard.html moved here
 */

// --- Chart Instances & Global State ---
let chartInstances = { historical: null, realtime: null, appUsage: null };
const REALTIME_REFRESH_INTERVAL_MS = 15000;
const STATUS_COLORS = { 
    'Active':'#22c55e', 
    'On Break':'#f97316', 
    'Idle':'#ef4444', 
    'In Meeting':'#4f46e5', 
    'Default': '#64748b' 
};

// --- Utility Functions ---
const formatDecimalHours = (h) => {
    if (isNaN(h) || h === null) return '00:00';
    const totalMinutes = Math.floor(h * 60);
    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;
    return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
};

const formatDuration = (s) => { 
    s = Math.max(0, s || 0); 
    const h = Math.floor(s / 3600).toString().padStart(2, '0');
    const m = Math.floor((s % 3600) / 60).toString().padStart(2, '0');
    const sec = Math.floor(s % 60).toString().padStart(2, '0'); 
    return `${h}:${m}:${sec}`; 
};

const parseISODate = (iso) => iso ? new Date(iso) : null;

const formatDateTime = (iso) => { 
    if(!iso || iso === 'Ongoing' || iso === 'null' || iso === 'undefined') return iso; 
    const d = parseISODate(iso); 
    return d ? d.toLocaleString('de-DE', { hour12: false, timeZone: 'Europe/Berlin' }) : 'Invalid'; 
};

const apiFetch = async (url, options = {}) => { 
    const r = await fetch(url, options); 
    if (!r.ok) {
        const errorData = await r.json().catch(() => ({ message: r.statusText }));
        throw new Error(errorData.message || `API request failed: ${r.status}`);
    }
    if (r.status === 204) return {};
    const contentType = r.headers.get("content-type");
    if (contentType && contentType.indexOf("application/json") !== -1) return r.json();
    return {};
};

// --- Debounce utility ---
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// --- Data Fetching & Rendering ---
async function refreshRealtimeData() {
    try {
        const [sessionsData, timelineData, summaryData] = await Promise.all([
            apiFetch(window.DashboardConfig.api.activeSessions),
            apiFetch(window.DashboardConfig.api.activeTimeline),
            apiFetch(window.DashboardConfig.api.dailySummary)
        ]);
        renderActiveSessions(sessionsData);
        renderDailySummary(summaryData);
        renderGanttChart('realtime', 'realtime-activity-timeline-canvas', timelineData.users, window.DashboardConfig.i18n.errorLoad);
    } catch (error) { 
        console.error('Error refreshing dashboard data:', error);
        renderGanttChart('realtime', 'realtime-activity-timeline-canvas', [], window.DashboardConfig.i18n.errorLoad);
    }
}

function renderDailySummary(data) {
    const tableBody = document.getElementById('daily-summary-table-body');
    const noActivityMessage = document.getElementById('no-daily-activity-message');
    const table = tableBody.closest('table');
    tableBody.innerHTML = '';
    const hasActivity = data.length > 0;
    table.classList.toggle('hidden', !hasActivity);
    noActivityMessage.classList.toggle('hidden', hasActivity);
    if (hasActivity) {
        data.forEach(row => {
            tableBody.innerHTML += `<tr>
                <td class="py-3 px-4 text-sm font-medium text-gray-900">${row.username}</td>
                <td class="py-3 px-4 text-sm text-gray-600">${formatDateTime(row.first_clock_in)}</td>
                <td class="py-3 px-4 text-sm text-gray-600">${formatDateTime(row.last_clock_out)}</td>
                <td class="py-3 px-4 text-sm font-mono">${formatDuration(row.gross_duration_seconds)}</td>
                <td class="py-3 px-4 text-sm font-mono text-yellow-600">${formatDuration(row.inactive_duration_seconds)}</td>
                <td class="py-3 px-4 text-sm font-bold text-gray-800 font-mono">${formatDuration(row.net_duration_seconds)}</td>
            </tr>`;
        });
    }
}

function renderActiveSessions(data) {
    document.getElementById('kpi-total-users').textContent = data.total_users || 0;
    document.getElementById('kpi-users-active').textContent = data.status_counts.Active || 0;
    document.getElementById('kpi-users-idle').textContent = data.status_counts['Idle'] || 0;
    document.getElementById('kpi-users-break').textContent = data.status_counts['On Break'] || 0;
    document.getElementById('kpi-users-inactive').textContent = data.inactive_users_count || 0;
    
    const tableBody = document.getElementById('active-users-table-body');
    const noUsersMessage = document.getElementById('no-active-users-message');
    const table = tableBody.closest('table');
    tableBody.innerHTML = '';
    const hasActiveUsers = data.active_sessions.length > 0;
    table.classList.toggle('hidden', !hasActiveUsers);
    noUsersMessage.classList.toggle('hidden', hasActiveUsers);
    if (hasActiveUsers) {
        data.active_sessions.forEach(s => {
            const statusClass = { 
                'Active': 'bg-green-100 text-green-800', 
                'In Meeting': 'bg-blue-100 text-blue-800', 
                'On Break': 'bg-yellow-100 text-yellow-800', 
                'Idle': 'bg-red-100 text-red-800' 
            }[s.status] || 'bg-gray-100 text-gray-800';
            tableBody.innerHTML += `<tr>
                <td class="py-3 px-4 text-sm font-medium text-gray-900">${s.username}</td>
                <td class="py-3 px-4 text-sm text-gray-600">${s.first_clock_in_time}</td>
                <td class="py-3 px-4 text-sm"><span class="px-2 py-1 inline-flex text-xs leading-5 font-semibold rounded-full ${statusClass}">${s.status}</span></td>
                <td class="py-3 px-4 text-sm text-gray-600">${s.location}</td>
                <td class="py-3 px-4 text-sm text-gray-500">${s.app_version}</td>
                <td class="py-3 px-4 text-sm text-gray-600 font-mono">${s.expected_clock_out}</td>
            </tr>`;
        });
    }
}

// --- Reports Functions ---
const fetchAndRenderAllReports = debounce(async function() {
    const userId = document.getElementById('report-user-select').value;
    let startDate = null, endDate = null;
    const picker = window.reportDatePicker;

    if (picker && typeof picker.getStartDate === 'function') {
        const sd = picker.getStartDate();
        const ed = picker.getEndDate();
        startDate = sd ? (sd.format ? sd.format('YYYY-MM-DD') : (sd.toISOString ? sd.toISOString().slice(0, 10) : null)) : null;
        endDate   = ed ? (ed.format ? ed.format('YYYY-MM-DD') : (ed.toISOString ? ed.toISOString().slice(0, 10) : null)) : null;
    } else {
        const raw = (document.getElementById('report-date-range').value || '').trim();
        const matches = raw.match(/\d{4}-\d{2}-\d{2}/g);
        if (matches && matches.length >= 2) {
            startDate = matches[0];
            endDate = matches[1];
        }
    }

    if (!startDate || !endDate) {
        document.getElementById('report-no-data-message').classList.remove('hidden');
        return;
    }

    const timelineTab = document.querySelector('[data-tab="timeline"]');
    const reportsContainer = document.getElementById('reports-container');
    if (reportsContainer) reportsContainer.style.opacity = '0.5';

    try {
        const promises = [
            fetchAndRenderActivityReport(userId, startDate, endDate),
            fetchAndRenderInactivityReport(userId, startDate, endDate),
            fetchAndRenderAppUsageReport(userId, startDate, endDate)
        ];

        if (startDate === endDate) {
            timelineTab.style.display = 'inline-block';
            promises.push(fetchAndRenderHistoricalTimeline(userId, startDate));
        } else {
            timelineTab.style.display = 'none';
            if (chartInstances.historical) { 
                chartInstances.historical.destroy(); 
                chartInstances.historical = null; 
            }
            document.getElementById('admin-activity-timeline-canvas').classList.add('hidden');
            document.getElementById('timeline-no-data-message').classList.remove('hidden');
        }

        const results = await Promise.all(promises);
        const anyHasContent = results.some(Boolean);
        document.getElementById('report-no-data-message').classList.toggle('hidden', anyHasContent);

    } catch (error) {
        console.error("Failed to render reports:", error);
        document.getElementById('report-no-data-message').classList.remove('hidden');
    } finally {
        if (reportsContainer) reportsContainer.style.opacity = '1';
    }
}, 300);

async function fetchAndRenderActivityReport(userId, startDate, endDate) {
    const tableBody = document.getElementById('report-table-body');
    const tableContainer = document.getElementById('tab-content-activity').querySelector('table');
    const noDataMessage = document.getElementById('report-no-data-message');

    tableBody.innerHTML = '';
    
    try {
        const reportData = await apiFetch(`${window.DashboardConfig.api.generateReport}?user_ids=${userId}&start_date=${startDate}&end_date=${endDate}&granularity=daily`);

        if (!reportData || reportData.length === 0) {
            tableContainer.classList.add('hidden');
            if (noDataMessage) noDataMessage.classList.remove('hidden');
            return false;
        }
        
        if (noDataMessage) noDataMessage.classList.add('hidden');
        tableContainer.classList.remove('hidden');
        
        const uniqueEntries = new Set();
        
        reportData.forEach(row => {
            const entryKey = `${row.Datum}-${row.Benutzername}`;
            
            if (!uniqueEntries.has(entryKey)) {
                uniqueEntries.add(entryKey);
                
                tableBody.innerHTML += `<tr>
                    <td class="py-3 px-4 text-sm">${row.Datum}</td>
                    <td class="py-3 px-4 text-sm font-medium text-gray-900">${row.Benutzername}</td>
                    <td class="py-3 px-4 text-sm text-gray-500">${row.ErsterLogin || 'N/A'}</td>
                    <td class="py-3 px-4 text-sm text-gray-500">${row.LetzterLogout || 'N/A'}</td>
                    <td class="py-3 px-4 text-sm font-mono">${formatDecimalHours(row.NettoStunden)}</td>
                    <td class="py-3 px-4 text-sm font-mono">${formatDuration(row.GesamtPause || 0)}</td>
                    <td class="py-3 px-4 text-sm font-mono">${formatDuration(row.GesamtLeerlauf || 0)}</td>
                </tr>`;
            }
        });
        
        return true;

    } catch (error) { 
        console.error('Error fetching activity report:', error); 
        tableContainer.classList.add('hidden');
        if (noDataMessage) {
             noDataMessage.textContent = `Error loading report: ${error.message}. Check console for details.`;
             noDataMessage.classList.remove('hidden');
        }
        return false; 
    }
}

async function fetchAndRenderInactivityReport(userId, startDate, endDate) {
    const tableBody = document.getElementById('inactivity-report-tbody');
    tableBody.innerHTML = '';
    try {
        const reportData = await apiFetch(`${window.DashboardConfig.api.inactivityReport}?user_ids=${userId}&start_date=${startDate}&end_date=${endDate}`);
        if (reportData.length > 0) {
            reportData.forEach(row => {
                const typeClass = { 
                    'In Meeting': 'text-blue-600', 
                    'Idle': 'text-red-600', 
                    'On Break': 'text-yellow-600' 
                }[row.type] || '';
                
                let typeCellContent = '';
                
                if (row.type === 'Idle' || row.type === 'In Meeting') {
                    typeCellContent = `<select class="form-select text-xs p-1 js-type-change border-gray-300 rounded shadow-sm focus:border-indigo-300 focus:ring focus:ring-indigo-200 focus:ring-opacity-50" data-entry-id="${row.specific_entry_id}">
                        <option value="Active">${window.DashboardConfig.i18n.active}</option>
                        <option value="Idle" ${row.type === 'Idle' ? 'selected' : ''}>${window.DashboardConfig.i18n.idle}</option>
                        <option value="In Meeting" ${row.type === 'In Meeting' ? 'selected' : ''}>${window.DashboardConfig.i18n.inMeeting}</option>
                    </select>`;
                } else if (row.type === 'On Break') {
                    typeCellContent = `<select class="form-select text-xs p-1 js-break-convert border-gray-300 rounded shadow-sm focus:border-indigo-300 focus:ring focus:ring-indigo-200 focus:ring-opacity-50" data-break-id="${row.specific_entry_id}">
                        <option value="On Break" selected>${window.DashboardConfig.i18n.onBreak}</option>
                        <option value="In Meeting">${window.DashboardConfig.i18n.convertMeeting}</option>
                        <option value="Active">${window.DashboardConfig.i18n.convertActive}</option>
                    </select>`;
                } else {
                    typeCellContent = `<span class="font-semibold ${typeClass}">${row.translated_type}</span>`;
                }

                tableBody.innerHTML += `<tr>
                    <td class="py-3 px-4 text-sm font-medium text-gray-900">${row.username}</td>
                    <td class="py-3 px-4 text-sm">${typeCellContent}</td>
                    <td class="py-3 px-4 text-sm text-gray-600">${formatDateTime(row.start_time)}</td>
                    <td class="py-3 px-4 text-sm font-mono">${formatDuration(row.duration_seconds)}</td>
                    <td class="py-3 px-4 text-sm text-gray-500 italic">${row.notes || ''}</td>
                </tr>`;
            });
            return true;
        }
        return false;
    } catch (error) { 
        console.error('Error fetching inactivity report:', error); 
        return false; 
    }
}

async function fetchAndRenderAppUsageReport(userId, startDate, endDate) {
    const tableBody = document.getElementById('app-usage-table-body');
    const chartCanvas = document.getElementById('app-usage-pie-chart');
    if (chartInstances.appUsage) { 
        chartInstances.appUsage.destroy(); 
        chartInstances.appUsage = null; 
    }
    tableBody.innerHTML = '';
    try {
        const reportData = await apiFetch(`${window.DashboardConfig.api.usageReport}?user_ids=${userId}&start_date=${startDate}&end_date=${endDate}`);
        if (reportData.length > 0) {
            reportData.forEach(row => { 
                tableBody.innerHTML += `<tr>
                    <td class="py-3 px-4 text-sm">${row.application_name}</td>
                    <td class="py-3 px-4 text-sm font-mono">${formatDuration(row.total_duration_seconds)}</td>
                    <td class="py-3 px-4 text-sm">${row.interaction_count}</td>
                </tr>`; 
            });
            
            const topData = reportData.slice(0, 7);
            const otherDuration = reportData.slice(7).reduce((a, r) => a + r.total_duration_seconds, 0);
            const chartLabels = topData.map(d => d.application_name);
            const chartValues = topData.map(d => d.total_duration_seconds);
            
            if (otherDuration > 0) { 
                chartLabels.push(window.DashboardConfig.i18n.other); 
                chartValues.push(otherDuration); 
            }
            
            chartInstances.appUsage = new Chart(chartCanvas, { 
                type: 'doughnut', 
                data: { 
                    labels: chartLabels, 
                    datasets: [{ 
                        data: chartValues, 
                        backgroundColor: ['#6d28d9', '#4f46e5', '#7c3aed', '#a78bfa', '#c4b5fd', '#ddd6fe', '#ede9fe', '#9ca3af'] 
                    }] 
                }, 
                options: { 
                    responsive: true, 
                    plugins: { 
                        legend: { position: 'right' }, 
                        tooltip: { 
                            callbacks: { 
                                label: c => `${c.label}: ${formatDuration(c.raw)}` 
                            } 
                        } 
                    } 
                } 
            });
            return true;
        } 
        return false;
    } catch (error) { 
        console.error('Error fetching app usage report:', error); 
        return false; 
    }
}

async function fetchAndRenderHistoricalTimeline(userId, date) {
    try {
        const url = `${window.DashboardConfig.api.historicalTimeline}?user_ids=${encodeURIComponent(userId)}&date=${encodeURIComponent(date)}`;
        const response = await fetch(url);
        if (!response.ok) throw new Error("HTTP " + response.status);
        const data = await response.json();

        const users = data.users || [];

        if (users.length === 0) {
            console.log("Timeline: no data returned for", userId, date);
            document.getElementById('timeline-no-data-message').classList.remove('hidden');
            return false;
        }

        renderGanttChart('historical', 'admin-activity-timeline-canvas', users, 'No data available for the selected criteria.');
        return true;
    } catch (err) {
        console.error("Failed to fetch timeline:", err);
        document.getElementById('timeline-no-data-message').classList.remove('hidden');
        return false;
    }
}

function renderGanttChart(instanceKey, canvasId, usersData, noDataMessage) {
    const canvas = document.getElementById(canvasId);
    const messageEl = canvas.nextElementSibling;
    const container = canvas.parentElement;

    if (!usersData || usersData.length === 0) {
        if (chartInstances[instanceKey]) { 
            chartInstances[instanceKey].destroy(); 
            chartInstances[instanceKey] = null; 
        }
        canvas.classList.add('hidden');
        messageEl.textContent = noDataMessage;
        messageEl.classList.remove('hidden');
        return;
    }

    let reportDateStr;
    if (instanceKey === 'historical' && window.reportDatePicker) {
        const sd = window.reportDatePicker.getStartDate();
        reportDateStr = sd ? (sd.format ? sd.format('YYYY-MM-DD') : sd.toISOString().slice(0,10)) : new Date().toISOString().slice(0,10);
    } else {
        reportDateStr = new Date().toISOString().split('T')[0];
    }

    const startOfDay = new Date(`${reportDateStr}T00:00:00.000Z`);
    const userLabels = usersData.map(u => u.username).sort();
    const chartPoints = [];
    const backgroundColors = [];
    let minTime = 24 * 3600, maxTime = 0;

    usersData.forEach(ud => {
        ud.segments?.forEach(seg => {
            const startTime = new Date(seg.start_time);
            const endTime = seg.end_time ? new Date(seg.end_time) : new Date();
            const startSeconds = (startTime.getTime() - startOfDay.getTime()) / 1000;
            const endSeconds = (endTime.getTime() - startOfDay.getTime()) / 1000;
            minTime = Math.min(minTime, startSeconds);
            maxTime = Math.max(maxTime, endSeconds);

            chartPoints.push({ x: [startSeconds, endSeconds], y: ud.username, label: seg.status });
            backgroundColors.push(STATUS_COLORS[seg.status] || STATUS_COLORS['Default']);
        });
    });

    const scaleMin = Math.floor(minTime / 3600) * 3600;
    const scaleMax = Math.ceil(maxTime / 3600) * 3600;
    container.style.minHeight = `${Math.max(150, 80 + (usersData.length * 40))}px`;

    const chartInstance = chartInstances[instanceKey];
    if (chartInstance) {
        messageEl.classList.add('hidden');
        canvas.classList.remove('hidden');
        chartInstance.data.labels = userLabels;
        chartInstance.data.datasets[0].data = chartPoints;
        chartInstance.data.datasets[0].backgroundColor = backgroundColors;
        chartInstance.options.scales.x.min = scaleMin;
        chartInstance.options.scales.x.max = scaleMax;
        chartInstance.update('none');
        return;
    }

    messageEl.classList.add('hidden');
    canvas.classList.remove('hidden');
    chartInstances[instanceKey] = new Chart(canvas, {
        type: 'bar',
        data: { 
            labels: userLabels, 
            datasets: [{ 
                data: chartPoints, 
                backgroundColor: backgroundColors, 
                borderWidth: 1, 
                barPercentage: 0.8, 
                categoryPercentage: 0.9, 
                borderColor: '#f9fafb' 
            }] 
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 0 },
            plugins: { 
                legend: { display: false }, 
                tooltip: { 
                    callbacks: { 
                        label: function(context) {
                            const raw = context.raw;
                            const duration = formatDuration(raw.x[1] - raw.x[0]);
                            const timeOpts = { timeZone: 'Europe/Berlin', hour: '2-digit', minute: '2-digit', second: '2-digit' };
                            
                            const startTime = new Date(startOfDay.getTime() + raw.x[0] * 1000).toLocaleTimeString('de-DE', timeOpts);
                            const endTime = new Date(startOfDay.getTime() + raw.x[1] * 1000).toLocaleTimeString('de-DE', timeOpts);
                            
                            return `${raw.label}: ${duration} (${startTime} - ${endTime})`;
                        }
                    } 
                } 
            },
            scales: {
                y: { 
                    stacked: true, 
                    ticks: { 
                        color: '#4b5563' 
                    } 
                },
                x: { 
                    min: scaleMin, 
                    max: scaleMax, 
                    ticks: { 
                        stepSize: 3600, 
                        color: '#6b7280',
                        callback: function(val) {
                            const utcDate = new Date(startOfDay.getTime() + val * 1000);
                            return utcDate.toLocaleTimeString('de-DE', {
                                timeZone: 'Europe/Berlin',
                                hour: '2-digit',
                                minute: '2-digit'
                            });
                        }
                    } 
                }
            }
        }
    });
}

// --- Modal Functions ---
function openCorrectionModal(entryId, clockInISO, clockOutISO) {
    const modal = document.getElementById('time-correction-modal');
    document.getElementById('correction-entry-id').value = entryId;
    
    const toLocalInputFormat = (iso) => {
        if (!iso || iso === 'null' || iso === 'undefined') return '';
        const d = new Date(iso);
        d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
        return d.toISOString().slice(0, 16);
    };
    
    document.getElementById('correction-clock-in').value = toLocalInputFormat(clockInISO);
    document.getElementById('correction-clock-out').value = toLocalInputFormat(clockOutISO);
    modal.classList.remove('hidden');
}

function closeCorrectionModal() { 
    document.getElementById('time-correction-modal').classList.add('hidden'); 
}

// --- Event Handlers ---
async function handleClockOutAll() {
    if (!confirm(window.DashboardConfig.i18n.confirmClockOut)) return;
    try {
        const result = await apiFetch(window.DashboardConfig.api.clockOutAll, { method: 'POST' });
        alert(result.message || 'All active users have been clocked out.');
        refreshRealtimeData();
    } catch (error) { 
        alert("Error: " + error.message); 
    }
}

async function handleCorrectionSubmit(e) {
    e.preventDefault();
    const entryId = document.getElementById('correction-entry-id').value;
    const clockIn = document.getElementById('correction-clock-in').value;
    const clockOut = document.getElementById('correction-clock-out').value;
    
    try {
        const result = await apiFetch(window.DashboardConfig.api.correctTime, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                entry_id: entryId, 
                clock_in_time: clockIn || null, 
                clock_out_time: clockOut || null 
            })
        });
        
        if (result.success) {
            closeCorrectionModal();
            refreshRealtimeData();
            fetchAndRenderAllReports();
        } else { 
            alert('Error: ' + (result.message || 'Could not save changes.')); 
        }
    } catch (error) { 
        console.error('Error correcting time entry:', error); 
        alert('An unexpected error occurred.'); 
    }
}

async function handleTypeChange(select) {
    try {
        const result = await apiFetch(window.DashboardConfig.api.updateIdle, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                idle_entry_id: select.dataset.entryId, 
                new_type: select.value 
            })
        });
        
        if (result.success) {
            refreshRealtimeData();
            fetchAndRenderAllReports();
        } else {
            alert('Error: ' + (result.message || 'Could not update entry.'));
            fetchAndRenderAllReports();
        }
    } catch (error) { 
        console.error('Error updating idle reason:', error); 
    }
}

async function handleBreakConvert(select) {
    const newType = select.value;
    if (newType === 'On Break') return;

    const confirmMsg = newType === 'Active' ? 
        window.DashboardConfig.i18n.confirmConvertActive :
        window.DashboardConfig.i18n.confirmConvertMeeting;
    
    if (!confirm(confirmMsg)) {
        select.value = 'On Break';
        return;
    }

    try {
        const result = await apiFetch(window.DashboardConfig.api.convertBreak, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                break_entry_id: select.dataset.breakId,
                convert_to: newType
            })
        });
        
        if (result.success) {
            refreshRealtimeData();
            fetchAndRenderAllReports();
        } else { 
            alert('Error: ' + (result.message || 'Could not convert entry.')); 
            fetchAndRenderAllReports();
        }
    } catch (err) { 
        console.error('Error converting break:', err); 
        alert('An unexpected error occurred.'); 
        fetchAndRenderAllReports();
    }
}

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    // Initialize Litepicker
    window.reportDatePicker = new Litepicker({
        element: document.getElementById('report-date-range'),
        singleMode: false,
        format: 'YYYY-MM-DD',
        setup: (pickerInstance) => {
            pickerInstance.on('selected', () => {
                fetchAndRenderAllReports();
            });
        }
    });

    // Set initial date to today
    if (window.reportDatePicker && typeof window.reportDatePicker.setDateRange === 'function') {
        window.reportDatePicker.setDateRange(new Date(), new Date());
    }

    // Tab switching
    const tabs = document.querySelectorAll('#report-tabs a');
    tabs.forEach(tab => {
        tab.addEventListener('click', e => {
            e.preventDefault();
            tabs.forEach(t => {
                t.className = 'whitespace-nowrap py-3 px-1 border-b-2 font-medium text-sm text-gray-500 hover:text-gray-700 hover:border-gray-300';
                document.getElementById(`tab-content-${t.dataset.tab}`).classList.add('hidden');
            });
            e.target.className = 'whitespace-nowrap py-3 px-1 border-b-2 font-medium text-sm text-indigo-600 border-indigo-500';
            document.getElementById(`tab-content-${e.target.dataset.tab}`).classList.remove('hidden');
        });
    });

    // Event listeners
    document.getElementById('report-user-select').addEventListener('change', fetchAndRenderAllReports);
    document.getElementById('manual-clock-out-all-btn').addEventListener('click', handleClockOutAll);
    document.getElementById('cancel-correction-btn').addEventListener('click', closeCorrectionModal);
    document.getElementById('time-correction-form').addEventListener('submit', handleCorrectionSubmit);

    // Event delegation for inactivity table
    const inactivityTableBody = document.getElementById('inactivity-report-tbody');
    
    inactivityTableBody.addEventListener('click', async (e) => {
        const editButton = e.target.closest('.js-edit-btn');
        if (editButton) {
            openCorrectionModal(editButton.dataset.entryId, editButton.dataset.clockIn, editButton.dataset.clockOut);
        }
    });
    
    inactivityTableBody.addEventListener('change', async (e) => {
        const typeSelect = e.target.closest('.js-type-change');
        if (typeSelect) {
            handleTypeChange(typeSelect);
        }

        const convertSelect = e.target.closest('.js-break-convert');
        if (convertSelect) {
            handleBreakConvert(convertSelect);
        }
    });

    // Initial data load
    fetchAndRenderAllReports();
    refreshRealtimeData();
    setInterval(refreshRealtimeData, REALTIME_REFRESH_INTERVAL_MS);
});