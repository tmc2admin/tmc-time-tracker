/**
 * Hausverwaltungen - offene Anfragen (admin/mail_followups.html)
 * Self-contained: not reusing admin_dashboard.js, which is only loaded on /admin/dashboard.
 */

const MF_STATUS_LABELS = {
    open: 'Offen',
    answered: 'Beantwortet',
    snoozed: 'Wiedervorlage',
    done: 'Erledigt',
};

const MF_STATUS_COLORS = {
    open: 'bg-red-100 text-red-700',
    answered: 'bg-emerald-100 text-emerald-700',
    snoozed: 'bg-amber-100 text-amber-700',
    done: 'bg-gray-100 text-gray-600',
};

let mfCurrentFilter = '';

async function mfFetch(url, options = {}) {
    const resp = await fetch(url, options);
    if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({ error: resp.statusText }));
        throw new Error(errorData.error || `Request failed (${resp.status})`);
    }
    return resp.json();
}

function mfIsOverdue(item) {
    if (!item.due_date) return false;
    if (item.status !== 'open' && item.status !== 'snoozed') return false;
    return item.due_date <= new Date().toISOString().slice(0, 10);
}

function mfRenderList(items) {
    const container = document.getElementById('followups-list');
    const template = document.getElementById('followup-card-template');
    container.innerHTML = '';

    if (!items.length) {
        container.innerHTML = '<p class="text-gray-400 text-sm">Keine Einträge in dieser Ansicht.</p>';
        return;
    }

    for (const item of items) {
        const node = template.content.cloneNode(true);
        const card = node.querySelector('.followup-card');
        card.dataset.id = item.id;

        node.querySelector('.company-label').textContent = item.company_label || '';
        node.querySelector('.subject').textContent = item.subject || '(ohne Betreff)';
        node.querySelector('.counterpart').textContent =
            `${item.counterpart_name || ''} <${item.counterpart_email || ''}>`.trim();

        const badge = node.querySelector('.status-badge');
        const overdue = mfIsOverdue(item);
        badge.textContent = overdue ? 'Wiedervorlage fällig' : (MF_STATUS_LABELS[item.status] || item.status);
        badge.className = 'status-badge inline-block px-3 py-1 rounded-full text-xs font-semibold ' +
            (overdue ? 'bg-red-100 text-red-700' : (MF_STATUS_COLORS[item.status] || 'bg-gray-100 text-gray-600'));

        node.querySelector('.last-inbound').textContent = item.last_inbound_at ? `Eingang: ${item.last_inbound_at}` : '';

        const errorEl = node.querySelector('.draft-error');
        if (item.draft_error) {
            errorEl.textContent = `Entwurf konnte nicht automatisch erstellt werden: ${item.draft_error}`;
            errorEl.classList.remove('hidden');
        }

        node.querySelector('.draft-text').value = item.draft_text || '';

        const webLink = node.querySelector('.web-link');
        if (item.web_link) {
            webLink.href = item.web_link;
        } else {
            webLink.classList.add('hidden');
        }

        const draftLink = node.querySelector('.outlook-draft-link');
        if (item.outlook_draft_web_link) {
            draftLink.href = item.outlook_draft_web_link;
            draftLink.classList.remove('hidden');
        }

        const dueInput = node.querySelector('.due-date-input');
        dueInput.value = item.due_date || '';

        node.querySelector('.save-btn').addEventListener('click', (e) => mfSaveDraft(e, item.id));
        node.querySelector('.regenerate-btn').addEventListener('click', (e) => mfRegenerate(e, item.id));
        node.querySelector('.create-draft-btn').addEventListener('click', (e) => mfCreateOutlookDraft(e, item.id));
        node.querySelector('.snooze-btn').addEventListener('click', (e) => mfSnooze(e, item.id));
        node.querySelector('.done-btn').addEventListener('click', (e) => mfMarkDone(e, item.id));

        container.appendChild(node);
    }
}

async function mfLoad() {
    const container = document.getElementById('followups-list');
    container.innerHTML = '<p class="text-gray-400 text-sm">Lade...</p>';
    try {
        let url = '/admin/api/mail_followups';
        if (mfCurrentFilter && mfCurrentFilter !== 'all') {
            url += `?status=${encodeURIComponent(mfCurrentFilter)}`;
        }
        const items = await mfFetch(url);
        mfRenderList(mfCurrentFilter === 'all' ? items : items.filter(i => {
            if (mfCurrentFilter) return i.status === mfCurrentFilter;
            return i.status === 'open' || i.status === 'snoozed';
        }));
    } catch (err) {
        container.innerHTML = `<p class="text-red-600 text-sm">Fehler beim Laden: ${err.message}</p>`;
    }
}

function mfCardOf(el) {
    return el.closest('.followup-card');
}

async function mfSaveDraft(evt, id) {
    const card = mfCardOf(evt.target);
    const text = card.querySelector('.draft-text').value;
    evt.target.disabled = true;
    try {
        await mfFetch(`/admin/api/mail_followups/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ draft_text: text }),
        });
        evt.target.textContent = 'Gespeichert ✓';
        setTimeout(() => { evt.target.textContent = 'Entwurf speichern'; }, 1500);
    } catch (err) {
        alert(`Speichern fehlgeschlagen: ${err.message}`);
    } finally {
        evt.target.disabled = false;
    }
}

async function mfRegenerate(evt, id) {
    const card = mfCardOf(evt.target);
    const feedback = card.querySelector('.feedback-input').value;
    evt.target.disabled = true;
    evt.target.textContent = 'Generiere...';
    try {
        const item = await mfFetch(`/admin/api/mail_followups/${id}/regenerate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ feedback }),
        });
        card.querySelector('.draft-text').value = item.draft_text || '';
        const errorEl = card.querySelector('.draft-error');
        if (item.draft_error) {
            errorEl.textContent = `Entwurf konnte nicht erstellt werden: ${item.draft_error}`;
            errorEl.classList.remove('hidden');
        } else {
            errorEl.classList.add('hidden');
        }
    } catch (err) {
        alert(`Regenerieren fehlgeschlagen: ${err.message}`);
    } finally {
        evt.target.disabled = false;
        evt.target.textContent = 'Mit Claude verfeinern';
    }
}

async function mfCreateOutlookDraft(evt, id) {
    if (!confirm('Entwurf jetzt in Outlook anlegen?')) return;
    evt.target.disabled = true;
    try {
        const item = await mfFetch(`/admin/api/mail_followups/${id}/create_outlook_draft`, { method: 'POST' });
        if (item.outlook_draft_web_link) {
            const card = mfCardOf(evt.target);
            const link = card.querySelector('.outlook-draft-link');
            link.href = item.outlook_draft_web_link;
            link.classList.remove('hidden');
        }
        evt.target.textContent = 'Angelegt ✓';
    } catch (err) {
        alert(`Outlook-Entwurf konnte nicht angelegt werden: ${err.message}`);
        evt.target.disabled = false;
    }
}

async function mfSnooze(evt, id) {
    const card = mfCardOf(evt.target);
    const dueDate = card.querySelector('.due-date-input').value;
    if (!dueDate) { alert('Bitte ein Datum wählen.'); return; }
    try {
        await mfFetch(`/admin/api/mail_followups/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: 'snoozed', due_date: dueDate }),
        });
        mfLoad();
    } catch (err) {
        alert(`Verschieben fehlgeschlagen: ${err.message}`);
    }
}

async function mfMarkDone(evt, id) {
    try {
        await mfFetch(`/admin/api/mail_followups/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: 'done' }),
        });
        mfLoad();
    } catch (err) {
        alert(`Konnte nicht als erledigt markiert werden: ${err.message}`);
    }
}

document.querySelectorAll('.filter-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
        mfCurrentFilter = btn.dataset.status;
        document.querySelectorAll('.filter-btn').forEach((b) => {
            b.classList.remove('bg-indigo-600', 'text-white');
            b.classList.add('bg-white', 'ring-1', 'ring-gray-300', 'text-gray-700');
        });
        btn.classList.remove('bg-white', 'ring-1', 'ring-gray-300', 'text-gray-700');
        btn.classList.add('bg-indigo-600', 'text-white');
        mfLoad();
    });
});

mfLoad();
