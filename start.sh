#!/bin/sh
# Railway start command. The volume is mounted by now, so migrations run
# against the real database before gunicorn takes requests.
set -e
cd "$(dirname "$0")/app"
python manage.py migrate --noinput
python manage.py collectstatic --noinput
exec gunicorn config.wsgi --bind "0.0.0.0:${PORT:-8000}" --workers 2
