export function getToken() { return sessionStorage.getItem('token') || ''; }
export function setToken(t) { sessionStorage.setItem('token', t); }
export function logout() { sessionStorage.removeItem('token'); window.location.replace('/'); }

export function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

export const DEFAULT_LABELS = {
  accounts_label: 'Home',
  sections_label: 'Sections',
  items_label: 'Items',
};

export const DEFAULT_PREFERENCES = {
  ...DEFAULT_LABELS,
  show_slugs: false,
};

export function getPreferences(user) {
  const prefs = user?.preferences || {};
  return {
    accounts_label: (prefs.accounts_label || DEFAULT_LABELS.accounts_label).trim() || DEFAULT_LABELS.accounts_label,
    sections_label: (prefs.sections_label || DEFAULT_LABELS.sections_label).trim() || DEFAULT_LABELS.sections_label,
    items_label: (prefs.items_label || DEFAULT_LABELS.items_label).trim() || DEFAULT_LABELS.items_label,
    show_slugs: !!prefs.show_slugs,
  };
}

export function getLabels(user) {
  const prefs = getPreferences(user);
  return {
    accounts_label: prefs.accounts_label,
    sections_label: prefs.sections_label,
    items_label: prefs.items_label,
  };
}

export async function api(path, opts = {}) {
  const headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
  const token = getToken();
  if (token) headers.Authorization = 'Bearer ' + token;
  const res = await fetch(path, Object.assign({}, opts, { headers }));
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(text || ('HTTP ' + res.status));
  }
  if (res.status === 204) return null;
  const ct = res.headers.get('content-type') || '';
  return ct.includes('application/json') ? res.json() : res.text();
}

export async function loadMeOrRedirect() {
  const token = getToken();
  if (!token) { window.location.replace('/'); return null; }
  try { return await api('/api/me'); }
  catch (e) { logout(); return null; }
}

export function renderShell(user) {
  const labels = getLabels(user);
  const header = document.getElementById('site-header');
  const footer = document.getElementById('site-footer');
  if (header) {
    header.innerHTML = `
      <div class="container header-bar">
        <a href="/accounts.html" class="logo-link">
          <img src="/logo.png" alt="ILG Forms" class="logo-img">
          <span class="logo-text">ILG Forms</span>
        </a>
        <div class="menu" style="margin-left:auto;">
          <span class="pill small">${escapeHtml(user?.email) || ''}</span>
          ${user?.is_admin ? '<a href="/settings.html" class="btn">Settings</a>' : ''}
          <a href="#" id="logoutBtn" class="btn">Logout</a>
        </div>
      </div>`;
    const btn = document.getElementById('logoutBtn'); if (btn) btn.addEventListener('click', (e) => { e.preventDefault(); logout(); });
  }
  if (footer) {
    const year = new Date().getFullYear();
    footer.innerHTML = `<div class="container small">&copy; ${year} ADIGI One Platform</div>`;
  }
}
