from __future__ import annotations

import importlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mneme.api.app import create_app
from mneme.crawl.discover import SpecCandidate
from mneme.index.ingest import ingest_text

ROOT = Path(__file__).resolve().parents[1]
TODO_SPEC = ROOT / "examples" / "specs" / "todo.yaml"
MANAGEMENT_TOKEN = "test-management-token"
MANAGEMENT_HEADERS = {"X-Mneme-Management-Token": MANAGEMENT_TOKEN}
app_module = importlib.import_module("mneme.api.app")


def _client(tmp_path: Path, *, auth_file: Path | None = None) -> TestClient:
    return TestClient(
        create_app(
            str(tmp_path / "catalog.db"),
            auth_config_path=str(auth_file or tmp_path / "auth.json"),
            management_token=MANAGEMENT_TOKEN,
        ),
        headers=MANAGEMENT_HEADERS,
    )


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/diagnostics", None),
        ("GET", "/specs", None),
        ("GET", "/specs/missing", None),
        ("POST", "/specs/ingest-url", {}),
        ("POST", "/specs/ingest-file", {}),
        ("POST", "/specs/discover", {}),
        ("DELETE", "/specs/missing", None),
        ("GET", "/operations", None),
        ("GET", "/auth/profiles", None),
        ("GET", "/auth/profiles/missing", None),
        ("POST", "/auth/profiles", {}),
        ("PUT", "/auth/profiles/missing", {}),
        ("DELETE", "/auth/profiles/missing", None),
    ],
)
def test_management_routes_require_valid_token(tmp_path, method, path, payload):
    client = TestClient(create_app(str(tmp_path / "catalog.db"), management_token=MANAGEMENT_TOKEN))

    missing = client.request(method, path, json=payload)
    wrong = client.request(
        method,
        path,
        json=payload,
        headers={"X-Mneme-Management-Token": "wrong-token"},
    )

    assert missing.status_code == 401
    assert missing.json() == {"detail": "Unauthorized"}
    assert wrong.status_code == 401
    assert wrong.json() == {"detail": "Unauthorized"}
    assert MANAGEMENT_TOKEN not in missing.text
    assert MANAGEMENT_TOKEN not in wrong.text


def test_management_routes_use_environment_token_and_disable_without_one(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("MNEME_MANAGEMENT_TOKEN", MANAGEMENT_TOKEN)
    enabled = TestClient(create_app(str(tmp_path / "enabled.db")))
    assert enabled.get("/diagnostics", headers=MANAGEMENT_HEADERS).status_code == 200

    monkeypatch.delenv("MNEME_MANAGEMENT_TOKEN")
    disabled = TestClient(create_app(str(tmp_path / "disabled.db")))
    response = disabled.get("/diagnostics", headers=MANAGEMENT_HEADERS)

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert MANAGEMENT_TOKEN not in response.text
    assert disabled.get("/version").status_code == 200
    assert disabled.get("/health").status_code == 200


def test_catalog_management_lifecycle_and_safe_spec_details(tmp_path):
    spec_file = tmp_path / "sensitive.yaml"
    text = TODO_SPEC.read_text(encoding="utf-8")
    text = text.replace(
        "info:\n",
        "x-private-token: should-never-be-returned\ninfo:\n  description: Public documentation.\n",
    )
    spec_file.write_text(text, encoding="utf-8")
    client = _client(tmp_path)

    ingested = client.post("/specs/ingest-file", json={"path": str(spec_file)})
    assert ingested.status_code == 201
    spec_id = ingested.json()["spec_id"]
    assert ingested.json()["operations"] == 3

    listed = client.get("/specs", params={"limit": 1, "offset": 0, "status": "ok"})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["operation_count"] == 3
    assert "raw_json" not in listed.text

    detail = client.get(f"/specs/{spec_id}")
    assert detail.status_code == 200
    assert detail.json()["documentation"]["info"]["description"] == "Public documentation."
    assert detail.json()["documentation"]["servers"] == [{"url": "https://api.example.test"}]
    assert "paths" not in detail.text
    assert "components" not in detail.text
    assert "should-never-be-returned" not in detail.text

    first_page = client.get(
        "/operations",
        params={"spec_id": spec_id, "method": "get", "limit": 1},
    )
    assert first_page.status_code == 200
    assert first_page.json()["total"] == 2
    assert len(first_page.json()["items"]) == 1
    assert first_page.json()["items"][0]["method"] == "GET"
    assert "spec_slice" not in first_page.text

    filtered = client.get("/operations", params={"q": "Create a todo"})
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["method"] == "POST"

    deleted = client.delete(f"/specs/{spec_id}")
    assert deleted.status_code == 200
    assert deleted.json()["operations"] == 3
    assert deleted.json()["fts_rows"] == 3
    assert client.get(f"/specs/{spec_id}").status_code == 404
    assert client.get("/operations").json()["total"] == 0

    with sqlite3.connect(tmp_path / "catalog.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM operations_fts").fetchone()[0] == 0


def test_ingest_file_rejects_missing_path(tmp_path):
    response = _client(tmp_path).post(
        "/specs/ingest-file",
        json={"path": str(tmp_path / "missing.yaml")},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "spec path is not a readable file"


def test_ingest_url_reuses_ingestion_function(tmp_path, monkeypatch):
    calls: list[tuple[str, str | None]] = []

    def fake_ingest_url(
        db: Any,
        url: str,
        *,
        discovered_via: str | None = None,
    ) -> dict[str, Any]:
        calls.append((url, discovered_via))
        return ingest_text(
            db,
            TODO_SPEC.read_text(encoding="utf-8"),
            source_url=url,
            discovered_via=discovered_via,
        )

    monkeypatch.setattr(app_module, "ingest_url", fake_ingest_url)
    signed_url = "https://example.test/openapi.yaml?token=signed-secret"
    response = _client(tmp_path).post(
        "/specs/ingest-url",
        json={"url": signed_url},
    )
    assert response.status_code == 201
    assert response.json()["operations"] == 3
    assert calls == [(signed_url, "management_api")]

    catalog = _client(tmp_path).get("/specs")
    assert catalog.json()["items"][0]["source_url"] == "https://example.test/openapi.yaml"
    assert "signed-secret" not in catalog.text


def test_discover_ingests_validated_candidates_without_returning_urls(tmp_path, monkeypatch):
    signed_url = "https://example.test/openapi.yaml?token=signed-secret"

    def fake_discover(domain, *, fetcher, validate, max_candidates):
        assert domain == "example.test"
        assert validate is True
        assert max_candidates == 10
        return [SpecCandidate(signed_url, "common_path")]

    def fake_ingest_url(db, url, *, fetcher, discovered_via):
        assert url == signed_url
        assert discovered_via == "common_path"
        return ingest_text(
            db,
            TODO_SPEC.read_text(encoding="utf-8"),
            source_url=url,
            discovered_via=discovered_via,
        )

    monkeypatch.setattr(app_module, "discover_domain", fake_discover)
    monkeypatch.setattr(app_module, "ingest_url", fake_ingest_url)

    response = _client(tmp_path).post("/specs/discover", json={"domain": "example.test"})

    assert response.status_code == 200
    assert response.json()["candidates"] == 1
    assert response.json()["ingested"] == 1
    assert response.json()["results"][0]["operations"] == 3
    assert "signed-secret" not in response.text


def test_ingest_url_rejects_credentials_without_echoing_them(tmp_path):
    secret = "url-password-secret"
    response = _client(tmp_path).post(
        "/specs/ingest-url",
        json={"url": f"https://user:{secret}@example.test/openapi.yaml"},
    )
    assert response.status_code == 422
    assert secret not in response.text


def test_diagnostics_and_version_are_local_and_secret_free(tmp_path, monkeypatch):
    monkeypatch.setenv("VERY_SECRET_TOKEN", "do-not-return")
    client = _client(tmp_path)

    version = client.get("/version")
    diagnostics = client.get("/diagnostics")

    assert version.status_code == 200
    assert version.json()["version"]
    assert diagnostics.status_code == 200
    assert diagnostics.json()["database"]["stats"]["operations"] == 0
    assert diagnostics.json()["auth_config"]["exists"] is False
    assert "do-not-return" not in diagnostics.text
    assert "network" not in diagnostics.json()


def test_auth_profile_metadata_crud_is_atomic_and_never_returns_secrets(tmp_path):
    auth_file = tmp_path / "config" / "auth.json"
    client = _client(tmp_path, auth_file=auth_file)
    payload = {
        "name": "github",
        "provider_domain": "api.github.com",
        "base_url": "https://api.github.com",
        "auth": {"type": "bearer", "token_env": "GITHUB_TOKEN"},
        "allowed_hosts": ["api.github.com"],
        "allow_methods": ["get", "post"],
        "require_confirmation": True,
    }

    created = client.post("/auth/profiles", json=payload)
    assert created.status_code == 201
    assert created.json()["auth"] == {"type": "bearer", "token_env": "GITHUB_TOKEN"}
    assert created.json()["allow_methods"] == ["GET", "POST"]
    assert auth_file.stat().st_mode & 0o777 == 0o600
    assert not list(auth_file.parent.glob("*.tmp"))

    listed = client.get("/auth/profiles")
    assert listed.status_code == 200
    assert [profile["name"] for profile in listed.json()["profiles"]] == ["github"]

    updated = client.put(
        "/auth/profiles/github",
        json={"allow_methods": ["get"], "verify_ssl": False},
    )
    assert updated.status_code == 200
    assert updated.json()["allow_methods"] == ["GET"]
    stored = json.loads(auth_file.read_text(encoding="utf-8"))["profiles"]["github"]
    assert stored["auth"]["token_env"] == "GITHUB_TOKEN"
    assert stored["verify_ssl"] is False

    duplicate = client.post("/auth/profiles", json=payload)
    assert duplicate.status_code == 409
    assert client.get("/auth/profiles/github").status_code == 200

    deleted = client.delete("/auth/profiles/github")
    assert deleted.status_code == 200
    assert deleted.json()["profile"]["auth"]["token_env"] == "GITHUB_TOKEN"
    assert client.get("/auth/profiles/github").status_code == 404


def test_auth_api_rejects_literal_secrets_without_echoing_them(tmp_path):
    client = _client(tmp_path)
    secret = "super-secret-value-123"

    response = client.post(
        "/auth/profiles",
        json={
            "name": "unsafe",
            "auth": {"type": "bearer", "token": secret},
        },
    )
    assert response.status_code == 422
    assert secret not in response.text
    assert not (tmp_path / "auth.json").exists()

    custom = client.post(
        "/auth/profiles",
        json={
            "name": "unsafe",
            "auth": {"type": "headers", "headers": {"X-Key": secret}},
        },
    )
    assert custom.status_code == 422
    assert secret not in custom.text


def test_auth_api_redacts_legacy_values_and_preserves_them_on_metadata_update(tmp_path):
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps(
            {
                "profiles": {
                    "legacy": {
                        "provider_domain": "old.example.test",
                        "auth": {"type": "bearer", "token": "legacy-secret"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    client = _client(tmp_path, auth_file=auth_file)

    fetched = client.get("/auth/profiles/legacy")
    assert fetched.status_code == 200
    assert fetched.json()["auth"] == {"type": "bearer"}
    assert "legacy-secret" not in fetched.text

    updated = client.put(
        "/auth/profiles/legacy",
        json={"provider_domain": "new.example.test"},
    )
    assert updated.status_code == 200
    assert "legacy-secret" not in updated.text
    stored = json.loads(auth_file.read_text(encoding="utf-8"))
    assert stored["profiles"]["legacy"]["auth"]["token"] == "legacy-secret"


def test_auth_api_validates_names_and_malformed_config(tmp_path):
    auth_file = tmp_path / "auth.json"
    client = _client(tmp_path, auth_file=auth_file)
    missing = client.put("/auth/profiles/missing", json={"allow_methods": ["GET"]})
    assert missing.status_code == 404
    assert not auth_file.exists()

    invalid_name = client.post("/auth/profiles", json={"name": "../unsafe"})
    assert invalid_name.status_code == 400
    assert not auth_file.exists()

    auth_file.write_text("{broken", encoding="utf-8")
    malformed = client.get("/auth/profiles")
    assert malformed.status_code == 400
    assert "invalid JSON auth config" in malformed.json()["detail"]
