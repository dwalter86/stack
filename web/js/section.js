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

function formatDateTime(val){
  if(!val) return '';
  try {
    return new Date(val).toLocaleString();
  } catch {
    return String(val);
  }
}

function normalizeExportValue(val){
  if(val === null || val === undefined) return '';
  if(val instanceof Date){
    return val.toISOString();
  }
  if(typeof val === 'object'){
    try {
      return JSON.stringify(val);
    } catch {
      return String(val);
    }
  }
  return String(val);
}

function columnPrefKey(accountId, slug){
  return `columnPrefs:${accountId}:${slug||'default'}`;
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
  const itemsHeading = document.getElementById('itemsHeading');
  const itemsEmptyCopy = document.getElementById('itemsEmptyCopy');
  const itemModalTitle = document.getElementById('itemModalTitle');
  const exportBtn = document.getElementById('exportItemsBtn');

  const menuButton = document.getElementById('sectionMenuButton');
  const menu = document.getElementById('sectionMenu');
  const editSectionMenuLabel = document.getElementById('editSectionMenuLabel');
  const itemSettingsMenuLabel = document.getElementById('itemSettingsMenuLabel');
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
  if(itemsEmptyCopy){ itemsEmptyCopy.textContent = `No ${labels.items_label.toLowerCase()} in this ${labels.sections_label.toLowerCase()} yet. Use the menu to add one.`; }
  if(editSectionMenuLabel){ editSectionMenuLabel.textContent = `Edit ${labels.sections_label}`; }
  if(itemSettingsMenuLabel){ itemSettingsMenuLabel.textContent = 'Settings'; }
  if(addItemMenuLabel){ addItemMenuLabel.textContent = `Add ${labels.items_label}`; }
  if(deleteSectionMenuLabel){ deleteSectionMenuLabel.textContent = `Delete ${labels.sections_label}`; }
  if(itemModalTitle){ itemModalTitle.textContent = `Add ${labels.items_label}`; }
  if(exportBtn){ exportBtn.disabled = true; }

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
  let itemsData = [];
  let columnDefs = [];
  let visibleColumns = [];
  let sortState = { key: 'created_at', direction: 'desc' };

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

  if(itemCancel){
    itemCancel.addEventListener('click', (e) => {
      e.preventDefault();
      closeItemModal();
    });
  }

  if(exportBtn){
    exportBtn.addEventListener('click', () => {
      exportItems();
    });
  }

  function buildColumnDefs(items){
    const cols = [
      { key: 'name', label: 'Name', locked: true },
      { key: 'created_at', label: 'Date added', type: 'date' },
    ];
    if(schemaFields && schemaFields.length){
      const visibleFields = schemaFields.filter(f => f.showInTable !== false);
      visibleFields.forEach(f => cols.push({ key: f.key, label: f.label || f.key }));
    } else {
      const autoKeys = getAutoKeys(items);
      autoKeys.forEach(k => cols.push({ key: k, label: k }));
    }
    return cols;
  }

  function sortItems(list){
    const dir = sortState.direction === 'asc' ? 1 : -1;
    const key = sortState.key;
    return [...list].sort((a, b) => {
      let va;
      let vb;
      if(key === 'name'){
        va = a.name || '';
        vb = b.name || '';
      } else if(key === 'created_at') {
        va = a.created_at ? new Date(a.created_at).getTime() : 0;
        vb = b.created_at ? new Date(b.created_at).getTime() : 0;
      } else {
        const rawA = a.data && typeof a.data === 'object' ? a.data[key] : undefined;
        const rawB = b.data && typeof b.data === 'object' ? b.data[key] : undefined;
        va = rawA;
        vb = rawB;
      }  

      if(typeof va === 'number' && typeof vb === 'number'){
        return (va - vb) * dir;
      }
      const strA = va === null || va === undefined ? '' : String(va);
      const strB = vb === null || vb === undefined ? '' : String(vb);
      return strA.localeCompare(strB, undefined, { numeric: true, sensitivity: 'base' }) * dir;
    });
  }

  function buildExportColumns(){
    const cols = [];
    const seen = new Set();
    columnDefs.forEach(col => {
      if(seen.has(col.key)) return;
      seen.add(col.key);
      cols.push({ key: col.key, label: col.label || col.key });
    });

    const extras = new Set();
    itemsData.forEach(it => {
      if(it.data && typeof it.data === 'object'){
        Object.keys(it.data).forEach(k => {
          if(!seen.has(k)) extras.add(k);
        });
      }
    });

    Array.from(extras).sort().forEach(k => {
      seen.add(k);
      cols.push({ key: k, label: k });
    });

    return cols;
  }

  function prepareExportRows(columns){
    return itemsData.map(it => columns.map(col => {
      if(col.key === 'name') return normalizeExportValue(it.name);
      if(col.key === 'created_at'){
        return it.created_at ? new Date(it.created_at).toISOString() : '';
      }
      const val = it.data && typeof it.data === 'object' ? it.data[col.key] : '';
      return normalizeExportValue(val);
    }));
  }

  function escapeXml(str){
    return String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&apos;');
  }

  function columnLetter(idx){
    let n = idx + 1;
    let letters = '';
    while(n > 0){
      const rem = (n - 1) % 26;
      letters = String.fromCharCode(65 + rem) + letters;
      n = Math.floor((n - 1) / 26);
    }
    return letters;
  }

  function buildSheetXml(columns, rows, sheetName){
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
    for(let i = 0; i < 256; i++){
      let c = i;
      for(let k = 0; k < 8; k++){
        c = ((c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1));
      }
      table[i] = c >>> 0;
    }
    return table;
  })();

  function crc32(bytes){
    let crc = 0 ^ (-1);
    for(let i = 0; i < bytes.length; i++){
      crc = (crc >>> 8) ^ CRC_TABLE[(crc ^ bytes[i]) & 0xFF];
    }
    return (crc ^ (-1)) >>> 0;
  }

  function dateToDos(date){
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

  function concatUint8(arrays){
    const total = arrays.reduce((sum, a) => sum + a.length, 0);
    const out = new Uint8Array(total);
    let offset = 0;
    arrays.forEach(a => {
      out.set(a, offset);
      offset += a.length;
    });
    return out;
  }

  function createZip(entries){
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

  function createXlsx(columns, rows, sheetName){
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

  function exportItems(){
    if(!itemsData.length){
      alert(`No ${labels.items_label.toLowerCase()} to export.`);
      return;
    }

    const columns = buildExportColumns();
    const rows = prepareExportRows(columns);

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

  function setExportEnabled(enabled){
    if(exportBtn){
      exportBtn.disabled = !enabled;
    }
  }

  function renderSortIndicator(col){
    if(sortState.key !== col.key){
      return '<span class="sort-arrow" aria-hidden="true">↕</span>';
    }
    const arrow = sortState.direction === 'asc' ? '↑ asc' : '↓ dsc';
    return `<span class="sort-arrow active" aria-hidden="true">${arrow}</span>`;
  }

  function renderItemsTable(){
    const visibleSet = new Set(visibleColumns);
    const activeColumns = columnDefs.filter(c => visibleSet.has(c.key));
    if(!itemsData.length){
      itemsTableContainer.innerHTML = '';
      return;
    }
    
    const headerCells = activeColumns.map(col => {
      const ariaSort = sortState.key === col.key ? (sortState.direction === 'asc' ? 'ascending' : 'descending') : 'none';
      return `<th><button type="button" class="sort-toggle" data-key="${escapeHtml(col.key)}" aria-sort="${ariaSort}">${escapeHtml(col.label)} ${renderSortIndicator(col)}</button></th>`;
    }).join('');

    const sortedItems = sortItems(itemsData);
    const rowsHtml = sortedItems.map(it => {
      const cells = [];
      for(const col of activeColumns){
        if(col.key === 'name'){
          cells.push(`<td>${escapeHtml(it.name)}</td>`);
        } else if(col.key === 'created_at'){
          cells.push(`<td>${escapeHtml(formatDateTime(it.created_at))}</td>`);
        } else {
          const val = it.data && typeof it.data === 'object' ? it.data[col.key] : undefined;
          cells.push(`<td>${formatCellValue(val)}</td>`);
        }
      }
      const viewHref = `/item.html?account=${encodeURIComponent(accountId)}&section=${encodeURIComponent(slug)}&item=${encodeURIComponent(it.id)}`;
      const commentsHref = `/comments.html?account_id=${encodeURIComponent(accountId)}&item_id=${encodeURIComponent(it.id)}&section_slug=${encodeURIComponent(slug)}`;
      cells.push(`<td style="width:1%;white-space:nowrap;"><a class="btn small" href="${viewHref}">View</a> <a class="btn small" href="${commentsHref}">Comments</a></td>`);
      return `<tr>${cells.join('')}</tr>`;
    }).join('');

    itemsTableContainer.innerHTML = `<div class="table-wrapper"><table><thead><tr>${headerCells}<th></th></tr></thead><tbody>${rowsHtml}</tbody></table></div>`;
    const headerButtons = itemsTableContainer.querySelectorAll('.sort-toggle');
    headerButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        const key = btn.getAttribute('data-key');
        if(!key) return;
        if(sortState.key === key){
          sortState = { key, direction: sortState.direction === 'asc' ? 'desc' : 'asc' };
        } else {
          sortState = { key, direction: key === 'created_at' ? 'desc' : 'asc' };
        }
        renderItemsTable();
      });
    });
  }

  async function loadItems(){
    try{
      const page = await api(`/api/accounts/${accountId}/sections/${encodeURIComponent(slug)}/items?limit=200`);
      itemsData = page.items || [];
      setExportEnabled(itemsData.length > 0);
      columnDefs = buildColumnDefs(itemsData);
      const stored = loadColumnPrefs(accountId, slug);
      const base = stored.length ? [...stored, ...columnDefs.map(c => c.key)] : columnDefs.map(c => c.key);
      visibleColumns = reconcileVisibility(columnDefs, base);
      const visibleSet = new Set(visibleColumns);
      if(!visibleSet.has(sortState.key)){
        const fallbackKey = visibleColumns[0] || 'created_at';
        sortState = { key: fallbackKey, direction: fallbackKey === 'created_at' ? 'desc' : 'asc' };
      }

      if(!itemsData.length){
        itemsEmptyState.classList.remove('hidden');
        itemsTableContainer.innerHTML = '';
        return;
      }
      itemsEmptyState.classList.add('hidden');
      renderItemsTable();
    }catch(e){
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
    } else if(action === 'settings'){
      window.location.href = `/item-columns.html?account=${encodeURIComponent(accountId)}&slug=${encodeURIComponent(slug)}`;
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
