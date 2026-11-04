from dataclasses import replace
from urllib.parse import unquote, urlsplit

import pytest

from rapip.config import Config
from rapip.connection import build_connection
from rapip.gateway import Gateway
from rapip.storage import Storage

HEADERS = {"authorization": "Bearer test-api-token"}


async def test_complete_links_require_auth_and_do_not_allocate(client, gateway):
    request = {"host": "proxy.example.com", "worker_id": "01", "session_id": "abc123"}
    assert (await client.post("/api/v1/connection-config", json=request)).status_code == 401
    response = await client.post("/api/v1/connection-config", headers=HEADERS, json=request)
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "admin-worker-01-session-abc123"
    assert unquote(urlsplit(data["links"]["http"]).password) == gateway.config.socks_password
    assert response.headers["cache-control"] == "no-store"
    assert not gateway.bindings and gateway.test_provider.calls == 0
    assert gateway.config.socks_password not in data["ai_guide"]
    assert gateway.config.admin_token not in data["ai_guide"]
    assert "_worker_01_session_" in data["ai_guide"]


def test_password_encoding_ipv6_and_public_ports():
    config = Config(socks_password="p@ss:/#% +密", public_socks_port=21080, public_http_proxy_port=28888)
    result = build_connection(config, "2001:db8::1", "02", "case01", "US")
    for url in result["links"].values():
        parsed = urlsplit(url)
        assert parsed.hostname == "2001:db8::1"
        assert unquote(parsed.password) == config.socks_password
        assert unquote(parsed.username) == "admin-country-US-worker-02-session-case01"
    assert urlsplit(result["links"]["socks5"]).port == 21080
    assert urlsplit(result["links"]["http"]).port == 28888


@pytest.mark.parametrize(
    "host", ["https://proxy.example", "example.com:8888", "host/path", "bad@host", "../env"]
)
async def test_invalid_advertised_host_rejected(client, host):
    response = await client.post(
        "/api/v1/connection-config", headers=HEADERS, json={"host": host, "session_id": "abc"}
    )
    assert response.status_code == 422
    assert "test-socks-password" not in response.text


async def test_connection_generator_cookie_requires_csrf(client):
    login = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "test-admin-password"}
    )
    assert (await client.post("/api/v1/connection-config", json={"session_id": "abc"})).status_code == 403
    response = await client.post(
        "/api/v1/connection-config",
        json={"session_id": "abc", "worker_id": None},
        headers={"x-csrf-token": login.json()["csrf"]},
    )
    assert response.status_code == 200
    assert response.json()["username"] == "admin-session-abc"


async def test_country_requires_worker(client):
    response = await client.post(
        "/api/v1/connection-config",
        headers=HEADERS,
        json={"session_id": "abc", "worker_id": None, "country_code": "US"},
    )
    assert response.status_code == 422


async def test_probe_switch_persists_and_recovers(gateway, tmp_path):
    await gateway.update_settings({"probe_enabled": False})
    assert not gateway.config.probe_enabled
    snapshot = await gateway.storage.load()
    directory = tmp_path / "restored"
    store = Storage(directory)
    await store.open()
    await store.save(snapshot)
    await store.close()
    config = replace(gateway.config, data_dir=directory, probe_enabled=True)
    restored = Gateway(config, provider_factory=gateway.provider_factory, probe=gateway.probe)
    await restored.start(background=False, sync=False)
    try:
        assert not restored.config.probe_enabled
        assert not restored.stats()["probe_enabled"]
    finally:
        await restored.stop()


async def test_probe_switch_api_stops_and_starts_without_replacement(client, gateway):
    response = await client.patch("/api/v1/settings", headers=HEADERS, json={"probe_enabled": False})
    assert response.status_code == 200 and response.json()["values"]["probe_enabled"] is False
    assert not (await client.get("/api/v1/stats", headers=HEADERS)).json()["probe_enabled"]
    response = await client.patch("/api/v1/settings", headers=HEADERS, json={"probe_enabled": True})
    assert response.status_code == 200 and gateway.config.probe_enabled
    assert gateway.test_provider.calls == 0


def test_environment_controls_all_ports_and_advertised_address(monkeypatch):
    for key, value in {
        "RAPIP_ADMIN_PASSWORD": "admin",
        "RAPIP_ADMIN_TOKEN": "token",
        "RAPIP_SOCKS_PASSWORD": "proxy",
        "RAPIP_HTTP_PORT": "19080",
        "RAPIP_SOCKS_PORT": "19081",
        "RAPIP_PROXY_HTTP_PORT": "19082",
        "RAPIP_PUBLIC_PROXY_HOST": "proxy.example.com",
        "RAPIP_PUBLIC_SOCKS_PORT": "29081",
        "RAPIP_PUBLIC_HTTP_PROXY_PORT": "29082",
    }.items():
        monkeypatch.setenv(key, value)
    config = Config.from_env()
    assert (config.http_port, config.socks_port, config.proxy_http_port) == (19080, 19081, 19082)
    assert config.public_proxy_host == "proxy.example.com"
    assert (config.public_socks_port, config.public_http_proxy_port) == (29081, 29082)
