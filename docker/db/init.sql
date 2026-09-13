-- Runs once, when the Postgres volume is first created.
--
-- The app must NOT connect as the superuser: superusers bypass row-level
-- security, and RLS is what keeps one person's rows invisible to a colleague.
-- forge_app owns everything it creates, so FORCE ROW LEVEL SECURITY applies to
-- it; it cannot escalate. The superuser `forge` is kept for pgAdmin only.
CREATE ROLE forge_app LOGIN PASSWORD 'forge_app' NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB;
GRANT CONNECT, CREATE, TEMP ON DATABASE forge TO forge_app;
