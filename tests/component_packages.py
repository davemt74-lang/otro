"""Content reuse, update compatibility, tampering and package boundaries."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services import component_packages as components


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


builder = module('component_builder', ROOT / 'scripts/build_component_packages.py')
budget = module('installer_budget', ROOT / 'scripts/installer_space_budget.py')
fixture = module('space_fixture', ROOT / 'tests/installer_space_budget.py').fixture


class ComponentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'dist'; fixture(self.root)
        budget.write(self.root, Path(self.tmp.name) / 'SpaceBudget.iss', self.root / 'INSTALL_SPACE.json')
        self.catalog = builder.prepare(self.root, Path(self.tmp.name) / 'installer')

    def release(self):
        (self.root / 'HomeServerCoreSetup.exe').write_bytes(b'MZ-core-fixture')
        release = {'format': 'vp3-os-release-v1', 'version': '2.4', 'channel': 'stable', 'minimum_schema_version': 27,
                   'files': {n: components.digest(self.root / n) for n in ('HomeServer.exe', 'HomeServerCoreSetup.exe', 'COMPONENTS.json', 'CORE_INSTALL_SPACE.json')}}
        builder.write_json(self.root / 'RELEASE.json', release)
        out = Path(self.tmp.name) / 'packages'; builder.assemble(self.root, out)
        return out

    def archive(self, directory):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            for p in directory.iterdir(): archive.writestr(p.name, p.read_bytes())
        return stream.getvalue()

    def test_only_core_changes_when_application_changes(self):
        (self.root / 'HomeServer.exe').write_bytes(b'new exe')
        changed = components.inventory(self.root, '2.4')
        for key in ('browser', 'media'):
            self.assertEqual(self.catalog['components'][key], changed['components'][key])
        self.assertNotEqual(self.catalog['components']['core']['content_sha256'], changed['components']['core']['content_sha256'])
        full = json.loads((self.root / 'INSTALL_SPACE.json').read_text())
        core = json.loads((self.root / 'CORE_INSTALL_SPACE.json').read_text())
        self.assertLess(core['payload_bytes'], full['payload_bytes'])

    def test_package_boundaries_and_runtime_identity(self):
        out = self.release()
        self.assertEqual({p.name for p in (out / 'core-update').iterdir()}, {'HomeServerSetup.exe', 'RELEASE.json', 'COMPONENTS.json', 'INSTALL_SPACE.json', 'SHA256SUMS.txt'})
        self.assertFalse((out / 'core-update' / 'tools').exists())
        for key in ('browser', 'media'):
            manifest = json.loads((out / key / 'COMPONENT.json').read_text())
            for name, record in manifest['files'].items():
                self.assertEqual(components.digest(out / key / 'payload' / name), record['sha256'])

    def test_missing_changed_dependencies_block_and_private_files_survive(self):
        sentinel = self.root / 'private.txt'; sentinel.write_text('keep')
        components.verify_runtimes(self.root, self.catalog)
        browser = self.root / 'tools/browser/chrome.exe'; browser.write_bytes(b'changed')
        with self.assertRaises(ValueError): components.verify_runtimes(self.root, self.catalog)
        browser.write_bytes(b'fixture')
        (self.root / 'tools/ffmpeg/ffprobe.exe').unlink()
        with self.assertRaises(ValueError): components.verify_runtimes(self.root, self.catalog)
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_invalid_catalog_and_staged_tampering(self):
        for name in ('../x', '/x', 'tools/browser/CON.txt', 'tools/browser/a:b', 'tools/browser/x.', 'tools\\browser\\x'):
            with self.assertRaises(ValueError): components.safe_path(name)
        bad = copy.deepcopy(self.catalog); bad['components']['core']['requires']['media'] = '0' * 64
        with self.assertRaises(ValueError): components.validate(bad)
        bad = copy.deepcopy(self.catalog); bad['components']['browser']['content_sha256'] = '0' * 64
        with self.assertRaises(ValueError): components.validate(bad)
        self.release()
        (self.root / 'tools/browser/chrome.exe').write_bytes(b'changed')
        with self.assertRaises(ValueError): builder.assemble(self.root, Path(self.tmp.name) / 'changed')

    def test_core_package_validates_and_rehashed_bad_dependencies_reject(self):
        from app.services import device_rollout
        out = self.release() / 'core-update'
        package = Path(self.tmp.name) / 'core.zip'; package.write_bytes(self.archive(out))
        self.assertEqual(device_rollout._validate_package(package)['package_kind'], 'core_update')
        catalog = json.loads((out / 'COMPONENTS.json').read_text())
        catalog['components']['core']['requires']['media'] = '0' * 64
        builder.write_json(out / 'COMPONENTS.json', catalog)
        release = json.loads((out / 'RELEASE.json').read_text())
        release['files']['COMPONENTS.json'] = components.digest(out / 'COMPONENTS.json')
        builder.write_json(out / 'RELEASE.json', release)
        (out / 'SHA256SUMS.txt').write_text(''.join(f'{sha}  {name}\n' for name, sha in release['files'].items()))
        package.write_bytes(self.archive(out))
        with self.assertRaisesRegex(device_rollout.RolloutError, 'component metadata'): device_rollout._validate_package(package)

    def test_official_versions_stage_and_downgrades_reject(self):
        from app.database import initialize_database
        from app.services import device_rollout
        initialize_database()
        out = self.release() / 'core-update'
        self.assertEqual(device_rollout.stage_package(io.BytesIO(self.archive(out)), 'core.zip')['version'], '2.4')
        release = json.loads((out / 'RELEASE.json').read_text()); release['version'] = '2.3'; release.pop('package_kind')
        builder.write_json(out / 'RELEASE.json', release)
        with self.assertRaisesRegex(device_rollout.RolloutError, 'older than'): device_rollout.stage_package(io.BytesIO(self.archive(out)), 'older.zip')


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as data:
        os.environ['HOMESERVER_DATA_DIR'] = data
        unittest.main()
