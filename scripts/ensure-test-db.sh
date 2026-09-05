#!/bin/sh
set -eu

test_database=incident_ops_test
database_user=${POSTGRES_USER:-incident_ops}

existing_database=$(psql -U "$database_user" -d postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname = 'incident_ops_test'")

if [ "$existing_database" != "1" ]; then
  createdb -U "$database_user" "$test_database"
fi
