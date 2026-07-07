import { escapeHtml } from './common.js';

const TOAST_DURATION_MS = 4000;
const TOAST_ERROR_DURATION_MS = 7000;

function toastContainer() {
  let el = document.getElementById('toast-container');
  if (!el) {
    el = document.createElement('div');
    el.id = 'toast-container';
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    document.body.appendChild(el);
  }
  return el;
}

export function toast(message, opts = {}) {
  const type = opts.type || 'info';
  const duration = opts.duration || (type === 'error' ? TOAST_ERROR_DURATION_MS : TOAST_DURATION_MS);
  const container = toastContainer();

  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.innerHTML = `
    <span class="toast-message">${escapeHtml(message)}</span>
    <button type="button" class="toast-close" aria-label="Dismiss">&times;</button>`;

  let hideTimer = null;
  const dismiss = () => {
    if (hideTimer) clearTimeout(hideTimer);
    el.classList.add('toast-hide');
    el.addEventListener('transitionend', () => el.remove(), { once: true });
    setTimeout(() => el.remove(), 400);
  };
  el.querySelector('.toast-close').addEventListener('click', dismiss);
  hideTimer = setTimeout(dismiss, duration);

  container.appendChild(el);
  requestAnimationFrame(() => el.classList.add('toast-show'));
  return dismiss;
}

const QUEUED_TOASTS_KEY = 'queuedToasts';

// Store a toast to be shown after the next page load (survives redirects).
export function queueToast(message, opts = {}) {
  let queued = [];
  try { queued = JSON.parse(sessionStorage.getItem(QUEUED_TOASTS_KEY)) || []; } catch { /* ignore */ }
  queued.push({ message, opts });
  sessionStorage.setItem(QUEUED_TOASTS_KEY, JSON.stringify(queued));
}

function showQueuedToasts() {
  let queued = [];
  try { queued = JSON.parse(sessionStorage.getItem(QUEUED_TOASTS_KEY)) || []; } catch { /* ignore */ }
  sessionStorage.removeItem(QUEUED_TOASTS_KEY);
  queued.forEach(({ message, opts }) => toast(message, opts || {}));
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', showQueuedToasts, { once: true });
} else {
  showQueuedToasts();
}

export const notifySuccess = (message, opts = {}) => toast(message, { ...opts, type: 'success' });
export const notifyError = (message, opts = {}) => toast(message, { ...opts, type: 'error' });
export const notifyInfo = (message, opts = {}) => toast(message, { ...opts, type: 'info' });
export const notifyWarning = (message, opts = {}) => toast(message, { ...opts, type: 'warning' });

export function confirmDialog(message, opts = {}) {
  const title = opts.title || 'Please confirm';
  const confirmLabel = opts.confirmLabel || 'Confirm';
  const cancelLabel = opts.cancelLabel || 'Cancel';
  const danger = opts.danger !== false;

  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'confirm-overlay';
    overlay.innerHTML = `
      <div class="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
        <h3 id="confirm-title">${escapeHtml(title)}</h3>
        <p>${escapeHtml(message)}</p>
        <div class="confirm-actions">
          <button type="button" class="btn confirm-cancel">${escapeHtml(cancelLabel)}</button>
          <button type="button" class="btn ${danger ? 'confirm-danger' : 'confirm-ok'}">${escapeHtml(confirmLabel)}</button>
        </div>
      </div>`;

    const previouslyFocused = document.activeElement;
    const close = (result) => {
      overlay.remove();
      document.removeEventListener('keydown', onKey);
      if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus();
      resolve(result);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') close(false);
    };

    overlay.querySelector('.confirm-cancel').addEventListener('click', () => close(false));
    overlay.querySelector(danger ? '.confirm-danger' : '.confirm-ok').addEventListener('click', () => close(true));
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(false); });
    document.addEventListener('keydown', onKey);

    document.body.appendChild(overlay);
    overlay.querySelector('.confirm-cancel').focus();
  });
}

export function promptDialog(message, opts = {}) {
  const title = opts.title || message;
  const confirmLabel = opts.confirmLabel || 'Save';
  const cancelLabel = opts.cancelLabel || 'Cancel';
  const initialValue = opts.value || '';
  const placeholder = opts.placeholder || '';

  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'confirm-overlay';
    overlay.innerHTML = `
      <div class="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="prompt-title">
        <h3 id="prompt-title">${escapeHtml(title)}</h3>
        <input type="text" class="prompt-input" value="${escapeHtml(initialValue)}" placeholder="${escapeHtml(placeholder)}">
        <div class="confirm-actions">
          <button type="button" class="btn confirm-cancel">${escapeHtml(cancelLabel)}</button>
          <button type="button" class="btn confirm-ok">${escapeHtml(confirmLabel)}</button>
        </div>
      </div>`;

    const input = overlay.querySelector('.prompt-input');
    const previouslyFocused = document.activeElement;
    const close = (result) => {
      overlay.remove();
      if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus();
      resolve(result);
    };

    overlay.querySelector('.confirm-cancel').addEventListener('click', () => close(null));
    overlay.querySelector('.confirm-ok').addEventListener('click', () => close(input.value));
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(null); });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') close(input.value);
      if (e.key === 'Escape') close(null);
    });

    document.body.appendChild(overlay);
    input.focus();
    input.select();
  });
}
