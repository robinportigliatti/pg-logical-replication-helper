import os
import subprocess
import datetime
import sys
import time
import psycopg
from psycopg.errors import Error
import re
import secrets
import string


def generate_password(length=32):
    characters = string.ascii_letters + string.digits
    password = ''.join(secrets.choice(characters) for _ in range(length))
    return password


def get_db_connection(conn_string):
    try:
        conn = psycopg.connect(conn_string)
        return conn
    except psycopg.Error as e:
        print(f"Error {e} on connection string {conn_string}", file=sys.stderr)
        sys.exit(1)


def execute_query(conn_string, query, fetch=True):
    conn = get_db_connection(conn_string)
    if conn is None:
        return None

    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(query)
            if fetch:
                results = cur.fetchall()
                return results
    except Error as e:
        print(
            f"Error {e} with query '{query}' on host '{conn_string}'", file=sys.stderr)
        return None
    finally:
        conn.close()


def run_dump_restore_pre(conn_sender_string, db_schemas, conn_receiver_string):
    command = [
        "pg_dump",
        "-d", conn_sender_string,
        "-Fp",
        "-T", "public.spatial_ref_sys",
        "--no-acl",
        "--no-owner",
        f"--section=pre-data"
    ]
    for schema in db_schemas:
        command.append("-n")
        command.append(schema)

    print(f" dump section pre-data")
    print(" ".join(command))

    dump = subprocess.Popen(command, stdout=subprocess.PIPE, text=True)

    # Connection to the database
    with psycopg.connect(conn_receiver_string) as conn:
        with conn.cursor() as cur:
            try:
                print(f"pg_restore pre begin")
                dump_queries = dump.stdout.read()
                result = dump_queries.replace("CREATE SCHEMA public;", "")
                cur.execute(result)

                # Commit of the changes
                conn.commit()

            except Exception as e:
                print(f"An error occurred: {e}")
                conn.rollback()

    print(f"run_dump_restore_pre end")


def run_dump_restore_post_onlypk(conn_sender_string, db_schemas, conn_receiver_string):
    command = [
        "pg_dump",
        "-d", conn_sender_string,
        "-Fp",
        "-T", "public.spatial_ref_sys",
        "--no-acl",
        f"--section=post-data"
    ]
    for schema in db_schemas:
        command.append("-n")
        command.append(schema)

    print(f" dump section post-data")
    print(" ".join(command))

    dump = subprocess.Popen(command, stdout=subprocess.PIPE, text=True)

    # Connection to the database
    with psycopg.connect(conn_receiver_string) as conn:
        with conn.cursor() as cur:
            try:
                print(f"pg_restore post début")
                dump_str = dump.stdout.read()
                splitlines = dump_str.splitlines()
                current_query = ""
                for i in range(0, len(splitlines)-1):
                    if re.match(r'.*ADD CONSTRAINT.*PRIMARY KEY.*', splitlines[i]):
                        current_query = current_query + \
                            splitlines[i-1] + splitlines[i]

                cur.execute(current_query)

                # Commit of changes
                conn.commit()

            except Exception as e:
                print(f"Une erreur s'est produite : {e}")
                conn.rollback()

    print(f"run_dump_restore_post fin")


def run_dump_restore_post_without_pk(conn_sender_string, db_schemas, conn_receiver_string):
    command = [
        "pg_dump",
        "-d", conn_sender_string,
        "-Fp",
        "-T", "public.spatial_ref_sys",
        "--no-acl",
        f"--section=post-data"
    ]
    for schema in db_schemas:
        command.append("-n")
        command.append(schema)

    print(f" dump section post-data without primary keys")
    print(" ".join(command))

    dump = subprocess.Popen(command, stdout=subprocess.PIPE, text=True)

    # Connection to the database
    with psycopg.connect(conn_receiver_string) as conn:
        with conn.cursor() as cur:
            try:
                print(f"pg_restore post (without PK) début")
                dump_str = dump.stdout.read()
                splitlines = dump_str.splitlines()
                current_query = ""
                line_before = ""
                for i in range(0, len(splitlines)-1):
                    # Skip primary key constraints
                    if re.match(r'.*ADD CONSTRAINT.*PRIMARY KEY.*', splitlines[i]):
                        line_before = ""
                        continue
                    else:
                        current_query += line_before
                        line_before = splitlines[i] + "\n"

                current_query += line_before
                cur.execute(current_query)
                conn.commit()

                print(f"pg_restore post (without PK) terminé avec succès")

            except Exception as e:
                print(f"Une erreur s'est produite : {e}")
                conn.rollback()

        conn.rollback()
    print(f"run_dump_restore_post_without_pk fin")


# Initialisation of replication
script_name = os.path.basename(__file__)
connection_primary = sys.argv[1]
db_name_primary = sys.argv[2]
connection_secondary = sys.argv[3]
db_name_secondary = sys.argv[4] if len(sys.argv) > 4 else db_name_primary
b_create_db = True if len(sys.argv) > 5 and sys.argv[5] == "true" else False
schema_excluded_list = sys.argv[6] if len(
    sys.argv) > 6 else 'information_schema'
schema_excluded = schema_excluded_list.split(',')

# Random replication password
replication_password = generate_password()

# Get the primary db connexion string fron environment
connection_primary_full = os.environ.get('CONN_DB_PRIMARY_FULL')


print(f"START SCRIPT")
print(f"python {script_name} {connection_primary} {db_name_primary} {connection_secondary} {db_name_secondary}")

# Logs settings
today = datetime.datetime.now().strftime("%Y%m%d-%H-%M-%S")
print(f"\n\nStarting script : {script_name} at {today}\n")


date_start = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
unique_name = f"{db_name_primary}_{date_start}"

# Retrieve DB Infos
if schema_excluded is None:
    schema_query = "SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT ILIKE 'pg_%'"
else:
    schema_excluded_str = ",".join(
        [f"'{schema}'" for schema in schema_excluded])
    schema_query = f"SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT ILIKE 'pg_%' AND schema_name NOT IN ({schema_excluded_str});"
results = execute_query(connection_primary, schema_query)
db_schemas = None
if results and results[0]:
    db_schemas = [schema[0] for schema in results]

results = execute_query(
    connection_primary, f"SELECT pg_size_pretty(pg_database_size('{db_name_primary}'))")
db_size = None
if results and results[0]:
    db_size = results[0][0]

results = execute_query(
    connection_primary, "SELECT count(*) from pg_stat_user_tables")
db_tables = None
if results and results[0]:
    db_tables = results[0][0]

print(
    f"$today - Starting pg_dump from server {connection_primary} database {db_name_primary} {db_size}")
print(f"db_schemas : {db_schemas}")
print(f"db_size : {db_size}")
print(f"db_tables : {db_tables}")

# # Check if replication is already started
query = f"select subslotname from pg_subscription where subname like 'subscription_{db_name_secondary}_%'"
print(f"psql \"{connection_secondary}\" --no-align -tc \"{query}\"")
results = execute_query(connection_secondary, query)
if results is None:
    print(
        f"Error on query {query} on host {connection_secondary}", file=sys.stderr)

else:

    # Check if replication is already started
    if not results:

        print("Replication not in progress")
        print(f"{today} - Starting process : {script_name} {connection_primary} {db_name_primary} - {connection_secondary} database {db_name_secondary}")

        print(f" create replication user on {connection_primary}")
        # Verify if replication user already exist
        results = execute_query(
            connection_primary, "SELECT count(rolname) FROM pg_roles WHERE rolname ='replication'")
        if results and results[0][0] > 0:
            print(f"user replication already exist")
        else:
            execute_query(connection_primary, f"CREATE USER replication LOGIN ENCRYPTED PASSWORD '{replication_password}'; "
                          f"ALTER ROLE replication WITH REPLICATION", fetch=False)
            print(f"user replication created")

        for schema in db_schemas:
            # Grant privileges on the schema
            execute_query(connection_primary, f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO replication; "
                          f"GRANT USAGE ON SCHEMA {schema} TO replication", fetch=False)
            print(f"GRANT right on {schema} to replication user")

        if b_create_db:
            # Create database on new host
            print(
                f" Create database {db_name_secondary} on host {connection_secondary}")
            execute_query(connection_secondary,
                          # to fix with the right owner
                          f"CREATE DATABASE {db_name_secondary} WITH OWNER 'admin'", fetch=False)

            print(
                f" Create extension pg_stat_statements on db {db_name_secondary} on host {connection_secondary}")
            execute_query(connection_secondary,
                          f"CREATE EXTENSION pg_stat_statements", fetch=False)

        # Section pre-data
        run_dump_restore_pre(connection_primary,
                             db_schemas, connection_secondary)

        # Section post-data
        run_dump_restore_post_onlypk(
            connection_primary, db_schemas, connection_secondary)

        # Create publication on primary
        print(
            f"Create publication on primary {connection_primary} database {db_name_primary}")
        execute_query(connection_primary,
                      f"CREATE PUBLICATION publication_{unique_name};", fetch=False)
        # Add tables to publication
        results = execute_query(
            connection_primary, f"select schemaname, relname from pg_stat_user_tables where relname <> 'spatial_ref_sys'")
        if results:
            for schema, table in results:
                print(
                    f"Add table {schema}.{table} to publication {unique_name}")
                execute_query(connection_primary,
                              f"ALTER PUBLICATION publication_{unique_name} ADD TABLE {schema}.{table};", fetch=False)

        # Create subscription on secondary
        subscription_name = f"subscription_{unique_name}"
        print(
            f"Create subscription on secondary {connection_secondary} database {db_name_secondary}")
        execute_query(connection_secondary,
                      f"CREATE SUBSCRIPTION {subscription_name} CONNECTION '{connection_primary_full}' PUBLICATION publication_{unique_name} with (copy_data=true, create_slot=true, enabled=true, slot_name='{subscription_name}');", fetch=False)

    # Check if replication is still running
    results = execute_query(
        connection_secondary, f"select subname from pg_subscription where subname like 'subscription_{db_name_primary}_%'")
    if results:
        subscription_name = results[0][0]
        # Wait for the first step of replication to complete
        while True:
            print(
                f"Check if first step of replication is done - db {db_name_secondary} on host {connection_secondary} from {connection_primary} database {db_name_primary}")

            query = "select a.* from pg_subscription_rel a inner join pg_class on srrelid=pg_class.oid where relname <> 'spatial_ref_sys' and srsubstate <> 'r';"
            results = execute_query(connection_secondary, query)
            if not results:
                break

            try:
                execute_query(connection_secondary, query)
                print(
                    "The first step of logical replication is not finished - retrying later")

                # Log progress
                progress_query = """
                with ready as (
                    select count(a.*) as ready from pg_subscription_rel a 
                    inner join pg_class on srrelid=pg_class.oid 
                    where relname <> 'spatial_ref_sys' and srsubstate = 'r'
                ), 
                total as (
                    select count(a.*) as total from pg_subscription_rel a 
                    inner join pg_class on srrelid=pg_class.oid 
                    where relname <> 'spatial_ref_sys'
                ) 
                select * from ready, total;
                """
                results = execute_query(connection_secondary, progress_query)
                print(
                    f"Replication progress : {results[0][0]}/{results[0][1]}")

                time.sleep(10)
            except:
                # If the query fails, it means there are no more tables in non-ready state
                break

        # Disable subscription
        print(f"Disable subscription on {connection_secondary}")
        query = f"ALTER SUBSCRIPTION {subscription_name} DISABLE;"
        execute_query(connection_secondary, query, fetch=False)

        # Restore post section without primary keys
        print("Restore post section - without primary key")
        run_dump_restore_post_without_pk(
            connection_primary, db_schemas, connection_secondary)

        # Enable subscription
        print(f"Enable subscription on {connection_secondary}")
        query = f"ALTER SUBSCRIPTION {subscription_name} ENABLE;"
        execute_query(connection_secondary, query, fetch=False)

        end_time = datetime.datetime.now().strftime("%Y%m%d-%H-%M-%S")
        print(f"end={end_time}")
    else:
        print("No replication running, exiting")

print("end")