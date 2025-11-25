import { getToken, loadMeOrRedirect, renderShell } from './common.js';

(async () => {
  const me = await loadMeOrRedirect(); if(!me) return;
  if(me.user_type !== 'super_admin') { window.location.replace('/settings.html'); return; }
  renderShell(me);

  const apiKeyEl = document.getElementById('apiKey');
  const token = getToken();
  apiKeyEl.textContent = token || 'No API token found. Please log in again to obtain a token.';

  SwaggerUIBundle({
    url: '/api/openapi.json',
    dom_id: '#swagger-ui',
    presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
    layout: 'BaseLayout',
    requestInterceptor: (req) => {
      const sessionToken = getToken();
      if(sessionToken){
        req.headers['Authorization'] = 'Bearer ' + sessionToken;
      }
      return req;
    }
  });
})();