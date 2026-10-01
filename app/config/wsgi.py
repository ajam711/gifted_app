import os
import sys
from pathlib import Path

from django.core.wsgi import get_wsgi_application

APP_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = APP_DIR.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(APP_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()
