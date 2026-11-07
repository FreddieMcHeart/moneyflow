-- Read-only role for Metabase's data connection. psql variables: reader (role name), db (database name).
-- The role itself (LOGIN, password) is created by the caller; this file only sets what it may do.
-- SELECT and nothing else: PUBLIC may neither connect nor create temp tables; each role that needs the
-- database is granted CONNECT explicitly (postgres runs migrations, the reader serves Metabase).
REVOKE CONNECT, TEMPORARY ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"db" TO postgres;
GRANT CONNECT ON DATABASE :"db" TO :"reader";
GRANT USAGE ON SCHEMA public TO :"reader";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"reader";
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT ON TABLES TO :"reader";
