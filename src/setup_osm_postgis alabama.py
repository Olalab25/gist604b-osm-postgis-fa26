"""
OSM PostGIS Setup Workflow - Student Implementation

Complete the function in this file.
Use the notebook to build and run the workflow.

📋 FUNCTION TO IMPLEMENT IN THIS FILE:
=====================================
✅ Function: setup_osm_postgis() → notebooks/setup_osm_postgis.ipynb
"""

import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import psycopg2
from psycopg2 import sql
import requests

# Function: Setup PostGIS + Load OSM Shapefile Data

def setup_osm_postgis(
    osm_url: str,
    db_name: str = "osm_db",
    user: str = "postgres",
    password: str = "postgres",
    host: str = "localhost",
    port: int = 5432,
    data_dir: Optional[Path] = None,
    load_shapefiles: Optional[list[str]] = None
) -> None:
    """
    Create a PostGIS database and load Geofabrik shapefile data.

    This function performs a complete workflow:
    - Connect to PostgreSQL
    - Create a new database
    - Enable PostGIS extension
    - Download shapefile data from Geofabrik
    - Unzip shapefile data
    - Load shapefiles into PostGIS using shp2pgsql

    Args:
        osm_url: URL to Geofabrik shapefile ZIP
        db_name: Name of the database to create
        user: PostgreSQL username
        password: PostgreSQL password
        host: Database host
        port: Database port
        data_dir: Optional directory to store downloaded OSM data
        load_shapefiles: Optional list of shapefile layer names to load

    Returns:
        None

    Example:
        >>> setup_osm_postgis(
        ...     osm_url="https://download.geofabrik.de/north-america/us/alabama-latest-free.shp.zip", db_name="alabama", load_shapefiles=["places_a", "pois"]
        ... )
    """

    # Step 1: Setup data directory
    data_dir = Path(data_dir) if data_dir is not None else Path("../data") / db_name
    data_dir.mkdir(parents=True, exist_ok=True)

    filename = Path(urlparse(osm_url).path).name
    if not filename:
        raise ValueError("osm_url must include a ZIP filename")
    file_path = data_dir / filename

    # Step 2: Download shapefile ZIP data
    if not file_path.exists():
        print("Downloading shapefile data...")
        print("URL:", osm_url)
        partial_path = file_path.with_name(f"{file_path.name}.part")
        download_complete = False
        try:
            with requests.get(osm_url, stream=True, timeout=300) as response:
                response.raise_for_status()
                file_size = int(response.headers.get("content-length", 0))
                if file_size > 0:
                    print(f"File size: {file_size / (1024 * 1024):.1f} MB")

                downloaded = 0
                with partial_path.open("wb") as file:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            file.write(chunk)
                            downloaded += len(chunk)
                            if file_size > 0:
                                progress = downloaded / file_size * 100
                                print(
                                    f"\rProgress: {progress:.1f}%",
                                    end="",
                                    flush=True,
                                )

            os.replace(partial_path, file_path)
            download_complete = True
        finally:
            if not download_complete:
                partial_path.unlink(missing_ok=True)

        print("\nDownload complete")
        print("Saved to:", file_path)
    else:
        print("File already exists:")
        print(file_path)

    # Step 3: Connect to PostgreSQL and create the working database
    connection = psycopg2.connect(
        dbname="postgres",
        user=user,
        password=password,
        host=host,
        port=port,
    )
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s;",
                (db_name,),
            )
            if cursor.fetchone() is None:
                cursor.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name))
                )
                print(f"Database '{db_name}' created")
            else:
                print(f"Database '{db_name}' already exists")
    finally:
        connection.close()

    # Step 4: Enable PostGIS in the working database
    connection = psycopg2.connect(
        dbname=db_name,
        user=user,
        password=password,
        host=host,
        port=port,
    )
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
            cursor.execute("SELECT PostGIS_version();")
            result = cursor.fetchone()
            if result is None:
                raise RuntimeError("Could not retrieve the PostGIS version")
            print("PostGIS version:", result[0])
    finally:
        connection.close()

    # Step 5: Unzip shapefile data
    extract_path = data_dir / "shapefiles"
    if not extract_path.exists():
        print("Extracting shapefiles...")
        with tempfile.TemporaryDirectory(
            prefix=".shapefiles-", dir=data_dir
        ) as temporary_path:
            temporary_extract_path = Path(temporary_path)
            with zipfile.ZipFile(file_path, "r") as zip_file:
                root = temporary_extract_path.resolve()
                for member in zip_file.infolist():
                    member_path = (root / member.filename).resolve()
                    if member_path != root and root not in member_path.parents:
                        raise ValueError(
                            f"Unsafe path in shapefile archive: {member.filename}"
                        )
                zip_file.extractall(temporary_extract_path)
            temporary_extract_path.replace(extract_path)
        print("Extraction complete")
        print("Extracted to:", extract_path)
    else:
        print("Extracted folder already exists:", extract_path)

    # Step 6: Load selected shapefiles into PostGIS
    env = os.environ.copy()
    env["PGPASSWORD"] = password

    for shp_path in sorted(extract_path.glob("*.shp")):
        filename = shp_path.stem
        table_name = filename.replace("gis_osm_", "").replace("_free_1", "")
        if load_shapefiles is not None and table_name not in load_shapefiles:
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name) is None:
            raise ValueError(f"Invalid shapefile table name: {table_name!r}")

        print(f"\nLoading {table_name} from {filename}...")
        shp2pgsql_command = [
            "shp2pgsql",
            "-d",
            "-I",
            "-s",
            "4326",
            str(shp_path),
            f"public.{table_name}",
        ]
        psql_command = [
            "psql",
            "-h",
            host,
            "-p",
            str(port),
            "-U",
            user,
            "-d",
            db_name,
            "-v",
            "ON_ERROR_STOP=1",
        ]

        with tempfile.TemporaryFile() as shp2pgsql_stderr:
            with tempfile.TemporaryFile() as psql_stderr:
                shp2pgsql_process = subprocess.Popen(
                    shp2pgsql_command,
                    stdout=subprocess.PIPE,
                    stderr=shp2pgsql_stderr,
                )
                try:
                    psql_process = subprocess.Popen(
                        psql_command,
                        stdin=shp2pgsql_process.stdout,
                        stdout=subprocess.DEVNULL,
                        stderr=psql_stderr,
                        env=env,
                    )
                except OSError:
                    if shp2pgsql_process.stdout is not None:
                        shp2pgsql_process.stdout.close()
                    shp2pgsql_process.terminate()
                    shp2pgsql_process.wait()
                    raise

                if shp2pgsql_process.stdout is not None:
                    shp2pgsql_process.stdout.close()
                psql_returncode = psql_process.wait()
                shp2pgsql_returncode = shp2pgsql_process.wait()

                if psql_returncode != 0 or shp2pgsql_returncode != 0:
                    errors = []
                    for process_name, error_file in (
                        ("psql", psql_stderr),
                        ("shp2pgsql", shp2pgsql_stderr),
                    ):
                        error_file.seek(0)
                        error_text = error_file.read().decode(
                            "utf-8", errors="replace"
                        ).strip()
                        if error_text:
                            errors.append(f"{process_name}: {error_text}")
                    raise RuntimeError(
                        f"Failed to load {table_name} "
                        f"(psql exit {psql_returncode}, "
                        f"shp2pgsql exit {shp2pgsql_returncode})"
                        + (f": {'; '.join(errors)}" if errors else "")
                    )

        print(f"{table_name} loaded successfully")

    print("Database connection closed")
