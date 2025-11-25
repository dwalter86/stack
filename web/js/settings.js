import { loadMeOrRedirect, renderShell } from './common.js';

(async () => {
  const me = await loadMeOrRedirect(); if(!me) return;
  renderShell(me);
  if(!me.is_admin){ window.location.replace('/accounts.html'); return; }

  const list = document.getElementById('settingsList');
  const sections = [
    { key:'users', label:'Users', description:'Manage user roles, access, and settings.', href:'/admin.html' },
    { key:'customisation', label:'Customisation', description:'Rename UI labels for accounts, sections, and items for your user.', href:'/customisation.html' },
    { key:'api-calls', label:'API calls', description:'View endpoints for managing sections and items.', href:'/settings-api-calls.html' },
  ];

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
