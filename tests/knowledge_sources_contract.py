from __future__ import annotations

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
index = (ROOT_DIR / "ui" / "index.html").read_text(encoding="utf-8")
module = (ROOT_DIR / "ui" / "knowledge-sources.js").read_text(encoding="utf-8")

assert '<script src="/assets/knowledge-sources.js" defer></script>' in index
assert index.count('/assets/knowledge-sources.js') == 1
assert 'watch local folders' in index.lower()

for marker in (
    'knowledgeSourcesPanel',
    'showKnowledgeSourceForm',
    'knowledgeSourcePath',
    'knowledgeSourceInterval',
    'data-source-files',
    'data-source-scan',
    'data-source-toggle',
    'data-source-delete',
    '/api/v1/control/knowledge/sources',
    'Original local files were left untouched',
):
    assert marker in module

assert "window.loadHomeServerKnowledgeSources = loadSources" in module
assert "if (typeof window.loadKnowledge === 'function')" in module
assert "method:'DELETE'" in module
assert "method:'PATCH'" in module
assert "method:'POST'" in module

print("HomeServer Knowledge Sources UI integration contract passed")
