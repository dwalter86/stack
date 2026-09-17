import { loadMeOrRedirect, renderShell, api, escapeHtml } from './common.js';
import { notifySuccess, notifyError, notifyInfo, confirmDialog } from './notify.js';

const PAGE_SIZE = 50;
const DIRECTION_LABEL = { received: 'Received', sent: 'Sent', updated: 'Updated' };

(async () => {
  const me = await loadMeOrRedirect(); if (!me) return;
  renderShell(me);
  if (me.user_type !== 'super_admin') { window.location.replace('/accounts.html'); return; }

  const $ = (id) => document.getElementById(id);
  const rowsEl = $('logRows');
  let tab = 'activity';
  let offset = 0;
  let total = 0;
  let rows = [];

  const fmt = (iso) => (iso ? new Date(iso).toLocaleString() : '');

  // ---------- summary ----------
  async function loadSummary() {
    try {
      const s = await api('/api/admin/ilgforms/summary');
      const banner = $('outboundBanner');
      if (!s.outbound_enabled) {
        banner.textContent = 'Outbound sync is switched off on this server. Submissions are still received and processed, but itemId writebacks wait in the queue and sync status is not refreshed.';
        banner.classList.remove('hidden');
      } else {
        banner.classList.add('hidden');
      }
      const tiles = [
        ['Received today', s.today.received],
        ['Items created', s.today.items_created],
        ['Items updated', s.today.items_updated],
        ['Sent to ILG Forms', s.today.sent],
        ['Failed today', s.today.failed, s.today.failed ? 'bad' : ''],
        ['Waiting in queue', s.jobs.pending + s.jobs.running, s.jobs.pending + s.jobs.running ? 'warn' : ''],
        ['Failed writebacks', s.jobs.failed, s.jobs.failed ? 'bad' : ''],
      ];
      $('summaryStrip').innerHTML = tiles.map(([label, value, tone]) =>
        `<div class="sync-tile ${tone ? `sync-tile--${tone}` : ''}"><div class="sync-tile-value">${escapeHtml(String(value))}</div><div class="sync-tile-label">${escapeHtml(label)}</div></div>`).join('');

      const lastChecked = s.integrations.map(i => `${escapeHtml(i.name)}: ${i.last_reconciled_at ? `last checked ${escapeHtml(fmt(i.last_reconciled_at))}` : 'never checked'}${i.enabled ? '' : ' (disabled)'}`).join(' · ');
      $('accountStrip').innerHTML = s.accounts.length
        ? `<table class="sync-accounts-table"><thead><tr><th>Account</th><th>Items</th><th>Synced</th><th>Waiting</th><th>Not synced</th></tr></thead><tbody>` +
          s.accounts.map(a => `<tr><td>${escapeHtml(a.name)}</td><td>${a.items}</td><td class="sync-good">${a.synced}</td><td>${a.pending}</td><td class="${a.not_synced ? 'sync-muted-strong' : ''}">${a.not_synced}</td></tr>`).join('') +
          `</tbody></table><div class="small">${lastChecked}</div>`
        : '<p class="small">No ILG Forms integration is configured yet.</p>';

      $('failedCount').textContent = s.jobs.failed ? String(s.jobs.failed) : '';
      const orphans = s.integrations.reduce((n, i) => n + i.orphans, 0);
      $('orphanCount').textContent = orphans ? String(orphans) : '';
      $('reconcileBtn').disabled = !s.outbound_enabled;

      const select = $('filterAccount');
      if (select.options.length <= 1) {
        select.insertAdjacentHTML('beforeend', s.accounts.map(a => `<option value="${escapeHtml(a.account_id)}">${escapeHtml(a.name)}</option>`).join(''));
      }
    } catch (err) {
      notifyError(err.message || 'Failed to load the sync summary');
    }
  }

  // ---------- log ----------
  function filters() {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    const account = $('filterAccount').value;
    const direction = tab === 'failed' ? 'sent' : $('filterDirection').value;
    const result = tab === 'failed' ? 'failed' : $('filterResult').value;
    const from = $('filterFrom').value;
    const to = $('filterTo').value;
    const search = $('filterSearch').value.trim();
    if (account) params.set('account_id', account);
    if (direction) params.set('direction', direction);
    if (result) params.set('result', result);
    if (from) params.set('date_from', from);
    if (to) params.set('date_to', to);
    if (search) params.set('search', search);
    // Grouped by submission unless the view is narrowed: then show each matching row on its own.
    if (direction || result || search) params.set('flat', 'true');
    return params;
  }

  function resultBadge(r) {
    const retry = r.job && r.job.status === 'pending' && r.job.next_attempt_at && r.result === 'retrying'
      ? ` title="Next attempt ${escapeHtml(fmt(r.job.next_attempt_at))}"` : '';
    return `<span class="sync-result sync-result--${escapeHtml(r.result)}"${retry}>${escapeHtml(r.result)}</span>`;
  }

  function rowHtml(r, { child = false } = {}) {
    const incident = r.section_slug && r.account_id
      ? `<a href="/section.html?account=${encodeURIComponent(r.account_id)}&slug=${encodeURIComponent(r.section_slug)}">${escapeHtml(r.section_label || r.section_slug)}</a>`
      : escapeHtml(r.section_label || '');
    const item = r.item_id && r.account_id && r.section_slug
      ? ` <a class="small" href="/item.html?account=${encodeURIComponent(r.account_id)}&section=${encodeURIComponent(r.section_slug)}&item=${encodeURIComponent(r.item_id)}">view item</a>` : '';
    const failedKids = r.failed_children ? ` <span class="sync-result sync-result--failed">${r.failed_children} failed</span>` : '';
    const actions = [];
    if (!child && r.children) actions.push(`<button class="btn" data-expand="${escapeHtml(r.id)}">${r.children} step${r.children === 1 ? '' : 's'}</button>`);
    if (r.has_payload) actions.push(`<button class="btn" data-payload="${escapeHtml(r.id)}">Payload</button>`);
    if (r.direction === 'sent' && r.result === 'failed' && r.job) actions.push(`<button class="btn" data-retry="${escapeHtml(r.id)}">Retry</button>`);
    return `
      <tr class="${child ? 'sync-child' : 'sync-parent'} sync-row--${escapeHtml(r.result)}" data-row="${escapeHtml(r.id)}">
        <td class="audit-time">${escapeHtml(fmt(r.created_at))}</td>
        <td><span class="sync-direction sync-direction--${escapeHtml(r.direction)}">${escapeHtml(DIRECTION_LABEL[r.direction] || r.direction)}</span></td>
        <td>${escapeHtml(r.account_name || '')}</td>
        <td>${incident}</td>
        <td>${escapeHtml(r.summary || r.event)}${item}${failedKids}${r.error ? `<div class="sync-error">${escapeHtml(r.error)}</div>` : ''}${r.row_id ? `<div class="sync-rowid">row ${escapeHtml(r.row_id)}</div>` : ''}</td>
        <td>${resultBadge(r)}</td>
        <td class="sync-actions">${actions.join(' ')}</td>
      </tr>
      <tr class="hidden" data-slot="${escapeHtml(r.id)}"><td colspan="7"></td></tr>`;
  }

  async function loadLog() {
    rowsEl.innerHTML = '<tr><td colspan="7" class="small">Loading…</td></tr>';
    try {
      const data = await api(`/api/admin/ilgforms/sync-log?${filters().toString()}`);
      total = data.total; rows = data.rows;
      rowsEl.innerHTML = rows.length
        ? rows.map(r => rowHtml(r)).join('')
        : `<tr><td colspan="7" class="small">${tab === 'failed' ? 'No failed writebacks. Everything sent to ILG Forms has gone through.' : 'No sync activity matches.'}</td></tr>`;
      $('logMeta').textContent = `${total} ${data.flat ? 'entries' : 'events'}`;
      $('pageInfo').textContent = total ? `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)} of ${total}` : '';
      $('pagePrev').disabled = offset === 0;
      $('pageNext').disabled = offset + PAGE_SIZE >= total;
      $('retryAllBtn').classList.toggle('hidden', !(tab === 'failed' && total > 0));
    } catch (err) {
      rowsEl.innerHTML = '<tr><td colspan="7" class="small">Failed to load the sync log.</td></tr>';
      notifyError(err.message || 'Failed to load the sync log');
    }
  }

  rowsEl.addEventListener('click', async (e) => {
    const btn = e.target.closest('button'); if (!btn) return;
    const id = btn.dataset.expand || btn.dataset.payload || btn.dataset.retry;
    const slot = rowsEl.querySelector(`tr[data-slot="${id}"]`);
    try {
      if (btn.dataset.expand) {
        if (!slot.classList.contains('hidden') && slot.dataset.kind === 'children') { slot.classList.add('hidden'); return; }
        const children = await api(`/api/admin/ilgforms/sync-log/${encodeURIComponent(id)}/children`);
        slot.firstElementChild.innerHTML = `<table class="audit-table sync-children"><tbody>${children.map(c => rowHtml(c, { child: true })).join('')}</tbody></table>`;
        slot.dataset.kind = 'children'; slot.classList.remove('hidden');
      } else if (btn.dataset.payload) {
        if (!slot.classList.contains('hidden') && slot.dataset.kind === 'payload') { slot.classList.add('hidden'); return; }
        const payload = await api(`/api/admin/ilgforms/sync-log/${encodeURIComponent(id)}/payload`);
        slot.firstElementChild.innerHTML = `<div class="audit-details"><pre>${escapeHtml(JSON.stringify(payload, null, 2))}</pre></div>`;
        slot.dataset.kind = 'payload'; slot.classList.remove('hidden');
      } else if (btn.dataset.retry) {
        btn.disabled = true;
        const res = await api('/api/admin/ilgforms/retry', { method: 'POST', body: JSON.stringify({ log_id: id }) });
        if (res.requeued) notifySuccess('Writeback put back in the queue.'); else notifyInfo('Nothing to retry for that entry.');
        await Promise.all([loadSummary(), loadLog()]);
      }
    } catch (err) {
      btn.disabled = false;
      notifyError(err.message || 'That did not work');
    }
  });

  // ---------- orphans ----------
  async function loadOrphans() {
    const el = $('orphanRows');
    el.innerHTML = '<tr><td colspan="8" class="small">Loading…</td></tr>';
    try {
      const list = await api('/api/admin/ilgforms/orphans');
      el.innerHTML = list.length ? list.map(o => `
        <tr><td>${escapeHtml(o.datasource || '')}</td><td>${escapeHtml(o.account_name || '')}</td><td>${escapeHtml(o.incident || '')}</td>
            <td>${escapeHtml(String(o.house ?? ''))}</td><td>${escapeHtml(o.detail || '')}</td><td class="audit-path">${escapeHtml(o.row_id)}</td>
            <td class="audit-path">${escapeHtml(o.item_id)}</td><td class="audit-time">${escapeHtml(fmt(o.first_seen_at))}</td></tr>`).join('')
        : '<tr><td colspan="8" class="small">No orphan rows found at the last check.</td></tr>';
    } catch (err) {
      el.innerHTML = '<tr><td colspan="7" class="small">Failed to load orphan rows.</td></tr>';
      notifyError(err.message || 'Failed to load orphan rows');
    }
  }

  // ---------- accounts ----------
  const LIST_STATUS = { synced: ['In the account list', 'success'], pending: ['Waiting to be added', 'pending'], not_synced: ['Not in the account list', 'failed'] };
  async function loadAccounts() {
    const el = $('accountsBody');
    el.innerHTML = '<p class="small">Loading…</p>';
    try {
      const data = await api('/api/admin/ilgforms/integrations');
      const options = data.unlinked_accounts.map(a => `<option value="${escapeHtml(a.id)}">${escapeHtml(a.name)}</option>`).join('');
      el.innerHTML = data.integrations.map(i => `
        <div class="card sync-integration">
          <div class="sync-integration-head"><strong>${escapeHtml(i.name)}</strong>
            <span class="small">company ${escapeHtml(String(i.company_id))} · list <code>${escapeHtml(i.account_datasource)}</code>${i.enabled ? '' : ' · disabled'} ·
            new rows in the list ${i.accept_new_accounts ? 'create accounts here' : 'are ignored'}</span></div>
          <table class="audit-table"><thead><tr><th>Account</th><th>Id</th><th>ILG Forms account list</th><th></th></tr></thead><tbody>
            ${i.accounts.map(a => { const st = LIST_STATUS[a.list_status] || LIST_STATUS.not_synced; return `
              <tr><td>${escapeHtml(a.name)}</td><td class="audit-path">${escapeHtml(a.id)}</td>
                  <td><span class="sync-result sync-result--${st[1]}">${st[0]}</span>${a.list_checked_at ? ` <span class="small">checked ${escapeHtml(fmt(a.list_checked_at))}</span>` : ''}</td>
                  <td class="sync-actions"><button class="btn" data-unlink="${escapeHtml(a.id)}" data-name="${escapeHtml(a.name)}">Unlink</button></td></tr>`; }).join('')
              || '<tr><td colspan="4" class="small">No accounts linked yet.</td></tr>'}
          </tbody></table>
          <div class="sync-link-row">
            <select data-link-select="${escapeHtml(i.id)}"><option value="">Link another account…</option>${options}</select>
            <button class="btn" data-link="${escapeHtml(i.id)}">Link</button>
          </div>
        </div>`).join('') || '<p class="small">No ILG Forms integration is configured yet.</p>';
    } catch (err) {
      el.innerHTML = '<p class="small">Failed to load accounts.</p>';
      notifyError(err.message || 'Failed to load accounts');
    }
  }

  $('accountsBody').addEventListener('click', async (e) => {
    const btn = e.target.closest('button'); if (!btn) return;
    try {
      if (btn.dataset.link) {
        const select = document.querySelector(`select[data-link-select="${btn.dataset.link}"]`);
        if (!select.value) { notifyInfo('Choose an account to link first.'); return; }
        await api(`/api/admin/ilgforms/integrations/${encodeURIComponent(btn.dataset.link)}/accounts`, { method: 'POST', body: JSON.stringify({ account_id: select.value }) });
        notifySuccess('Account linked. It will be added to the ILG Forms account list shortly.');
      } else if (btn.dataset.unlink) {
        const ok = await confirmDialog(`"${btn.dataset.name}" will stop syncing and be removed from the ILG Forms account list, so forms can no longer choose it. Its incidents and items on this platform are not touched.`, { title: 'Unlink this account?', confirmLabel: 'Unlink' });
        if (!ok) return;
        await api(`/api/admin/ilgforms/accounts/${encodeURIComponent(btn.dataset.unlink)}`, { method: 'DELETE' });
        notifySuccess('Account unlinked.');
      }
      await Promise.all([loadAccounts(), loadSummary()]);
    } catch (err) { notifyError(err.message || 'That did not work'); }
  });

  // ---------- tabs + controls ----------
  function showTab(next) {
    tab = next; offset = 0;
    document.querySelectorAll('.sync-tab').forEach(b => b.classList.toggle('is-active', b.dataset.tab === tab));
    $('logPanel').classList.toggle('hidden', tab === 'orphans' || tab === 'accounts');
    $('orphanPanel').classList.toggle('hidden', tab !== 'orphans');
    $('accountsPanel').classList.toggle('hidden', tab !== 'accounts');
    ['filterDirection', 'filterResult'].forEach(id => { $(id).disabled = tab === 'failed'; });
    if (tab === 'orphans') loadOrphans(); else if (tab === 'accounts') loadAccounts(); else loadLog();
  }
  document.querySelectorAll('.sync-tab').forEach(b => b.addEventListener('click', () => showTab(b.dataset.tab)));

  $('applyFilters').addEventListener('click', () => { offset = 0; loadLog(); });
  $('filterSearch').addEventListener('keydown', (e) => { if (e.key === 'Enter') { offset = 0; loadLog(); } });
  $('clearFilters').addEventListener('click', () => {
    ['filterAccount', 'filterDirection', 'filterResult', 'filterFrom', 'filterTo', 'filterSearch'].forEach(id => { $(id).value = ''; });
    offset = 0; loadLog();
  });
  $('pagePrev').addEventListener('click', () => { offset = Math.max(0, offset - PAGE_SIZE); loadLog(); });
  $('pageNext').addEventListener('click', () => { offset += PAGE_SIZE; loadLog(); });
  $('refreshBtn').addEventListener('click', () => { loadSummary(); if (tab === 'orphans') loadOrphans(); else if (tab === 'accounts') loadAccounts(); else loadLog(); });

  $('retryAllBtn').addEventListener('click', async () => {
    const ok = await confirmDialog('Every failed itemId writeback goes back in the queue and is sent to ILG Forms again.', { title: 'Retry all failed writebacks?', confirmLabel: 'Retry all', danger: false });
    if (!ok) return;
    try {
      const res = await api('/api/admin/ilgforms/retry', { method: 'POST', body: JSON.stringify({}) });
      notifySuccess(`${res.requeued} writeback${res.requeued === 1 ? '' : 's'} put back in the queue.`);
      await Promise.all([loadSummary(), loadLog()]);
    } catch (err) { notifyError(err.message || 'Retry failed'); }
  });

  $('reconcileBtn').addEventListener('click', async () => {
    const btn = $('reconcileBtn'); btn.disabled = true; btn.textContent = 'Checking…';
    try {
      const res = await api('/api/admin/ilgforms/reconcile', { method: 'POST' });
      const failed = (res.results || []).filter(r => r.error);
      if (failed.length) notifyError(`Check failed: ${failed[0].error}`);
      else notifySuccess(`Checked ILG Forms: ${(res.results || []).reduce((n, r) => n + (r.synced || 0), 0)} items confirmed synced.`);
      await Promise.all([loadSummary(), tab === 'orphans' ? loadOrphans() : loadLog()]);
    } catch (err) { notifyError(err.message || 'Could not check ILG Forms'); }
    finally { btn.textContent = 'Check ILG Forms now'; await loadSummary(); }
  });

  await Promise.all([loadSummary(), loadLog()]);
})();
