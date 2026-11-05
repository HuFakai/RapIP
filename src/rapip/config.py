from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def private_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass
class Account:
    account_id: str
    name: str = "Webshare"
    notes: str = ""
    api_key_env: str = ""
    enabled: bool = True
    replacement_country: str = "original"
    replacement_budget: int | None = None
    traffic_limit_gb: int | None = None
    plan_id: str | None = None


@dataclass
class Settings:
    pool_wait_timeout: float = 5
    session_switch_mode: str = "graceful"
    drain_timeout: float = 120
    blacklist_ttl: float = 21600
    retired_session_ttl: float = 86400
    probe_interval: float = 900
    probe_timeout: float = 5
    probe_concurrency: int = 10
    sync_interval: float = 300
    max_fail_count: int = 2
    max_collision_redraws: int = 3
    max_active_bindings: int = 0
    max_connections: int = 1024
    max_connections_per_binding: int = 128
    max_waiters: int = 128
    min_spare_ips: int = 0
    auto_failover: bool = False

    def public(self) -> dict:
        return asdict(self)


@dataclass
class Config:
    data_dir: Path = Path("data")
    config_dir: Path = Path("config")
    web_dir: Path = Path("web/dist")
    http_host: str = "127.0.0.1"
    http_port: int = 8080
    socks_host: str = "127.0.0.1"
    socks_port: int = 1080
    proxy_http_host: str = "127.0.0.1"
    proxy_http_port: int = 8888
    public_proxy_host: str = ""
    public_socks_port: int | None = None
    public_http_proxy_port: int | None = None
    admin_user: str = "admin"
    admin_password: str = ""
    admin_token: str = ""
    socks_user: str = "admin"
    socks_password: str = ""
    cookie_secure: bool = False
    egress_proxy: str = field(default="", repr=False)
    probe_enabled: bool = True
    settings: Settings = field(default_factory=Settings)

    @classmethod
    def from_env(cls) -> Config:
        load_dotenv()
        config = cls(
            data_dir=Path(os.getenv("RAPIP_DATA_DIR", "data")),
            config_dir=Path(os.getenv("RAPIP_CONFIG_DIR", "config")),
            web_dir=Path(os.getenv("RAPIP_WEB_DIR", "web/dist")),
            http_host=os.getenv("RAPIP_HTTP_HOST", "127.0.0.1"),
            http_port=int(os.getenv("RAPIP_HTTP_PORT", "8080")),
            socks_host=os.getenv("RAPIP_SOCKS_HOST", "127.0.0.1"),
            socks_port=int(os.getenv("RAPIP_SOCKS_PORT", "1080")),
            proxy_http_host=os.getenv("RAPIP_PROXY_HTTP_HOST", "127.0.0.1"),
            proxy_http_port=int(os.getenv("RAPIP_PROXY_HTTP_PORT", "8888")),
            public_proxy_host=os.getenv("RAPIP_PUBLIC_PROXY_HOST", ""),
            public_socks_port=int(os.environ["RAPIP_PUBLIC_SOCKS_PORT"])
            if os.getenv("RAPIP_PUBLIC_SOCKS_PORT")
            else None,
            public_http_proxy_port=int(os.environ["RAPIP_PUBLIC_HTTP_PROXY_PORT"])
            if os.getenv("RAPIP_PUBLIC_HTTP_PROXY_PORT")
            else None,
            admin_user=os.getenv("RAPIP_ADMIN_USER", "admin"),
            admin_password=os.getenv("RAPIP_ADMIN_PASSWORD", ""),
            admin_token=os.getenv("RAPIP_ADMIN_TOKEN", ""),
            socks_user=os.getenv("RAPIP_SOCKS_USER", "admin"),
            socks_password=os.getenv("RAPIP_SOCKS_PASSWORD", ""),
            cookie_secure=os.getenv("RAPIP_COOKIE_SECURE", "false").lower() == "true",
            egress_proxy=os.getenv("RAPIP_EGRESS_PROXY", ""),
            probe_enabled=os.getenv("RAPIP_PROBE_ENABLED", "true").lower() == "true",
        )
        if not all((config.admin_password, config.admin_token, config.socks_password)):
            raise ValueError("Set RAPIP_ADMIN_PASSWORD, RAPIP_ADMIN_TOKEN and RAPIP_SOCKS_PASSWORD in .env")
        for port in (
            config.http_port,
            config.socks_port,
            config.proxy_http_port,
            config.public_socks_port,
            config.public_http_proxy_port,
        ):
            if port is not None and not 1 <= port <= 65535:
                raise ValueError("Port must be between 1 and 65535")
        return config

    def accounts(self) -> list[Account]:
        path = self.config_dir / "accounts.json"
        rows = json.loads(path.read_text()) if path.exists() else []
        accounts = [Account(**row) for row in rows]
        if len({a.account_id for a in accounts}) != len(accounts):
            raise ValueError("Duplicate account_id")
        return accounts

    def key(self, account: Account) -> str:
        if account.api_key_env:
            return os.getenv(account.api_key_env, "")
        path = self.config_dir / "secrets.json"
        return json.loads(path.read_text()).get(account.account_id, "") if path.exists() else ""
