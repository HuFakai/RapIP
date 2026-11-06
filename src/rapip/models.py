from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum


def uid() -> str:
    return uuid.uuid4().hex


class GatewayError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class IPState(StrEnum):
    FRESH = "FRESH"
    RESERVED = "RESERVED"
    IN_USE = "IN_USE"
    DRAINING = "DRAINING"
    QUARANTINED = "QUARANTINED"
    REPLACING = "REPLACING"
    ARCHIVED = "ARCHIVED"


@dataclass
class Route:
    user: str
    session: str
    worker: str | None = None
    country: str | None = None
    group: str = "default"

    @property
    def slot(self) -> str:
        prefix = "owner" if self.group == "default" else f"owner:group:{self.group}"
        return f"{prefix}:worker:{self.worker}" if self.worker else f"{prefix}:anonymous:{self.session}"

    @classmethod
    def parse(cls, username: str) -> Route:
        if len(username.encode()) > 255 or ("-" in username and "_" in username):
            raise GatewayError("INVALID_USERNAME", "用户名格式不正确", 400)
        parts = username.split("_" if "_" in username else "-")
        if not parts or not re.fullmatch(r"[A-Za-z0-9]{1,64}", parts[0]):
            raise GatewayError("INVALID_USERNAME", "用户名格式不正确", 400)
        user, rest = parts[0], parts[1:]
        country = worker = None
        group = "default"
        if rest[:1] == ["country"] and len(rest) >= 2:
            country, rest = rest[1].upper(), rest[2:]
            if not re.fullmatch(r"[A-Z]{2}", country):
                raise GatewayError("INVALID_COUNTRY", "国家代码应为两位字母", 400)
        if rest[:1] == ["group"] and len(rest) >= 2:
            group, rest = rest[1], rest[2:]
            if not re.fullmatch(r"[A-Za-z0-9]{1,32}", group):
                raise GatewayError("INVALID_GROUP", "分组 ID 需为 1–32 位字母数字", 400)
        if rest[:1] == ["worker"] and len(rest) >= 2:
            worker, rest = rest[1], rest[2:]
        if len(rest) != 2 or rest[0] != "session" or (country and not worker):
            raise GatewayError("INVALID_USERNAME", "请提供 worker/session 或 session 参数", 400)
        session = rest[1]
        if not all(re.fullmatch(r"[A-Za-z0-9]{1,64}", s) for s in (session, worker or "x")):
            raise GatewayError("INVALID_USERNAME", "会话和工位仅支持字母数字", 400)
        return cls(user, session, worker, country, group)


@dataclass
class Node:
    id: str
    account_id: str
    provider_id: str
    ip: str
    port: int
    username: str = field(repr=False)
    password: str = field(repr=False)
    country: str = ""
    state: str = IPState.QUARANTINED
    used: bool = False
    reason: str = "待验证"
    created_at: float = field(default_factory=time.time)
    checked_at: float | None = None
    fail_count: int = 0
    drain_until: float | None = None
    binding_id: str | None = None
    generation: int = 0
    group_id: str = "default"
    slot_id: str = ""
    membership_version: int = 1
    quality: dict = field(default_factory=dict)
    latency_ms: float | None = None
    last_success_at: float | None = None

    def public(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k not in {"username", "password"}}


@dataclass
class Binding:
    id: str
    slot: str
    session: str
    worker: str | None
    country: str | None
    node_id: str | None
    generation: int = 1
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    last_detached: float = field(default_factory=time.time)
    released: bool = False
    failovers: int = 0
    unavailable: str = ""
    released_at: float | None = None
    release_reason: str = ""
    rotation: dict = field(default_factory=dict)
    group_id: str = "default"
    policy_version: int = 0
    reserved: bool = False
    connected_generation: int = 0


@dataclass
class Replacement:
    id: str
    node_id: str
    account_id: str
    state: str = "QUEUED"
    provider_id: str | None = None
    reason: str = ""
    error: str | None = None
    attempts: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    retry_at: float = 0
    before_ips: list[str] = field(default_factory=list)
    redraws: int = 0
    group_id: str = "default"
    slot_id: str = ""
    cause: str = "business"
    selection: dict | None = None
    reservation_id: str = ""
    rebind_id: str = ""
    new_ip: str = ""
    fallback_enabled: bool | None = None
    preferred_selection: dict | None = None
    fallback_reason: str = ""
    failed_provider_id: str = ""


@dataclass
class Operation:
    id: str
    kind: str
    state: str = "ACCEPTED"
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    error: str | None = None
    result: dict = field(default_factory=dict)
    group_id: str = ""
    account_id: str = ""
