import { loadMeOrRedirect, renderShell, api, getLabels, getPreferences } from './common.js';

function qs(name){
  const m = new URLSearchParams(location.search).get(name);
  return m && decodeURIComponent(m);
}

function slugify(val){
  const s = (val || '').toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return s || 'section';
}

(async () => {
  const me = await loadMeOrRedirect(); if(!me) return;
  renderShell(me);
  const labels = getLabels(me);
  const preferences = getPreferences(me);
  const showSlugs = preferences.show_slugs;

  const accountId = qs('id');
  if(!accountId){
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

  if(sectionsHeading){ sectionsHeading.textContent = labels.sections_label; }
  if(sectionsEmptyCopy){ sectionsEmptyCopy.textContent = `No ${labels.sections_label.toLowerCase()} have been created for this account yet.`; }
  if(sectionModalTitle){ sectionModalTitle.textContent = `Create ${labels.sections_label}`; }
  if(sectionLabelPrompt){ sectionLabelPrompt.firstChild.textContent = `${labels.sections_label} name`;
    const input = sectionLabelPrompt.querySelector('input');
    if(input) input.placeholder = `${labels.sections_label} name`;
  }

  const modal = document.getElementById('sectionModal');
  const sectionForm = document.getElementById('sectionForm');
  const sectionMsg = document.getElementById('sectionMsg');
  const sectionSlugInput = document.getElementById('sectionSlug');
  const sectionLabelInput = document.getElementById('sectionLabel');
  const sectionCancel = document.getElementById('sectionCancel');

  const menuButton = document.getElementById('accountMenuButton');
  const menu = document.getElementById('accountMenu');
  const addSectionMenuBtn = menu ? menu.querySelector('button[data-action="add-section"]') : null;

  if(emptyCreateBtn){ emptyCreateBtn.textContent = `Create a ${labels.sections_label}`; }
  if(addSectionMenuBtn){ addSectionMenuBtn.textContent = `Add ${labels.sections_label}`; }

  let accountName = `Account ${accountId}`;

  try {
    const myAccounts = await api('/api/me/accounts');
    const match = myAccounts.find(a => a.id === accountId);
    if(match){
      accountName = match.name;
      acctNameEl.textContent = match.name;
    } else {
      acctNameEl.textContent = `Account ${accountId}`;
    }
  } catch {
    acctNameEl.textContent = `Account ${accountId}`;
  }
  document.title = `${accountName} | ${labels.sections_label}`;

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

  function openModal(){
    sectionMsg.textContent = '';
    sectionForm.reset();
    modal.classList.remove('hidden');
    setTimeout(() => sectionSlugInput.focus(), 0);
  }

  function closeModal(){
    modal.classList.add('hidden');
    sectionMsg.textContent = '';
  }

  if(emptyCreateBtn){
    emptyCreateBtn.addEventListener('click', (e) => {
      e.preventDefault();
      openModal();
    });
  }

  sectionCancel.addEventListener('click', (e) => {
    e.preventDefault();
    closeModal();
  });

  document.addEventListener('keydown', (e) => {
    if(e.key === 'Escape'){
      closeMenu();
      closeModal();
    }
  });

  async function loadSections(){
    try{
      const sections = await api(`/api/accounts/${accountId}/sections`);
      if(!sections.length){
        sectionListEl.innerHTML = '';
        emptyStateEl.classList.remove('hidden');
        return;
      }
      emptyStateEl.classList.add('hidden');
      sectionListEl.innerHTML = sections.map(s => {
        const slugLine = showSlugs ? `<div class="small"><code>${s.slug}</code></div>` : '';
        return `
        <div class="card" style="margin-bottom:8px;">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
            <div>
              <strong>${s.label}</strong>
              ${slugLine}
            </div>
            <div>
              <a class="btn" href="/section.html?account=${encodeURIComponent(accountId)}&slug=${encodeURIComponent(s.slug)}">Open</a>
            </div>
          </div>
        </div>
      `;
      }).join('');
    }catch(e){
      sectionListEl.innerHTML = `<p class="small">Failed to load sections: ${e.message}</p>`;
      emptyStateEl.classList.add('hidden');
    }
  }

  await loadSections();

  // Section create form (modal)
  sectionForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    sectionMsg.textContent = 'Saving…';
    const rawSlug = sectionSlugInput.value;
    const label = sectionLabelInput.value.trim();
    const slug = slugify(rawSlug);

    if(slug === 'default'){
      sectionMsg.textContent = '"default" is reserved. Choose another slug.';
      return;
    }

    try {
      await api(`/api/accounts/${accountId}/sections`, {
        method:'POST',
        body: JSON.stringify({ slug, label: label || slug, schema: {} })
      });
      sectionMsg.textContent = 'Section saved.';
      closeModal();
      await loadSections();
    } catch(err){
      sectionMsg.textContent = err.message || 'Failed to save section';
    }
  });

  // 3-dot menu actions
  menu.addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-action]');
    if(!btn) return;
    const action = btn.dataset.action;
    closeMenu();

    if(action === 'add-section'){
      openModal();
    } else if(action === 'edit'){
      const next = prompt('Account name', accountName);
      if(!next) return;
      const trimmed = next.trim();
      if(!trimmed || trimmed === accountName) return;
      try {
        const updated = await api(`/api/accounts/${accountId}`, {
          method:'PUT',
          body: JSON.stringify({ name: trimmed })
        });
        accountName = updated.name;
        acctNameEl.textContent = updated.name;
      } catch(err){
        alert(err.message || 'Failed to update account');
      }
    } else if(action === 'delete'){
      if(!confirm('Delete this account and all its data? This cannot be undone.')){
        return;
      }
      try {
        await api(`/api/accounts/${accountId}`, { method:'DELETE' });
        window.location.replace('/accounts.html');
      } catch(err){
        alert(err.message || 'Failed to delete account');
      }
    }
  });
})();
