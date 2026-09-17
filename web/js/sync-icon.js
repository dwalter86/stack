// ILG Forms sync indicator, shared by the section table and the item page.
// "Synced" means the ILG Forms datasource row holds this item's id.
import { escapeHtml } from './common.js';

const ICONS = {
  synced: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="currentColor"/><path d="M4.6 8.3l2.2 2.2 4.6-4.8" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  pending: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M8 4.6V8l2.3 1.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  not_synced: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="2.6 2.2"/><path d="M5.4 8h5.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
};

function when(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString();
}

export function syncLabel(state) {
  const status = state && ICONS[state.status] ? state.status : 'not_synced';
  const sheets = ((state && state.links) || []).filter(l => l.status === status).map(l => l.datasource).join(', ');
  if (status === 'synced') {
    const at = when(state.last_synced_at);
    return `Synced with ILG Forms${sheets ? ` (${sheets})` : ''}${at ? ` · confirmed ${at}` : ''}`;
  }
  if (status === 'pending') return `Waiting to sync with ILG Forms${sheets ? ` (${sheets})` : ''}`;
  const reason = state && state.last_error ? ` · ${state.last_error}` : '';
  return `Not synced with ILG Forms${reason}`;
}

// state: {status, last_synced_at, last_error} or undefined (= not synced)
export function syncIconHtml(state, { withText = false } = {}) {
  const status = state && ICONS[state.status] ? state.status : 'not_synced';
  const label = syncLabel(state);
  const text = withText ? `<span class="sync-icon-text">${escapeHtml(label)}</span>` : '';
  return `<span class="sync-icon sync-icon--${status}" role="img" aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}">${ICONS[status]}${text}</span>`;
}
