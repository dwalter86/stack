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

function formatCellValue(val){
  if(val === null || val === undefined) return '';
  if(typeof val === 'object'){
    try{
      const s = JSON.stringify(val);
      return s.length > 40 ? escapeHtml(s.slice(0, 37) + '…') : escapeHtml(s);
    }catch{
      return escapeHtml(String(val));
    }
  }
  return escapeHtml(String(val));
}

function parseLooseValue(str){
  const trimmed = str.trim();
  if(!trimmed) return '';
  // Try JSON parse for structured/typed values
  try {
    return JSON.parse(trimmed);
  } catch {
    return str;
  }
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

  const backLink = document.getElementById('backLink');
  const titleEl = document.getElementById('sectionTitle');
  const metaEl = document.getElementById('sectionMeta');
  const itemsEmptyState = document.getElementById('itemsEmptyState');
  const itemsTableContainer = document.getElementById('itemsTableContainer');
  const addItemButton = document.getElementById('addItemButton');
  const emptyAddItemButton = document.getElementById('emptyAddItemButton');
  const itemsHeading = document.getElementById('itemsHeading');
  const itemsEmptyCopy = document.getElementById('itemsEmptyCopy');
  const itemModalTitle = document.getElementById('itemModalTitle');

  const menuButton = document.getElementById('sectionMenuButton');
  const menu = document.getElementById('sectionMenu');
  const editSectionMenuLabel = document.getElementById('editSectionMenuLabel');
  const addItemMenuLabel = document.getElementById('addItemMenuLabel');
  const deleteSectionMenuLabel = document.getElementById('deleteSectionMenuLabel');

  const itemModal = document.getElementById('itemModal');
  const itemForm = document.getElementById('itemForm');
  const itemNameInput = document.getElementById('itemName');
  const itemMsg = document.getElementById('itemMsg');
  const itemCancel = document.getElementById('itemCancel');
  const schemaFieldsContainer = document.getElementById('schemaFieldsContainer');
  const kvEditorContainer = document.getElementById('kvEditorContainer');
  const kvRowsTbody = document.getElementById('kvRows');
  const addKVRowBtn = document.getElementById('addKVRowBtn');

  if(itemsHeading){ itemsHeading.textContent = labels.items_label; }
  if(addItemButton){ addItemButton.textContent = `Add ${labels.items_label}`; }
  if(emptyAddItemButton){ emptyAddItemButton.textContent = `Add your first ${labels.items_label.toLowerCase()}`; }
  if(itemsEmptyCopy){ itemsEmptyCopy.textContent = `No ${labels.items_label.toLowerCase()} in this ${labels.sections_label.toLowerCase()} yet.`; }
  if(editSectionMenuLabel){ editSectionMenuLabel.textContent = `Edit ${labels.sections_label}`; }
  if(addItemMenuLabel){ addItemMenuLabel.textContent = `Add ${labels.items_label}`; }
  if(deleteSectionMenuLabel){ deleteSectionMenuLabel.textContent = `Delete ${labels.sections_label}`; }
  if(itemModalTitle){ itemModalTitle.textContent = `Add ${labels.items_label}`; }

  if(backLink){
    backLink.href = `/account.html?id=${encodeURIComponent(accountId)}`;
  }

  let accountName = `Account ${accountId}`;
  try {
    const myAccounts = await api('/api/me/accounts');
    const match = myAccounts.find(a => a.id === accountId);
    if(match) accountName = match.name;
  } catch {
    // ignore
  }

  let currentSection = null;
  let schemaFields = []; // section.schema.fields || []

  async function loadSectionMeta(){
    try {
      const section = await api(`/api/accounts/${accountId}/sections/${encodeURIComponent(slug)}`);
      currentSection = section;
      titleEl.textContent = section.label;
      metaEl.textContent = `${accountName} · slug: ${section.slug}`;
      const s = section.schema || {};
      schemaFields = Array.isArray(s.fields) ? s.fields : [];
      document.title = `${section.label} | ${labels.sections_label}`;
    } catch {
      titleEl.textContent = `Section ${slug}`;
      metaEl.textContent = accountName;
      currentSection = { slug, label: slug, schema: {} };
      schemaFields = [];
      document.title = `${labels.sections_label} ${slug}`;
    }
  }

  function openMenu(){
    menu.classList.add('open');
    menuButton.setAttribute('aria-expanded', 'true');
    const handler = (ev) => {
      if(!menu.contains(ev.target) && ev.target !== menuButton){
        closeMenu();
      }
    };
    document.addEventListener('click', handler, { once:true });
  }

  function closeMenu(){
    menu.classList.remove('open');
    menuButton.setAttribute('aria-expanded', 'false');
  }

  menuButton.addEventListener('click', (e) => {
    e.stopPropagation();
    if(menu.classList.contains('open')) closeMenu(); else openMenu();
  });

  document.addEventListener('keydown', (e) => {
    if(e.key === 'Escape'){
      closeMenu();
      closeItemModal();
    }
  });

  function openItemModal(){
    itemMsg.textContent = '';
    itemForm.reset();
    // Setup UI depending on schema
    if(schemaFields && schemaFields.length){
      schemaFieldsContainer.innerHTML = schemaFields.map(f => {
        const type = (f.type || 'text').toLowerCase();
        const required = f.required ? 'required' : '';
        const keyAttr = `data-key="${escapeHtml(f.key)}" data-type="${escapeHtml(type)}"`;
        if(type === 'textarea'){
          return `<p><label>${escapeHtml(f.label || f.key)}<textarea ${keyAttr} ${required}></textarea></label></p>`;
        } else if(type === 'select' && Array.isArray(f.options)) {
          const opts = f.options.map(o => `<option value="${escapeHtml(String(o))}">${escapeHtml(String(o))}</option>`).join('');
          return `<p><label>${escapeHtml(f.label || f.key)}<select ${keyAttr} ${required}>${opts}</select></label></p>`;
        } else if(type === 'checkbox') {
          return `<p><label><input type="checkbox" ${keyAttr}> ${escapeHtml(f.label || f.key)}</label></p>`;
        } else {
          return `<p><label>${escapeHtml(f.label || f.key)}<input type="text" ${keyAttr} ${required}></label></p>`;
        }
      }).join('');
      schemaFieldsContainer.classList.remove('hidden');
      kvEditorContainer.classList.add('hidden');
    } else {
      // Fallback key/value editor
      schemaFieldsContainer.classList.add('hidden');
      kvEditorContainer.classList.remove('hidden');
      kvRowsTbody.innerHTML = '';
      addKVRow();
    }
    itemModal.classList.remove('hidden');
    setTimeout(() => itemNameInput.focus(), 0);
  }

  function closeItemModal(){
    itemModal.classList.add('hidden');
    itemMsg.textContent = '';
  }

  function addKVRow(key='', value=''){
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><input type="text" class="kv-key" value="${escapeHtml(key)}"></td>
      <td><input type="text" class="kv-value" value="${escapeHtml(value)}"></td>
      <td style="width:1%;white-space:nowrap;">
        <button type="button" class="kv-remove-btn" title="Remove row">×</button>
      </td>
    `;
    const btn = tr.querySelector('.kv-remove-btn');
    btn.addEventListener('click', () => {
      tr.remove();
    });
    kvRowsTbody.appendChild(tr);
  }

  if(addKVRowBtn){
    addKVRowBtn.addEventListener('click', (e) => {
      e.preventDefault();
      addKVRow();
    });
  }

  if(addItemButton){
    addItemButton.addEventListener('click', (e) => {
      e.preventDefault();
      openItemModal();
    });
  }
  if(emptyAddItemButton){
    emptyAddItemButton.addEventListener('click', (e) => {
      e.preventDefault();
      openItemModal();
    });
  }

  if(itemCancel){
    itemCancel.addEventListener('click', (e) => {
      e.preventDefault();
      closeItemModal();
    });
  }

  async function loadItems(){
    try{
      const page = await api(`/api/accounts/${accountId}/sections/${encodeURIComponent(slug)}/items?limit=200`);
      const items = page.items || [];
      if(!items.length){
        itemsEmptyState.classList.remove('hidden');
        itemsTableContainer.innerHTML = '';
        return;
      }
      itemsEmptyState.classList.add('hidden');
      const html = renderItemsTable(items);
      itemsTableContainer.innerHTML = html;
    }catch(e){
      itemsTableContainer.innerHTML = `<p class="small">Failed to load items: ${e.message}</p>`;
      itemsEmptyState.classList.add('hidden');
    }
  }

  function renderItemsTable(items){
    const hasSchema = schemaFields && schemaFields.length;
    if(hasSchema){
      return renderSchemaTable(items, schemaFields);
    }
    return renderAutoTable(items);
  }

  function renderSchemaTable(items, fields){
    const visibleFields = fields.filter(f => f.showInTable !== false);
    const headers = ['Name', ...visibleFields.map(f => f.label || f.key), ''];
    const headerHtml = '<tr>' + headers.map(h => `<th>${escapeHtml(h)}</th>`).join('') + '</tr>';
    const rowsHtml = items.map(it => {
      const cells = [];
      cells.push(`<td>${escapeHtml(it.name)}</td>`);
      for(const f of visibleFields){
        const key = f.key;
        const val = it.data && typeof it.data === 'object' ? it.data[key] : undefined;
        cells.push(`<td>${formatCellValue(val)}</td>`);
      }
      const viewHref = `/item.html?account=${encodeURIComponent(accountId)}&section=${encodeURIComponent(slug)}&item=${encodeURIComponent(it.id)}`;
      cells.push(`<td style="width:1%;white-space:nowrap;"><a class="btn small" href="${viewHref}">View</a></td>`);
      return `<tr>${cells.join('')}</tr>`;
    }).join('');

    return `<div class="table-wrapper"><table><thead>${headerHtml}</thead><tbody>${rowsHtml}</tbody></table></div>`;
  }

  function renderAutoTable(items){
    const keySet = new Set();
    for(const it of items){
      if(it.data && typeof it.data === 'object'){
        Object.keys(it.data).forEach(k => keySet.add(k));
      }
    }
    let keys = Array.from(keySet);
    // Optional: move common keys to front
    const priority = ['title','name','label'];
    keys.sort((a,b) => {
      const ia = priority.indexOf(a.toLowerCase());
      const ib = priority.indexOf(b.toLowerCase());
      if(ia !== -1 && ib === -1) return -1;
      if(ib !== -1 && ia === -1) return 1;
      return a.localeCompare(b);
    });
    // Limit columns to avoid over-wide tables
    const MAX_COLS = 8;
    if(keys.length > MAX_COLS) keys = keys.slice(0, MAX_COLS);

    const headers = ['Name', ...keys, ''];
    const headerHtml = '<tr>' + headers.map(h => `<th>${escapeHtml(h)}</th>`).join('') + '</tr>';

    const rowsHtml = items.map(it => {
      const cells = [];
      cells.push(`<td>${escapeHtml(it.name)}</td>`);
      for(const k of keys){
        const val = it.data && typeof it.data === 'object' ? it.data[k] : undefined;
        cells.push(`<td>${formatCellValue(val)}</td>`);
      }
      const viewHref = `/item.html?account=${encodeURIComponent(accountId)}&section=${encodeURIComponent(slug)}&item=${encodeURIComponent(it.id)}`;
      cells.push(`<td style="width:1%;white-space:nowrap;"><a class="btn small" href="${viewHref}">View</a></td>`);
      return `<tr>${cells.join('')}</tr>`;
    }).join('');

    return `<div class="table-wrapper"><table><thead>${headerHtml}</thead><tbody>${rowsHtml}</tbody></table></div>`;
  }

  // Item form submit
  itemForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    itemMsg.textContent = 'Saving…';
    const name = itemNameInput.value.trim();
    if(!name){
      itemMsg.textContent = 'Name is required.';
      return;
    }

    let data = {};
    if(schemaFields && schemaFields.length){
      const inputs = schemaFieldsContainer.querySelectorAll('[data-key]');
      inputs.forEach(el => {
        const key = el.getAttribute('data-key');
        const type = (el.getAttribute('data-type') || 'text').toLowerCase();
        if(type === 'checkbox'){
          data[key] = el.checked;
        } else {
          data[key] = parseLooseValue(el.value);
        }
      });
    } else {
      const rows = kvRowsTbody.querySelectorAll('tr');
      rows.forEach(row => {
        const kInput = row.querySelector('.kv-key');
        const vInput = row.querySelector('.kv-value');
        if(!kInput) return;
        const key = kInput.value.trim();
        if(!key) return;
        const raw = vInput ? vInput.value : '';
        data[key] = parseLooseValue(raw);
      });
    }

    try {
      await api(`/api/accounts/${accountId}/sections/${encodeURIComponent(slug)}/items`, {
        method:'POST',
        body: JSON.stringify({ name, data })
      });
      itemMsg.textContent = 'Item added.';
      closeItemModal();
      await loadItems();
    } catch(err){
      itemMsg.textContent = err.message || 'Failed to add item';
    }
  });

  // 3-dot menu actions
  menu.addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-action]');
    if(!btn) return;
    const action = btn.dataset.action;
    closeMenu();

    if(action === 'add-item'){
      openItemModal();
    } else if(action === 'edit'){
      const currentLabel = currentSection?.label || slug;
      const next = prompt('Section name', currentLabel);
      if(!next) return;
      const trimmed = next.trim();
      if(!trimmed || trimmed === currentLabel) return;
      try {
        const updated = await api(`/api/accounts/${accountId}/sections/${encodeURIComponent(slug)}`, {
          method:'PUT',
          body: JSON.stringify({ label: trimmed, schema: currentSection?.schema || {} })
        });
        currentSection = updated;
        titleEl.textContent = updated.label;
      } catch(err){
        alert(err.message || 'Failed to update section');
      }
    } else if(action === 'delete'){
      if(!confirm('Delete this section and all its items? This cannot be undone.')){
        return;
      }
      try {
        await api(`/api/accounts/${accountId}/sections/${encodeURIComponent(slug)}`, { method:'DELETE' });
        window.location.replace(`/account.html?id=${encodeURIComponent(accountId)}`);
      } catch(err){
        alert(err.message || 'Failed to delete section');
      }
    }
  });

  await loadSectionMeta();
  await loadItems();
})();
