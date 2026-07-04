/**
 * KLSID Coverage Report Logic
 * Handles the virtualized table, filtering, and inline editing for the KLSID page.
 */

if (window.KlsidConfig) {
    let allRecords = window.KlsidConfig.records;
    let filteredRecords = [];
    let currentPage = 1;
    let perPage = 10;
    let searchTerm = '';
    let currentSelectedKlsid = null;
    let searchTimeout = null;

    document.addEventListener('DOMContentLoaded', function() {
        if(document.getElementById('klsid-filter')) {
            filterRecords();
            renderTable();
            updatePagination();
        }
    });

    function filterRecords() {
        if (!searchTerm) {
            filteredRecords = allRecords;
        } else {
            const term = searchTerm.toLowerCase();
            filteredRecords = allRecords.filter(r => 
                (r.KLSID && r.KLSID.toLowerCase().includes(term)) ||
                (r.Customer_Name && r.Customer_Name.toLowerCase().includes(term))
            );
        }
        
        currentPage = 1;
        document.getElementById('total-count').textContent = allRecords.length;
        document.getElementById('showing-count').textContent = Math.min(perPage, filteredRecords.length);
        renderTable();
        window.updatePagination();
    }

    function renderTable() {
        const tbody = document.getElementById('table-body');
        const start = (currentPage - 1) * perPage;
        const end = Math.min(start + perPage, filteredRecords.length);
        const pageRecords = filteredRecords.slice(start, end);
        
        if (pageRecords.length === 0) {
            tbody.innerHTML = `<tr><td colspan="6" class="px-6 py-12 text-center"><i class="fas fa-search text-4xl text-gray-300 mb-3"></i><p class="text-gray-500">No records found</p></td></tr>`;
            return;
        }
        
        let html = '';
        pageRecords.forEach(record => {
            const stillInStock = record.still_in_stock || record.StillInStock || '';
            const newCustomer = record.new_customer || record.NewCustomer || '';
            const notes = record.notes || record.Notes || '';
            const hasEdits = stillInStock || newCustomer || notes;
            
            html += `
                <tr class="hover:bg-blue-50 transition-colors cursor-pointer ${hasEdits ? 'bg-yellow-50' : ''}" 
                    onclick="selectRow('${record.KLSID}')" data-klsid="${record.KLSID}">
                    
                    <td class="px-4 py-3">
                        <div class="flex items-center">
                            <div class="flex-shrink-0 h-8 w-8 bg-blue-100 rounded-full flex items-center justify-center">
                                <span class="text-blue-600 font-semibold text-xs">${record.KLSID ? record.KLSID.slice(0,2) : 'ID'}</span>
                            </div>
                            <div class="ml-2">
                                <div class="text-sm font-medium text-gray-900">${record.KLSID || '-'}</div>
                                ${record.Installed_Base_ID ? `<div class="text-xs text-gray-400">${record.Installed_Base_ID}</div>` : ''}
                            </div>
                        </div>
                    </td>
                    
                    <td class="px-4 py-3">
                        <div class="text-sm font-medium text-gray-900">${record.Customer_Name || '-'}</div>
                        ${record.Main_Contact ? `<div class="text-xs text-gray-500 mt-1"><i class="fas fa-user-circle mr-1 text-gray-400"></i>${record.Main_Contact}</div>` : ''}
                        ${record.Account_Manager ? `<div class="text-xs text-gray-400 mt-1"><i class="fas fa-briefcase mr-1"></i>${record.Account_Manager}</div>` : ''}
                    </td>
                    
                    <td class="px-4 py-3">
                        <div class="text-sm text-gray-600">${record.Straße && record.HausNr ? `${record.Straße} ${record.HausNr}` : (record.Customer_Address || '-')}</div>
                        <div class="text-xs text-gray-500 mt-1">${record.PLZ || ''} ${record.Ort || ''}</div>
                        ${record.FTTH_Ausbaustatus ? `<div class="mt-1"><span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${record.FTTH_Ausbaustatus === 'Ausgebaut' ? 'bg-green-100 text-green-800' : record.FTTH_Ausbaustatus === 'Im Bau' ? 'bg-yellow-100 text-yellow-800' : record.FTTH_Ausbaustatus === 'Geplant' ? 'bg-blue-100 text-blue-800' : 'bg-gray-100 text-gray-800'}"><i class="fas fa-wifi mr-1"></i>${record.FTTH_Ausbaustatus}</span></div>` : ''}
                    </td>
                    
                    <td class="px-4 py-3">
                        <select class="stock-select text-sm rounded-lg border-2 ${stillInStock === 'Yes' ? 'border-green-300 bg-green-50' : stillInStock === 'No' ? 'border-red-300 bg-red-50' : 'border-gray-300'} focus:border-blue-500 focus:ring-blue-500 w-24 px-2 py-1.5"
                                onclick="event.stopPropagation()" onchange="updateField('${record.KLSID}', 'still_in_stock', this.value)">
                            <option value="" ${!stillInStock ? 'selected' : ''}>—</option>
                            <option value="Yes" ${stillInStock === 'Yes' ? 'selected' : ''}>✓ Yes</option>
                            <option value="No" ${stillInStock === 'No' ? 'selected' : ''}>✗ No</option>
                        </select>
                    </td>
                    
                    <td class="px-4 py-3">
                        <div class="relative">
                            <i class="fas fa-pen absolute left-2 top-3 text-gray-400 text-xs"></i>
                            <input type="text" class="form-input text-sm rounded-lg border-gray-300 focus:border-blue-500 focus:ring-blue-500 w-36 pl-7 py-1.5"
                                   value="${newCustomer.replace(/"/g, '&quot;')}" onclick="event.stopPropagation()" onchange="updateField('${record.KLSID}', 'new_customer', this.value)" placeholder="Add...">
                        </div>
                    </td>
                    
                    <td class="px-4 py-3">
                        <div class="relative">
                            <i class="fas fa-edit absolute left-2 top-3 text-gray-400 text-xs"></i>
                            <input type="text" class="form-input text-sm rounded-lg border-gray-300 focus:border-blue-500 focus:ring-blue-500 w-36 pl-7 py-1.5"
                                   value="${notes.replace(/"/g, '&quot;')}" onclick="event.stopPropagation()" onchange="updateField('${record.KLSID}', 'notes', this.value)" placeholder="Add...">
                        </div>
                    </td>
                </tr>
            `;
        });
        
        tbody.innerHTML = html;
        document.getElementById('showing-count').textContent = pageRecords.length;
        const editedCount = allRecords.filter(r => r.still_in_stock || r.StillInStock || r.new_customer || r.NewCustomer || r.notes || r.Notes).length;
        document.getElementById('edited-count').innerHTML = `<i class="fas fa-check-circle text-green-500 mr-1"></i>${editedCount} edited`;
    }

    // Expose functions to Window for inline HTML event handlers
    window.updatePagination = function() {
        const totalPages = Math.ceil(filteredRecords.length / perPage) || 1;
        document.getElementById('total-pages').textContent = totalPages;
        document.getElementById('current-page').textContent = currentPage;
        document.getElementById('prev-btn').disabled = currentPage <= 1;
        document.getElementById('next-btn').disabled = currentPage >= totalPages;
    };

    window.changePage = function(direction) {
        const totalPages = Math.ceil(filteredRecords.length / perPage) || 1;
        if (direction === 'prev' && currentPage > 1) currentPage--;
        else if (direction === 'next' && currentPage < totalPages) currentPage++;
        renderTable();
        window.updatePagination();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    };

    window.changePerPage = function() {
        perPage = parseInt(document.getElementById('per-page').value);
        currentPage = 1;
        filterRecords();
    };

    window.debounceSearch = function() {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => { searchTerm = document.getElementById('search-input').value; filterRecords(); }, 300);
    };

    window.applyFilters = function() {
        const klsid = document.getElementById('klsid-filter').value;
        const customer = document.getElementById('customer-filter').value;
        const params = new URLSearchParams();
        if (klsid) params.append('klsid', klsid);
        if (customer) params.append('customer', customer);
        window.location.href = window.KlsidConfig.endpoints.base + (params.toString() ? '?' + params.toString() : '');
    };

    window.removeFilter = function(type) {
        const params = new URLSearchParams(window.location.search);
        params.delete(type);
        let url = window.KlsidConfig.endpoints.base;
        if (params.toString()) url += '?' + params.toString();
        window.location.href = url;
    };

    window.clearFilters = function() {
        window.location.href = window.KlsidConfig.endpoints.base;
    };

    window.selectRow = function(klsid) {
        const record = allRecords.find(r => r.KLSID === klsid);
        if (!record) return;
        if (currentSelectedKlsid) {
            const prevRow = document.querySelector(`tr[data-klsid="${currentSelectedKlsid}"]`);
            if (prevRow) prevRow.classList.remove('bg-blue-100', 'border-l-4', 'border-blue-500');
        }
        const row = document.querySelector(`tr[data-klsid="${klsid}"]`);
        if (row) row.classList.add('bg-blue-100', 'border-l-4', 'border-blue-500');
        currentSelectedKlsid = klsid;
        showSidebar(record);
    };

    function showSidebar(record) {
        const sidebar = document.getElementById('detail-sidebar');
        const content = document.getElementById('sidebar-content');
        
        let ftthClass = 'bg-gray-100 text-gray-800';
        if (record.FTTH_Ausbaustatus === 'Ausgebaut') ftthClass = 'bg-green-100 text-green-800';
        else if (record.FTTH_Ausbaustatus === 'Im Bau') ftthClass = 'bg-yellow-100 text-yellow-800';
        else if (record.FTTH_Ausbaustatus === 'Geplant') ftthClass = 'bg-blue-100 text-blue-800';
        
        const address = record.Straße && record.HausNr ? `${record.Straße} ${record.HausNr}` : (record.Customer_Address || '-');
        const stillInStock = record.still_in_stock || record.StillInStock || '';
        const newCustomer = record.new_customer || record.NewCustomer || '';
        const notes = record.notes || record.Notes || '';
        
        content.innerHTML = `
            <div class="bg-gray-50 p-4 rounded-lg space-y-3">
                <div class="grid grid-cols-2 gap-3">
                    <div><label class="block text-xs font-medium text-gray-500">KLSID</label><p class="text-sm font-bold text-gray-900">${record.KLSID || '-'}</p></div>
                    <div><label class="block text-xs font-medium text-gray-500">Installed Base</label><p class="text-sm text-gray-700">${record.Installed_Base_ID || '-'}</p></div>
                </div>
                <div><label class="block text-xs font-medium text-gray-500">Customer</label><p class="text-sm font-medium text-gray-900">${record.Customer_Name || '-'}</p></div>
                <div><label class="block text-xs font-medium text-gray-500">Main Contact</label><p class="text-sm text-gray-700">${record.Main_Contact || '-'}</p></div>
                <div><label class="block text-xs font-medium text-gray-500">Address</label><p class="text-sm text-gray-700">${address}</p><p class="text-sm text-gray-700">${record.PLZ || ''} ${record.Ort || ''}</p></div>
                <div><label class="block text-xs font-medium text-gray-500">Account Manager</label><p class="text-sm text-gray-700">${record.Account_Manager || '-'}</p></div>
                <div><label class="block text-xs font-medium text-gray-500">FTTH Status</label><span class="inline-flex items-center px-2 py-1 rounded text-xs font-medium ${ftthClass}"><i class="fas fa-wifi mr-1"></i>${record.FTTH_Ausbaustatus || 'N/A'}</span></div>
                
                <div class="border-t border-gray-200 pt-3 mt-3">
                    <h4 class="text-sm font-semibold text-gray-700 mb-2">Edit Fields</h4>
                    <div class="space-y-3">
                        <div>
                            <label class="block text-xs font-medium text-gray-500">Still in Stock</label>
                            <select id="sidebar_stock" class="form-select mt-1 text-sm" onchange="updateFromSidebar('still_in_stock', this.value)">
                                <option value="" ${!stillInStock ? 'selected' : ''}>—</option>
                                <option value="Yes" ${stillInStock === 'Yes' ? 'selected' : ''}>✓ Yes</option>
                                <option value="No" ${stillInStock === 'No' ? 'selected' : ''}>✗ No</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-gray-500">New Customer</label>
                            <textarea id="sidebar_customer" rows="2" class="form-input mt-1 text-sm" onchange="updateFromSidebar('new_customer', this.value)">${newCustomer || ''}</textarea>
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-gray-500">Notes</label>
                            <textarea id="sidebar_notes" rows="3" class="form-input mt-1 text-sm" onchange="updateFromSidebar('notes', this.value)">${notes || ''}</textarea>
                        </div>
                    </div>
                </div>
            </div>
        `;
        sidebar.classList.remove('translate-x-full');
    }

    window.closeSidebar = function() {
        document.getElementById('detail-sidebar').classList.add('translate-x-full');
        if (currentSelectedKlsid) {
            const row = document.querySelector(`tr[data-klsid="${currentSelectedKlsid}"]`);
            if (row) row.classList.remove('bg-blue-100', 'border-l-4', 'border-blue-500');
            currentSelectedKlsid = null;
        }
    };

    function getCsrfToken() {
        const metaTag = document.querySelector('meta[name="csrf-token"]');
        return metaTag ? metaTag.getAttribute('content') : '';
    }

    window.updateField = function(klsid, field, value) {
        const element = event.target;
        element.classList.add('opacity-50', 'cursor-wait');
        element.disabled = true;
        
        const headers = { 'Content-Type': 'application/json', 'Accept': 'application/json' };
        const token = getCsrfToken();
        if (token) headers['X-CSRFToken'] = token;
        
        fetch(window.KlsidConfig.endpoints.update, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({klsid, field, value})
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                const record = allRecords.find(r => r.KLSID === klsid);
                if (record) {
                    if (field === 'still_in_stock') { record.still_in_stock = value; record.StillInStock = value; }
                    else if (field === 'new_customer') { record.new_customer = value; record.NewCustomer = value; }
                    else if (field === 'notes') { record.notes = value; record.Notes = value; }
                }
                
                if (field === 'still_in_stock') {
                    element.classList.remove('border-green-300', 'bg-green-50', 'border-red-300', 'bg-red-50', 'border-gray-300');
                    if (value === 'Yes') element.classList.add('border-green-300', 'bg-green-50');
                    else if (value === 'No') element.classList.add('border-red-300', 'bg-red-50');
                    else element.classList.add('border-gray-300');
                }
                
                showNotification('success', '✓ Saved');
                
                if (currentSelectedKlsid === klsid) {
                    if (field === 'still_in_stock') document.getElementById('sidebar_stock').value = value;
                    else if (field === 'new_customer') document.getElementById('sidebar_customer').value = value;
                    else if (field === 'notes') document.getElementById('sidebar_notes').value = value;
                }
                
                const editedCount = allRecords.filter(r => r.still_in_stock || r.StillInStock || r.new_customer || r.NewCustomer || r.notes || r.Notes).length;
                document.getElementById('edited-count').innerHTML = `<i class="fas fa-check-circle text-green-500 mr-1"></i>${editedCount} edited`;
            } else {
                showNotification('error', '✗ ' + (data.error || 'Failed'));
            }
        })
        .catch(err => {
            console.error("Save error:", err);
            showNotification('error', '✗ Network error saving data.');
        })
        .finally(() => {
            element.classList.remove('opacity-50', 'cursor-wait');
            element.disabled = false;
        });
    };

    window.updateFromSidebar = function(field, value) {
        if (!currentSelectedKlsid) return showNotification('warning', '⚠ Select a record first');
        const row = document.querySelector(`tr[data-klsid="${currentSelectedKlsid}"]`);
        if (row) {
            let target;
            if (field === 'still_in_stock') target = row.querySelector('td:nth-child(4) select');
            else if (field === 'new_customer') target = row.querySelector('td:nth-child(5) input');
            else if (field === 'notes') target = row.querySelector('td:nth-child(6) input');
            
            if (target) {
                target.value = value;
                const fakeEvent = {target};
                const originalEvent = window.event;
                window.event = fakeEvent;
                window.updateField(currentSelectedKlsid, field, value);
                window.event = originalEvent;
            }
        }
    };

    function showNotification(type, message) {
        const notif = document.createElement('div');
        notif.className = `fixed top-4 right-4 px-4 py-2 rounded-lg shadow-lg z-50 transform transition-all duration-300 ${ type === 'success' ? 'bg-green-500' : type === 'warning' ? 'bg-yellow-500' : 'bg-red-500' } text-white flex items-center space-x-2`;
        notif.innerHTML = `<span>${message}</span>`;
        notif.style.animation = 'slideInRight 0.3s ease-out';
        document.body.appendChild(notif);
        setTimeout(() => {
            notif.style.animation = 'slideOutRight 0.3s ease-in';
            notif.style.opacity = '0';
            setTimeout(() => notif.remove(), 300);
        }, 2000);
    }
}