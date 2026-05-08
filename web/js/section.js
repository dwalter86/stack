import { loadMeOrRedirect, renderShell, api, getLabels, getPreferences, escapeHtml, getToken } from './common.js';

function qs(name) {
  const m = new URLSearchParams(location.search).get(name);
  return m && decodeURIComponent(m);
}

function parseStatusSummaryConfig(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const fieldKey = String(raw.field_key || '').trim();
  if (!raw.enabled || !fieldKey) return null;

  const normalizeValues = (arr) => {
    if (!Array.isArray(arr)) return new Set();
    return new Set(
      arr
        .map(v => String(v).trim().toLowerCase())
        .filter(Boolean)
    );
  };

  const red = normalizeValues(raw.red_values);
  const yellow = normalizeValues(raw.yellow_values);
  const green = normalizeValues(raw.green_values);
  if (!red.size && !yellow.size && !green.size) return null;

  return { fieldKey, red, yellow, green };
}

function getItemStatusClass(item, config) {
  if (!config) return '';
  const value = String(item?.data?.[config.fieldKey] ?? '').trim().toLowerCase();
  if (!value) return '';
  if (config.red.has(value)) return 'item-row-status-red';
  if (config.yellow.has(value)) return 'item-row-status-yellow';
  if (config.green.has(value)) return 'item-row-status-green';
  return '';
}

function renderObjectTable(obj) {
  const entries = Object.entries(obj || {});
  if (!entries.length) return '<span class="muted">{}</span>';
  const rows = entries.map(([k, v]) => `<tr><th>${escapeHtml(k)}</th><td>${renderStructuredValue(v)}</td></tr>`).join('');
  return `<table class="nested-table"><tbody>${rows}</tbody></table>`;
}

function renderArrayTable(arr) {
  if (!arr.length) return '<span class="muted">[]</span>';

  const objectRows = arr.every(v => v && typeof v === 'object' && !Array.isArray(v));
  if (objectRows) {
    const keys = Array.from(new Set(arr.flatMap(v => Object.keys(v || {}))));
    if (keys.length) {
      const header = `<tr><th></th>${keys.map(k => `<th>${escapeHtml(k)}</th>`).join('')}</tr>`;
      const rows = arr.map((v, idx) => {
        const cells = keys.map(k => `<td>${renderStructuredValue((v || {})[k])}</td>`).join('');
        return `<tr><th class="muted">#${idx + 1}</th>${cells}</tr>`;
      }).join('');
      return `<table class="nested-table"><thead>${header}</thead><tbody>${rows}</tbody></table>`;
    }
  }

  const rows = arr.map((item, idx) => `<tr><th class="muted">[${idx}]</th><td>${renderStructuredValue(item)}</td></tr>`).join('');
  return `<table class="nested-table"><tbody>${rows}</tbody></table>`;
}

function renderStructuredValue(val) {
  if (val === null || val === undefined) {
    return '<span class="muted">(empty)</span>';
  }

  if (Array.isArray(val)) {
    return renderArrayTable(val);
  }

  if (typeof val === 'object') {
    return renderObjectTable(val);
  }

  const str = String(val);
  if (str.includes('\n')) {
    return `<pre style="margin:0;white-space:pre-wrap;">${escapeHtml(str)}</pre>`;
  }
  return escapeHtml(str);
}

function formatCellValue(val) {
  if (val === null || val === undefined) return '';
  if (typeof val === 'object') {
    const isArray = Array.isArray(val);
    const summary = isArray
      ? `Array (${val.length})`
      : `Object${Object.keys(val).length ? ` (${Object.keys(val).length} keys)` : ''}`;
    return `<details class="struct-preview"><summary>${escapeHtml(summary)}</summary>${renderStructuredValue(val)}</details>`;
  }
  return escapeHtml(String(val));
}

function parseLooseValue(str) {
  const trimmed = str.trim();
  if (!trimmed) return '';
  // Try JSON parse for structured/typed values
  try {
    return JSON.parse(trimmed);
  } catch {
    return str;
  }
}

function formatDateTime(val) {
  if (!val) return '';
  try {
    return new Date(val).toLocaleString();
  } catch {
    return String(val);
  }
}

function normalizeExportValue(val) {
  if (val === null || val === undefined) return '';
  if (val instanceof Date) {
    return val.toISOString();
  }
  if (typeof val === 'object') {
    try {
      return JSON.stringify(val);
    } catch {
      return String(val);
    }
  }
  return String(val);
}

function normalizeOptions(raw) {
  if (Array.isArray(raw)) {
    return raw.map(o => String(o));
  }
  if (raw && typeof raw === 'object') {
    return Object.values(raw).map(o => String(o));
  }
  return [];
}

function columnPrefKey(accountId, slug) {
  return `columnPrefs:${accountId}:${slug || 'default'}`;
}

function loadColumnPrefs(accountId, slug) {
  try {
    const raw = localStorage.getItem(columnPrefKey(accountId, slug));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function templatePrefKey(accountId, slug) {
  return `columnTemplate:${accountId}:${slug || 'default'}`;
}

function loadColumnTemplate(accountId, slug) {
  try {
    const raw = localStorage.getItem(templatePrefKey(accountId, slug));
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function normalizeField(field, idx = 0) {
  if (!field || typeof field !== 'object') return null;
  const key = field.key || field.name;
  if (!key) return null;
  const type = (field.type || 'string').toLowerCase();
  const orderRaw = field.order;
  const parsedOrder = typeof orderRaw === 'number' ? orderRaw : (typeof orderRaw === 'string' ? parseInt(orderRaw, 10) : null);
  let options = [];
  if (type === 'dropdown' || type === 'select') {
    if (Array.isArray(field.options)) {
      options = field.options.map(o => String(o));
    } else if (field.options && typeof field.options === 'object') {
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
    required: field.required,
  };
}

function parseTemplate(tpl) {
  if (!tpl || typeof tpl !== 'object') return { fields: [] };

  if (Array.isArray(tpl.fields)) {
    const normalizedFields = tpl.fields.map((f, idx) => normalizeField(f, idx)).filter(Boolean);
    normalizedFields.sort((a, b) => {
      const orderA = a.order ?? Number.MAX_SAFE_INTEGER;
      const orderB = b.order ?? Number.MAX_SAFE_INTEGER;
      if (orderA !== orderB) return orderA - orderB;
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
    if (orderA !== orderB) return orderA - orderB;
    return a.index - b.index;
  });

  return { fields };
}

function orderFields(fields) {
  return [...(fields || [])].sort((a, b) => {
    const orderA = a?.order ?? Number.MAX_SAFE_INTEGER;
    const orderB = b?.order ?? Number.MAX_SAFE_INTEGER;
    if (orderA !== orderB) return orderA - orderB;
    const labelA = a?.label || a?.key || '';
    const labelB = b?.label || b?.key || '';
    return labelA.localeCompare(labelB, undefined, { sensitivity: 'base' });
  });
}

function reconcileVisibility(columns, stored) {
  const available = new Set(columns.map(c => c.key));
  const result = [];
  for (const key of stored) {
    if (available.has(key) && !result.includes(key)) result.push(key);
  }
  for (const col of columns) {
    if (col.locked && !result.includes(col.key)) result.unshift(col.key);
  }
  if (!result.length) {
    return columns.map(c => c.key);
  }
  return result;
}

function getAutoKeys(items) {
  const keys = [];
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i];
    if (it.data && typeof it.data === 'object') {
      Object.keys(it.data).forEach(k => {
        if (!keys.includes(k)) keys.push(k);
      });
    }
  }
  const MAX_COLS = 8;
  return keys.slice(0, MAX_COLS);
}

function columnCountKey(accountId, slug) {
  return `columnCount:${accountId}:${slug || 'default'}`;
}

function loadColumnCount(accountId, slug) {
  try {
    const raw = localStorage.getItem(columnCountKey(accountId, slug));
    if (!raw) return null;
    const parsed = parseInt(raw, 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  } catch {
    return null;
  }
}

function sortPrefKey(accountId, slug) {
  return `sortPref:${accountId}:${slug || 'default'}`;
}

function loadSortPref(accountId, slug) {
  try {
    const raw = localStorage.getItem(sortPrefKey(accountId, slug));
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function saveSortPref(accountId, slug, sortState) {
  try {
    localStorage.setItem(sortPrefKey(accountId, slug), JSON.stringify(sortState));
  } catch {
    // ignore
  }
}

function itemZoomPrefKey(accountId, slug) {
  return `itemZoom:${accountId}:${slug || 'default'}`;
}

function normalizeZoomValue(value) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) return 100;
  return Math.max(10, Math.min(200, parsed));
}

function loadItemZoom(accountId, slug) {
  try {
    const raw = localStorage.getItem(itemZoomPrefKey(accountId, slug));
    return normalizeZoomValue(raw);
  } catch {
    return 100;
  }
}

function saveItemZoom(accountId, slug, zoomPercent) {
  try {
    localStorage.setItem(itemZoomPrefKey(accountId, slug), String(normalizeZoomValue(zoomPercent)));
  } catch {
    // ignore
  }
}

(async () => {
  const me = await loadMeOrRedirect(); if (!me) return;
  renderShell(me);
  const labels = getLabels(me);
  const preferences = getPreferences(me);
  const showSlugs = preferences.show_slugs;

  const accountId = qs('account');
  const slug = qs('slug');
  if (!accountId || !slug) {
    document.body.innerHTML = '<main class="container"><p>Missing account or section.</p></main>';
    return;
  }

  const backLink = document.getElementById('backLink');
  const titleEl = document.getElementById('sectionTitle');
  const metaEl = document.getElementById('sectionMeta');
  const itemsEmptyState = document.getElementById('itemsEmptyState');
  const itemsTableContainer = document.getElementById('itemsTableContainer');
  const itemsHeading = document.getElementById('itemsHeading');
  const itemsEmptyCopy = document.getElementById('itemsEmptyCopy');
  const itemModalTitle = document.getElementById('itemModalTitle');
  const exportBtn = document.getElementById('exportItemsBtn');
  const notesBtn = document.getElementById('notesBtn');
  const addItemBtn = document.getElementById('addItemBtn');

  const menuButton = document.getElementById('sectionMenuButton');
  const menu = document.getElementById('sectionMenu');
  const editSectionMenuLabel = document.getElementById('editSectionMenuLabel');
  const itemSettingsMenuLabel = document.getElementById('itemSettingsMenuLabel');
  const deleteSectionMenuLabel = document.getElementById('deleteSectionMenuLabel');

  const itemSearch = document.getElementById('itemSearch');
  const itemsZoomOutBtn = document.getElementById('itemsZoomOutBtn');
  const itemsZoomResetBtn = document.getElementById('itemsZoomResetBtn');
  const itemsZoomInBtn = document.getElementById('itemsZoomInBtn');

  const itemModal = document.getElementById('itemModal');
  const itemForm = document.getElementById('itemForm');
  const itemNameInput = document.getElementById('itemName');
  const itemMsg = document.getElementById('itemMsg');
  const itemCancel = document.getElementById('itemCancel');
  const editSectionModal = document.getElementById('editSectionModal');
  const editSectionForm = document.getElementById('editSectionForm');
  const editSectionLabelInput = document.getElementById('editSectionLabel');
  const editSectionDetailInput = document.getElementById('editSectionDetail');
  const editSectionCancel = document.getElementById('editSectionCancel');
  const editSectionMsg = document.getElementById('editSectionMsg');
  const schemaFieldsContainer = document.getElementById('schemaFieldsContainer');
  const kvEditorContainer = document.getElementById('kvEditorContainer');
  const kvRowsTbody = document.getElementById('kvRows');
  const addKVRowBtn = document.getElementById('addKVRowBtn');

  const isReadOnly = me.user_type === 'standard';

  if (itemsHeading) { itemsHeading.textContent = labels.items_label; }
  if (itemsEmptyCopy) { itemsEmptyCopy.textContent = `No ${labels.items_label.toLowerCase()} in this ${labels.sections_label.toLowerCase()} yet. Use the Add button to create one.`; }
  if (editSectionMenuLabel) { editSectionMenuLabel.textContent = `Edit ${labels.sections_label}`; }
  if (itemSettingsMenuLabel) { itemSettingsMenuLabel.textContent = 'Settings'; }
  if (deleteSectionMenuLabel) { deleteSectionMenuLabel.textContent = `Delete ${labels.sections_label}`; }
  if (itemModalTitle) { itemModalTitle.textContent = `Add ${labels.items_label}`; }
  if (exportBtn) { exportBtn.disabled = true; }

  if (backLink) {
    backLink.href = `/account.html?id=${encodeURIComponent(accountId)}`;
  }
  if (notesBtn) {
    notesBtn.href = `/notes.html?account_id=${encodeURIComponent(accountId)}&section_slug=${encodeURIComponent(slug)}`;
  }

  async function updateNotesButtonLabel() {
    if (!notesBtn) return;
    notesBtn.textContent = 'Notes';
    try {
      const notes = await api(`/api/accounts/${accountId}/sections/${encodeURIComponent(slug)}/notes`);
      const noteCount = Array.isArray(notes) ? notes.length : 0;
      if (noteCount > 0) {
        notesBtn.textContent = `Notes x ${noteCount}`;
      }
    } catch {
      // Keep default label when notes cannot be loaded.
    }
  }

  let accountName = `Account ${accountId}`;
  try {
    const myAccounts = await api('/api/me/accounts');
    const match = myAccounts.find(a => a.id === accountId);
    if (match) accountName = match.name;
  } catch {
    // ignore
  }

  let currentSection = null;
  const templateFromPrefs = parseTemplate(loadColumnTemplate(accountId, slug));
  let schemaFields = templateFromPrefs.fields || [];
  let itemsData = [];
  let currentVisibleItems = [];
  const selectedItemIds = new Set();
  let columnDefs = [];
  let visibleColumns = [];
  let columnCount = null;
  const savedSort = loadSortPref(accountId, slug);
  let sortState = savedSort || { key: 'created_at', direction: 'desc' };
  let itemZoom = loadItemZoom(accountId, slug);
  let statusSummaryConfig = null;

  function applyItemZoom() {
    const normalized = normalizeZoomValue(itemZoom);
    const scale = normalized / 100;
    const cellPaddingY = Math.round(8 * scale);
    const cellPaddingX = Math.round(8 * scale);

    if (itemsTableContainer) {
      itemsTableContainer.classList.add('items-table-scaled');
      itemsTableContainer.style.setProperty('--items-zoom-scale', scale.toFixed(2));
      itemsTableContainer.style.setProperty('--items-cell-padding-y', `${cellPaddingY}px`);
      itemsTableContainer.style.setProperty('--items-cell-padding-x', `${cellPaddingX}px`);
    }

    if (itemsZoomResetBtn) {
      itemsZoomResetBtn.textContent = `${normalized}%`;
      itemsZoomResetBtn.disabled = normalized === 100;
    }
    if (itemsZoomOutBtn) {
      itemsZoomOutBtn.disabled = normalized <= 10;
    }
    if (itemsZoomInBtn) {
      itemsZoomInBtn.disabled = normalized >= 200;
    }
  }

  function updateItemZoom(nextZoom) {
    itemZoom = normalizeZoomValue(nextZoom);
    saveItemZoom(accountId, slug, itemZoom);
    applyItemZoom();
  }

  function getSchemaFields() {
    const latestTemplate = parseTemplate(loadColumnTemplate(accountId, slug));
    if (latestTemplate.fields && latestTemplate.fields.length) {
      return latestTemplate.fields;
    }
    if (currentSection && currentSection.schema) {
      const fromSchema = parseTemplate(currentSection.schema).fields || [];
      if (fromSchema.length) return fromSchema;
    }
    return schemaFields || [];
  }

  async function loadSectionMeta() {
    try {
      const section = await api(`/api/accounts/${accountId}/sections/${encodeURIComponent(slug)}`);
      currentSection = section;
      titleEl.textContent = section.label;
      metaEl.textContent = showSlugs ? `${accountName} · slug: ${section.slug}` : accountName;
      const schema = section.schema || {};
      const apiFields = parseTemplate(schema).fields || [];
      schemaFields = templateFromPrefs.fields.length ? templateFromPrefs.fields : apiFields;
      statusSummaryConfig = parseStatusSummaryConfig(schema.status_summary);
      document.title = `${section.label} | ${labels.sections_label}`;
    } catch {
      titleEl.textContent = `Section ${slug}`;
      metaEl.textContent = showSlugs ? `${accountName} · slug: ${slug}` : accountName;
      currentSection = { slug, label: slug, schema: {} };
      schemaFields = templateFromPrefs.fields.length ? templateFromPrefs.fields : [];
      statusSummaryConfig = null;
      document.title = `${labels.sections_label} ${slug}`;
    }
  }

  function openMenu() {
    menu.classList.add('open');
    menuButton.setAttribute('aria-expanded', 'true');
    const handler = (ev) => {
      if (!menu.contains(ev.target) && ev.target !== menuButton) {
        closeMenu();
      }
    };
    document.addEventListener('click', handler, { once: true });
  }

  function closeMenu() {
    menu.classList.remove('open');
    menuButton.setAttribute('aria-expanded', 'false');
  }

  menuButton.addEventListener('click', (e) => {
    e.stopPropagation();
    if (menu.classList.contains('open')) closeMenu(); else openMenu();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeMenu();
      closeItemModal();
      closeEditSectionModal();
    }
  });

  function openItemModal() {
    itemMsg.textContent = '';
    itemForm.reset();
    // Setup UI depending on schema or inferred columns
    let fieldsForForm = getSchemaFields();
    if (!fieldsForForm || !fieldsForForm.length) {
      const visibleSet = new Set(visibleColumns);
      const inferred = columnDefs
        .filter(c => c.key !== 'name' && c.key !== 'created_at' && visibleSet.has(c.key))
        .map((c, idx) => ({
          key: c.key,
          label: c.label || c.key,
          type: 'string',
          options: [],
          order: idx,
        }));
      fieldsForForm = inferred;
    }

    if (fieldsForForm && fieldsForForm.length) {
      const ordered = orderFields(fieldsForForm);
      schemaFieldsContainer.innerHTML = ordered.map(f => {
        const type = (f.type || 'text').toLowerCase();
        const required = f.required ? 'required' : '';
        const keyAttr = `data-key="${escapeHtml(f.key)}" data-type="${escapeHtml(type)}"`;
        const label = escapeHtml(f.label || f.key);
        const options = normalizeOptions(f.options);
        if (type === 'textarea') {
          return `<p><label>${label}<textarea ${keyAttr} ${required}></textarea></label></p>`;
        } else if ((type === 'select' || type === 'dropdown') && options.length) {
          const opts = options.map(o => `<option value="${escapeHtml(String(o))}">${escapeHtml(String(o))}</option>`).join('');
          return `<p><label>${label}<select ${keyAttr} ${required}>${opts}</select></label></p>`;
        } else if (type === 'checkbox') {
          return `<p><label><input type="checkbox" ${keyAttr}> ${label}</label></p>`;
        } else if (type === 'number') {
          return `<p><label>${label}<input type="number" ${keyAttr} ${required}></label></p>`;
        } else {
          return `<p><label>${label}<input type="text" ${keyAttr} ${required}></label></p>`;
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

  function closeItemModal() {
    itemModal.classList.add('hidden');
    itemMsg.textContent = '';
  }

  function openEditSectionModal() {
    const currentLabel = currentSection?.label || slug;
    const currentDetail = (currentSection?.detail || '').trim();
    editSectionMsg.textContent = '';
    editSectionForm.reset();
    editSectionLabelInput.value = currentLabel;
    editSectionDetailInput.value = currentDetail;
    editSectionModal.classList.remove('hidden');
    setTimeout(() => editSectionLabelInput.focus(), 0);
  }

  function closeEditSectionModal() {
    editSectionModal.classList.add('hidden');
    editSectionMsg.textContent = '';
  }

  function addKVRow(key = '', value = '') {
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

  if (addKVRowBtn) {
    addKVRowBtn.addEventListener('click', (e) => {
      e.preventDefault();
      addKVRow();
    });
  }

  if (itemCancel) {
    itemCancel.addEventListener('click', (e) => {
      e.preventDefault();
      closeItemModal();
    });
  }

  if (editSectionCancel) {
    editSectionCancel.addEventListener('click', (e) => {
      e.preventDefault();
      closeEditSectionModal();
    });
  }

  if (editSectionForm) {
    editSectionForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      editSectionMsg.textContent = 'Saving...';
      const currentLabel = currentSection?.label || slug;
      const currentDetail = (currentSection?.detail || '').trim();
      const nextLabel = editSectionLabelInput.value.trim();
      const nextDetail = editSectionDetailInput.value.trim();
      if (!nextLabel) {
        editSectionMsg.textContent = 'Section name is required.';
        return;
      }
      if (nextLabel === currentLabel && nextDetail === currentDetail) {
        closeEditSectionModal();
        return;
      }
      try {
        const updated = await api(`/api/accounts/${accountId}/sections/${encodeURIComponent(slug)}`, {
          method: 'PUT',
          body: JSON.stringify({
            label: nextLabel,
            detail: nextDetail,
            schema: currentSection?.schema || {}
          })
        });
        currentSection = updated;
        titleEl.textContent = updated.label;
        closeEditSectionModal();
      } catch (err) {
        editSectionMsg.textContent = err.message || 'Failed to update section';
      }
    });
  }

  const exportModal = document.getElementById('exportModal');
  const exportXlsxBtn = document.getElementById('exportXlsxBtn');
  const exportPdfBtn = document.getElementById('exportPdfBtn');
  const exportDocxBtn = document.getElementById('exportDocxBtn');
  const exportCancelBtn = document.getElementById('exportCancel');
  const exportMsg = document.getElementById('exportMsg');
  const exportItemCount = document.getElementById('exportItemCount');
  const exportTemplateMissing = document.getElementById('exportTemplateMissing');
  const exportTemplateLink = document.getElementById('exportTemplateLink');
  const exportSubtitle = document.getElementById('exportSubtitle');
  let exportTemplateAvailable = false;

  function getItemsForExport() {
    if (!currentVisibleItems.length) {
      return [];
    }
    const selectedVisible = currentVisibleItems.filter(it => selectedItemIds.has(it.id));
    return selectedVisible.length ? selectedVisible : currentVisibleItems;
  }

  function setExportButtonsBusy(busy) {
    [exportXlsxBtn, exportPdfBtn, exportDocxBtn].forEach(b => { if (b) b.disabled = busy; });
  }

  function syncExportTemplateButtons() {
    const enabled = exportTemplateAvailable && !!itemsData.length;
    if (exportPdfBtn) exportPdfBtn.disabled = !enabled;
    if (exportDocxBtn) exportDocxBtn.disabled = !enabled;
    if (exportTemplateMissing) exportTemplateMissing.classList.toggle('hidden', exportTemplateAvailable);
  }

  async function refreshExportTemplateAvailability() {
    try {
      await api(`/api/accounts/${accountId}/template`);
      exportTemplateAvailable = true;
    } catch {
      exportTemplateAvailable = false;
    }
    syncExportTemplateButtons();
  }

  function openExportModal() {
    if (!exportModal) return;
    if (!itemsData.length) {
      alert(`No ${labels.items_label.toLowerCase()} to export.`);
      return;
    }
    const exportItems = getItemsForExport();
    const selectedVisibleCount = currentVisibleItems.filter(it => selectedItemIds.has(it.id)).length;
    if (exportItemCount) exportItemCount.textContent = String(exportItems.length);
    if (exportSubtitle) {
      exportSubtitle.textContent = selectedVisibleCount
        ? `Exporting ${exportItems.length} selected item(s) from ${currentVisibleItems.length} visible item(s).`
        : `Choose a format. Exporting all ${exportItems.length} visible item(s).`;
    }
    if (exportMsg) exportMsg.textContent = '';
    if (exportTemplateLink) exportTemplateLink.href = `/account.html?id=${encodeURIComponent(accountId)}`;
    setExportButtonsBusy(false);
    syncExportTemplateButtons();
    if (exportXlsxBtn) exportXlsxBtn.disabled = false;
    exportModal.classList.remove('hidden');
  }

  function closeExportModal() {
    if (!exportModal) return;
    exportModal.classList.add('hidden');
  }

  if (exportBtn) {
    if (isReadOnly) {
      exportBtn.disabled = true;
      exportBtn.classList.add('hidden');
    } else {
      exportBtn.addEventListener('click', () => {
        openExportModal();
      });
    }
  }

  if (exportCancelBtn) {
    exportCancelBtn.addEventListener('click', closeExportModal);
  }

  if (exportXlsxBtn) {
    exportXlsxBtn.addEventListener('click', () => {
      try {
        exportItemsXlsx();
      } catch (err) {
        if (exportMsg) exportMsg.textContent = `Spreadsheet export failed: ${err.message || err}`;
        return;
      }
      closeExportModal();
    });
  }

  if (exportPdfBtn) {
    exportPdfBtn.addEventListener('click', () => exportItemsTemplate('pdf'));
  }
  if (exportDocxBtn) {
    exportDocxBtn.addEventListener('click', () => exportItemsTemplate('docx'));
  }

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && exportModal && !exportModal.classList.contains('hidden')) {
      closeExportModal();
    }
  });

  if (!isReadOnly) {
    refreshExportTemplateAvailability();
  }

  if (addItemBtn) {
    if (isReadOnly) {
      addItemBtn.disabled = true;
      addItemBtn.classList.add('hidden');
    } else {
      addItemBtn.addEventListener('click', (e) => {
        e.preventDefault();
        openItemModal();
      });
    }
  }

  function buildColumnDefs(items) {
    const cols = [
      { key: 'name', label: 'Name', locked: true },
      { key: 'created_at', label: 'Date added', type: 'date' },
    ];
    if (schemaFields && schemaFields.length) {
      const visibleFields = orderFields(schemaFields.filter(f => f.showInTable !== false));
      visibleFields.forEach(f => cols.push({
        key: f.key,
        label: f.label || f.key,
        type: f.type,
        options: normalizeOptions(f.options),
      }));
    } else {
      const autoKeys = getAutoKeys(items);
      autoKeys.forEach(k => cols.push({ key: k, label: k }));
    }
    return cols;
  }

  function sortItems(list) {
    const dir = sortState.direction === 'asc' ? 1 : -1;
    const key = sortState.key;
    return [...list].sort((a, b) => {
      let va;
      let vb;
      if (key === 'name') {
        va = a.name || '';
        vb = b.name || '';
      } else if (key === 'created_at') {
        va = a.created_at ? new Date(a.created_at).getTime() : 0;
        vb = b.created_at ? new Date(b.created_at).getTime() : 0;
      } else {
        const rawA = a.data && typeof a.data === 'object' ? a.data[key] : undefined;
        const rawB = b.data && typeof b.data === 'object' ? b.data[key] : undefined;
        va = rawA;
        vb = rawB;
      }

      if (typeof va === 'number' && typeof vb === 'number') {
        return (va - vb) * dir;
      }
      const strA = va === null || va === undefined ? '' : String(va);
      const strB = vb === null || vb === undefined ? '' : String(vb);
      return strA.localeCompare(strB, undefined, { numeric: true, sensitivity: 'base' }) * dir;
    });
  }

  function buildExportColumns(sourceItems = itemsData) {
    const cols = [];
    const seen = new Set();
    columnDefs.forEach(col => {
      if (seen.has(col.key)) return;
      seen.add(col.key);
      cols.push({ key: col.key, label: col.label || col.key });
    });

    const extras = new Set();
    sourceItems.forEach(it => {
      if (it.data && typeof it.data === 'object') {
        Object.keys(it.data).forEach(k => {
          if (!seen.has(k)) extras.add(k);
        });
      }
    });

    Array.from(extras).sort().forEach(k => {
      seen.add(k);
      cols.push({ key: k, label: k });
    });

    return cols;
  }

  function prepareExportRows(columns, sourceItems = itemsData) {
    return sourceItems.map(it => columns.map(col => {
      if (col.key === 'name') return normalizeExportValue(it.name);
      if (col.key === 'created_at') {
        return it.created_at ? new Date(it.created_at).toISOString() : '';
      }
      const val = it.data && typeof it.data === 'object' ? it.data[col.key] : '';
      return normalizeExportValue(val);
    }));
  }

  function escapeXml(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&apos;');
  }

  function columnLetter(idx) {
    let n = idx + 1;
    let letters = '';
    while (n > 0) {
      const rem = (n - 1) % 26;
      letters = String.fromCharCode(65 + rem) + letters;
      n = Math.floor((n - 1) / 26);
    }
    return letters;
  }

  function buildSheetXml(columns, rows, sheetName) {
    const headerCells = columns.map((col, i) => {
      const ref = `${columnLetter(i)}1`;
      return `<c r="${ref}" t="inlineStr"><is><t>${escapeXml(col.label || col.key)}</t></is></c>`;
    }).join('');

    const bodyRows = rows.map((row, rowIdx) => {
      const cells = row.map((cell, colIdx) => {
        const ref = `${columnLetter(colIdx)}${rowIdx + 2}`;
        return `<c r="${ref}" t="inlineStr"><is><t>${escapeXml(cell)}</t></is></c>`;
      }).join('');
      return `<row r="${rowIdx + 2}">${cells}</row>`;
    }).join('');

    return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
      `<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">` +
      `<sheetData>` +
      `<row r="1">${headerCells}</row>` +
      bodyRows +
      `</sheetData>` +
      `</worksheet>`;
  }

  const CRC_TABLE = (() => {
    const table = new Uint32Array(256);
    for (let i = 0; i < 256; i++) {
      let c = i;
      for (let k = 0; k < 8; k++) {
        c = ((c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1));
      }
      table[i] = c >>> 0;
    }
    return table;
  })();

  function crc32(bytes) {
    let crc = 0 ^ (-1);
    for (let i = 0; i < bytes.length; i++) {
      crc = (crc >>> 8) ^ CRC_TABLE[(crc ^ bytes[i]) & 0xFF];
    }
    return (crc ^ (-1)) >>> 0;
  }

  function dateToDos(date) {
    const d = new Date(date);
    const year = d.getFullYear();
    const month = d.getMonth() + 1;
    const day = d.getDate();
    const hours = d.getHours();
    const minutes = d.getMinutes();
    const seconds = Math.floor(d.getSeconds() / 2);
    const dosDate = ((year - 1980) << 9) | (month << 5) | day;
    const dosTime = (hours << 11) | (minutes << 5) | seconds;
    return { dosDate, dosTime };
  }

  function concatUint8(arrays) {
    const total = arrays.reduce((sum, a) => sum + a.length, 0);
    const out = new Uint8Array(total);
    let offset = 0;
    arrays.forEach(a => {
      out.set(a, offset);
      offset += a.length;
    });
    return out;
  }

  function createZip(entries) {
    const encoder = new TextEncoder();
    const files = [];
    const central = [];
    let offset = 0;

    entries.forEach(entry => {
      const nameBytes = encoder.encode(entry.name);
      const dataBytes = typeof entry.data === 'string' ? encoder.encode(entry.data) : entry.data;
      const { dosDate, dosTime } = dateToDos(entry.date || new Date());
      const crc = crc32(dataBytes);

      const localHeader = new Uint8Array(30);
      const dvLocal = new DataView(localHeader.buffer);
      dvLocal.setUint32(0, 0x04034b50, true);
      dvLocal.setUint16(4, 20, true);
      dvLocal.setUint16(6, 0x0800, true);
      dvLocal.setUint16(8, 0, true);
      dvLocal.setUint16(10, dosTime, true);
      dvLocal.setUint16(12, dosDate, true);
      dvLocal.setUint32(14, crc, true);
      dvLocal.setUint32(18, dataBytes.length, true);
      dvLocal.setUint32(22, dataBytes.length, true);
      dvLocal.setUint16(26, nameBytes.length, true);
      dvLocal.setUint16(28, 0, true);

      const fileRecord = concatUint8([localHeader, nameBytes, dataBytes]);
      files.push(fileRecord);

      const centralHeader = new Uint8Array(46);
      const dvCentral = new DataView(centralHeader.buffer);
      dvCentral.setUint32(0, 0x02014b50, true);
      dvCentral.setUint16(4, 20, true);
      dvCentral.setUint16(6, 20, true);
      dvCentral.setUint16(8, 0x0800, true);
      dvCentral.setUint16(10, 0, true);
      dvCentral.setUint16(12, dosTime, true);
      dvCentral.setUint16(14, dosDate, true);
      dvCentral.setUint32(16, crc, true);
      dvCentral.setUint32(20, dataBytes.length, true);
      dvCentral.setUint32(24, dataBytes.length, true);
      dvCentral.setUint16(28, nameBytes.length, true);
      dvCentral.setUint16(30, 0, true);
      dvCentral.setUint16(32, 0, true);
      dvCentral.setUint16(34, 0, true);
      dvCentral.setUint16(36, 0, true);
      dvCentral.setUint32(38, 0, true);
      dvCentral.setUint32(42, offset, true);

      central.push(concatUint8([centralHeader, nameBytes]));
      offset += fileRecord.length;
    });

    const centralDirSize = central.reduce((sum, a) => sum + a.length, 0);
    const centralDirOffset = offset;

    const endRecord = new Uint8Array(22);
    const dvEnd = new DataView(endRecord.buffer);
    dvEnd.setUint32(0, 0x06054b50, true);
    dvEnd.setUint16(4, 0, true);
    dvEnd.setUint16(6, 0, true);
    dvEnd.setUint16(8, entries.length, true);
    dvEnd.setUint16(10, entries.length, true);
    dvEnd.setUint32(12, centralDirSize, true);
    dvEnd.setUint32(16, centralDirOffset, true);
    dvEnd.setUint16(20, 0, true);

    return concatUint8([...files, ...central, endRecord]);
  }

  function createXlsx(columns, rows, sheetName) {
    const sheetXml = buildSheetXml(columns, rows, sheetName);
    const workbookXml = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
      `<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">` +
      `<sheets><sheet name="${escapeXml(sheetName)}" sheetId="1" r:id="rId1"/></sheets>` +
      `</workbook>`;

    const workbookRels = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
      `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">` +
      `<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>` +
      `</Relationships>`;

    const rootRels = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
      `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">` +
      `<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>` +
      `</Relationships>`;

    const contentTypes = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
      `<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">` +
      `<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>` +
      `<Default Extension="xml" ContentType="application/xml"/>` +
      `<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>` +
      `<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>` +
      `</Types>`;

    const zip = createZip([
      { name: '[Content_Types].xml', data: contentTypes },
      { name: '_rels/.rels', data: rootRels },
      { name: 'xl/workbook.xml', data: workbookXml },
      { name: 'xl/_rels/workbook.xml.rels', data: workbookRels },
      { name: 'xl/worksheets/sheet1.xml', data: sheetXml },
    ]);

    return new Blob([zip], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
  }

  function exportItemsXlsx() {
    const exportItems = getItemsForExport();
    if (!exportItems.length) {
      alert(`No ${labels.items_label.toLowerCase()} to export.`);
      return;
    }

    const columns = buildExportColumns(exportItems);
    const rows = prepareExportRows(columns, exportItems);

    const sectionName = (currentSection?.label || slug || 'section').replace(/[^a-z0-9]+/gi, '_').replace(/_+/g, '_').replace(/^_+|_+$/g, '') || 'section';
    const dateStamp = new Date().toISOString().split('T')[0];
    const filename = `${sectionName}_${dateStamp}.xlsx`;

    const blob = createXlsx(columns, rows, currentSection?.label || sectionName || 'Section');
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    setTimeout(() => {
      URL.revokeObjectURL(url);
      link.remove();
    }, 0);
  }

  async function exportItemsTemplate(format) {
    const exportItems = getItemsForExport();
    if (!exportItems.length) {
      if (exportMsg) exportMsg.textContent = `No ${labels.items_label.toLowerCase()} to export.`;
      return;
    }
    if (!exportTemplateAvailable) {
      if (exportMsg) exportMsg.textContent = 'No template uploaded for this account.';
      return;
    }
    setExportButtonsBusy(true);
    if (exportMsg) exportMsg.textContent = format === 'pdf' ? 'Building PDF…' : 'Building document…';

    try {
      const token = getToken();
      const itemIds = exportItems.map(it => it.id).filter(Boolean);
      const res = await fetch(`/api/accounts/${accountId}/sections/${encodeURIComponent(slug)}/export`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: 'Bearer ' + token } : {}),
        },
        body: JSON.stringify({ item_ids: itemIds, format }),
      });

      if (!res.ok) {
        const text = await res.text().catch(() => res.statusText);
        throw new Error(text || `HTTP ${res.status}`);
      }

      const blob = await res.blob();
      const sectionName = (currentSection?.label || slug || 'section').replace(/[^a-z0-9]+/gi, '_').replace(/_+/g, '_').replace(/^_+|_+$/g, '') || 'section';
      const dateStamp = new Date().toISOString().split('T')[0];
      const ext = format === 'pdf' ? 'pdf' : 'docx';
      const filename = `${sectionName}_${dateStamp}.${ext}`;

      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      setTimeout(() => {
        URL.revokeObjectURL(url);
        link.remove();
      }, 0);
      if (exportMsg) exportMsg.textContent = 'Done.';
      closeExportModal();
    } catch (err) {
      if (exportMsg) exportMsg.textContent = `Export failed: ${err.message || err}`;
    } finally {
      setExportButtonsBusy(false);
      syncExportTemplateButtons();
    }
  }

  function setExportEnabled(enabled) {
    if (exportBtn) {
      exportBtn.disabled = !enabled;
    }
  }

  function renderSortIndicator(col) {
    if (sortState.key !== col.key) {
      return '<span class="sort-arrow" aria-hidden="true">↕</span>';
    }
    const arrow = sortState.direction === 'asc' ? '↑ asc' : '↓ dsc';
    return `<span class="sort-arrow active" aria-hidden="true">${arrow}</span>`;
  }

  function renderItemsTable(term = '') {
    const visibleSet = new Set(visibleColumns);
    let activeColumns = columnDefs.filter(c => visibleSet.has(c.key));
    if (Number.isFinite(columnCount) && columnCount > 0) {
      activeColumns = activeColumns.slice(0, columnCount);
    }

    let displayItems = itemsData;
    if (term) {
      const lowerTerm = term.toLowerCase();
      displayItems = itemsData.filter(item => {
        // Check name
        if ((item.name || '').toLowerCase().includes(lowerTerm)) return true;
        // Check filtering visible columns? Or just check all data values for simplicity?
        // Let's check visible columns + basics for now.
        return activeColumns.some(col => {
          let val;
          if (col.key === 'name') val = item.name;
          else if (col.key === 'created_at') val = item.created_at;
          else val = (item.data || {})[col.key];
          return String(val || '').toLowerCase().includes(lowerTerm);
        });
      });
    }

    currentVisibleItems = sortItems(displayItems);
    if (!displayItems.length) {
      itemsTableContainer.innerHTML = '';
      if (itemsEmptyState) {
        if (!term && !itemsData.length) {
          itemsEmptyState.classList.remove('hidden');
          if (itemsEmptyCopy) itemsEmptyCopy.textContent = `No ${labels.items_label.toLowerCase()} in this ${labels.sections_label.toLowerCase()} yet. Use the Add button to create one.`;
        } else {
          // Search yielded no results
          itemsTableContainer.innerHTML = '<p class="small" style="text-align:center">No items match your search.</p>';
          itemsEmptyState.classList.add('hidden');
        }
      }

      setExportEnabled(false);
      return;
    }
    if (itemsEmptyState) {
      itemsEmptyState.classList.add('hidden');
    }
    setExportEnabled(true);

    if (activeColumns.length && !activeColumns.some(c => c.key === sortState.key)) {
      const fallback = activeColumns[0];
      sortState = { key: fallback.key, direction: fallback.key === 'created_at' ? 'desc' : 'asc' };
    }

    const headerCells = activeColumns.map(col => {
      const ariaSort = sortState.key === col.key ? (sortState.direction === 'asc' ? 'ascending' : 'descending') : 'none';
      return `<th><button type="button" class="sort-toggle" data-key="${escapeHtml(col.key)}" aria-sort="${ariaSort}">${escapeHtml(col.label)} ${renderSortIndicator(col)}</button></th>`;
    }).join('');

    const selectedVisibleCount = currentVisibleItems.filter(it => selectedItemIds.has(it.id)).length;
    const allVisibleSelected = currentVisibleItems.length > 0 && selectedVisibleCount === currentVisibleItems.length;

    const rowsHtml = currentVisibleItems.map(it => {
      const cells = [];
      cells.push(
        `<td style="width:1%;white-space:nowrap;">` +
        `<input type="checkbox" data-item-select data-item-id="${escapeHtml(it.id)}"${selectedItemIds.has(it.id) ? ' checked' : ''} aria-label="Select ${escapeHtml(it.name || 'item')}" />` +
        `</td>`
      );
      for (const col of activeColumns) {
        if (col.key === 'name') {
          cells.push(`<td>${escapeHtml(it.name)}</td>`);
        } else if (col.key === 'created_at') {
          cells.push(`<td>${escapeHtml(formatDateTime(it.created_at))}</td>`);
        } else {
          const val = it.data && typeof it.data === 'object' ? it.data[col.key] : undefined;
          if ((col.type || '').toLowerCase() === 'dropdown') {
            const opts = [...new Set(normalizeOptions(col.options))];
            const currentVal = val === undefined || val === null ? '' : String(val);
            if (opts.length) {
              if (currentVal && !opts.includes(currentVal)) opts.unshift(currentVal);
              const optionsHtml = ['<option value="">Select…</option>', ...opts.map(o => `<option value="${escapeHtml(String(o))}"${o === currentVal ? ' selected' : ''}>${escapeHtml(String(o))}</option>`)].join('');
              cells.push(`<td><select class="inline-dropdown" data-inline-dropdown data-item-id="${escapeHtml(it.id)}" data-col-key="${escapeHtml(col.key)}" data-prev="${escapeHtml(currentVal)}">${optionsHtml}</select></td>`);
            } else {
              cells.push(`<td>${formatCellValue(val)}</td>`);
            }
          } else {
            cells.push(`<td>${formatCellValue(val)}</td>`);
          }
        }
      }
      const viewHref = `/item.html?account=${encodeURIComponent(accountId)}&section=${encodeURIComponent(slug)}&item=${encodeURIComponent(it.id)}`;
      const commentsHref = `/comments.html?account_id=${encodeURIComponent(accountId)}&item_id=${encodeURIComponent(it.id)}&section_slug=${encodeURIComponent(slug)}`;
      const commentCount = Number.isFinite(it.comment_count) ? it.comment_count : 0;
      const commentCountText = commentCount === 1 ? '1 comment' : `${commentCount} comments`;
      const commentCountClass = commentCount > 0 ? 'comment-count comment-count--active' : 'comment-count';
      const commentsBtn = `<a class="btn comment-btn" href="${commentsHref}" aria-label="View ${escapeHtml(commentCountText)}">` +
        `<span class="comment-icon" aria-hidden="true">💬</span>` +
        `<span class="comment-label">Comments</span>` +
        `<span class="${commentCountClass}" aria-hidden="true">${escapeHtml(String(commentCount))}</span>` +
        `</a>`;
      const deleteBtn = isReadOnly
        ? ''
        : `<button type="button" class="btn danger" data-action="delete-item" data-item-id="${escapeHtml(it.id)}">Delete</button>`;
      cells.push(`<td style="width:1%;white-space:nowrap;">` +
        `<a class="btn" href="${viewHref}">View</a> ` +
        `${commentsBtn} ` +
        `${deleteBtn}` +
        `</td>`);
      const rowStatusClass = getItemStatusClass(it, statusSummaryConfig);
      return `<tr class="${rowStatusClass}">${cells.join('')}</tr>`;
    }).join('');

    itemsTableContainer.innerHTML = `<div class="table-wrapper"><table><thead><tr><th style="width:1%;white-space:nowrap;"><input type="checkbox" id="selectAllVisibleItems"${allVisibleSelected ? ' checked' : ''} aria-label="Select all visible items" /></th>${headerCells}<th></th></tr></thead><tbody>${rowsHtml}</tbody></table></div>`;
    const headerButtons = itemsTableContainer.querySelectorAll('.sort-toggle');
    headerButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        const key = btn.getAttribute('data-key');
        if (!key) return;
        if (sortState.key === key) {
          sortState = { key, direction: sortState.direction === 'asc' ? 'desc' : 'asc' };
        } else {
          sortState = { key, direction: key === 'created_at' ? 'desc' : 'asc' };
        }
        renderItemsTable(itemSearch ? itemSearch.value : '');
      });
    });

    const dropdowns = itemsTableContainer.querySelectorAll('[data-inline-dropdown]');
    dropdowns.forEach(select => {
      select.addEventListener('change', async () => {
        const itemId = select.getAttribute('data-item-id');
        const key = select.getAttribute('data-col-key');
        const prev = select.getAttribute('data-prev') || '';
        if (!itemId || !key) return;
        const nextVal = select.value;
        select.disabled = true;
        try {
          const item = itemsData.find(i => i.id === itemId);
          if (!item) throw new Error('Item not found');
          const updatedData = { ...(item.data || {}) };
          updatedData[key] = nextVal;
          if (isReadOnly) {
            throw new Error('You have read-only access and cannot update items.');
          }
          const updated = await api(`/api/accounts/${accountId}/items/${encodeURIComponent(itemId)}`, {
            method: 'PUT',
            headers: { 'X-Update-Source': 'web-ui' },
            body: JSON.stringify({ name: item.name || '', data: updatedData }),
          });
          item.data = updated?.data || updatedData;
          select.setAttribute('data-prev', nextVal);
          renderItemsTable(itemSearch ? itemSearch.value : '');
        } catch (err) {
          select.value = prev;
          alert(err.message || 'Failed to update value');
        } finally {
          select.disabled = false;
        }
      });
    });

    const deleteButtons = itemsTableContainer.querySelectorAll('button[data-action="delete-item"]');
    deleteButtons.forEach(btn => {
      btn.addEventListener('click', async () => {
        const itemId = btn.getAttribute('data-item-id');
        if (!itemId) return;
        if (isReadOnly) {
          alert('You have read-only access and cannot delete items.');
          return;
        }
        const matchedItem = itemsData.find(item => item.id === itemId);
        const itemName = matchedItem?.name?.trim() || '';
        const labelSource = (labels.items_label || 'Items').trim();
        const singularLabel = labelSource.toLowerCase().endsWith('s') && labelSource.length > 1
          ? labelSource.slice(0, -1)
          : labelSource;
        const fallbackTarget = `this ${singularLabel.toLowerCase() || 'item'}`;
        const promptTarget = itemName ? `"${itemName}"` : fallbackTarget;
        const shouldDelete = confirm(`Delete ${promptTarget}? This cannot be undone.`);
        if (!shouldDelete) {
          return;
        }
        const previousText = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'Deleting…';
        try {
          await api(`/api/accounts/${accountId}/items/${encodeURIComponent(itemId)}`, { method: 'DELETE' });
          itemsData = itemsData.filter(item => item.id !== itemId);
          selectedItemIds.delete(itemId);
          renderItemsTable(itemSearch ? itemSearch.value : '');
        } catch (err) {
          alert(err.message || 'Failed to delete item');
          btn.disabled = false;
          btn.textContent = previousText;
        }
      });
    });

    const selectAllVisibleEl = document.getElementById('selectAllVisibleItems');
    if (selectAllVisibleEl) {
      selectAllVisibleEl.addEventListener('change', () => {
        if (selectAllVisibleEl.checked) {
          currentVisibleItems.forEach(it => selectedItemIds.add(it.id));
        } else {
          currentVisibleItems.forEach(it => selectedItemIds.delete(it.id));
        }
        renderItemsTable(itemSearch ? itemSearch.value : '');
      });
    }

    const rowCheckboxes = itemsTableContainer.querySelectorAll('input[data-item-select]');
    rowCheckboxes.forEach(chk => {
      chk.addEventListener('change', () => {
        const itemId = chk.getAttribute('data-item-id');
        if (!itemId) return;
        if (chk.checked) selectedItemIds.add(itemId);
        else selectedItemIds.delete(itemId);
      });
    });
  }

  async function loadItems() {
    try {
      const page = await api(`/api/accounts/${accountId}/sections/${encodeURIComponent(slug)}/items?limit=200`);
      itemsData = page.items || [];
      const itemIds = new Set(itemsData.map(item => item.id));
      for (const selectedId of Array.from(selectedItemIds)) {
        if (!itemIds.has(selectedId)) {
          selectedItemIds.delete(selectedId);
        }
      }
      setExportEnabled(itemsData.length > 0);
      columnDefs = buildColumnDefs(itemsData);
      const stored = loadColumnPrefs(accountId, slug);
      const base = stored.length ? [...stored, ...columnDefs.map(c => c.key)] : columnDefs.map(c => c.key);
      visibleColumns = reconcileVisibility(columnDefs, base);
      const rawCount = loadColumnCount(accountId, slug);
      const maxCount = visibleColumns.length;
      if (Number.isFinite(rawCount) && rawCount > 0) {
        columnCount = maxCount ? Math.min(rawCount, maxCount) : rawCount;
      } else {
        columnCount = null;
      }
      const visibleSet = new Set(visibleColumns);
      if (!visibleSet.has(sortState.key)) {
        const fallbackKey = visibleColumns[0] || 'created_at';
        sortState = { key: fallbackKey, direction: fallbackKey === 'created_at' ? 'desc' : 'asc' };
      }

      if (!itemsData.length) {
        itemsEmptyState.classList.remove('hidden');
        itemsTableContainer.innerHTML = '';
        return;
      }
      itemsEmptyState.classList.add('hidden');
      renderItemsTable(itemSearch ? itemSearch.value : '');
    } catch (e) {
      itemsTableContainer.innerHTML = `<p class="small">Failed to load items: ${e.message}</p>`;
      itemsEmptyState.classList.add('hidden');
      setExportEnabled(false);
    }
  }

  // Item form submit
  itemForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    itemMsg.textContent = 'Saving…';
    const name = itemNameInput.value.trim();
    if (!name) {
      itemMsg.textContent = 'Name is required.';
      return;
    }

    let data = {};
    const keyedInputs = schemaFieldsContainer.querySelectorAll('[data-key]');
    if (keyedInputs.length) {
      keyedInputs.forEach(el => {
        const key = el.getAttribute('data-key');
        const type = (el.getAttribute('data-type') || 'text').toLowerCase();
        if (!key) return;
        if (type === 'checkbox') {
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
        if (!kInput) return;
        const key = kInput.value.trim();
        if (!key) return;
        const raw = vInput ? vInput.value : '';
        data[key] = parseLooseValue(raw);
      });
    }

    try {
      const createdItem = await api(`/api/accounts/${accountId}/sections/${encodeURIComponent(slug)}/items`, {
        method: 'POST',
        body: JSON.stringify({ name, data })
      });
      const sectionName = (currentSection && currentSection.label) ? currentSection.label : slug;
      // Fire-and-forget webhook with new item details
      try {
        fetch('https://n8n.adigi8.app/webhook/415312f7-a131-40cc-b86b-d9e51604a99e', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            event: 'item_created',
            account_id: accountId,
            section_slug: slug,
            section_name: sectionName,
            item: createdItem || { name, data },
            user: {
              id: me.id,
              email: me.email,
            },
            created_at: new Date().toISOString(),
            source: 'web_ui',
          }),
        }).catch(() => {
          // Ignore webhook errors so the UI flow is not blocked
        });
      } catch {
        // Ignore synchronous errors from fetch setup
      }
      itemMsg.textContent = 'Item added.';
      closeItemModal();
      await loadItems();
    } catch (err) {
      itemMsg.textContent = err.message || 'Failed to add item';
    }
  });

  // 3-dot menu actions
  menu.addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const action = btn.dataset.action;
    closeMenu();

    if (isReadOnly && (action === 'add-item' || action === 'edit' || action === 'delete' || action === 'settings')) {
      alert('You have read-only access and cannot modify this section.');
      return;
    }

    if (action === 'add-item') {
      openItemModal();
    } else if (action === 'settings') {
      window.location.href = `/item-columns.html?account=${encodeURIComponent(accountId)}&slug=${encodeURIComponent(slug)}`;
    } else if (action === 'edit') {
      openEditSectionModal();
    } else if (action === 'delete') {
      if (!confirm('Delete this section and all its items? This cannot be undone.')) {
        return;
      }
      try {
        await api(`/api/accounts/${accountId}/sections/${encodeURIComponent(slug)}`, { method: 'DELETE' });
        window.location.replace(`/account.html?id=${encodeURIComponent(accountId)}`);
      } catch (err) {
        alert(err.message || 'Failed to delete section');
      }
    } else if (action === 'save-sort') {
      saveSortPref(accountId, slug, sortState);
      // Optional: Visual feedback
      const originalText = btn.textContent;
      btn.textContent = 'Saved!';
      setTimeout(() => {
        btn.textContent = originalText;
        closeMenu();
      }, 1000);
      return; // Don't close immediately so user sees "Saved!"
    }
  });

  if (itemSearch) {
    itemSearch.addEventListener('input', (e) => {
      renderItemsTable(e.target.value);
    });
  }

  if (itemsZoomOutBtn) {
    itemsZoomOutBtn.addEventListener('click', () => {
      updateItemZoom(itemZoom - 10);
    });
  }

  if (itemsZoomResetBtn) {
    itemsZoomResetBtn.addEventListener('click', () => {
      updateItemZoom(100);
    });
  }

  if (itemsZoomInBtn) {
    itemsZoomInBtn.addEventListener('click', () => {
      updateItemZoom(itemZoom + 10);
    });
  }

  await loadSectionMeta();
  await updateNotesButtonLabel();
  applyItemZoom();
  await loadItems();
})();