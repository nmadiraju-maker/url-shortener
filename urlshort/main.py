"""Process entry point for uvicorn:  uvicorn urlshort.main:app_factory --factory

create_app() is a library function: on bad configuration it raises ConfigError, which is right for
tests and other callers. A server process should instead fail the way operators expect: one
structured log line saying exactly what is wrong, then a non-zero exit, with no traceback (a bad
configuration is not a crash). Uvicorn does not catch errors from the factory itself, so without this
wrapper Python would print a raw multi-line traceback that log pipelines cannot parse.
"""

from __future__ import annotations

import logging
import sys

from fastapi import FastAPI

from .api import create_app
from .config import ConfigError
from .logging_setup import configure_logging, shutdown_logging

EXIT_CONFIG_ERROR = 2
log = logging.getLogger("urlshort.main")


def app_factory() -> FastAPI:
    try:
        return create_app()
    except ConfigError as exc:
        configure_logging()   # may not have happened yet if loading the settings themselves failed
        log.critical("refusing to start: invalid configuration", extra={"problems": exc.problems})
        shutdown_logging()    # flush the queue before the process exits
        sys.exit(EXIT_CONFIG_ERROR)
