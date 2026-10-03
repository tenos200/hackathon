-- Create the two login users for the group roles from migrations/0001.
-- Run once as the admin identity with psql variables (never commit passwords):
--
--   psql "$ADMIN_DATABASE_URL" -v api_password="$(cat api.pw)" -v publisher_password="$(cat pub.pw)" \
--        -f scripts/create_login_roles.sql
--
-- On Supabase, use the connection string shown for each custom role in the
-- dashboard's connection panel (direct or session pooler); pooler usernames
-- carry the project reference suffix. Do not invent hostnames.

CREATE ROLE atlas_api_login LOGIN PASSWORD :'api_password' IN ROLE atlas_api
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT;
ALTER ROLE atlas_api_login SET default_transaction_read_only = on;
ALTER ROLE atlas_api_login SET search_path = atlas;
ALTER ROLE atlas_api_login SET statement_timeout = '5s';
-- NOINHERIT plus an explicit SET ROLE keeps the session to exactly the group's rights.
ALTER ROLE atlas_api_login SET role = 'atlas_api';

CREATE ROLE atlas_publisher_login LOGIN PASSWORD :'publisher_password' IN ROLE atlas_publisher
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT;
ALTER ROLE atlas_publisher_login SET search_path = atlas;
ALTER ROLE atlas_publisher_login SET role = 'atlas_publisher';
