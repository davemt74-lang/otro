// Tracky V2.80 Section 3 — Physical World Dashboard & Site Switching.
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  let selectedSiteId = '';

  const confidence = (value) => Math.round(Math.max(0, Math.min(1, Number(value || 0))) * 100) + '%';
  const fmtObserved = (value) => {
    if (!value) return 'No timestamp';
    const n = Number(value);
    if (Number.isFinite(n) && n > 0) {
      const ms = n > 100000000000 ? n : n * 1000;
      const date = new Date(ms);
      if (!Number.isNaN(date.getTime())) return date.toLocaleString();
    }
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
  };
  const stateBadge = (state, current) => '<span class="world-state ' + (current ? 'current' : esc(state || 'unknown')) + '">' + esc(current ? 'Current' : (state || 'unknown')) + '</span>';

  function entityCard(item) {
    const location = item.location || null;
    const locationText = location?.location_label || location?.location_local_id || '';
    const last = item.observed_at ? fmtObserved(item.observed_at) : 'Unknown';
    return '<article class="world-entity-card">' +
      '<div class="world-entity-head"><div><strong>' + esc(item.label || item.local_id || item.type) + '</strong><span>' + esc(item.type || 'entity') + '</span></div>' + stateBadge(item.state, item.current) + '</div>' +
      '<div class="world-entity-meta">' +
        '<span>Confidence <b>' + confidence(item.confidence) + '</b></span>' +
        '<span>Last evidence <b>' + esc(last) + '</b></span>' +
      '</div>' +
      (locationText ? '<div class="world-location"><span>Location</span><strong>' + esc(locationText) + '</strong><small>' + esc(location?.predicate || '') + ' · ' + confidence(location?.confidence) + '</small></div>' : '<div class="world-location unknown"><span>Location</span><strong>Not established</strong><small>No location is invented without evidence.</small></div>') +
    '</article>';
  }

  function hardwareCard(item) {
    return '<article class="world-hardware-card">' +
      '<div><strong>' + esc(item.label || item.id) + '</strong><span>' + esc(item.hardware_profile_label || item.hardware_profile || 'Device') + '</span></div>' +
      '<div class="world-hardware-meta">' +
        (item.is_authority ? '<span class="world-authority">Site authority</span>' : '') +
        '<span>' + esc(item.trust_state || 'unknown') + '</span>' +
        '<span>' + esc(item.runtime_status || 'unknown') + '</span>' +
        (item.version ? '<span>v' + esc(item.version) + '</span>' : '') +
      '</div>' +
    '</article>';
  }

  function fillList(id, items, emptyText) {
    const node = $(id);
    if (!node) return;
    node.innerHTML = items.length ? items.map(entityCard).join('') : '<div class="empty-state">' + esc(emptyText) + '</div>';
  }

  function render(report) {
    const selected = report.selected_site || {};
    selectedSiteId = selected.site_id || '';
    const selector = $('physicalWorldSiteSelect');
    if (selector) {
      selector.innerHTML = (report.site_options || []).map(site =>
        '<option value="' + esc(site.site_id) + '"' + (site.selected ? ' selected' : '') + '>' +
        esc(site.label || site.site_id) + ' · ' + esc(site.health || 'unknown') + '</option>'
      ).join('');
      selector.value = selectedSiteId;
      selector.disabled = !(report.site_options || []).length;
    }
    $('physicalWorldSiteName').textContent = selected.label || selected.site_id || 'No site';
    $('physicalWorldSiteHealth').textContent = selected.health || 'unknown';
    $('physicalWorldFederationState').textContent = selected.federation_status || 'unknown';
    $('physicalWorldRevision').textContent = String(selected.world_revision || 0);

    const counts = report.counts || {};
    $('physicalWorldRoomCount').textContent = Number(counts.rooms || 0);
    $('physicalWorldPeopleCount').textContent = Number(counts.people || 0);
    $('physicalWorldObjectCount').textContent = Number(counts.objects || 0);
    $('physicalWorldDeviceCount').textContent = Number(counts.hardware_units || 0);

    const context = report.agent_context || {};
    $('physicalWorldAgentView').textContent = context.view_site_id || 'None';
    $('physicalWorldAgentPhysical').textContent = context.physical_current_site_id || 'Unknown';
    $('physicalWorldAgentBasis').textContent = String(context.view_basis || 'none').replaceAll('_', ' ');
    const contextNote = $('physicalWorldAgentNote');
    if (contextNote) {
      contextNote.textContent = context.view_site_id && context.physical_current_site_id && context.view_site_id !== context.physical_current_site_id
        ? 'Agent view is following the selected site; physical current-site evidence remains unchanged.'
        : 'Agent view and current physical site are aligned.';
    }

    fillList('physicalWorldRooms', report.rooms || [], 'No rooms are visible in this governed site projection.');
    fillList('physicalWorldPeople', report.people || [], 'No people are visible at this site under current policy and consent.');
    fillList('physicalWorldObjects', report.objects || [], 'No objects are visible in this site projection.');
    fillList('physicalWorldWorldDevices', report.world_devices || [], 'No world-model devices are visible in this site projection.');
    $('physicalWorldHardware').innerHTML = (report.hardware_units || []).length
      ? report.hardware_units.map(hardwareCard).join('')
      : '<div class="empty-state">No registered hardware units at this site.</div>';

    const freshness = report.federation_freshness || selected.federation_freshness || null;
    const syncWarning = $('physicalWorldSyncWarning');
    if (syncWarning) {
      if (freshness) {
        syncWarning.hidden = false;
        syncWarning.className = 'world-sync-warning ' + esc(freshness.status || 'unknown');
        syncWarning.textContent = freshness.fresh
          ? 'Federation freshness: current.'
          : 'Federation freshness: ' + String(freshness.status || 'unknown').replaceAll('_',' ') + '. ' + (freshness.message || 'Remote data is not verified current.');
      } else {
        syncWarning.hidden = true;
      }
    }

    const warning = $('physicalWorldSelectionWarning');
    const requestedMissing = (report.issues || []).some(row => row.code === 'requested_site_not_available');
    if (warning) {
      warning.hidden = !requestedMissing;
      warning.textContent = requestedMissing ? 'The requested site is not currently available. The dashboard fell back to an authorized site.' : '';
    }
  }

  async function load(siteId = selectedSiteId) {
    const status = $('physicalWorldLoading');
    if (status) status.textContent = 'Refreshing…';
    const query = siteId ? '?site=' + encodeURIComponent(siteId) : '';
    const response = await fetch('/api/v1/control/physical-world-dashboard' + query, {headers:{'Accept':'application/json'}});
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(data.detail || 'Physical World dashboard is unavailable.');
    render(data.dashboard || {});
    if (typeof window.loadCrossSitePresence === 'function') {
      try { await window.loadCrossSitePresence(); } catch (_) {}
    }
    if (status) status.textContent = 'Governed live semantic view';
  }

  window.loadPhysicalWorldDashboard = () => load(selectedSiteId);
  document.addEventListener('change', (event) => {
    if (event.target?.id === 'physicalWorldSiteSelect') {
      selectedSiteId = event.target.value || '';
      load(selectedSiteId).catch(err => {
        const status = $('physicalWorldLoading');
        if (status) status.textContent = err.message;
      });
    }
  });
  document.addEventListener('click', (event) => {
    if (event.target?.id === 'refreshPhysicalWorld') {
      load(selectedSiteId).catch(err => {
        const status = $('physicalWorldLoading');
        if (status) status.textContent = err.message;
      });
    }
  });
})();
