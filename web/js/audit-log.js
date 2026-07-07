import { loadMeOrRedirect, renderShell, api, escapeHtml } from './common.js';
import { notifyError } from './notify.js';

const PAGE_SIZE = 50;

(async () => {
  const me = await loadMeOrRedirect(); if (!me) return;
  renderShell(me);
  if (me.user_type !== 'super_admin') { window.location.replace('/accounts.html'); return; }

  const rowsEl = document.getElementById('auditRows');
  const metaEl = document.getElementById('auditMeta');
  const pageInfo = document.getElementById('pageInfo');
  const prevBtn = document.getElementById('pagePrev');
  const nextBtn = document.getElementById('pageNext');
  const actionSelect = document.getElementById('filterAction');

  let offset = 0;
  let total = 0;
  let actionsLoaded = false;

  function currentFilters() {
    const params = new URLSearchParams();
    params.set('limit', String(PAGE_SIZE));
    params.set('offset', String(offset));
    const user = document.getElementById('filterUser').value.trim();
    const action = actionSelect.value;
    const from = document.getElementById('filterFrom').value;
    const to = document.getElementById('filterTo').value;
    const search = document.getElementById('filterSearch').value.trim();
    if (user) params.set('user_email', user);
    if (action) params.set('action', action);
    if (from) params.set('date_from', from);
    if (to) params.set('date_to', to);
    if (search) params.set('search', search);
    return params;
  }

  function statusClass(status) {
    if (status >= 500) return 'audit-status-error';
    if (status >= 400) return 'audit-status-warn';
    return 'audit-status-ok';
  }

  function actionClass(action) {
    if (action === 'login.failed') return 'audit-action-failed';
    if (action.endsWith('.delete')) return 'audit-action-delete';
    return '';
  }

  function renderRows(rows) {
    if (!rows.length) {
      rowsEl.innerHTML = '<tr><td colspan="7" class="small">No audit entries match.</td></tr>';
      return;
    }
    rowsEl.innerHTML = rows.map((r, i) => {
      const time = new Date(r.created_at).toLocaleString();
      const hasDetails = r.details && Object.keys(r.details).length;
      return `
        <tr class="audit-row ${actionClass(r.action)}" data-idx="${i}">
          <td class="audit-time">${escapeHtml(time)}</td>
          <td>${escapeHtml(r.user_email || 'unknown')}</td>
          <td><code>${escapeHtml(r.action)}</code></td>
          <td class="${statusClass(r.status)}">${r.status}</td>
          <td>${escapeHtml(r.ip || '')}</td>
          <td class="audit-path">${escapeHtml(r.path || '')}</td>
          <td>${hasDetails ? '<button class="btn audit-expand" data-idx="' + i + '">Details</button>' : ''}</td>
        </tr>
        ${hasDetails ? `<tr class="audit-details hidden" data-details="${i}"><td colspan="7"><pre>${escapeHtml(JSON.stringify(r.details, null, 2))}</pre></td></tr>` : ''}`;
    }).join('');

    rowsEl.querySelectorAll('.audit-expand').forEach(btn => {
      btn.addEventListener('click', () => {
        const detailsRow = rowsEl.querySelector(`tr[data-details="${btn.dataset.idx}"]`);
        if (detailsRow) detailsRow.classList.toggle('hidden');
      });
    });
  }

  async function load() {
    rowsEl.innerHTML = '<tr><td colspan="7" class="small">Loading…</td></tr>';
    try {
      const data = await api(`/api/admin/audit-log?${currentFilters().toString()}`);
      total = data.total;
      renderRows(data.rows);
      if (!actionsLoaded && data.actions.length) {
        actionSelect.innerHTML = '<option value="">All actions</option>' +
          data.actions.map(a => `<option value="${escapeHtml(a)}">${escapeHtml(a)}</option>`).join('');
        actionsLoaded = true;
      }
      const from = total ? offset + 1 : 0;
      const to = Math.min(offset + PAGE_SIZE, total);
      metaEl.textContent = `${total} entries`;
      pageInfo.textContent = total ? `${from}–${to} of ${total}` : '';
      prevBtn.disabled = offset === 0;
      nextBtn.disabled = offset + PAGE_SIZE >= total;
    } catch (err) {
      rowsEl.innerHTML = '<tr><td colspan="7" class="small">Failed to load audit log.</td></tr>';
      notifyError(err.message || 'Failed to load audit log');
    }
  }

  document.getElementById('applyFilters').addEventListener('click', () => { offset = 0; load(); });
  document.getElementById('refreshBtn').addEventListener('click', () => { offset = 0; load(); });
  document.getElementById('clearFilters').addEventListener('click', () => {
    ['filterUser', 'filterFrom', 'filterTo', 'filterSearch'].forEach(id => { document.getElementById(id).value = ''; });
    actionSelect.value = '';
    offset = 0;
    load();
  });
  document.getElementById('filterSearch').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { offset = 0; load(); }
  });
  prevBtn.addEventListener('click', () => { offset = Math.max(0, offset - PAGE_SIZE); load(); });
  nextBtn.addEventListener('click', () => { offset += PAGE_SIZE; load(); });

  await load();
})();
