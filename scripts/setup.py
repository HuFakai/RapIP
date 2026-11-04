"""Generate local credentials without printing secrets. Never overwrites existing configuration."""

import json
import os
import re
import secrets
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    env = root / ".env"
    if not env.exists():
        values = {
            "RAPIP_ADMIN_USER": "admin",
            "RAPIP_ADMIN_PASSWORD": secrets.token_urlsafe(24),
            "RAPIP_SOCKS_USER": "admin",
            "RAPIP_SOCKS_PASSWORD": secrets.token_urlsafe(24),
            "RAPIP_ADMIN_TOKEN": secrets.token_urlsafe(32),
            "WEBSHARE_API_KEY": "",
        }
        fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            template = (root / ".env.example").read_text()
            for key, value in values.items():
                template = re.sub(
                    rf"^{key}=.*$", lambda match, value=value: f"{key}={value}", template, flags=re.M
                )
            output.write(template)
    config = root / "config/accounts.json"
    if not config.exists():
        config.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump([], output)
    print("Local files ready. Read .env for credentials; add an account in the console or accounts.json.")


if __name__ == "__main__":
    main()
