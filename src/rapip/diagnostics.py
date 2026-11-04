"""Read-only supplier diagnostics: never allocates a binding or calls Replace API."""

import argparse
import asyncio
import json

import aiohttp

from .config import Config
from .models import Node, uid
from .provider import ProviderError, Webshare
from .socks import probe_node


async def run(list_only: bool):
    config = Config.from_env()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
        for account in config.accounts():
            provider = Webshare(account, config.key(account), session)
            try:
                rows = await provider.list_nodes()
            except ProviderError as error:
                print(json.dumps({"account_id": account.account_id, "error": str(error)}, ensure_ascii=False))
                continue
            print(
                json.dumps(
                    {
                        "account_id": account.account_id,
                        "count": len(rows),
                        "mode": "list-only" if list_only else "read-only-probe",
                    }
                )
            )
            if list_only:
                continue
            for row in rows:
                if not row.get("proxy_address"):
                    continue
                node = Node(
                    uid(),
                    account.account_id,
                    str(row.get("id", "")),
                    row["proxy_address"],
                    int(row["port"]),
                    row["username"],
                    row["password"],
                    row.get("country_code", ""),
                )
                healthy = await probe_node(node, 5, config.egress_proxy)
                print(
                    json.dumps(
                        {"ip": node.ip, "country": node.country, "probe_ok": healthy, "replace_calls": 0}
                    )
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list-only", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.list_only))
