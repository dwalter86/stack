import { loadMeOrRedirect, renderShell, api, getLabels } from './common.js';

function qs(name){
  const m = new URLSearchParams(location.search).get(name);
  return m && decodeURIComponent(m);
}

function escapeHtml(str){
  return String(str ?? '')
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;');
}

function formatDateTime(val){
  if(!val) return '';
  try {
    return new Date(val).toLocaleString();
  } catch {
    return String(val);
  }
}

(async () => {
  const me = await loadMeOrRedirect(); if(!me) return;
  renderShell(me);
  const labels = getLabels(me);

  const accountId = qs('account');
  const sectionSlug = qs('section') || qs('slug');
  const itemId = qs('item');
  const safeAccountId = encodeURIComponent(accountId || '');
  const safeItemId = encodeURIComponent(itemId || '');
  const commentsPath = sectionSlug
    ? `/api/accounts/${safeAccountId}/sections/${encodeURIComponent(sectionSlug)}/items/${safeItemId}/comments`
    : `/api/accounts/${safeAccountId}/items/${safeItemId}/comments`;

  const backToSection = document.getElementById('backToSection');
  const itemNameEl = document.getElementById('itemName');
  const itemMetaEl = document.getElementById('itemMeta');
  const commentsList = document.getElementById('commentsList');
  const noComments = document.getElementById('noComments');
  const commentForm = document.getElementById('commentForm');
  const commentBody = document.getElementById('commentBody');
  const commentMsg = document.getElementById('commentMsg');
  const submitComment = document.getElementById('submitComment');

  if(!accountId || !itemId){
    document.body.innerHTML = '<main class="container"><p>Missing account or item id.</p></main>';
    return;
  }

  if(backToSection){
    if(sectionSlug){
      backToSection.href = `/section.html?account=${encodeURIComponent(accountId)}&slug=${encodeURIComponent(sectionSlug)}`;
    } else {
      backToSection.href = `/account.html?id=${encodeURIComponent(accountId)}`;
    }
  }

  let accountName = `Account ${accountId}`;
  try {
    const myAccounts = await api('/api/me/accounts');
    const match = myAccounts.find(a => a.id === accountId);
    if(match) accountName = match.name;
  } catch {
    // ignore
  }

  async function loadItem(){
    try {
      const itemPath = sectionSlug
        ? `/api/accounts/${safeAccountId}/sections/${encodeURIComponent(sectionSlug)}/items/${safeItemId}`
        : `/api/accounts/${safeAccountId}/items/${safeItemId}`;
      const item = await api(itemPath);
      itemNameEl.textContent = `${item.name} comments`;
      const sectionLabel = sectionSlug ? `${labels.sections_label}: ${sectionSlug}` : labels.sections_label;
      const createdCopy = item.created_at ? ` · Added ${formatDateTime(item.created_at)}` : '';
      itemMetaEl.textContent = `${accountName} · ${sectionLabel} · id: ${itemId}${createdCopy}`;
      document.title = `${item.name} comments | ${labels.items_label}`;
    } catch(e){
      itemNameEl.textContent = 'Item not found';
      itemMetaEl.textContent = e.message || 'Failed to load item.';
      document.title = 'Item comments';
    }
  }

  function renderComments(comments){
    if(!comments.length){
      commentsList.innerHTML = '';
      noComments.classList.remove('hidden');
      return;
    }
    noComments.classList.add('hidden');
    commentsList.innerHTML = comments.map(c => `
      <div class="card" style="margin:0;">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
          <div>
            <div class="small" aria-label="Comment timestamp">${escapeHtml(formatDateTime(c.created_at))}</div>
            <div style="white-space:pre-wrap;">${escapeHtml(c.body)}</div>
          </div>
        </div>
      </div>
    `).join('');
  }

  async function loadComments(){
    try {
      const comments = await api(commentsPath);
      renderComments(comments || []);
    } catch(e){
      commentsList.innerHTML = `<p class="small">Failed to load comments: ${escapeHtml(e.message)}</p>`;
      noComments.classList.add('hidden');
    }
  }

  commentForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    commentMsg.textContent = '';
    const body = (commentBody.value || '').trim();
    if(!body){
      commentMsg.textContent = 'Comment cannot be empty.';
      return;
    }
    submitComment.disabled = true;
    commentMsg.textContent = 'Saving…';
    try {
      await api(commentsPath, {
        method:'POST',
        body: JSON.stringify({ body })
      });
      commentBody.value = '';
      commentMsg.textContent = 'Saved.';
      await loadComments();
    } catch(err){
      commentMsg.textContent = err.message || 'Failed to save comment';
    } finally {
      submitComment.disabled = false;
    }
  });

  await loadItem();
  await loadComments();
})();