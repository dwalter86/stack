import { setToken, renderShell } from './common.js';
renderShell(null);
const form = document.getElementById('loginForm');
const msg = document.getElementById('msg');
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const email = document.getElementById('email').value.trim();
  const password = document.getElementById('password').value;
  try {
    const res = await fetch('/api/login', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({email, password})});
    if(!res.ok) throw new Error('Login failed');
    const data = await res.json();
    if(!data?.access_token) throw new Error('No token');
    setToken(data.access_token);
    window.location.replace('/accounts.html');
  } catch(err){ msg.textContent = err.message || 'Login failed'; }
});
