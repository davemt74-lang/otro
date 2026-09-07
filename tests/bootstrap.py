from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from desktop.bootstrap import ensure_loopback_proxy_bypass, prepare_data_directory  # noqa: E402


proxy_env = {
    "NO_PROXY": "example.test,LOCALHOST",
    "no_proxy": "internal.test",
}
merged_proxy_bypass = ensure_loopback_proxy_bypass(proxy_env)
merged_values = {value.strip().lower() for value in merged_proxy_bypass.split(",") if value.strip()}
assert {"example.test", "internal.test", "localhost", "127.0.0.1", "::1"}.issubset(merged_values)
assert proxy_env["NO_PROXY"] == proxy_env["no_proxy"] == merged_proxy_bypass
assert merged_proxy_bypass.lower().split(",").count("localhost") == 1

with tempfile.TemporaryDirectory(prefix="homeserver-bootstrap-") as root_value:
    root = Path(root_value)
    home = root / "home"
    local = root / "local"
    legacy = home / ".homeserver"
    legacy.mkdir(parents=True)
    (legacy / "homeserver.db").write_bytes(b"legacy-sentinel")
    env: dict[str, str] = {}

    state = prepare_data_directory(
        platform_name="nt",
        home=home,
        local_app_data=local,
        environ=env,
    )
    preferred = local / "HomeServer" / "Data"
    assert state["migration"] == "migrated_legacy"
    assert Path(env["HOMESERVER_DATA_DIR"]) == preferred
    assert (preferred / "homeserver.db").read_bytes() == b"legacy-sentinel"
    assert not legacy.exists()
    assert (preferred / "runtime" / "bootstrap-state.json").is_file()

with tempfile.TemporaryDirectory(prefix="homeserver-bootstrap-conflict-") as root_value:
    root = Path(root_value)
    home = root / "home"
    local = root / "local"
    legacy = home / ".homeserver"
    preferred = local / "HomeServer" / "Data"
    legacy.mkdir(parents=True)
    preferred.mkdir(parents=True)
    (legacy / "legacy.txt").write_text("legacy", encoding="utf-8")
    (preferred / "current.txt").write_text("current", encoding="utf-8")
    env = {}

    state = prepare_data_directory(
        platform_name="nt",
        home=home,
        local_app_data=local,
        environ=env,
    )
    assert state["migration"] == "conflict_preserved"
    assert state["warning"]
    assert Path(env["HOMESERVER_DATA_DIR"]) == preferred
    assert (legacy / "legacy.txt").is_file()
    assert (preferred / "current.txt").is_file()

with tempfile.TemporaryDirectory(prefix="homeserver-bootstrap-explicit-") as root_value:
    explicit = Path(root_value) / "custom"
    env = {"HOMESERVER_DATA_DIR": str(explicit)}
    state = prepare_data_directory(platform_name="nt", environ=env)
    assert state["mode"] == "explicit"
    assert explicit.is_dir()

print("HomeServer Windows data bootstrap test passed")
