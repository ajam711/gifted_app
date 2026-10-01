#!/usr/bin/env python3
"""Django's command-line utility for administrative tasks."""

import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent


def main() -> None:
    # Repo root so `domain` imports; app dir so `config` and `gifts` import.
    sys.path.insert(0, str(ROOT_DIR))
    sys.path.insert(0, str(APP_DIR))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
