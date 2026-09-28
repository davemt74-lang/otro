// Tracky V2.80 Section 2 — Live Federation Topology & Control Center UI.
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (value = '') => String(value).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const healthLabel = (value) => {
    const state = String(value || 'unknown').toLowerCase();
    return state.charAt(0).toUpperCase() + state.slice(1);
  };
  const badge = (value) => '<span class="federation-health ' + esc(value || 'unknown') + '">' + esc(healthLabel(value)) + '</span>';

  function siteMarkup(site) {
    const authority = site.authority || {};
    const federation = site.federation || {};
    const profiles = (site.profiles || []).map(p => '<span class="tag">' + esc(p) + '</span>').join('');
    return '<article class="panel federation-site-card ' + esc(site.health || 'unknown') + '">' +
      '<div class="federation-site-head"><div><p class="eyebrow">SITE</p><h3>' + esc(site.label || site.id) + '</h3></div>' + badge(site.health) + '</div>' +
      '<div class="federation-site-id">' + esc(site.id || '') + '</div>' +
      '<div class="federation-site-grid">' +
        '<div><span>Federation</span><strong>' + esc(healthLabel(federation.status)) + '</strong></div>' +
        '<div><span>Devices</span><strong>' + Number(site.device_count || 0) + '</strong></div>' +
        '<div><span>Authority epoch</span><strong>' + Number(authority.epoch || 0) + '</strong></div>' +
        '<div><span>Authority</span><strong>' + esc(authority.status || 'unknown') + '</strong></div>' +
      '</div>' +
      '<div class="federation-profiles">' + (profiles || '<span class="muted">No hardware profiles</span>') + '</div>' +
      '<div class="federation-authority">Authority device <code>' + esc(authority.device_id || 'unassigned') + '</code></div>' +
    '</article>';
  }

  function deviceMarkup(device) {
    return '<div class="federation-device-row">' +
      '<div><strong>' + esc(device.label || device.id) + '</strong><span>' + esc(device.hardware_profile_label || device.hardware_profile || 'Custom') + ' · ' + esc(device.site_id || 'Unassigned') + '</span></div>' +
      '<div class="federation-device-meta"><span>' + esc(device.trust_state || 'unknown') + '</span><span>' + esc(device.runtime_status || 'unknown') + '</span>' + (device.version ? '<span>v' + esc(device.version) + '</span>' : '') + '</div>' +
    '</div>';
  }

  function issueMarkup(issue) {
    return '<div class="federation-issue ' + esc(issue.severity || 'unknown') + '">' +
      '<div><strong>' + esc(issue.code || 'issue') + '</strong><span>' + esc(issue.site_id || '') + '</span></div>' +
      '<p>' + esc(issue.message || '') + '</p>' +
    '</div>';
  }

  function render(report) {
    const summary = report.summary || {};
    $('federationHealth').innerHTML = badge(report.health || 'unknown');
    $('federationSiteCount').textContent = Number(summary.site_count || 0);
    $('federationDeviceCount').textContent = Number(summary.device_count || 0);
    $('federationCurrentCount').textContent = Number(summary.current_site_count || 0);
    $('federationIssueCount').textContent = Number((report.issues || []).length);
    $('federationLocalSite').textContent = report.local_site_id || 'Not pinned';
    $('federationTopologyRevision').textContent = String(report.topology_revision || 0);

    $('federationSites').innerHTML = (report.sites || []).length
      ? report.sites.map(siteMarkup).join('')
      : '<div class="panel empty-state">No physical sites are registered yet.</div>';
    $('federationDevices').innerHTML = (report.devices || []).length
      ? report.devices.map(deviceMarkup).join('')
      : '<div class="empty-state">No hardware units are registered yet.</div>';
    $('federationIssues').innerHTML = (report.issues || []).length
      ? report.issues.map(issueMarkup).join('')
      : '<div class="federation-all-clear"><strong>All current federation checks are clear.</strong><span>No partition, stale peer, failed peer, or authority defect is active.</span></div>';

    const profileCounts = summary.profile_counts || {};
    $('federationProfiles').innerHTML = Object.keys(profileCounts).length
      ? Object.entries(profileCounts).map(([name,count]) => '<div><span>' + esc(name) + '</span><strong>' + Number(count || 0) + '</strong></div>').join('')
      : '<div><span>No profiles</span><strong>0</strong></div>';
  }

  async function load() {
    const state = $('federationLoading');
    if (state) state.textContent = 'Refreshing…';
    const response = await fetch('/api/v1/control/federation-operations', {headers:{'Accept':'application/json'}});
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(data.detail || 'Federation operations are unavailable.');
    render(data.operations || {});
    if (state) state.textContent = 'Live local authority view';
  }

  window.loadFederationControlCenter = load;
  document.addEventListener('click', (event) => {
    if (event.target?.id === 'refreshFederation') {
      load().catch(err => {
        const state = $('federationLoading');
        if (state) state.textContent = err.message;
      });
    }
  });
})();
