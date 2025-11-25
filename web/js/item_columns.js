import { loadMeOrRedirect, renderShell, api, getLabels } from './common.js';

function qs(name){
  const m = new URLSearchParams(location.search).get(name);
  return m && decodeURIComponent(m);
}

function escapeHtml(str){
  return String(str ?? '')
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;');
}

function prefKey(accountId, slug){
  return `columnPrefs:${accountId}:${slug||'default'}`;
}

function loadColumnPrefs(accountId, slug){
  try {
    const raw = localStorage.getItem(prefKey(accountId, slug));
    if(!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveColumnPrefs(accountId, slug, keys){
  localStorage.setItem(prefKey(accountId, slug), JSON.stringify(keys));
}

function getAutoKeys(items){
  const keySet = new Set();
  for(const it of items){
    if(it.data && typeof it.data === 'object'){
      Object.keys(it.data).forEach(k => keySet.add(k));
    }
  }
  let keys = Array.from(keySet);
  const priority = ['title','name','label'];
  keys.sort((a,b) => {
    const ia = priority.indexOf(a.toLowerCase());
    const ib = priority.indexOf(b.toLowerCase());
    if(ia !== -1 && ib === -1) return -1;
    if(ib !== -1 && ia === -1) return 1;
    return a.localeCompare(b);
  });
  const MAX_COLS = 8;
  if(keys.length > MAX_COLS) keys = keys.slice(0, MAX_COLS);
  return keys;
}

function buildColumns(items, schemaFields){
  const columns = [
    { key: 'name', label: 'Name', locked: true },
    { key: 'created_at', label: 'Date added' },
  ];
  if(schemaFields && schemaFields.length){
    const visibleFields = schemaFields.filter(f => f.showInTable !== false);
    visibleFields.forEach(f => {
      columns.push({ key: f.key, label: f.label || f.key });
    });
  } else {
    const autoKeys = getAutoKeys(items);
    autoKeys.forEach(k => columns.push({ key: k, label: k }));
  }
  return columns;
}

function reconcileVisibility(columns, stored){
  const available = new Set(columns.map(c => c.key));
  const result = [];
  for(const key of stored){
    if(available.has(key) && !result.includes(key)) result.push(key);
  }
  for(const col of columns){
    if(col.locked && !result.includes(col.key)) result.unshift(col.key);
  }
  if(!result.length){
    return columns.map(c => c.key);
  }
  return result;
}

(async () => {
  const me = await loadMeOrRedirect(); if(!me) return;
  renderShell(me);
  const labels = getLabels(me);

  const accountId = qs('account');
  const slug = qs('slug');
  if(!accountId || !slug){
    document.body.innerHTML = '<main class="container"><p>Missing account or section.</p></main>';
    return;
  }

  const backToSection = document.getElementById('backToSection');
  const settingsMeta = document.getElementById('settingsMeta');
  const columnsList = document.getElementById('columnsList');
  const saveBtn = document.getElementById('saveColumnsBtn');
  const saveMessage = document.getElementById('saveMessage');
  const title = document.getElementById('settingsTitle');

  if(backToSection){
    backToSection.href = `/section.html?account=${encodeURIComponent(accountId)}&slug=${encodeURIComponent(slug)}`;
  }

  let accountName = `Account ${accountId}`;
  try {
    const myAccounts = await api('/api/me/accounts');
    const match = myAccounts.find(a => a.id === accountId);
    if(match) accountName = match.name;
  } catch {
    // ignore
  }

  let sectionLabel = slug;
  let schemaFields = [];
  try {
    const section = await api(`/api/accounts/${accountId}/sections/${encodeURIComponent(slug)}`);
    const schema = section.schema || {};
    schemaFields = Array.isArray(schema.fields) ? schema.fields : [];
    sectionLabel = section.label || slug;
  } catch {
    schemaFields = [];
    sectionLabel = slug;
  }

  title.textContent = `${labels.items_label} columns`;
  settingsMeta.textContent = `${accountName} · ${labels.sections_label}: ${sectionLabel}`;

  let items = [];
  try {
    const page = await api(`/api/accounts/${accountId}/sections/${encodeURIComponent(slug)}/items?limit=200`);
    items = page.items || [];
  } catch {
    items = [];
  }

  const columns = buildColumns(items, schemaFields);
  const stored = loadColumnPrefs(accountId, slug);
  let visibleKeys = reconcileVisibility(columns, stored.length ? stored : columns.map(c => c.key));

  function renderList(){
    if(!columns.length){
      columnsList.innerHTML = '<p class="small">No columns available.</p>';
      return;
    }
    columnsList.innerHTML = columns.map(col => {
      const checked = visibleKeys.includes(col.key) ? 'checked' : '';
      const disabled = col.locked ? 'disabled' : '';
      const note = col.locked ? '<span class="small" style="margin-left:6px;">Required</span>' : '';
      return `
        <label class="account-card" style="align-items:center;">
          <div style="display:flex;align-items:center;gap:8px;">
            <input type="checkbox" data-key="${escapeHtml(col.key)}" ${checked} ${disabled}>
            <strong>${escapeHtml(col.label)}</strong>${note}
          </div>
        </label>
      `;
    }).join('');
  }

  renderList();

  saveBtn?.addEventListener('click', () => {
    const boxes = columnsList.querySelectorAll('input[type="checkbox"][data-key]');
    const selected = [];
    boxes.forEach(box => {
      if(box.disabled || box.checked){
        selected.push(box.getAttribute('data-key'));
      }
    });
    const merged = reconcileVisibility(columns, selected);
    visibleKeys = merged;
    saveColumnPrefs(accountId, slug, merged);
    saveMessage.textContent = 'Saved. Return to the section to see your updated table.';
  });
})();