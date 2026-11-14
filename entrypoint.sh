#!/bin/sh
set -e

if [ "${SKIP_MIGRATIONS}" != "1" ]; then
  python manage.py migrate --noinput
fi

if [ "${SKIP_COLLECTSTATIC}" != "1" ]; then
  python manage.py collectstatic --noinput --clear
fi

if [ "${SKIP_SEED_PROVIDERS}" != "1" ]; then
  python manage.py seed_providers
fi

exec "$@"
