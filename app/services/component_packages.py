"""Content identities and dependency checks shared by packaging and the updater."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re

CONTRACT = 'vp3.homeserver.components.v1'
ROOTS = {'core': 'HomeServer.exe', 'browser': 'tools/browser/', 'media': 'tools/ffmpeg/'}
SHA = re.compile(r'^[a-f0-9]{64}$')


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_path(value: str) -> str:
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError('Invalid component path')
    for part in value.split('/'):
        if (not part or part in ('.', '..') or part[-1] in '. ' or
            any(ord(c) < 32 or c in ':<>"|?*' for c in part) or
            re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])', part.split('.')[0])):
            raise ValueError('Unsafe component path')
    return value


def validate(catalog: dict) -> dict:
    if not isinstance(catalog, dict) or catalog.get('contract') != CONTRACT or catalog.get('platform') != 'windows-x64':
        raise ValueError('Unsupported component catalog')
    components = catalog.get('components')
    if not isinstance(components, dict) or set(components) != set(ROOTS):
        raise ValueError('Incomplete component catalog')
    seen = set()
    for key, prefix in ROOTS.items():
        item = components[key]
        if not isinstance(item, dict) or not isinstance(item.get('version'), str) or not 0 < len(item['version']) <= 80:
            raise ValueError('Invalid component version')
        files = item.get('files')
        if not isinstance(files, dict) or not 1 <= len(files) <= 5000:
            raise ValueError('Invalid component inventory')
        for name, record in files.items():
            safe_path(name)
            if not (name == prefix if key == 'core' else name.startswith(prefix)) or name.casefold() in seen:
                raise ValueError('Invalid or duplicate component file')
            seen.add(name.casefold())
            if (not isinstance(record, dict) or set(record) != {'bytes', 'sha256'} or
                type(record['bytes']) is not int or not 0 <= record['bytes'] <= 2**32 or
                not isinstance(record['sha256'], str) or not SHA.fullmatch(record['sha256'])):
                raise ValueError('Invalid component file identity')
        if item.get('content_sha256') != hashlib.sha256(canonical(files)).hexdigest():
            raise ValueError('Component fingerprint mismatch')
    for key, required in {'browser': ['manifest.json'], 'media': ['manifest.json', 'ffmpeg.exe', 'ffprobe.exe']}.items():
        if any(ROOTS[key] + name not in components[key]['files'] for name in required):
            raise ValueError('Missing required runtime file')
    if components['core'].get('requires') != {key: components[key]['content_sha256'] for key in ('browser', 'media')}:
        raise ValueError('Core runtime dependency mismatch')
    return catalog


def inventory(root: Path, version: str) -> dict:
    root = root.resolve()
    components = {}
    for key, prefix in ROOTS.items():
        location = root / prefix
        if not location.exists() or location.is_symlink():
            raise ValueError('Missing or symbolic staged component')
        paths = [location] if key == 'core' else sorted(location.rglob('*'))
        files = {}
        for path in paths:
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError('Symbolic or external component file')
            if path.is_file():
                files[path.relative_to(root).as_posix()] = {'bytes': path.stat().st_size, 'sha256': digest(path)}
        item_version = version
        if key != 'core':
            manifest = json.loads((location / 'manifest.json').read_text(encoding='utf-8-sig'))
            item_version = manifest.get('playwright' if key == 'browser' else 'version')
        components[key] = {'version': item_version, 'files': files, 'content_sha256': hashlib.sha256(canonical(files)).hexdigest()}
    components['core']['requires'] = {key: components[key]['content_sha256'] for key in ('browser', 'media')}
    return validate({'contract': CONTRACT, 'platform': 'windows-x64', 'components': components})


def verify_runtimes(root: Path, catalog: dict) -> None:
    validate(catalog)
    root = root.resolve()
    for key in ('browser', 'media'):
        for name, expected in catalog['components'][key]['files'].items():
            path = root / name
            if (any(p.is_symlink() for p in [path, *path.parents] if p != root and p.is_relative_to(root)) or
                not path.is_file() or not path.resolve().is_relative_to(root) or
                path.stat().st_size != expected['bytes'] or digest(path) != expected['sha256']):
                raise ValueError('Installed browser or media runtime is missing or changed. Use the complete HomeServer installer.')
