"""Runtime credentials, persisted separately from logs and scheduling snapshots."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets

from .config import private_json
from .models import GatewayError


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=3, maxmem=64 * 1024 * 1024
    ).hex()
    return f"scrypt${salt}${digest}"


class Credentials:
    def __init__(self, config):
        self.config = config
        self.path = config.config_dir / "credentials.json"
        self.lock = asyncio.Lock()
        self.hash_slots = asyncio.Semaphore(2)
        self.saved = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.revision = self.saved.get("revision", "bootstrap")
        if admin := self.saved.get("admin"):
            # Invalid saved credentials must fail closed instead of restoring an old password.
            algorithm, salt, digest = admin["password_hash"].split("$")
            if algorithm != "scrypt" or len(bytes.fromhex(salt)) != 16 or len(bytes.fromhex(digest)) != 64:
                raise ValueError("Invalid persisted administrator credentials")
            config.admin_user = admin["username"]
        if proxy := self.saved.get("proxy"):
            config.socks_user, config.socks_password = proxy["username"], proxy["password"]

    async def verify(self, password):
        revision = self.revision
        admin = self.saved.get("admin")
        if not admin:
            return hmac.compare_digest(password.encode(), self.config.admin_password.encode())
        async with self.hash_slots:
            actual = await asyncio.to_thread(password_hash, password, admin["password_hash"].split("$")[1])
        return revision == self.revision and hmac.compare_digest(actual, admin["password_hash"])

    def public(self):
        return {"admin_username": self.config.admin_user, "proxy_username": self.config.socks_user}

    async def change(self, scope, current_password, username, new_password):
        async with self.lock:
            if not await self.verify(current_password):
                raise GatewayError("REAUTHENTICATION_FAILED", "当前管理密码不正确", 403)
            old_username = self.config.admin_user if scope == "admin" else self.config.socks_user
            if username == old_username and not new_password:
                raise GatewayError("NO_CHANGE", "请修改用户名或填写新密码", 400)
            saved = dict(self.saved)
            if scope == "admin":
                # Hash the bootstrap password as well when changing only the username.
                async with self.hash_slots:
                    hashed = await asyncio.to_thread(password_hash, new_password or current_password)
                saved.update(
                    admin={"username": username, "password_hash": hashed}, revision=secrets.token_hex(16)
                )
            else:
                saved["proxy"] = {
                    "username": username,
                    "password": new_password or self.config.socks_password,
                }
            await asyncio.to_thread(private_json, self.path, saved)
            self.saved = saved
            self.revision = saved.get("revision", "bootstrap")
            if scope == "admin":
                self.config.admin_user = username
            else:
                self.config.socks_user = username
                self.config.socks_password = saved["proxy"]["password"]
            return {"ok": True, "requires_login": scope == "admin", **self.public()}
