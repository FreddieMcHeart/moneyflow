-- Read-only role for Metabase's data connection. psql variables: reader (role name), db (database name).
-- The role itself (LOGIN, password) is created by the caller; this file only sets what it may do.
GRANT CONNECT ON DATABASE :"db" TO :"reader";
GRANT USAGE ON SCHEMA public TO :"reader";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"reader";
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT ON TABLES TO :"reader";
