import { loadMeOrRedirect, renderShell, api, getLabels, getPreferences, escapeHtml } from './common.js';
(async () => {
  const me = await loadMeOrRedirect(); if (!me) return;
  renderShell(me);

  const labels = getLabels(me);
  const preferences = getPreferences(me);
  const showSlugs = preferences.show_slugs;
  const accountHeading = document.getElementById('accountsHeading');
  const emptyCopy = document.getElementById('accountsEmptyCopy');
  const addAccountBtnLabel = document.getElementById('addAccountBtnLabel');
  if (accountHeading) { accountHeading.textContent = labels.accounts_label; }
  if (emptyCopy) { emptyCopy.textContent = `You do not have any ${labels.accounts_label.toLowerCase()} yet.`; }
  if (addAccountBtnLabel) { addAccountBtnLabel.textContent = `Add ${labels.accounts_label}`; }
  document.title = labels.accounts_label;

  const listEl = document.getElementById('accountList');
  const emptyStateEl = document.getElementById('accountsEmptyState');
  const menuButton = document.getElementById('accountsMenuButton');
  const menu = document.getElementById('accountsMenu');

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
    if (e.key === 'Escape') { closeMenu(); }
  });

  let allAccounts = [];

  function renderAccounts(term = '') {
    const filtered = term
      ? allAccounts.filter(a => (a.name || '').toLowerCase().includes(term.toLowerCase()) || (a.id || '').toLowerCase().includes(term.toLowerCase()))
      : allAccounts;

    if (!filtered.length) {
      listEl.innerHTML = '';
      if (!term && !allAccounts.length) {
        // Only show empty state if no accounts exist at all (not just filtered out)
        emptyStateEl.classList.remove('hidden');
      } else {
        // Search yielded no results
        listEl.innerHTML = '<p class="small" style="text-align:center">No accounts match your search.</p>';
        emptyStateEl.classList.add('hidden');
      }
      return;
    }

    emptyStateEl.classList.add('hidden');
    listEl.innerHTML = filtered.map(a => {
      const slugLine = showSlugs ? `<div class="small"><code>${escapeHtml(a.id)}</code></div>` : '';
      return `
        <div class="card account-card">
          <div>
            <strong>${escapeHtml(a.name)}</strong>
            ${slugLine}
          </div>
          <div>
            <a class="btn" href="/account.html?id=${encodeURIComponent(a.id)}">Open</a>
          </div>
        </div>
      `;
    }).join('');
  }

  const searchInput = document.getElementById('accountSearch');
  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      renderAccounts(e.target.value);
    });
  }

  async function loadAccounts() {
    try {
      allAccounts = await api('/api/me/accounts');
      renderAccounts(searchInput ? searchInput.value : '');
    } catch (e) {
      listEl.innerHTML = `<p class="small">Failed to load accounts: ${e.message}</p>`;
      emptyStateEl.classList.add('hidden');
    }
  }

  menu.addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const action = btn.dataset.action;
    closeMenu();
    if (action === 'add-account') {
      const name = prompt('Account name');
      if (!name) return;
      const trimmed = name.trim();
      if (!trimmed) return;
      try {
        await api('/api/accounts', { method: 'POST', body: JSON.stringify({ name: trimmed }) });
        await loadAccounts();
      } catch (err) {
        alert(err.message || 'Failed to create account');
      }
    }
  });

  await loadAccounts();
})();
