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

function renderObjectTable(obj){
  const entries = Object.entries(obj || {});
  if(!entries.length) return '<span class="muted">{}</span>';
  const rows = entries.map(([k, v]) => `<tr><th>${escapeHtml(k)}</th><td>${renderValueHtml(v)}</td></tr>`).join('');
  return `<table class="nested-table"><tbody>${rows}</tbody></table>`;
}

function renderArrayTable(arr){
  if(!arr.length) return '<span class="muted">[]</span>';

  const objectRows = arr.every(v => v && typeof v === 'object' && !Array.isArray(v));
  if(objectRows){
    const keys = Array.from(new Set(arr.flatMap(v => Object.keys(v || {}))));
    if(keys.length){
      const header = `<tr><th></th>${keys.map(k => `<th>${escapeHtml(k)}</th>`).join('')}</tr>`;
      const rows = arr.map((v, idx) => {
        const cells = keys.map(k => `<td>${renderValueHtml((v || {})[k])}</td>`).join('');
        return `<tr><th class="muted">#${idx + 1}</th>${cells}</tr>`;
      }).join('');
      return `<table class="nested-table"><thead>${header}</thead><tbody>${rows}</tbody></table>`;
    }
  }

  const rows = arr.map((item, idx) => `<tr><th class="muted">[${idx}]</th><td>${renderValueHtml(item)}</td></tr>`).join('');
  return `<table class="nested-table"><tbody>${rows}</tbody></table>`;
}

function isImageUrl(url){
  try {
    const parsed = new URL(url);
    if(parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return false;
    const path = parsed.pathname.toLowerCase();
    return ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'].some(ext => path.endsWith(ext));
  } catch {
    return false;
  }
}

function extractImageUrls(str){
  const urlPattern = /(https?:\/\/(?:(?!https?:\/\/)[^\s])+)/gi;
  const matches = [];
  let m;
  while((m = urlPattern.exec(str))){
    matches.push(m[1].replace(/[)\]\s.,;]+$/, ''));
  }
  return matches.filter(isImageUrl);
}

function renderImages(urls){
  if(!urls.length) return '';
  const images = urls.map(u => {
    const safeUrl = escapeHtml(u);
    return `<a class="item-image-link" href="${safeUrl}" target="_blank" rel="noopener noreferrer"><img class="item-image" src="${safeUrl}" alt="Item attachment"></a>`;
  }).join('');
  return `<div class="item-image-list">${images}</div>`;
}

function renderValueHtml(val){
  if(val === null || val === undefined){
    return '<span class="muted">(empty)</span>';
  }

  if(Array.isArray(val)){
    return renderArrayTable(val);
  }

  if(typeof val === 'object'){
    return renderObjectTable(val);
  }

  const str = String(val);
  const imageUrls = extractImageUrls(str);
  if(imageUrls.length){
    const images = renderImages(imageUrls);
    if(str.trim() === imageUrls[0] && imageUrls.length === 1){
      return images;
    }
    return `${images}<div class="small">${escapeHtml(str)}</div>`;
  }

  if(str.includes('\n')){
    return `<pre style="margin:0;white-space:pre-wrap;">${escapeHtml(str)}</pre>`;
  }
  return escapeHtml(str);
}

function templatePrefKey(accountId, slug){
  return `columnTemplate:${accountId}:${slug||'default'}`;
}

function columnPrefKey(accountId, slug){
  return `columnPrefs:${accountId}:${slug||'default'}`;
}

function columnCountKey(accountId, slug){
  return `columnCount:${accountId}:${slug||'default'}`;
}

function loadColumnCount(accountId, slug){
  try {
    const raw = localStorage.getItem(columnCountKey(accountId, slug));
    if(!raw) return null;
    const parsed = parseInt(raw, 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  } catch {
    return null;
  }
}

function saveColumnCount(accountId, slug, val){
  localStorage.setItem(columnCountKey(accountId, slug), String(val));
}

function loadColumnPrefs(accountId, slug){
  try {
    const raw = localStorage.getItem(columnPrefKey(accountId, slug));
    if(!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function loadColumnTemplate(accountId, slug){
  try {
    const raw = localStorage.getItem(templatePrefKey(accountId, slug));
    if(!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function parseTemplate(tpl){
  if(!tpl || typeof tpl !== 'object') return { fields: [] };
  const data = tpl.data && typeof tpl.data === 'object' ? tpl.data : {};
  const fields = Object.entries(data).map(([key, val], idx) => {
    const type = (val?.type || 'string').toLowerCase();
    const orderRaw = val?.order;
    const parsedOrder = typeof orderRaw === 'number' ? orderRaw : (typeof orderRaw === 'string' ? parseInt(orderRaw, 10) : null);
    let options = [];
    if(type === 'dropdown'){
      if(Array.isArray(val?.options)){
        options = val.options.map(o => String(o));
      } else if(val?.options && typeof val.options === 'object'){
        options = Object.values(val.options).map(o => String(o));
      }
    }
    return {
      key,
      label: val?.friendlyname || key,
      type,
      options,
      order: Number.isFinite(parsedOrder) ? parsedOrder : null,
      index: idx,
    };
  });

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

function formatDateTime(val){
  if(!val) return '';
  try {
    return new Date(val).toLocaleString();
  } catch {
    return String(val);
  }
}

(async () => {
  const me = await loadMeOrRedirect(); if(!me) return;
  renderShell(me);
  const labels = getLabels(me);
  document.title = labels.items_label;

  const accountId = qs('account');
  const sectionSlug = qs('section');
  const itemId = qs('item');

  const backToSection = document.getElementById('backToSection');
  const itemNameEl = document.getElementById('itemName');
  const itemMetaEl = document.getElementById('itemMeta');
  const itemPropsBody = document.getElementById('itemProperties');
  const itemRawStructured = document.getElementById('itemRawStructured');
  const itemRaw = document.getElementById('itemRaw');
  const columnCountCard = document.getElementById('columnCountCard');
  const columnCountForm = document.getElementById('columnCountForm');
  const columnCountInput = document.getElementById('columnCountInput');
  const columnCountMessage = document.getElementById('columnCountMessage');

  if(!accountId || !itemId){
    document.body.innerHTML = '<main class="container"><p>Missing account or item id.</p></main>';
    return;
  }

  if(backToSection){
    if(sectionSlug){
      backToSection.href = `/section.html?account=${encodeURIComponent(accountId)}&slug=${encodeURIComponent(sectionSlug)}`;
    } else {
      backToSection.href = `/account.html?id=${encodeURIComponent(accountId)}`;
    }
  }

  let accountName = `Account ${accountId}`;
  try {
    const myAccounts = await api('/api/me/accounts');
    const match = myAccounts.find(a => a.id === accountId);
    if(match) accountName = match.name;
  } catch {
    // ignore
  }

  let section = null;
  let schemaFields = [];
  let templateFields = [];
  if(sectionSlug){
    try {
      section = await api(`/api/accounts/${accountId}/sections/${encodeURIComponent(sectionSlug)}`);
      const s = section.schema || {};
      schemaFields = Array.isArray(s.fields) ? s.fields : [];
      const columnTemplate = loadColumnTemplate(accountId, sectionSlug);
      templateFields = orderFields(parseTemplate(columnTemplate).fields);
    } catch {
      section = null;
      schemaFields = [];
      templateFields = [];
    }

    const storedCount = loadColumnCount(accountId, sectionSlug);
    const visiblePref = loadColumnPrefs(accountId, sectionSlug);
    const fallbackCount = visiblePref.length ? visiblePref.length : null;
    const initialCount = Number.isFinite(storedCount) ? storedCount : fallbackCount;
    if(columnCountCard){
      columnCountCard.style.display = 'block';
      if(columnCountInput && initialCount){
        columnCountInput.value = initialCount;
      }
      if(columnCountForm){
        columnCountForm.addEventListener('submit', (ev) => {
          ev.preventDefault();
          if(!columnCountInput) return;
          columnCountMessage.textContent = '';
          const parsed = parseInt(columnCountInput.value, 10);
          if(!Number.isFinite(parsed) || parsed < 1){
            columnCountMessage.textContent = 'Enter a number of columns (minimum 1).';
            return;
          }
          saveColumnCount(accountId, sectionSlug, parsed);
          columnCountMessage.textContent = 'Saved. Refresh the section to apply this column count.';
        });
      }
    }
  }

  try {
    const item = await api(`/api/accounts/${accountId}/items/${encodeURIComponent(itemId)}`);
    itemNameEl.textContent = item.name;
    const sectionLabel = section ? section.label : (sectionSlug || 'No section');
    const createdCopy = item.created_at ? ` · Added ${formatDateTime(item.created_at)}` : '';
    itemMetaEl.textContent = `${accountName} · ${labels.sections_label}: ${sectionLabel} · id: ${itemId}${createdCopy}`;
    document.title = `${item.name} | ${labels.items_label}`;

    const data = item.data || {};
    const rows = [];

    // If schema present, respect its order/labels
    const templateByKey = new Map(templateFields.map(f => [f.key, f]));

    if(schemaFields.length){
      const usedKeys = new Set();
      for(const f of schemaFields){
        const key = f.key;
        usedKeys.add(key);
        const tplField = templateByKey.get(key);
        const label = tplField?.label || f.label || key;
        const val = data ? data[key] : undefined;
        rows.push({ label, value: val });
      }
      // Include any extra keys not in schema at the bottom
      if(data && typeof data === 'object'){
        Object.keys(data).forEach(k => {
          if(usedKeys.has(k)) return;
          const tplField = templateByKey.get(k);
          rows.push({ label: tplField?.label || k, value: data[k] });
        });
      }
    } else if(templateFields.length){
      const usedKeys = new Set();
      for(const f of templateFields){
        const key = f.key;
        usedKeys.add(key);
        rows.push({ label: f.label || key, value: data ? data[key] : undefined });
      }
      if(data && typeof data === 'object'){
        Object.keys(data).forEach(k => {
          if(usedKeys.has(k)) return;
          rows.push({ label: k, value: data[k] });
        });
      }
    } else {
      // No schema: list keys in default object order
      if(data && typeof data === 'object'){
        Object.keys(data).forEach(k => {
          rows.push({ label: k, value: data[k] });
        });
      }
    }

    if(!rows.length){
      itemPropsBody.innerHTML = `<tr><td class="small" colspan="2">No properties for this ${labels.items_label.toLowerCase()}.</td></tr>`;
    } else {
      itemPropsBody.innerHTML = rows.map(r => `
        <tr>
          <th>${escapeHtml(r.label)}</th>
          <td>${renderValueHtml(r.value)}</td>
        </tr>
      `).join('');
    }

    if(itemRawStructured){
      try {
        itemRawStructured.innerHTML = renderValueHtml(item.data || {});
      } catch {
        itemRawStructured.innerHTML = '<span class="small">Unable to render structured view.</span>';
      }
    }

    try {
      itemRaw.textContent = JSON.stringify(item.data || {}, null, 2);
    } catch {
      itemRaw.textContent = String(item.data || '');
    }
  } catch(e){
    itemNameEl.textContent = 'Item not found';
    itemMetaEl.textContent = e.message || 'Failed to load item.';
    itemPropsBody.innerHTML = '';
    itemRaw.textContent = '';
  }
})();
