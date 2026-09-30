from pathlib import Path

root=Path(__file__).resolve().parents[1]
index=(root/'ui'/'index.html').read_text(encoding='utf-8')
app=(root/'ui'/'app.js').read_text(encoding='utf-8')
overview=(root/'ui'/'tracky-overview.js').read_text(encoding='utf-8')

assert 'data-view="tracky">Tracky<' in index
assert 'id="view-tracky"' in index
for marker in ['trackyRoomCount','trackyPeopleCount','trackyObjectCount','trackyHardwareCount','trackyFederationHealth','trackyAgentView']:
    assert marker in index
assert '/assets/tracky-overview.js' in index
assert "tracky:'Tracky'" in app
assert "name === 'tracky'" in app
assert '/api/v1/control/physical-world-dashboard' in overview
assert '/api/v1/control/federation-operations' in overview
assert 'Promise.allSettled' in overview
assert 'loadPromise' in overview
assert 'Raw camera/audio perception remains local' in overview
print('Tracky navigation and overview contract: PASS')
