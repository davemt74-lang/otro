(() => {
  'use strict';

  const $ = id => document.getElementById(id);
  let loadPromise = null;

  async function getJson(path) {
    const response = await fetch(path, {cache:'no-store', credentials:'same-origin', headers:{Accept:'application/json'}});
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
    return payload;
  }

  function render(world, federation) {
    const dashboard = world?.dashboard || {};
    const selected = dashboard.selected_site || {};
    const counts = dashboard.counts || {};
    const context = dashboard.agent_context || {};
    const operations = federation?.operations || {};
    const summary = operations.summary || {};

    if ($('trackyRoomCount')) $('trackyRoomCount').textContent = Number(counts.rooms || 0);
    if ($('trackyPeopleCount')) $('trackyPeopleCount').textContent = Number(counts.people || 0);
    if ($('trackyObjectCount')) $('trackyObjectCount').textContent = Number(counts.objects || 0);
    if ($('trackyHardwareCount')) $('trackyHardwareCount').textContent = Number(counts.hardware_units || summary.device_count || 0);
    if ($('trackySiteName')) $('trackySiteName').textContent = selected.label || selected.site_id || operations.local_site_id || 'No site';
    if ($('trackyWorldRevision')) $('trackyWorldRevision').textContent = String(selected.world_revision || 0);
    if ($('trackyFederationHealth')) $('trackyFederationHealth').textContent = operations.health || selected.federation_status || 'unknown';
    if ($('trackyAgentView')) $('trackyAgentView').textContent = context.view_site_id || selected.site_id || 'None';

    const issues = Array.isArray(operations.issues) ? operations.issues : [];
    const state = $('trackyOverviewState');
    if (state) state.textContent = issues.length ? `${issues.length} issue${issues.length === 1 ? '' : 's'} need attention` : 'Tracky ready';

    const message = $('trackyOverviewMessage');
    if (message) {
      const freshness = dashboard.federation_freshness || selected.federation_freshness || null;
      if (!selected.site_id && !operations.local_site_id) {
        message.textContent = 'No Tracky site is registered yet. Connect perception hardware or synchronize a Tracky site to begin.';
      } else if (freshness && !freshness.fresh) {
        message.textContent = freshness.message || 'Tracky data is available, but federation reconciliation says the remote projection is not yet current.';
      } else if (issues.length) {
        message.textContent = issues[0]?.message || 'Tracky is online with a federation issue that needs review.';
      } else {
        message.textContent = 'Tracky is exposing the current governed physical-world projection to Agent Brain. Raw camera/audio perception remains local.';
      }
    }
  }

  async function load() {
    if (loadPromise) return loadPromise;
    loadPromise = (async () => {
      const state = $('trackyOverviewState');
      if (state) state.textContent = 'Refreshing…';
      const [worldResult, federationResult] = await Promise.allSettled([
        getJson('/api/v1/control/physical-world-dashboard'),
        getJson('/api/v1/control/federation-operations'),
      ]);
      const world = worldResult.status === 'fulfilled' ? worldResult.value : {};
      const federation = federationResult.status === 'fulfilled' ? federationResult.value : {};
      if (worldResult.status === 'rejected' && federationResult.status === 'rejected') {
        throw worldResult.reason || federationResult.reason || new Error('Tracky overview is unavailable.');
      }
      render(world, federation);
    })();
    try { return await loadPromise; }
    finally { loadPromise = null; }
  }

  window.loadTrackyOverview = load;
  document.addEventListener('click', event => {
    if (event.target?.id === 'refreshTrackyOverview') load().catch(error => {
      const state = $('trackyOverviewState');
      if (state) state.textContent = error.message;
    });
  });
})();