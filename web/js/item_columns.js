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

function columnCountKey(accountId, slug){
  return `columnCount:${accountId}:${slug||'default'}`;
}

function loadColumnCount(accountId, slug){
  try {
    const raw = localStorage.getItem(columnCountKey(accountId, slug));
    if(!raw) return null;
    const num = parseInt(raw, 10);
    return Number.isFinite(num) && num > 0 ? num : null;
  } catch {
    return null;
  }
}

function saveColumnCount(accountId, slug, val){
  localStorage.setItem(columnCountKey(accountId, slug), String(val));
}

function templatePrefKey(accountId, slug){
  return `columnTemplate:${accountId}:${slug||'default'}`;
}

function loadTemplate(accountId, slug){
  try {
    const raw = localStorage.getItem(templatePrefKey(accountId, slug));
    if(!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function saveTemplate(accountId, slug, tpl){
  localStorage.setItem(templatePrefKey(accountId, slug), JSON.stringify(tpl));
}

const SAMPLE_TEMPLATE = {
  name: 'template',
  fields: [
    {
      key: 'contact',
      label: 'Contact',
      type: 'string',
      order: 1,
    },
    {
      key: 'amount',
      label: 'Amount',
      type: 'number',
      order: 2,
    },
    {
      key: 'status',
      label: 'Status',
      type: 'dropdown',
      order: 3,
      options: ['Ready', 'Done', 'In Progress'],
    },
  ]
};

function normalizeField(field, idx = 0){
  if(!field || typeof field !== 'object') return null;
  const key = field.key || field.name;
  if(!key) return null;
  const type = (field.type || 'string').toLowerCase();
  const orderRaw = field.order;
  const parsedOrder = typeof orderRaw === 'number' ? orderRaw : (typeof orderRaw === 'string' ? parseInt(orderRaw, 10) : null);
  let options = [];
  if(type === 'dropdown'){
    if(Array.isArray(field.options)){
      options = field.options.map(o => String(o));
    } else if(field.options && typeof field.options === 'object'){
      options = Object.values(field.options).map(o => String(o));
    }
  }
  return {
    key,
    label: field.label || field.friendlyname || key,
    type,
    options,
    order: Number.isFinite(parsedOrder) ? parsedOrder : null,
    index: idx,
  };
}

function parseTemplate(tpl){
  if(!tpl || typeof tpl !== 'object') return { fields: [] };

  if(Array.isArray(tpl.fields)){
    const normalizedFields = tpl.fields.map((f, idx) => normalizeField(f, idx)).filter(Boolean);
    normalizedFields.sort((a, b) => {
      const orderA = a.order ?? Number.MAX_SAFE_INTEGER;
      const orderB = b.order ?? Number.MAX_SAFE_INTEGER;
      if(orderA !== orderB) return orderA - orderB;
      return a.index - b.index;
    });
    return { fields: normalizedFields };
  }

  const data = tpl.data && typeof tpl.data === 'object' ? tpl.data : {};
  const fields = Object.entries(data)
    .map(([key, val], idx) => normalizeField({ key, ...(val || {}) }, idx))
    .filter(Boolean);

  fields.sort((a, b) => {
    const orderA = a.order ?? Number.MAX_SAFE_INTEGER;
    const orderB = b.order ?? Number.MAX_SAFE_INTEGER;
    if(orderA !== orderB) return orderA - orderB;
    return a.index - b.index;
  });

  return { fields };
}

function orderFields(fields){
  return [...(fields || [])].sort((a, b) => {
    const orderA = a?.order ?? Number.MAX_SAFE_INTEGER;
    const orderB = b?.order ?? Number.MAX_SAFE_INTEGER;
    if(orderA !== orderB) return orderA - orderB;
    const labelA = a?.label || a?.key || '';
    const labelB = b?.label || b?.key || '';
    return labelA.localeCompare(labelB, undefined, { sensitivity: 'base' });
  });
}

function fieldsToTemplate(slug, fields){
  if(!fields || !fields.length) return null;
  const cleaned = fields.map(({ index, ...rest }) => rest).filter(f => f && f.key);
  return { name: slug || 'template', fields: cleaned };
}

function getAutoKeys(items){
  const keySet = new Set();
  for(const it of items){
    if(it.data && typeof it.data === 'object'){
      Object.keys(it.data).forEach(k => keySet.add(k));
    }
  }
  let keys = Array.from(keySet);
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
    const visibleFields = orderFields(schemaFields.filter(f => f.showInTable !== false));
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
  const columnCountForm = document.getElementById('columnCountForm');
  const columnCountInput = document.getElementById('columnCountInput');
  const columnCountMessage = document.getElementById('columnCountMessage');
  const title = document.getElementById('settingsTitle');
  const templateInput = document.getElementById('templateInput');
  const templateExample = document.getElementById('templateExample');
  const templateMessage = document.getElementById('templateMessage');
  const saveTemplateBtn = document.getElementById('saveTemplateBtn');

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
  const storedTemplate = loadTemplate(accountId, slug);
  const parsedTemplate = parseTemplate(storedTemplate);
  let schemaFields = parsedTemplate.fields || [];
  let apiTemplate = null;
  try {
    const section = await api(`/api/accounts/${accountId}/sections/${encodeURIComponent(slug)}`);
    const schema = section.schema || {};
    const apiFields = parseTemplate(schema).fields || [];
    if(!schemaFields.length){
      schemaFields = apiFields;
    }
    sectionLabel = section.label || slug;
    apiTemplate = fieldsToTemplate(slug, apiFields);
  } catch {
    if(!schemaFields.length){
      schemaFields = [];
    }
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

  if(templateExample){
    templateExample.textContent = JSON.stringify(SAMPLE_TEMPLATE, null, 2);
  }
  if(templateInput){
    const tplToShow = storedTemplate || apiTemplate || SAMPLE_TEMPLATE;
    templateInput.value = JSON.stringify(tplToShow, null, 2);
  }

  let columns = buildColumns(items, schemaFields);
  const stored = loadColumnPrefs(accountId, slug);
  let visibleKeys = reconcileVisibility(columns, stored.length ? stored : columns.map(c => c.key));

  const storedCount = loadColumnCount(accountId, slug);
  const fallbackCount = visibleKeys.length ? visibleKeys.length : null;
  const initialCount = Number.isFinite(storedCount) ? storedCount : fallbackCount;
  if(columnCountInput && initialCount){
    columnCountInput.value = initialCount;
  }

  columnCountForm?.addEventListener('submit', (ev) => {
    ev.preventDefault();
    if(!columnCountInput) return;
    columnCountMessage.textContent = '';
    const parsed = parseInt(columnCountInput.value, 10);
    if(!Number.isFinite(parsed) || parsed < 1){
      columnCountMessage.textContent = 'Enter a number of columns (minimum 1).';
      return;
    }
    saveColumnCount(accountId, slug, parsed);
    columnCountMessage.textContent = 'Saved. Return to the section to see your updated table.';
  });

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

  saveTemplateBtn?.addEventListener('click', () => {
    templateMessage.textContent = '';
    if(!templateInput){
      templateMessage.textContent = 'Template editor not found.';
      return;
    }
    const raw = templateInput.value.trim();
    if(!raw){
      templateMessage.textContent = 'Enter a template to save.';
      return;
    }
    try {
      const parsed = JSON.parse(raw);
      const normalized = parseTemplate(parsed);
      if(!normalized.fields.length){
        templateMessage.textContent = 'Template must include at least one column (fields array or data object).';
        return;
      }
      const storedPayload = { name: slug, fields: normalized.fields.map(({ index, ...rest }) => rest) };
      saveTemplate(accountId, slug, storedPayload);
      schemaFields = normalized.fields;
      columns = buildColumns(items, schemaFields);
      visibleKeys = reconcileVisibility(columns, columns.map(c => c.key));
      saveColumnPrefs(accountId, slug, visibleKeys);
      renderList();
      templateInput.value = JSON.stringify(storedPayload, null, 2);
      templateMessage.textContent = 'Template saved. Column visibility was refreshed from the template.';
    } catch(err){
      templateMessage.textContent = `Could not parse JSON: ${err.message}`;
    }
  });
})();