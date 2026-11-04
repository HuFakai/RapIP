import logging

import uvicorn

from .api import create_app
from .config import Config


def main():
    config = Config.from_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(
        create_app(config),
        host=config.http_host,
        port=config.http_port,
        workers=1,
        timeout_graceful_shutdown=int(config.settings.drain_timeout + 15),
    )


if __name__ == "__main__":
    main()
