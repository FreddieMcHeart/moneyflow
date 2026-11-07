#!/bin/sh
# Runs once, on an empty data dir. Creates Metabase's own role and database, and the read-only data role.
# Passwords come from the container environment (compose passes them from .env), never literals.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v mb_password="$MB_DB_PASSWORD" -v reader_password="$MF_READER_PASSWORD" <<'SQL'
CREATE ROLE metabase LOGIN PASSWORD :'mb_password';
CREATE DATABASE metabase OWNER metabase;
CREATE ROLE mf_reader LOGIN PASSWORD :'reader_password';
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v reader=mf_reader -v db="$POSTGRES_DB" -f /opt/moneyflow/reader.sql
