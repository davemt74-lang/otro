"""Build core-only updates and separately versioned runtime payloads."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.component_packages import digest, inventory, validate

MARGIN = 64 * 1024 * 1024


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def prepare(root: Path, installer: Path, version='2.4') -> dict:
    catalog = inventory(root, version)
    full = json.loads((root / 'INSTALL_SPACE.json').read_text(encoding='utf-8-sig'))
    core = catalog['components']['core']['files']['HomeServer.exe']
    actual = sum(f['bytes'] for c in catalog['components'].values() for f in c['files'].values())
    if (full.get('contract') != 'vp3.installer.space.v1' or full.get('application_sha256') != core['sha256'] or
        full.get('payload_bytes') != actual or type(full.get('runtime_extraction_bytes')) is not int or full['runtime_extraction_bytes'] <= 0):
        raise ValueError('Installer measurement does not match staged components')
    size = core['bytes']
    budget = {**full, 'package_kind': 'core_update', 'payload_bytes': size, 'largest_file_bytes': size,
              'install_budget_bytes': 2 * size + MARGIN,
              'temporary_budget_bytes': full['runtime_extraction_bytes'] + size + MARGIN,
              'file_count': 1, 'components': {'browser': 0, 'ffmpeg': 0}}
    write_json(root / 'COMPONENTS.json', catalog)
    write_json(root / 'CORE_INSTALL_SPACE.json', budget)
    installer.mkdir(parents=True, exist_ok=True)
    fields = {'PayloadBytes': 'payload_bytes', 'InstallBudgetBytes': 'install_budget_bytes',
              'TemporaryBudgetBytes': 'temporary_budget_bytes', 'DataHeadroomBytes': 'data_headroom_bytes',
              'RuntimeExtractionBytes': 'runtime_extraction_bytes'}
    (installer / 'CoreSpaceBudget.iss').write_text('// Generated from this build.\n' + ''.join(
        f'#define {key} "{budget[value]}"\n' for key, value in fields.items()), encoding='utf-8')
    lines = ["function CoreRuntimeProblem(Target: String): String;", 'begin',
             "  Result := 'This core update needs an existing HomeServer installation with matching browser and media runtimes. Use the complete HomeServer installer.';",
             "  if not FileExists(AddBackslash(Target) + 'HomeServer.exe') then Exit;", '  try']
    for key in ('browser', 'media'):
        for name, record in catalog['components'][key]['files'].items():
            name = name.replace('/', '\\').replace("'", "''")
            lines += [f"    if not FileExists(AddBackslash(Target) + '{name}') then Exit;",
                      f"    if Lowercase(GetSHA256OfFile(AddBackslash(Target) + '{name}')) <> '{record['sha256']}' then Exit;"]
    lines += ['  except', '    Exit;', '  end;', "  Result := '';", 'end;', '']
    (installer / 'CoreRuntimeRequirements.iss').write_text('\n'.join(lines), encoding='utf-8')
    return catalog


def assemble(root: Path, output: Path) -> None:
    catalog = validate(json.loads((root / 'COMPONENTS.json').read_text(encoding='utf-8-sig')))
    release = json.loads((root / 'RELEASE.json').read_text(encoding='utf-8-sig'))
    for item in catalog['components'].values():
        for name, expected in item['files'].items():
            path = root / name
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()) or path.stat().st_size != expected['bytes'] or digest(path) != expected['sha256']:
                raise ValueError('Certified staged component changed: ' + name)
    for name in ('HomeServer.exe', 'HomeServerCoreSetup.exe', 'COMPONENTS.json', 'CORE_INSTALL_SPACE.json'):
        if digest(root / name) != release['files'][name]:
            raise ValueError('Release identity mismatch: ' + name)
    if release['version'] != catalog['components']['core']['version']:
        raise ValueError('Release component version mismatch')
    output.mkdir(parents=True, exist_ok=False)
    target = output / 'core-update'; target.mkdir()
    for source, dest in [('HomeServerCoreSetup.exe', 'HomeServerSetup.exe'), ('COMPONENTS.json', 'COMPONENTS.json'), ('CORE_INSTALL_SPACE.json', 'INSTALL_SPACE.json')]:
        shutil.copy2(root / source, target / dest)
    files = {name: digest(target / name) for name in ('HomeServerSetup.exe', 'COMPONENTS.json', 'INSTALL_SPACE.json')}
    files['HomeServer.exe'] = release['files']['HomeServer.exe']
    write_json(target / 'RELEASE.json', {**release, 'package_kind': 'core_update', 'files': files})
    (target / 'SHA256SUMS.txt').write_text(''.join(f'{sha}  {name}\n' for name, sha in files.items()), encoding='ascii')
    for key in ('browser', 'media'):
        target = output / key; target.mkdir()
        write_json(target / 'COMPONENT.json', {'contract': 'vp3.homeserver.component-package.v1', 'component': key, **catalog['components'][key]})
        for name in catalog['components'][key]['files']:
            dest = target / 'payload' / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / name, dest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, default=Path('dist'))
    parser.add_argument('--installer', type=Path, default=Path('installer'))
    parser.add_argument('--version', default='2.4')
    parser.add_argument('--assemble', type=Path)
    args = parser.parse_args()
    if args.assemble:
        assemble(args.dist, args.assemble)
    else:
        prepare(args.dist, args.installer, args.version)
