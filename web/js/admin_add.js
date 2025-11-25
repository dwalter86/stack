import { loadMeOrRedirect, renderShell, api } from './common.js';
(async () => {
  const me = await loadMeOrRedirect(); if(!me) return;
  renderShell(me);
  if(!me.is_admin){ window.location.replace('/accounts.html'); return; }

  const grid = document.getElementById('acctGrid');
  const selectAll = document.getElementById('selectAll');
  const msg = document.getElementById('msg');
  const form = document.getElementById('addForm');
  const userType = document.getElementById('userType');
  const nameInput = document.getElementById('name');

  if(me.user_type !== 'super_admin'){
    const superOpt = userType.querySelector('option[value="super_admin"]');
    if(superOpt) superOpt.disabled = true;
    if(userType.value === 'super_admin') userType.value = 'admin';
  }
  
  try {
    const accounts = await api('/api/admin/all-accounts');
    grid.innerHTML = accounts.map(a => `
      <label><input type="checkbox" value="${a.id}"> ${a.name} <span class="small"><code>${a.id}</code></span></label>
    `).join('');
  } catch(e){
    grid.innerHTML = `<p class="small">Failed to load accounts: ${e.message}</p>`;
  }

  selectAll.addEventListener('change', () => {
    grid.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = selectAll.checked);
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    msg.textContent = 'Creating…';
    const name = nameInput.value.trim();
    const email = document.getElementById('email').value.trim();
    const password = document.getElementById('password').value;
    const selected = Array.from(grid.querySelectorAll('input[type="checkbox"]:checked')).map(cb => cb.value);
    const role = userType.value;
    if(!name){ msg.textContent = 'Name is required'; return; }
    try {
      await api('/api/admin/users', { method:'POST', body: JSON.stringify({ name, email, password, user_type: role, accounts: selected }) });
      msg.textContent = 'User created successfully.';
      form.reset();
      selectAll.checked = false;
      if(me.user_type !== 'super_admin'){ userType.value = 'admin'; }
    } catch(err){
      msg.textContent = err.message || 'Failed to create user';
    }
  });
})();
