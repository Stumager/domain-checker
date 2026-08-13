"""Logging configuration.

A single stream handler on the root logger, so records from this package and
from libraries end up in one place. Under a process manager that stream is the
captured stdout; locally it is the console.
"""

import logging
import sys

DEFAULT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def configure_logging(level: str = "INFO"):
    """Attach one handler to the root logger. Repeat calls only adjust level.

    create_app() runs once per process in production but many times across a
    test session, so the handler must not stack up.
    """
    global _configured

    resolved = getattr(logging, str(level or "INFO").upper(), logging.INFO)
    root = logging.getLogger()

    if _configured:
        root.setLevel(resolved)
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(DEFAULT_FORMAT, DEFAULT_DATEFMT))
    root.addHandler(handler)
    root.setLevel(resolved)

    # urllib3 logs a line per connection; at DEBUG that buries everything else
    # during a scan, which opens thousands of connections.
    logging.getLogger("urllib3").setLevel(max(resolved, logging.INFO))

    _configured = True
