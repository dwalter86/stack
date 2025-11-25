import { loadMeOrRedirect, renderShell, api, DEFAULT_LABELS } from './common.js';

const TYPE_LABELS = {
  super_admin: 'Super admin',
  admin: 'Admin',
  standard: 'Standard user'
};

(async () => {
  const me = await loadMeOrRedirect(); if(!me) return;
  renderShell(me);
  if(!me.is_admin){ window.location.replace('/accounts.html'); return; }

  const list = document.getElementById('userList');
  const emptyState = document.getElementById('usersEmptyState');
  const showPreferences = me.user_type === 'super_admin';

  function renderPrefs(user){
    if(!showPreferences || !user.preferences) return '';
    const prefs = user.preferences;
    const changed = Object.entries(prefs).filter(([k,v]) => {
      const defaultVal = DEFAULT_LABELS[k] || '';
      return (v || '').trim() && v.trim() !== defaultVal;
    });
    if(!changed.length) return '<div class="small">Customised fields: none</div>';
    const items = changed.map(([k,v]) => `<li><strong>${k.replace('_',' ')}:</strong> ${v}</li>`).join('');
    return `<div class="small">Customised fields:<ul>${items}</ul></div>`;
  }

  try {
    const users = await api('/api/admin/users');
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
      return `
        <div class="card account-card">
          <div>
            <strong>${name}</strong>
            <div class="small">${u.email}</div>
            <div class="small">${typeLabel} • ${status}</div>
            ${prefs}
          </div>
          <div>
            <span class="pill small">${typeLabel}</span>
          </div>
        </div>
      `;
    }).join('');
  } catch(e){
    list.innerHTML = `<p class="small">Failed to load users: ${e.message}</p>`;
    emptyState.classList.add('hidden');
  }
})();
