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

function formatValue(val){
  if(val === null || val === undefined) return '';
  if(typeof val === 'object'){
    try{
      return escapeHtml(JSON.stringify(val, null, 2));
    }catch{
      return escapeHtml(String(val));
    }
  }
  return escapeHtml(String(val));
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
  const itemRaw = document.getElementById('itemRaw');

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
  if(sectionSlug){
    try {
      section = await api(`/api/accounts/${accountId}/sections/${encodeURIComponent(sectionSlug)}`);
      const s = section.schema || {};
      schemaFields = Array.isArray(s.fields) ? s.fields : [];
    } catch {
      section = null;
      schemaFields = [];
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
    if(schemaFields.length){
      const usedKeys = new Set();
      for(const f of schemaFields){
        const key = f.key;
        usedKeys.add(key);
        const label = f.label || key;
        const val = data ? data[key] : undefined;
        rows.push({ label, value: val });
      }
      // Include any extra keys not in schema at the bottom
      if(data && typeof data === 'object'){
        Object.keys(data).forEach(k => {
          if(usedKeys.has(k)) return;
          rows.push({ label: k, value: data[k] });
        });
      }
    } else {
      // No schema: list keys alphabetically
      if(data && typeof data === 'object'){
        Object.keys(data).sort().forEach(k => {
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
          <td><pre style="margin:0;white-space:pre-wrap;">${formatValue(r.value)}</pre></td>
        </tr>
      `).join('');
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
