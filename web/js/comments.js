import { loadMeOrRedirect, renderShell, api } from './common.js';
document.addEventListener('DOMContentLoaded', () => {
    const params = new URLSearchParams(window.location.search);
    const accountId = params.get('account_id');
    const itemId = params.get('item_id');
    const sectionSlug = params.get('section_slug') || 'default';

    if (!accountId || !itemId) {
        document.getElementById('page-title').textContent = 'Error: Missing Account or Item ID.';
        return;
    }

    const backLink = document.getElementById('back-link');
    backLink.href = `/section.html?account=${accountId}&slug=${sectionSlug}`;

    const pageTitle = document.getElementById('page-title');
    const itemMeta = document.getElementById('item-meta');
    const commentsList = document.getElementById('comments-list');
    const commentForm = document.getElementById('comment-form');

    const loadItemDetails = async (me) => {
        try {
            const item = await api(`/api/accounts/${accountId}/items/${itemId}`);
            pageTitle.textContent = `Comments for "${item.name}"`;
            itemMeta.textContent = `Item ID: ${item.id}`;
        } catch (error) {
            console.error('Error loading item details:', error);
            pageTitle.textContent = 'Could not load item';
        }
    };

    const renderComments = (comments) => {
        commentsList.innerHTML = ''; // Clear existing comments
        if (comments.length === 0) {
            commentsList.innerHTML = '<p>No comments yet. Be the first to comment!</p>';
            return;
        }

        comments.forEach(comment => {
            const commentEl = document.createElement('div');
            commentEl.className = 'comment-item';
            const createdAt = new Date(comment.created_at).toLocaleString();
            commentEl.innerHTML = `
                <p class="comment-body">${comment.comment}</p>
                <div class="comment-meta">
                    <span>By: <strong>${comment.user_name || 'Unknown User'}</strong></span>
                    <span>On: ${createdAt}</span>
                </div>
            `;
            commentsList.appendChild(commentEl);
        });
    };

    const loadComments = async () => {
        try {
            const comments = await api(`/api/accounts/${accountId}/items/${itemId}/comments`);
            renderComments(comments);
        } catch (error) {
            console.error('Error loading comments:', error);
            commentsList.innerHTML = '<p class="small">Could not load comments.</p>';
        }
    };

    const handleFormSubmit = async (event) => {
        event.preventDefault();
        const commentText = document.getElementById('comment-text').value.trim();
        if (!commentText) return;

        try {
            await api(`/api/accounts/${accountId}/items/${itemId}/comments`, {
                method: 'POST',
                body: JSON.stringify({ comment: commentText })
            });
            commentForm.reset();
            loadComments(); // Refresh comments list
        } catch (error) {
            console.error('Error posting comment:', error);
            alert('There was an error posting your comment.');
        }
    };

    (async () => {
        const me = await loadMeOrRedirect(); if (!me) return;
        renderShell(me);
        await loadItemDetails(me);
        await loadComments();
        commentForm.addEventListener('submit', handleFormSubmit);
    })();
});
