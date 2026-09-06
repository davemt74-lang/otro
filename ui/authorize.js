const statusNode = document.getElementById('status');
const params = new URLSearchParams(location.hash.slice(1));
const bootstrapToken = params.get('owner') || '';
const nextParam = new URLSearchParams(location.search).get('next') || '/';
const nextPath = nextParam.startsWith('/') && !nextParam.startsWith('//') ? nextParam : '/';

history.replaceState(null, '', location.pathname + location.search);

async function authorize() {
  if (!bootstrapToken) {
    statusNode.textContent = 'Authorization information is missing. Open HomeServer from the tray menu.';
    return;
  }

  try {
    const response = await fetch('/__owner/session', {
      method: 'POST',
      headers: {'X-HomeServer-Owner': bootstrapToken},
      credentials: 'same-origin',
      cache: 'no-store',
    });
    if (!response.ok) throw new Error('Authorization failed');
    location.replace(nextPath);
  } catch (_) {
    statusNode.textContent = 'Could not authorize this control session. Reopen HomeServer from the tray menu.';
  }
}

authorize();
