#!/bin/sh
set -e

echo "Waiting for database..."
# Simple wait if using postgres
if [ "$USE_POSTGRES" = "true" ]; then
  until python -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('$DB_HOST', int('$DB_PORT'))); s.close()" 2>/dev/null; do
    sleep 1
  done
fi

echo "Running migrations..."
python manage.py migrate --noinput

echo "Starting application..."
exec "$@"
