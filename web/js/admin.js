import { loadMeOrRedirect, renderShell, api, DEFAULT_PREFERENCES } from './common.js';

const TYPE_LABELS = {
  super_admin: 'Super admin',
  admin: 'Admin',
  standard: 'Standard user'
};

(async () => {
  const me = await loadMeOrRedirect(); if(!me) return;
  renderShell(me);
  if(!me.is_admin){ window.location.replace('/accounts.html'); return; }

  // Modal elements
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

  let allUsers = []; // Cache for users

  const list = document.getElementById('userList');
  const emptyState = document.getElementById('usersEmptyState');
  const showPreferences = me.user_type === 'super_admin';

  function renderPrefs(user){
    if(!showPreferences || !user.preferences) return '';
    const prefs = user.preferences;
    const changed = Object.entries(prefs).filter(([k,v]) => {
      if(!(k in DEFAULT_PREFERENCES)) return false;
      const defaultVal = DEFAULT_PREFERENCES[k];
      if(typeof defaultVal === 'boolean') return Boolean(v) !== defaultVal;
      if(typeof v !== 'string') return false;
      const trimmed = v.trim();
      return trimmed && trimmed !== defaultVal;
    });
    if(!changed.length) return '<div class="small">Customised fields: none</div>';
    const items = changed.map(([k,v]) => {
      const label = k.replace('_',' ');
      if(typeof DEFAULT_PREFERENCES[k] === 'boolean'){
        return `<li><strong>${label}:</strong> ${v ? 'Yes' : 'No'}</li>`;
      }
      return `<li><strong>${label}:</strong> ${v}</li>`;
    }).join('');
    return `<div class="small">Customised fields:<ul>${items}</ul></div>`;
  }

  try {
    const users = await api('/api/admin/users'); // Renamed to 'users'
    allUsers = users; // Cache the user list
    if(!users.length){
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

      const editButton = canEdit ? `<button class="btn small" data-action="edit" data-id="${u.id}">Edit</button>` : '';
      const deleteButton = canDelete ? `<button class="btn small danger" data-action="delete" data-id="${u.id}">Delete</button>` : '';
      return `
        <div class="card account-card" id="user-card-${u.id}">
          <div>
            <strong>${name}</strong>
            <div class="small">${u.email}</div>
            <div class="small">${typeLabel} • ${status}</div>
            ${prefs}
          </div>
          <div class="card-actions">
            ${editButton}
            ${deleteButton}
          </div>
        </div>
      `;
    }).join('');
  } catch(e){
    list.innerHTML = `<p class="small">Failed to load users: ${e.message}</p>`;
    emptyState.classList.add('hidden');
  }

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
    editModalTitle.textContent = `Edit User: ${user.name || user.email}`;

    // Super admins can't be demoted by regular admins
    const typeSelect = document.getElementById('editUserType');
    if (me.user_type !== 'super_admin') {
      Array.from(typeSelect.options).forEach(opt => {
        if (opt.value === 'super_admin') {
          opt.disabled = true;
        }
      });
    }

    editModal.classList.remove('hidden');
    editUserName.focus();
  }

  if (editCancelBtn) {
    editCancelBtn.addEventListener('click', closeEditModal);
  }

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
      if (confirm('Are you sure you want to delete this user? This cannot be undone.')) {
        try {
          await api(`/api/admin/users/${userId}`, { method: 'DELETE' });
          document.getElementById(`user-card-${userId}`)?.remove();
        } catch (err) {
          alert(`Failed to delete user: ${err.message}`);
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

      try {
        const updatedUser = await api(`/api/admin/users/${userId}`, {
          method: 'PUT',
          body: JSON.stringify(payload),
        });

        // Update user in the cache
        const userIndex = allUsers.findIndex(u => u.id === userId);
        if (userIndex !== -1) {
          allUsers[userIndex] = { ...allUsers[userIndex], ...updatedUser };
        }

        // Re-render the specific user card
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
      } catch (err) {
        editMsg.textContent = `Error: ${err.message}`;
      }
    });
  }
})();
