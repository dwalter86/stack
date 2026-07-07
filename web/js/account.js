import { loadMeOrRedirect, renderShell, api, getLabels, getPreferences, escapeHtml } from './common.js';
import { notifySuccess, notifyError, notifyWarning, confirmDialog, promptDialog } from './notify.js';

function qs(name) {
  const m = new URLSearchParams(location.search).get(name);
  return m && decodeURIComponent(m);
}

function generateSectionSlug() {
  const now = new Date();
  const pad = (n, len = 2) => String(n).padStart(len, '0');
  const day = pad(now.getDate());
  const month = pad(now.getMonth() + 1);
  const year = now.getFullYear();
  const hours = pad(now.getHours());
  const minutes = pad(now.getMinutes());
  const seconds = pad(now.getSeconds());
  const letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  let prefix = '';
  for (let i = 0; i < 4; i++) {
    prefix += letters[Math.floor(Math.random() * letters.length)];
  }
  const randomTail = String(Math.floor(Math.random() * 1e8)).padStart(8, '0');
  return `${prefix}-${day}${month}${year}-${hours}${minutes}${seconds}-${randomTail}`;
}

function slugify(val) {
  const s = (val || '').toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return s || 'section';
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

  return {
    fieldKey,
    red,
    yellow,
    green,
    redLabel: String(raw.red_label || '').trim(),
    yellowLabel: String(raw.yellow_label || '').trim(),
    greenLabel: String(raw.green_label || '').trim(),
  };
}

function normalizeStatusValue(val) {
  return String(val ?? '').trim().toLowerCase();
}

function computeStatusCounts(items, config) {
  const counts = { red: 0, yellow: 0, green: 0 };
  for (const item of items || []) {
    const value = normalizeStatusValue(item?.data?.[config.fieldKey]);
    if (!value) continue;
    if (config.red.has(value)) {
      counts.red += 1;
    } else if (config.yellow.has(value)) {
      counts.yellow += 1;
    } else if (config.green.has(value)) {
      counts.green += 1;
    }
  }
  return counts;
}

function renderStatusText(label, count) {
  const safeCount = Number.isFinite(count) ? count : 0;
  return label ? `${escapeHtml(label)} ${safeCount}` : `${safeCount}`;
}

(async () => {
  const me = await loadMeOrRedirect(); if (!me) return;
  renderShell(me);
  const labels = getLabels(me);
  const accountBackLink = document.querySelector('.btnBack');
  if (accountBackLink) {
    accountBackLink.textContent = `Back to ${labels.accounts_label}`;
    accountBackLink.title = `Back to ${labels.accounts_label}`;
  }
  const pageEyebrow = document.getElementById('pageEyebrow');
  if (pageEyebrow) pageEyebrow.textContent = labels.accounts_label;
  const preferences = getPreferences(me);
  const showSlugs = preferences.show_slugs;

  const accountId = qs('id');
  if (!accountId) {
    document.body.innerHTML = '<main class="container"><p>Missing account id.</p></main>';
    return;
  }

  const acctNameEl = document.getElementById('acctName');
  const sectionListEl = document.getElementById('sectionList');
  const emptyStateEl = document.getElementById('emptyState');
  const emptyCreateBtn = document.getElementById('emptyCreateSectionBtn');

  const sectionsHeading = document.getElementById('sectionsHeading');
  const sectionsEmptyCopy = document.getElementById('sectionsEmptyCopy');
  const sectionModalTitle = document.getElementById('sectionModalTitle');
  const sectionLabelPrompt = document.getElementById('sectionLabelPrompt');

  if (sectionsHeading) { sectionsHeading.textContent = labels.sections_label; }
  if (sectionsEmptyCopy) { sectionsEmptyCopy.textContent = `No ${labels.sections_label.toLowerCase()} have been created for this account yet.`; }
  if (sectionModalTitle) { sectionModalTitle.textContent = `Create ${labels.sections_label}`; }
  if (sectionLabelPrompt) {
    sectionLabelPrompt.firstChild.textContent = `${labels.sections_label} name`;
    const input = sectionLabelPrompt.querySelector('input');
    if (input) input.placeholder = `${labels.sections_label} name`;
  }

  const modal = document.getElementById('sectionModal');
  const sectionForm = document.getElementById('sectionForm');
  const sectionMsg = document.getElementById('sectionMsg');
  const sectionSlugInput = document.getElementById('sectionSlug');
  const sectionLabelInput = document.getElementById('sectionLabel');
  const sectionDetailInput = document.getElementById('sectionDetail');
  const sectionCancel = document.getElementById('sectionCancel');

  const sectionSearch = document.getElementById('sectionSearch');
  let allSections = [];

  const menuButton = document.getElementById('accountMenuButton');
  const menu = document.getElementById('accountMenu');
  const addSectionMenuBtn = menu ? menu.querySelector('button[data-action="add-section"]') : null;
  const addSectionBtn = document.getElementById('addSectionBtn');

  const isReadOnly = me.user_type === 'standard';

  if (emptyCreateBtn) { emptyCreateBtn.textContent = `Create a ${labels.sections_label}`; }
  if (addSectionMenuBtn) { addSectionMenuBtn.textContent = `Add ${labels.sections_label}`; }
  if (addSectionBtn) { addSectionBtn.textContent = `Add ${labels.sections_label}`; }

  let accountName = `Account ${accountId}`;

  try {
    const myAccounts = await api('/api/me/accounts');
    const match = myAccounts.find(a => a.id === accountId);
    if (match) {
      accountName = match.name;
      acctNameEl.textContent = match.name;
    } else {
      acctNameEl.textContent = `Account ${accountId}`;
    }
  } catch {
    acctNameEl.textContent = `Account ${accountId}`;
  }
  document.title = `${accountName} | ${labels.sections_label}`;

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

  function openModal() {
    sectionMsg.textContent = '';
    sectionForm.reset();
    const autoSlug = generateSectionSlug();
    if (sectionSlugInput) {
      sectionSlugInput.value = autoSlug;
      sectionSlugInput.readOnly = true;
    }
    modal.classList.remove('hidden');
    setTimeout(() => {
      if (sectionLabelInput) {
        sectionLabelInput.focus();
      }
    }, 0);
  }

  function closeModal() {
    modal.classList.add('hidden');
    sectionMsg.textContent = '';
  }

  if (emptyCreateBtn) {
    if (isReadOnly) {
      emptyCreateBtn.classList.add('hidden');
    } else {
      emptyCreateBtn.addEventListener('click', (e) => {
        e.preventDefault();
        openModal();
      });
    }
  }

  if (addSectionBtn) {
    if (isReadOnly) {
      addSectionBtn.classList.add('hidden');
    } else {
      addSectionBtn.addEventListener('click', (e) => {
        e.preventDefault();
        openModal();
      });
    }
  }

  sectionCancel.addEventListener('click', (e) => {
    e.preventDefault();
    closeModal();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeMenu();
      closeModal();
    }
  });

  function renderSections(term = '') {
    const filtered = term
      ? allSections.filter(s =>
        (s.label || '').toLowerCase().includes(term.toLowerCase())
        || (s.slug || '').toLowerCase().includes(term.toLowerCase())
        || (s.detail || '').toLowerCase().includes(term.toLowerCase())
      )
      : allSections;

    if (!filtered.length) {
      sectionListEl.innerHTML = '';
      if (!term && !allSections.length) {
        emptyStateEl.classList.remove('hidden');
      } else {
        sectionListEl.innerHTML = '<p class="small" style="text-align:center">No sections match your search.</p>';
        emptyStateEl.classList.add('hidden');
      }
      return;
    }

    emptyStateEl.classList.add('hidden');
    sectionListEl.innerHTML = filtered.map(s => {
      const slugLine = showSlugs ? `<div class="small"><code>${escapeHtml(s.slug)}</code></div>` : '';
      const detailText = (s.detail || '').trim();
      const detailLine = detailText ? `<span class="small" style="margin-left:8px;">${escapeHtml(detailText)}</span>` : '';
      const statusSummaryContainerId = `sectionStatusSummary-${encodeURIComponent(s.slug)}`;
      return `
        <div class="card" style="margin-bottom:8px;">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
            <div>
              <strong>${escapeHtml(s.label)}</strong>${detailLine}
              ${slugLine}
            </div>
            <div class="section-actions">
              <div id="${statusSummaryContainerId}" class="section-status-summary hidden" aria-live="polite"></div>
              <a class="btn" href="/section.html?account=${encodeURIComponent(accountId)}&slug=${encodeURIComponent(s.slug)}">Open</a>
            </div>
          </div>
        </div>
      `;
    }).join('');

    renderStatusSummariesFor(filtered);
  }

  async function renderStatusSummariesFor(sections) {
    await Promise.all(sections.map(async (section) => {
      const config = parseStatusSummaryConfig(section?.schema?.status_summary);
      const summaryEl = document.getElementById(`sectionStatusSummary-${encodeURIComponent(section.slug)}`);
      if (!summaryEl || !config) return;

      try {
        const page = await api(`/api/accounts/${accountId}/sections/${encodeURIComponent(section.slug)}/items?limit=200`);
        const counts = computeStatusCounts(page?.items || [], config);
        summaryEl.innerHTML = `
          <span class="status-pill status-pill-red">${renderStatusText(config.redLabel, counts.red)}</span>
          <span class="status-pill status-pill-yellow">${renderStatusText(config.yellowLabel, counts.yellow)}</span>
          <span class="status-pill status-pill-green">${renderStatusText(config.greenLabel, counts.green)}</span>
        `;
        summaryEl.classList.remove('hidden');
      } catch {
        summaryEl.classList.add('hidden');
      }
    }));
  }

  if (sectionSearch) {
    sectionSearch.addEventListener('input', (e) => {
      renderSections(e.target.value);
    });
  }

  async function loadSections() {
    try {
      allSections = await api(`/api/accounts/${accountId}/sections`);
      renderSections(sectionSearch ? sectionSearch.value : '');
    } catch (e) {
      sectionListEl.innerHTML = `<p class="small">Failed to load sections: ${escapeHtml(e.message)}</p>`;
      emptyStateEl.classList.add('hidden');
    }
  }

  await loadSections();

  // Section create form (modal)
  sectionForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    sectionMsg.textContent = 'Saving…';
    const rawSlug = sectionSlugInput.value || generateSectionSlug();
    const label = sectionLabelInput.value.trim();
    const detail = sectionDetailInput ? sectionDetailInput.value.trim() : '';
    const slug = rawSlug.trim() || generateSectionSlug();

    if (slug === 'default') {
      sectionMsg.textContent = '"default" is reserved. Choose another slug.';
      return;
    }

    try {
      await api(`/api/accounts/${accountId}/sections`, {
        method: 'POST',
        body: JSON.stringify({ slug, label: label || slug, detail, schema: {} })
      });
      // Fire-and-forget webhook with new section details
      try {
        fetch('https://n8n.adigi8.app/webhook/4e02f681-fdf6-4dea-a4c8-77dca1d54a5a', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            event: 'section_created',
            account_id: accountId,
            section: {
              slug,
              label: label || slug,
              detail,
              schema: {},
            },
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
      sectionMsg.textContent = 'Section saved.';
      closeModal();
      await loadSections();
    } catch (err) {
      sectionMsg.textContent = err.message || 'Failed to save section';
    }
  });

  // 3-dot menu actions
  menu.addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const action = btn.dataset.action;
    closeMenu();

    if (isReadOnly && (action === 'add-section' || action === 'edit' || action === 'delete')) {
      notifyWarning('You have read-only access and cannot modify this account or its sections.');
      return;
    }

    if (action === 'add-section') {
      openModal();
    } else if (action === 'template-settings') {
      window.location.href = `/account-template.html?id=${encodeURIComponent(accountId)}`;
    } else if (action === 'edit') {
      const next = await promptDialog('Account name', { value: accountName, confirmLabel: 'Rename' });
      if (!next) return;
      const trimmed = next.trim();
      if (!trimmed || trimmed === accountName) return;
      try {
        const updated = await api(`/api/accounts/${accountId}`, {
          method: 'PUT',
          body: JSON.stringify({ name: trimmed })
        });
        accountName = updated.name;
        acctNameEl.textContent = updated.name;
        notifySuccess('Account renamed.');
      } catch (err) {
        notifyError(err.message || 'Failed to update account');
      }
    } else if (action === 'delete') {
      const ok = await confirmDialog('Delete this account and all its data? This cannot be undone.', {
        title: 'Delete account', confirmLabel: 'Delete',
      });
      if (!ok) return;
      try {
        await api(`/api/accounts/${accountId}`, { method: 'DELETE' });
        window.location.replace('/accounts.html');
      } catch (err) {
        notifyError(err.message || 'Failed to delete account');
      }
    }
  });
})();
