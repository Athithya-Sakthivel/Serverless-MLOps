"""
Container entrypoint.

Reads configuration, configures structured logging, and starts Uvicorn.
"""

from __future__ import annotations

import uvicorn
from utils.config import get_serving_config
from utils.logging import configure_logging


def main() -> int:
    config = get_serving_config()
    configure_logging(level=config.log_level)

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=config.port,
        log_level=config.log_level.lower(),
        access_log=False,
        log_config=None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
