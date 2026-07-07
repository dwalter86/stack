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
