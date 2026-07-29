"""Run MewCode with ``python -m mewcode``."""

import sys

from .app import MewCodeApp
from .client import ConfigError, Settings


def main() -> int:
    try:
        settings = Settings.from_env()
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2

    MewCodeApp(settings).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

