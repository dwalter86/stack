import { loadMeOrRedirect, renderShell } from './common.js';

(async () => {
  const me = await loadMeOrRedirect(); if(!me) return;
  renderShell(me);
  if(!me.is_admin){ window.location.replace('/accounts.html'); return; }

  const list = document.getElementById('settingsList');
  const sections = [
    { key:'users', label:'Users', description:'Manage user roles, access, and settings.', href:'/admin.html' },
    { key:'customisation', label:'Customisation', description:'Rename UI labels for accounts, sections, and items for your user.', href:'/customisation.html' },
  ];

  if(me.user_type === 'super_admin'){
    sections.push({ key:'api-explorer', label:'API explorer', description:'View Swagger documentation and try API calls with your token.', href:'/api-explorer.html' });
  }

  list.innerHTML = sections.map(section => `
    <div class="card account-card">
      <div>
        <strong>${section.label}</strong>
        <div class="small">${section.description}</div>
      </div>
      <div><a class="btn" href="${section.href}">Open</a></div>
    </div>
  `).join('');
})();
