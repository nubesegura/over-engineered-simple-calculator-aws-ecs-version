-- Least-privilege database identity of the services.
-- calc_app_rw: group role that cannot log in; select and insert on calculations only.
-- calc_app: the login user, a member of the group. It is created WITHOUT a password: the
-- migration job sets it at run time from the application secret (never stored in a file).
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'calc_app_rw') THEN
        CREATE ROLE calc_app_rw NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'calc_app') THEN
        CREATE ROLE calc_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE IN ROLE calc_app_rw;
    END IF;
END
$$;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT SELECT, INSERT ON calculations TO calc_app_rw;
