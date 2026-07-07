import { loadMeOrRedirect, renderShell, api, DEFAULT_PREFERENCES, escapeHtml } from './common.js';
import { notifySuccess, notifyError, confirmDialog } from './notify.js';

const TYPE_LABELS = {
  super_admin: 'Super admin',
  admin: 'Admin',
  standard: 'Standard user'
};

(async () => {
  const me = await loadMeOrRedirect(); if (!me) return;
  renderShell(me);
  if (!me.is_admin) { window.location.replace('/accounts.html'); return; }

  // Edit modal elements
  const editModal = document.getElementById('editUserModal');
  const editForm = document.getElementById('editUserForm');
  const editMsg = document.getElementById('editUserMsg');
  const editUserId = document.getElementById('editUserId');
  const editUserName = document.getElementById('editUserName');
  const editUserEmail = document.getElementById('editUserEmail');
  const editUserType = document.getElementById('editUserType');
  const editUserIsActive = document.getElementById('editUserIsActive');
  const editCancelBtn = document.getElementById('editUserCancel');
  const editModalTitle = document.getElementById('editUserModalTitle');
  const editAccountsSection = document.getElementById('editUserAccountsSection');
  const editAccountsGrid = document.getElementById('editUserAccountsGrid');
  const editSelectAll = document.getElementById('editUserSelectAll');
  const editAccountSearch = document.getElementById('editUserAccountSearch');

  // Add modal elements
  const addModal = document.getElementById('addUserModal');
  const addForm = document.getElementById('addUserForm');
  const addMsg = document.getElementById('addUserMsg');
  const addUserName = document.getElementById('addUserName');
  const addUserEmail = document.getElementById('addUserEmail');
  const addUserPassword = document.getElementById('addUserPassword');
  const addUserType = document.getElementById('addUserType');
  const addCancelBtn = document.getElementById('addUserCancel');
  const addAccountsGrid = document.getElementById('addUserAccountsGrid');
  const addSelectAll = document.getElementById('addUserSelectAll');
  const addAccountSearch = document.getElementById('addUserAccountSearch');

  const isSuperAdmin = me.user_type === 'super_admin';
  let allUsers = [];
  let allAccounts = [];

  const list = document.getElementById('userList');
  const emptyState = document.getElementById('usersEmptyState');
  const showPreferences = isSuperAdmin;

  // Non-super admins can't create or promote to super admin
  if (!isSuperAdmin) {
    for (const select of [editUserType, addUserType]) {
      const opt = select?.querySelector('option[value="super_admin"]');
      if (opt) opt.disabled = true;
    }
  }

  function renderChecklist(gridEl, accounts) {
    gridEl.innerHTML = accounts.map(a => `
      <label class="account-check" data-name="${escapeHtml(a.name.toLowerCase())}" title="${escapeHtml(a.id)}">
        <input type="checkbox" value="${escapeHtml(a.id)}">
        <span>${escapeHtml(a.name)}</span>
      </label>
    `).join('');
  }

  function setupChecklist(gridEl, selectAllEl, searchEl) {
    const visibleBoxes = () => Array.from(gridEl.querySelectorAll('.account-check'))
      .filter(row => !row.classList.contains('hidden'))
      .map(row => row.querySelector('input[type="checkbox"]'));

    const syncSelectAll = () => {
      const boxes = visibleBoxes();
      selectAllEl.checked = boxes.length > 0 && boxes.every(cb => cb.checked);
    };

    // Select all applies to the rows currently visible (i.e. matching the search).
    selectAllEl.addEventListener('change', () => {
      visibleBoxes().forEach(cb => { cb.checked = selectAllEl.checked; });
    });
    gridEl.addEventListener('change', syncSelectAll);
    searchEl.addEventListener('input', () => {
      const term = searchEl.value.trim().toLowerCase();
      gridEl.querySelectorAll('.account-check').forEach(row => {
        row.classList.toggle('hidden', term && !row.dataset.name.includes(term));
      });
      syncSelectAll();
    });

    return {
      reset(selectedIds = []) {
        searchEl.value = '';
        gridEl.querySelectorAll('.account-check').forEach(row => row.classList.remove('hidden'));
        const selected = new Set(selectedIds);
        gridEl.querySelectorAll('input[type="checkbox"]').forEach(cb => {
          cb.checked = selected.has(cb.value);
        });
        syncSelectAll();
      },
      checkedIds() {
        return Array.from(gridEl.querySelectorAll('input[type="checkbox"]:checked')).map(cb => cb.value);
      },
    };
  }

  let editChecklist = null;
  let addChecklist = null;
  try {
    allAccounts = await api('/api/admin/all-accounts');
    renderChecklist(addAccountsGrid, allAccounts);
    addChecklist = setupChecklist(addAccountsGrid, addSelectAll, addAccountSearch);
    if (isSuperAdmin && editAccountsSection) {
      renderChecklist(editAccountsGrid, allAccounts);
      editChecklist = setupChecklist(editAccountsGrid, editSelectAll, editAccountSearch);
      editAccountsSection.classList.remove('hidden');
    }
  } catch (e) {
    addAccountsGrid.innerHTML = `<p class="small">Failed to load accounts: ${escapeHtml(e.message)}</p>`;
    if (isSuperAdmin && editAccountsGrid) {
      editAccountsGrid.innerHTML = `<p class="small">Failed to load accounts: ${escapeHtml(e.message)}</p>`;
      editAccountsSection.classList.remove('hidden');
    }
  }

  function renderPrefs(user) {
    if (!showPreferences || !user.preferences) return '';
    const prefs = user.preferences;
    const changed = Object.entries(prefs).filter(([k, v]) => {
      if (!(k in DEFAULT_PREFERENCES)) return false;
      const defaultVal = DEFAULT_PREFERENCES[k];
      if (typeof defaultVal === 'boolean') return Boolean(v) !== defaultVal;
      if (typeof v !== 'string') return false;
      const trimmed = v.trim();
      return trimmed && trimmed !== defaultVal;
    });
    if (!changed.length) return '<div class="small">Customised fields: none</div>';
    const items = changed.map(([k, v]) => {
      const label = k.replace('_', ' ');
      if (typeof DEFAULT_PREFERENCES[k] === 'boolean') {
        return `<li><strong>${escapeHtml(label)}:</strong> ${v ? 'Yes' : 'No'}</li>`;
      }
      return `<li><strong>${escapeHtml(label)}:</strong> ${escapeHtml(v)}</li>`;
    }).join('');
    return `<div class="small">Customised fields:<ul>${items}</ul></div>`;
  }

  async function loadUsers() {
    try {
      const users = await api('/api/admin/users');
      allUsers = users;
      if (!users.length) {
        list.innerHTML = '';
        emptyState.classList.remove('hidden');
        return;
      }
      emptyState.classList.add('hidden');
      list.innerHTML = users.map(u => {
        const typeLabel = TYPE_LABELS[u.user_type] || u.user_type;
        const status = u.is_active ? 'Active' : 'Disabled';
        const prefs = renderPrefs(u);
        const name = u.name?.trim() || u.email;
        const canEdit = me.user_type === 'super_admin' || u.user_type !== 'super_admin';
        const canDelete = canEdit && me.id !== u.id;

        const editButton = canEdit ? `<button class="btn" data-action="edit" data-id="${u.id}">Edit</button>` : '';
        const deleteButton = canDelete ? `<button class="btn danger" data-action="delete" data-id="${u.id}">Delete</button>` : '';
        return `
          <div class="card account-card account-card-user" id="user-card-${u.id}">
            <div style="display:flex;align-items:flex-start;gap:16px;">
              <div style="min-width:0;">
                <strong>${escapeHtml(name)}</strong>
                <div class="small">${escapeHtml(u.email)}</div>
                <div class="small">${escapeHtml(typeLabel)} • ${status}</div>
              </div>
              <div style="flex:1;min-width:0;text-align:left;">
                ${prefs}
              </div>
              <div class="card-actions" style="white-space:nowrap;margin-left:auto;">
                ${editButton}
                ${deleteButton}
              </div>
            </div>
          </div>
        `;
      }).join('');
    } catch (e) {
      list.innerHTML = `<p class="small">Failed to load users: ${escapeHtml(e.message)}</p>`;
      emptyState.classList.add('hidden');
    }
  }

  await loadUsers();

  // ----- Edit modal -----
  function closeEditModal() {
    editModal.classList.add('hidden');
    editMsg.textContent = '';
  }

  function openEditModal(user) {
    editMsg.textContent = '';
    editUserId.value = user.id;
    editUserName.value = user.name;
    editUserEmail.value = user.email;
    editUserType.value = user.user_type;
    editUserIsActive.checked = user.is_active;
    editModalTitle.textContent = `Edit user: ${user.name || user.email}`;
    if (editChecklist) editChecklist.reset(user.accounts || []);
    editModal.classList.remove('hidden');
    editUserName.focus();
  }

  if (editCancelBtn) editCancelBtn.addEventListener('click', closeEditModal);
  editModal.addEventListener('click', (e) => { if (e.target === editModal) closeEditModal(); });

  // ----- Add modal -----
  function closeAddModal() {
    addModal.classList.add('hidden');
    addMsg.textContent = '';
  }

  function openAddModal() {
    addForm.reset();
    addMsg.textContent = '';
    addUserType.value = 'standard';
    if (addChecklist) addChecklist.reset([]);
    addModal.classList.remove('hidden');
    addUserName.focus();
  }

  const addUserBtn = document.getElementById('addUserBtn');
  const emptyAddUserBtn = document.getElementById('emptyAddUserBtn');
  if (addUserBtn) addUserBtn.addEventListener('click', openAddModal);
  if (emptyAddUserBtn) emptyAddUserBtn.addEventListener('click', openAddModal);
  if (addCancelBtn) addCancelBtn.addEventListener('click', closeAddModal);
  addModal.addEventListener('click', (e) => { if (e.target === addModal) closeAddModal(); });

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!editModal.classList.contains('hidden')) closeEditModal();
    if (!addModal.classList.contains('hidden')) closeAddModal();
  });

  list.addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;

    const action = btn.dataset.action;
    const userId = btn.dataset.id;
    const user = allUsers.find(u => u.id === userId);

    if (!user) return;

    if (action === 'edit') {
      openEditModal(user);
    } else if (action === 'delete') {
      const ok = await confirmDialog('Are you sure you want to delete this user? This cannot be undone.', {
        title: 'Delete user', confirmLabel: 'Delete',
      });
      if (ok) {
        try {
          await api(`/api/admin/users/${userId}`, { method: 'DELETE' });
          document.getElementById(`user-card-${userId}`)?.remove();
          notifySuccess('User deleted.');
        } catch (err) {
          notifyError(`Failed to delete user: ${err.message}`);
        }
      }
    }
  });

  if (editForm) {
    editForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      editMsg.textContent = 'Saving...';

      const userId = editUserId.value;
      const payload = {
        name: editUserName.value.trim(),
        user_type: editUserType.value,
        is_active: editUserIsActive.checked,
      };
      if (editChecklist) {
        payload.accounts = editChecklist.checkedIds();
      }

      try {
        const updatedUser = await api(`/api/admin/users/${userId}`, {
          method: 'PUT',
          body: JSON.stringify(payload),
        });

        const userIndex = allUsers.findIndex(u => u.id === userId);
        if (userIndex !== -1) {
          allUsers[userIndex] = { ...allUsers[userIndex], ...updatedUser };
        }

        const card = document.getElementById(`user-card-${userId}`);
        if (card) {
          const typeLabel = TYPE_LABELS[updatedUser.user_type] || updatedUser.user_type;
          const status = updatedUser.is_active ? 'Active' : 'Disabled';
          const name = updatedUser.name?.trim() || updatedUser.email;
          const cardContent = card.querySelector('div:first-child');
          if (cardContent) {
            cardContent.querySelector('strong').textContent = name;
            cardContent.querySelector('.small:nth-of-type(2)').textContent = `${typeLabel} • ${status}`;
          }
        }

        closeEditModal();
        notifySuccess('User updated.');
      } catch (err) {
        editMsg.textContent = `Error: ${err.message}`;
      }
    });
  }

  if (addForm) {
    addForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      addMsg.textContent = 'Creating…';

      const name = addUserName.value.trim();
      if (!name) { addMsg.textContent = 'Name is required'; return; }
      const payload = {
        name,
        email: addUserEmail.value.trim(),
        password: addUserPassword.value,
        user_type: addUserType.value,
        accounts: addChecklist ? addChecklist.checkedIds() : [],
      };

      try {
        await api('/api/admin/users', { method: 'POST', body: JSON.stringify(payload) });
        closeAddModal();
        notifySuccess(`User ${name} created.`);
        await loadUsers();
      } catch (err) {
        addMsg.textContent = err.message || 'Failed to create user';
      }
    });
  }
})();
