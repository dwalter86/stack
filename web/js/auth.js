import { setToken, renderShell } from './common.js';
import { notifyError, queueToast } from './notify.js';
renderShell(null);
const form = document.getElementById('loginForm');
const emailInput = document.getElementById('email');
const passwordInput = document.getElementById('password');

function setInputError(on) {
  emailInput.classList.toggle('input-error', on);
  passwordInput.classList.toggle('input-error', on);
}
emailInput.addEventListener('input', () => setInputError(false));
passwordInput.addEventListener('input', () => setInputError(false));

// --- Password reveal with colour-coded characters ---
// The overlay mirrors the input's text with per-character colours
// (letters black, digits red, symbols blue). It is only shown while
// the password is revealed; masked dots stay uniform so the password's
// structure isn't visible to onlookers.
const passwordToggle = document.getElementById('passwordToggle');
const passwordOverlay = document.getElementById('passwordOverlay');

function charClass(ch) {
  if (/\p{L}/u.test(ch)) return 'char-letter';
  if (/\p{N}/u.test(ch)) return 'char-digit';
  return 'char-symbol';
}

function renderOverlay() {
  passwordOverlay.textContent = '';
  for (const ch of passwordInput.value) {
    const span = document.createElement('span');
    span.className = charClass(ch);
    span.textContent = ch;
    passwordOverlay.appendChild(span);
  }
  syncOverlayScroll();
}

function syncOverlayScroll() {
  passwordOverlay.scrollLeft = passwordInput.scrollLeft;
}

if (passwordToggle && passwordOverlay) {
  // Match the input's exact text metrics so overlay characters align.
  const cs = getComputedStyle(passwordInput);
  passwordOverlay.style.font = cs.font;
  passwordOverlay.style.letterSpacing = cs.letterSpacing;
  passwordOverlay.style.paddingLeft = cs.paddingLeft;

  passwordToggle.addEventListener('click', () => {
    const reveal = passwordInput.type === 'password';
    passwordInput.type = reveal ? 'text' : 'password';
    passwordInput.classList.toggle('password-revealed', reveal);
    passwordToggle.setAttribute('aria-pressed', String(reveal));
    passwordToggle.setAttribute('aria-label', reveal ? 'Hide password' : 'Show password');
    passwordToggle.querySelector('.eye-open').classList.toggle('hidden', reveal);
    passwordToggle.querySelector('.eye-closed').classList.toggle('hidden', !reveal);
    if (reveal) renderOverlay();
    passwordInput.focus();
  });

  passwordInput.addEventListener('input', () => {
    if (passwordInput.classList.contains('password-revealed')) renderOverlay();
  });
  passwordInput.addEventListener('scroll', syncOverlayScroll);
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const email = emailInput.value.trim();
  const password = passwordInput.value;
  try {
    const res = await fetch('/api/login', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({email, password})});
    if (res.status === 401) {
      setInputError(true);
      throw new Error('Incorrect email or password.');
    }
    if (!res.ok) throw new Error('Login failed. Please try again.');
    const data = await res.json();
    if(!data?.access_token) throw new Error('Login failed. Please try again.');
    setToken(data.access_token);
    queueToast('Login successful.', { type: 'success' });
    window.location.replace('/accounts.html');
  } catch(err){ notifyError(err.message || 'Login failed. Please try again.'); }
});
