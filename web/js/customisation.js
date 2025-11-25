import { loadMeOrRedirect, renderShell, api, getLabels, DEFAULT_LABELS } from './common.js';

(async () => {
  const me = await loadMeOrRedirect(); if(!me) return;
  renderShell(me);
  if(!me.is_admin){ window.location.replace('/accounts.html'); return; }

  const form = document.getElementById('customisationForm');
  const msg = document.getElementById('customisationMsg');
  const accountsInput = document.getElementById('accountsLabel');
  const sectionsInput = document.getElementById('sectionsLabel');
  const itemsInput = document.getElementById('itemsLabel');
  const resetBtn = document.getElementById('resetDefaults');

  const labels = getLabels(me);
  document.title = 'Customisation';
  accountsInput.value = labels.accounts_label;
  sectionsInput.value = labels.sections_label;
  itemsInput.value = labels.items_label;

  async function save(payload){
    msg.textContent = 'Saving…';
    try{
      const res = await api('/api/me/preferences', { method:'PUT', body: JSON.stringify(payload) });
      me.preferences = res;
      renderShell(me);
      msg.textContent = 'Saved.';
    }catch(e){
      msg.textContent = e.message || 'Failed to save preferences';
    }
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    await save({
      accounts_label: accountsInput.value.trim(),
      sections_label: sectionsInput.value.trim(),
      items_label: itemsInput.value.trim(),
    });
  });

  if(resetBtn){
    resetBtn.addEventListener('click', async (e) => {
      e.preventDefault();
      accountsInput.value = DEFAULT_LABELS.accounts_label;
      sectionsInput.value = DEFAULT_LABELS.sections_label;
      itemsInput.value = DEFAULT_LABELS.items_label;
      await save(DEFAULT_LABELS);
    });
  }
})();
