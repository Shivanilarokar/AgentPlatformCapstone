-- The Cloud SQL equivalent of docker/db/init.sql.
--
-- Locally, the postgres Docker image runs that file automatically on first
-- boot (anything mounted into /docker-entrypoint-initdb.d does). Cloud SQL is
-- a managed service - there is no container of ours to mount a file into - so
-- the SAME statements are run once by hand, as the `postgres` user:
--
--   gcloud sql connect forge-db --user=postgres --database=forge
--   \i deploy/bootstrap.sql
--
-- Why it matters: the application must NOT connect as a superuser. A superuser
-- bypasses row-level security silently, and row-level security is what keeps
-- one person's agents and credentials invisible to a colleague in the same
-- company. forge_app is created deliberately without it.
--
--   NOSUPERUSER   cannot bypass anything
--   NOBYPASSRLS   cannot skip row-level security
--   CREATEROLE    needed: every company gets its own Postgres role at sign-up
--                 (app/tenancy/provision.py::create_tenant)
--   NOCREATEDB    one database is enough

-- Replace <APP_PASSWORD> before running; deploy/deploy.sh does it for you.
CREATE ROLE forge_app LOGIN PASSWORD '<APP_PASSWORD>'
    NOSUPERUSER NOBYPASSRLS CREATEROLE NOCREATEDB;

GRANT CONNECT, CREATE, TEMP ON DATABASE forge TO forge_app;

-- The app owns everything it creates inside the database; on Cloud SQL the
-- `public` schema is owned by cloudsqlsuperuser, so hand it over too. The app
-- never uses `public` (every table lives in `platform` or `t_<company>`), but
-- SQLAlchemy connects with it on the search_path.
GRANT USAGE ON SCHEMA public TO forge_app;

\echo 'forge_app created. Verify it cannot bypass row-level security:'
SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcanlogin
FROM pg_roles WHERE rolname IN ('postgres', 'forge_app');
