#!/usr/bin/env bash
set -o errexit
pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate --noinput
python manage.py ensure_admin
# Copies any photos still stored in the database to Supabase Storage (does nothing once everything is moved,
# and does not touch the database copy). Never blocks a deploy.
python manage.py migrate_images_to_storage || echo "migrate_images_to_storage skipped (check SUPABASE_* env vars)"
