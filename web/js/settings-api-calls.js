import { loadMeOrRedirect, renderShell, getLabels } from './common.js';

function escapeHtml(str){
  return String(str ?? '')
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;');
}

function formatJson(obj){
  try {
    return JSON.stringify(obj, null, 2);
  } catch {
    return String(obj || '');
  }
}

function singularize(label){
  const lower = String(label || '').toLowerCase();
  return lower.endsWith('s') ? lower.slice(0, -1) : lower;
}

function renderEndpoint(endpoint){
  const body = endpoint.body ? `<div class="small"><strong>Body</strong></div><pre>${escapeHtml(formatJson(endpoint.body))}</pre>` : '';
  const pathParams = endpoint.params?.length ? `<div class="small"><strong>Path params:</strong> ${endpoint.params.join(', ')}</div>` : '';
  const notes = endpoint.notes ? `<div class="small">${endpoint.notes}</div>` : '';
  return `
    <div class="card api-card">
      <div class="api-card-header">
        <span class="tag method-${endpoint.method.toLowerCase()}">${endpoint.method}</span>
        <code>${escapeHtml(endpoint.path)}</code>
      </div>
      <div>${endpoint.summary}</div>
      ${pathParams}
      ${body}
      ${notes}
    </div>
  `;
}

(async () => {
  const me = await loadMeOrRedirect(); if(!me) return;
  renderShell(me);
  if(!me.is_admin){ window.location.replace('/accounts.html'); return; }

  const labels = getLabels(me);
  const apiHeading = document.getElementById('apiHeading');
  const apiIntro = document.getElementById('apiIntro');
  const authCard = document.getElementById('authCard');
  const groupsContainer = document.getElementById('apiGroups');
  const sectionName = singularize(labels.sections_label);
  const itemName = singularize(labels.items_label);

  document.title = `${labels.sections_label}, ${labels.items_label} & comments API calls`;
  if(apiHeading){ apiHeading.textContent = `${labels.sections_label}, ${labels.items_label} & comments API calls`; }
  if(apiIntro){ apiIntro.textContent = `Endpoints for creating, editing, and removing ${labels.sections_label.toLowerCase()}, ${labels.items_label.toLowerCase()}, and item comments.`; }

  if(authCard){
    authCard.innerHTML = `
      <div class="card api-card">
        <div class="api-card-header">
          <span class="tag">Auth</span>
          <strong>Authorization</strong>
        </div>
        <div>All ${labels.sections_label.toLowerCase()}, ${labels.items_label.toLowerCase()}, and comment endpoints require the bearer token from <code>/api/login</code>.</div>
        <div class="small" style="margin-top:6px;">1. Authenticate with <code>/api/login</code> using <code>email</code> and <code>password</code> to get <code>access_token</code>.</div>
        <div class="small">2. Send the token on every request.</div>
        <pre>Authorization: Bearer &lt;access_token&gt;</pre>
        <div class="small">The web app stores <code>access_token</code> in session storage after login and reuses it in the <code>Authorization</code> header for each call.</div>
      </div>
    `;
  }

  const groups = [
    {
      title: `${labels.sections_label} endpoints`,
      endpoints: [
        {
          method: 'POST',
          path: '/api/accounts/{account_id}/sections',
          summary: `Create or update a ${sectionName} by slug within an account.`,
          params: ['account_id'],
          body: { slug: 'news', label: 'News', schema: { fields: [] } },
          notes: '<div class="tag">Upsert</div> Uses the same call to create or overwrite the label and schema for an existing slug.'
        },
        {
          method: 'PUT',
          path: '/api/accounts/{account_id}/sections/{slug}',
          summary: `Edit a ${sectionName} label or schema without changing its slug.`,
          params: ['account_id', 'slug'],
          body: { label: 'Updated label', schema: { fields: [{ key: 'title', label: 'Title' }] } }
        },
        {
          method: 'DELETE',
          path: '/api/accounts/{account_id}/sections/{slug}',
          summary: `Remove a ${sectionName} and delete its ${labels.items_label.toLowerCase()} from the tenant schema.`,
          params: ['account_id', 'slug'],
          notes: 'Deletes any items linked to the slug before removing the section.'
        }
      ]
    },
    {
      title: `${labels.items_label} endpoints`,
      endpoints: [
        {
          method: 'POST',
          path: '/api/accounts/{account_id}/items',
          summary: `Create an ${itemName} in the default section for an account.`,
          params: ['account_id'],
          body: { name: 'Example item', data: { key: 'value' } }
        },
        {
          method: 'POST',
          path: '/api/accounts/{account_id}/sections/{slug}/items',
          summary: `Create an ${itemName} inside a specific ${sectionName}.`,
          params: ['account_id', 'slug'],
          body: { name: 'Section item', data: { key: 'value' } }
        },
        {
          method: 'PUT',
          path: '/api/accounts/{account_id}/items/{item_id}',
          summary: `Edit an existing ${itemName} regardless of its section.`,
          params: ['account_id', 'item_id'],
          body: { name: 'Updated item', data: { key: 'new value' } }
        },
        {
          method: 'DELETE',
          path: '/api/accounts/{account_id}/items/{item_id}',
          summary: `Remove an ${itemName} from any section by its id.`,
          params: ['account_id', 'item_id']
        }
      ]
    },
    {
      title: 'Comment endpoints',
      endpoints: [
        {
          method: 'GET',
          path: '/api/accounts/{account_id}/items/{item_id}/comments',
          summary: `List comments attached to an ${itemName}. Newest comments come first.`,
          params: ['account_id', 'item_id']
        },
        {
          method: 'POST',
          path: '/api/accounts/{account_id}/items/{item_id}/comments',
          summary: `Add a comment to an ${itemName}.`,
          params: ['account_id', 'item_id'],
          body: { body: 'Follow-up question or progress update' },
          notes: 'Comment text is trimmed and cannot be empty.'
        },
        {
          method: 'GET',
          path: '/api/accounts/{account_id}/sections/{slug}/items/{item_id}/comments',
          summary: `List ${itemName} comments within a specific ${sectionName} slug.`,
          params: ['account_id', 'slug', 'item_id'],
          notes: `Returns 404 if the ${itemName} is not part of the slug.`
        },
        {
          method: 'POST',
          path: '/api/accounts/{account_id}/sections/{slug}/items/{item_id}/comments',
          summary: `Add a comment when the ${itemName} is accessed from a ${sectionName}.`,
          params: ['account_id', 'slug', 'item_id'],
          body: { body: 'Follow-up question or progress update' },
          notes: `Fails with 404 if the ${itemName} does not belong to the slug.`
        }
      ]
    }
  ];

  groupsContainer.innerHTML = groups.map(group => `
    <section class="api-group">
      <div class="api-group-header">
        <h2>${group.title}</h2>
      </div>
      <div class="api-call-grid">
        ${group.endpoints.map(renderEndpoint).join('')}
      </div>
    </section>
  `).join('');
})();