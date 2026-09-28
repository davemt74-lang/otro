from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def need(path: str, needle: str) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")
    assert needle in text, f"{path} missing {needle!r}"


def run():
    need("app/main.py", '@app.get("/api/v1/control/federation-operations")')
    need("app/main.py", "tracky_federation_operations.current_report()")
    need("ui/index.html", 'data-view="federation"')
    need("ui/index.html", 'id="view-federation"')
    need("ui/index.html", "federation-control-center-v280.css")
    need("ui/index.html", "federation-control-center-v280.js")
    need("ui/app.js", "loadFederationControlCenter")
    need("ui/app.js", "federation:'Physical Network'")
    need("ui/federation-control-center-v280.js", "/api/v1/control/federation-operations")
    need("ui/federation-control-center-v280.js", "Section 2")
    need("HomeServer.spec", "('ui', 'ui')")
    script = (ROOT / "ui/federation-control-center-v280.js").read_text(encoding="utf-8")
    for mutation in ("method:'POST'", 'method:"POST"', "method:'PUT'", "method:'PATCH'", "method:'DELETE'"):
        assert mutation not in script, f"Section 2 control center must remain observational: {mutation}"
    print("TRACKY_V280_FEDERATION_CONTROL_CENTER=PASS")


if __name__ == "__main__":
    run()
