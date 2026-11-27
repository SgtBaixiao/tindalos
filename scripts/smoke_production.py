"""Probe a running production app over loopback HTTP using only the stdlib."""
from __future__ import annotations

import argparse
import json
import time
import uuid
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

MODULE_TEXT = (
    "# Synthetic lighthouse smoke\n"
    "The lighthouse keeper is alive. The cellar key is in the harbour archive.\n"
    "Investigators compare the logbook with a witness before entering the cellar."
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


class HTTP:
    def __init__(self, base_url, timeout):
        url = urlsplit(base_url)
        require(url.scheme == "http" and url.hostname in {"127.0.0.1", "::1", "localhost"}
                and not url.username and not url.password and url.path in {"", "/"}
                and not url.query and not url.fragment, "base URL must be loopback HTTP")
        self.base_url = base_url.rstrip("/") + "/"
        self.deadline = time.monotonic() + timeout
        self.opener = build_opener(ProxyHandler({}))

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, "smoke exceeded its HTTP deadline")
        return remaining

    def request(self, path, *, body=None, key=None):
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if key:
            headers["Idempotency-Key"] = key
        request = Request(urljoin(self.base_url, path), headers=headers,
                          data=json.dumps(body).encode() if body is not None else None)
        with self.opener.open(request, timeout=min(3, self.remaining())) as response:
            return response.status, response.headers.get_content_type(), response.read()

    def json(self, path, *, body=None, key=None, status=200):
        code, content_type, payload = self.request(path, body=body, key=key)
        require(code == status and content_type == "application/json",
                f"{path}: expected JSON HTTP {status}, got {code} {content_type}")
        return json.loads(payload)

    def pause(self):
        time.sleep(min(0.1, self.remaining()))


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = {}
        self.root = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.root |= attrs.get("id") == "root"
        if tag == "script" and attrs.get("type") == "module" and attrs.get("src"):
            self.paths[attrs["src"]] = "js"
        if tag == "link" and attrs.get("rel") == "stylesheet" and attrs.get("href"):
            self.paths[attrs["href"]] = "css"


def wait_health(http):
    last_error = "not ready"
    while True:
        try:
            health = http.json("/api/health")
            require(health["ok"] is True and health["runtime"] == "single_process"
                    and health["storage"] == "sqlite_wal"
                    and health["job_workers"] == 2 and health["capacity"] == 16,
                    f"unexpected default runtime: {health}")
            return health
        except (URLError, OSError, ValueError, RuntimeError) as error:
            last_error = str(error)
        if time.monotonic() >= http.deadline:
            raise RuntimeError(f"health did not become ready: {last_error}")
        http.pause()


def probe_spa(http):
    code, content_type, html = http.request("/")
    require(code == 200 and content_type == "text/html", "SPA index is missing")
    assets = Assets()
    assets.feed(html.decode("utf-8"))
    require(assets.root and set(assets.paths.values()) == {"js", "css"},
            "SPA must include a root, module JS and CSS")
    for path, kind in assets.paths.items():
        resolved = urlsplit(urljoin(http.base_url, path))
        require(resolved.netloc == urlsplit(http.base_url).netloc
                and resolved.path.startswith("/assets/"), f"unexpected asset URL: {path}")
        code, mime, payload = http.request(resolved.path)
        expected = {"text/javascript", "application/javascript"} if kind == "js" else {"text/css"}
        require(code == 200 and mime in expected and payload.strip(),
                f"{path}: missing {kind} asset or HTML fallback")
    code, content_type, fallback = http.request("/workbench")
    require(code == 200 and content_type == "text/html" and fallback == html,
            "SPA fallback differs from the index")
    return len(assets.paths)


def read_campaign(http, job):
    require(job["status"] == "completed" and job["revision"] == 1 and not job["error"],
            f"offline job did not complete: {job}")
    cid = job["campaign_id"]
    record = http.json(f"/api/campaigns/{cid}")
    campaign, meta = record["campaign"], record["meta"]
    require(campaign["id"] == cid and campaign["source_text"] == MODULE_TEXT
            and campaign["rules"] == "COC7" and meta["revision"] == 1,
            "persisted campaign identity, source or revision differs")
    require(campaign["generation"]["mode"] == "deterministic"
            and campaign["generation"]["degraded"] is False
            and campaign["acts"] and campaign["npcs"]
            and all(act["scenes"] for act in campaign["acts"]),
            "real offline generation did not produce acts, scenes and NPCs")
    catalog = http.json("/api/campaigns")["campaigns"]
    require([row["id"] for row in catalog] == [cid], "isolated catalog must contain exactly this campaign")
    return record


def smoke(base_url, receipt, *, verify_persisted=False, timeout=60):
    started = time.monotonic()
    http = HTTP(base_url, timeout)
    health = wait_health(http)
    asset_count = probe_spa(http)
    if verify_persisted:
        prior = json.loads(receipt.read_text())
        job = http.json(f"/api/jobs/{prior['job']['id']}")["job"]
        record = read_campaign(http, job)
        require(job == prior["job"] and record == prior["record"],
                "job or campaign changed across process restart")
    else:
        require(http.json("/api/campaigns")["campaigns"] == [], "use an empty isolated runtime directory")
        require(http.json("/api/modules")["modules"] == [], "isolated module catalog is not empty")
        body = {"module_text": MODULE_TEXT, "llm": False, "rules": "COC7"}
        key = "production-smoke-" + uuid.uuid4().hex
        job = http.json("/api/jobs", body=body, key=key, status=202)["job"]
        while job["status"] not in {"completed", "failed", "cancelled", "interrupted"}:
            http.pause()
            job = http.json(f"/api/jobs/{job['id']}")["job"]
        record = read_campaign(http, job)
        repeated = http.json("/api/jobs", body=body, key=key, status=202)["job"]
        require(repeated == job, "same-key retry did not reuse the completed job")
        receipt.write_text(json.dumps({"job": job, "record": record}, indent=2) + "\n")
    return {"phase": "verify-persisted" if verify_persisted else "generate",
            "health": health, "asset_count": asset_count, "job_id": job["id"],
            "job_status": job["status"], "campaign_id": job["campaign_id"],
            "revision": job["revision"], "generation_mode": record["campaign"]["generation"]["mode"],
            "elapsed_seconds": round(time.monotonic() - started, 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8347")
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--verify-persisted", action="store_true")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    require(0 < args.timeout <= 60, "HTTP deadline must be between 0 and 60 seconds")
    print(json.dumps(smoke(args.base_url, args.receipt,
                           verify_persisted=args.verify_persisted, timeout=args.timeout)))


if __name__ == "__main__":
    main()
