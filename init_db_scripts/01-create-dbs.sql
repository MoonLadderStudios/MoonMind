-- Create temporal databases and role
CREATE USER temporal WITH ENCRYPTED PASSWORD 'temporal';
CREATE DATABASE temporal;
CREATE DATABASE temporal_visibility;
GRANT ALL PRIVILEGES ON DATABASE temporal TO temporal;
GRANT ALL PRIVILEGES ON DATABASE temporal_visibility TO temporal;

-- Create keycloak database and role
CREATE USER keycloak WITH ENCRYPTED PASSWORD 'keycloak';
CREATE DATABASE keycloak;
GRANT ALL PRIVILEGES ON DATABASE keycloak TO keycloak;
