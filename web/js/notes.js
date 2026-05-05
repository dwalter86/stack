import { loadMeOrRedirect, renderShell, api, escapeHtml } from './common.js';

document.addEventListener('DOMContentLoaded', () => {
  const params = new URLSearchParams(window.location.search);
  const accountId = params.get('account_id');
  const sectionSlug = params.get('section_slug');

  if (!accountId || !sectionSlug) {
    document.getElementById('page-title').textContent = 'Error: Missing Account or Section.';
    return;
  }

  const backLink = document.getElementById('back-link');
  backLink.href = `/section.html?account=${encodeURIComponent(accountId)}&slug=${encodeURIComponent(sectionSlug)}`;

  const pageTitle = document.getElementById('page-title');
  const sectionMeta = document.getElementById('section-meta');
  const notesList = document.getElementById('notes-list');
  const noteForm = document.getElementById('note-form');
  const noteText = document.getElementById('note-text');

  const loadSectionDetails = async () => {
    try {
      const section = await api(`/api/accounts/${accountId}/sections/${encodeURIComponent(sectionSlug)}`);
      pageTitle.textContent = `Notes for "${section.label}"`;
      sectionMeta.textContent = `Section slug: ${section.slug}`;
      document.title = `Notes | ${section.label}`;
    } catch (error) {
      console.error('Error loading section details:', error);
      pageTitle.textContent = 'Could not load section';
      sectionMeta.textContent = `Section slug: ${sectionSlug}`;
    }
  };

  const renderNotes = (notes) => {
    notesList.innerHTML = '';
    if (!notes.length) {
      notesList.innerHTML = '<p>No notes yet. Add the first note for this section.</p>';
      return;
    }

    notes.forEach((note) => {
      const noteEl = document.createElement('div');
      noteEl.className = 'comment-item';
      const createdAt = new Date(note.created_at).toLocaleString();
      noteEl.innerHTML = `
        <p class="comment-body">${escapeHtml(note.note)}</p>
        <div class="comment-meta">
          <span>By: <strong>${escapeHtml(note.user_name || 'Unknown User')}</strong></span>
          <span>On: ${createdAt}</span>
        </div>
      `;
      notesList.appendChild(noteEl);
    });
  };

  const loadNotes = async () => {
    try {
      const notes = await api(`/api/accounts/${accountId}/sections/${encodeURIComponent(sectionSlug)}/notes`);
      renderNotes(notes);
    } catch (error) {
      console.error('Error loading notes:', error);
      notesList.innerHTML = '<p class="small">Could not load notes.</p>';
    }
  };

  const handleFormSubmit = async (event) => {
    event.preventDefault();
    const note = noteText.value.trim();
    if (!note) return;

    try {
      await api(`/api/accounts/${accountId}/sections/${encodeURIComponent(sectionSlug)}/notes`, {
        method: 'POST',
        body: JSON.stringify({ note }),
      });
      noteForm.reset();
      await loadNotes();
    } catch (error) {
      console.error('Error saving note:', error);
      alert('There was an error saving your note.');
    }
  };

  (async () => {
    const me = await loadMeOrRedirect();
    if (!me) return;

    renderShell(me);
    await loadSectionDetails();
    await loadNotes();

    if (me.user_type === 'standard') {
      noteForm.classList.add('hidden');
      const notice = document.createElement('p');
      notice.className = 'small';
      notice.textContent = 'You have read-only access and cannot add notes.';
      noteForm.parentNode.insertBefore(notice, noteForm);
    } else {
      noteForm.addEventListener('submit', handleFormSubmit);
    }
  })();
});
