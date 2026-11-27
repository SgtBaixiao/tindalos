"""Exercise the production smoke CLI through real offline loopback HTTP."""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts" / "smoke_production.py"


@contextmanager
def offline_server(tmp_path, dist):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        "PATH": str(Path(sys.executable).parent),
        "PYTHONPATH": str(ROOT / "src"),
        "TINDALOS_DATA_DIR": str(tmp_path / "data"),
        "TINDALOS_SITE_DB": str(tmp_path / "data" / "site.db"),
        "TINDALOS_FRONTEND_DIST": str(dist),
        "TINDALOS_LLM_ENABLED": "0",
    }
    with (tmp_path / "server.log").open("a") as log:
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "tindalos.web:create_app", "--factory",
             "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
                raise AssertionError("loopback server exceeded its shutdown bound")


def frontend(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html><html><head><link rel="stylesheet" href="./assets/app.css">'
        '</head><body><div id="root"></div>'
        '<script type="module" src="./assets/app.js"></script></body></html>'
    )
    (dist / "assets" / "app.js").write_text('console.log("synthetic SPA");')
    (dist / "assets" / "app.css").write_text("body { color: #123; }")
    return dist


def run_smoke(base, receipt, *options):
    return subprocess.run(
        [sys.executable, str(SMOKE), "--base-url", base, "--receipt", str(receipt),
         "--timeout", "20", *options],
        cwd=ROOT, capture_output=True, text=True, timeout=25,
        env={"PATH": str(Path(sys.executable).parent)},
    )


def test_smoke_generates_offline_and_reads_same_campaign_after_restart(tmp_path):
    dist = frontend(tmp_path)
    receipt = tmp_path / "receipt.json"
    with offline_server(tmp_path, dist) as base:
        generated = run_smoke(base, receipt)
        assert generated.returncode == 0, generated.stdout + generated.stderr
        result = json.loads(generated.stdout)
        assert result["phase"] == "generate"
        assert result["asset_count"] == 2
        assert result["job_status"] == "completed"
        assert result["generation_mode"] == "deterministic"
        assert result["revision"] == 1
    with offline_server(tmp_path, dist) as base:
        persisted = run_smoke(base, receipt, "--verify-persisted")
        assert persisted.returncode == 0, persisted.stdout + persisted.stderr
        reloaded = json.loads(persisted.stdout)
        assert reloaded["phase"] == "verify-persisted"
        assert reloaded["job_id"] == result["job_id"]
        assert reloaded["campaign_id"] == result["campaign_id"]


def test_smoke_rejects_missing_asset_before_generating(tmp_path):
    dist = frontend(tmp_path)
    (dist / "assets" / "app.css").unlink()
    receipt = tmp_path / "receipt.json"
    with offline_server(tmp_path, dist) as base:
        result = run_smoke(base, receipt)
        assert result.returncode != 0
        assert "HTTP Error 404" in result.stderr
        assert not receipt.exists()


def test_smoke_bounds_wait_for_unavailable_server(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        base = f"http://127.0.0.1:{sock.getsockname()[1]}"
        started = time.monotonic()
        result = run_smoke(base, tmp_path / "receipt.json", "--timeout", "0.5")
        assert result.returncode != 0
        assert "deadline" in result.stderr or "health did not become ready" in result.stderr
        assert time.monotonic() - started < 3
