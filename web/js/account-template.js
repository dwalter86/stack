import { loadMeOrRedirect, renderShell, api, escapeHtml, getToken } from './common.js';
import { notifySuccess, notifyError, confirmDialog } from './notify.js';

function qs(name) {
  const m = new URLSearchParams(location.search).get(name);
  return m && decodeURIComponent(m);
}

async function downloadFile(path, suggestedName) {
  const token = getToken();
  const res = await fetch(path, {
    headers: token ? { Authorization: 'Bearer ' + token } : {},
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(text || `HTTP ${res.status}`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = suggestedName || 'download';
  document.body.appendChild(link);
  link.click();
  setTimeout(() => {
    URL.revokeObjectURL(url);
    link.remove();
  }, 0);
}

(async () => {
  const me = await loadMeOrRedirect(); if (!me) return;
  renderShell(me);

  const accountId = qs('id');
  if (!accountId) {
    document.body.innerHTML = '<main class="container"><p>Missing account id.</p></main>';
    return;
  }

  const isReadOnly = me.user_type === 'standard';
  const templateStatus = document.getElementById('templateStatus');
  const templateUploadBtn = document.getElementById('templateUploadBtn');
  const templateReplaceBtn = document.getElementById('templateReplaceBtn');
  const templateDeleteBtn = document.getElementById('templateDeleteBtn');
  const templateStarterBtn = document.getElementById('templateStarterBtn');
  const templateFileInput = document.getElementById('templateFileInput');
  const backToAccount = document.getElementById('backToAccount');
  const templateAccountMeta = document.getElementById('templateAccountMeta');

  if (backToAccount) {
    backToAccount.href = `/account.html?id=${encodeURIComponent(accountId)}`;
  }

  let accountName = `Account ${accountId}`;
  try {
    const myAccounts = await api('/api/me/accounts');
    const match = myAccounts.find(a => a.id === accountId);
    if (match) accountName = match.name;
  } catch {
    // ignore
  }
  document.title = `${accountName} | Export template`;
  if (templateAccountMeta) {
    templateAccountMeta.textContent = accountName;
  }

  function showElement(el, show) {
    if (!el) return;
    el.classList.toggle('hidden', !show);
  }

  function renderTemplateInfo(info) {
    if (!templateStatus) return;
    if (!info) {
      templateStatus.innerHTML = isReadOnly
        ? 'No template uploaded.'
        : 'No template uploaded yet. Upload a .docx to enable PDF and Word exports.';
      showElement(templateUploadBtn, !isReadOnly);
      showElement(templateReplaceBtn, false);
      showElement(templateDeleteBtn, false);
      return;
    }

    const updated = info.updated_at ? new Date(info.updated_at).toLocaleString() : '';
    const by = info.uploaded_by ? ` by ${escapeHtml(info.uploaded_by)}` : '';
    const fileLink = `<a href="#" id="templateDownloadLink">${escapeHtml(info.filename)}</a>`;
    templateStatus.innerHTML = `Current template: ${fileLink}<br><span class="small">Uploaded ${escapeHtml(updated)}${by}</span>`;
    const downloadLink = document.getElementById('templateDownloadLink');
    if (downloadLink) {
      downloadLink.addEventListener('click', (e) => {
        e.preventDefault();
        downloadFile(`/api/accounts/${accountId}/template/file`, info.filename || 'template.docx')
          .catch((err) => { notifyError(err.message || 'Download failed'); });
      });
    }
    showElement(templateUploadBtn, false);
    showElement(templateReplaceBtn, !isReadOnly);
    showElement(templateDeleteBtn, !isReadOnly);
  }

  async function loadTemplateInfo() {
    try {
      const info = await api(`/api/accounts/${accountId}/template`);
      renderTemplateInfo(info);
    } catch (err) {
      const msg = (err && err.message) || '';
      if (msg.includes('No template uploaded') || msg.includes('404')) {
        renderTemplateInfo(null);
      } else {
        templateStatus.textContent = `Failed to load template info: ${msg}`;
      }
    }
  }

  async function uploadTemplateFile(file) {
    const token = getToken();
    const formData = new FormData();
    formData.append('file', file, file.name || 'template.docx');
    const res = await fetch(`/api/accounts/${accountId}/template`, {
      method: 'PUT',
      headers: token ? { Authorization: 'Bearer ' + token } : {},
      body: formData,
    });
    if (!res.ok) {
      const text = await res.text().catch(() => res.statusText);
      throw new Error(text || `HTTP ${res.status}`);
    }
    return res.json();
  }

  if (templateStarterBtn) {
    templateStarterBtn.addEventListener('click', async (e) => {
      e.preventDefault();
      try {
        await downloadFile(`/api/accounts/${accountId}/template/starter`, 'starter_template.docx');
      } catch (err) {
        notifyError(err.message || 'Failed to download starter template');
      }
    });
  }

  if (!isReadOnly) {
    if (templateUploadBtn) {
      templateUploadBtn.addEventListener('click', () => {
        if (templateFileInput) templateFileInput.click();
      });
    }
    if (templateReplaceBtn) {
      templateReplaceBtn.addEventListener('click', () => {
        if (templateFileInput) templateFileInput.click();
      });
    }
    if (templateFileInput) {
      templateFileInput.addEventListener('change', async (e) => {
        const file = e.target.files && e.target.files[0];
        if (!file) return;
        templateStatus.textContent = 'Uploading...';
        try {
          const info = await uploadTemplateFile(file);
          renderTemplateInfo(info);
          notifySuccess('Template uploaded.');
        } catch (err) {
          templateStatus.textContent = `Upload failed: ${err.message || err}`;
        } finally {
          templateFileInput.value = '';
        }
      });
    }
    if (templateDeleteBtn) {
      templateDeleteBtn.addEventListener('click', async () => {
        const ok = await confirmDialog('Remove the export template for this account?', {
          title: 'Remove template', confirmLabel: 'Remove',
        });
        if (!ok) return;
        templateStatus.textContent = 'Removing...';
        try {
          await api(`/api/accounts/${accountId}/template`, { method: 'DELETE' });
          renderTemplateInfo(null);
          notifySuccess('Export template removed.');
        } catch (err) {
          templateStatus.textContent = `Failed to remove: ${err.message || err}`;
        }
      });
    }
  } else {
    showElement(templateUploadBtn, false);
    showElement(templateReplaceBtn, false);
    showElement(templateDeleteBtn, false);
  }

  await loadTemplateInfo();
})();
