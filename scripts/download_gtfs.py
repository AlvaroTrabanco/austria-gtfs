import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests


# ============================================================
# CONFIGURATION
# ============================================================

TOKEN_URL = (
    "https://user.mobilitaetsverbuende.at/auth/realms/"
    "dbp-public/protocol/openid-connect/token"
)

API_URL = "https://data.mobilitaetsverbuende.at/api/public/v1"

RELEASE_TAG = "latest"

OUTPUT_DIR = Path("output")
CACHE_DIR = Path("cache")

METADATA_FILE = OUTPUT_DIR / "metadata.json"
CACHED_METADATA_FILE = CACHE_DIR / "metadata.json"


# Dataset IDs verified against the Austrian API response.

FEEDS = {
    "52": {
        "name": "VOR - Eastern Austria",
        "filename": "vor.zip",
    },
    "53": {
        "name": "Verbund Linie - Styria",
        "filename": "styria.zip",
    },
    "54": {
        "name": "Salzburg Verkehr",
        "filename": "salzburg.zip",
    },
    "56": {
        "name": "OOEVV - Upper Austria",
        "filename": "upper-austria.zip",
    },
    "57": {
        "name": "VVT - Tyrol",
        "filename": "tyrol.zip",
    },
    "58": {
        "name": "VMOBIL - Vorarlberg",
        "filename": "vorarlberg.zip",
    },
    "69": {
        "name": "Linz AG",
        "filename": "linz.zip",
    },
}


REQUIRED_GTFS_FILES = {
    "agency.txt",
    "stops.txt",
    "routes.txt",
    "trips.txt",
    "stop_times.txt",
}


# ============================================================
# GITHUB RELEASE HELPERS
# ============================================================

def gh(*args, check=True):
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
    )

    if check and result.returncode != 0:
        raise RuntimeError(
            f"GitHub CLI failed: {' '.join(args)}\n"
            f"{result.stderr}"
        )

    return result


def ensure_release():
    result = gh(
        "release",
        "view",
        RELEASE_TAG,
        check=False,
    )

    if result.returncode == 0:
        print("Existing release found.")
        return

    print("Creating release...")

    gh(
        "release",
        "create",
        RELEASE_TAG,
        "--title",
        "Latest Austrian GTFS feeds",
        "--notes",
        (
            "Automatically updated GTFS feeds from "
            "Mobilitätsverbünde Österreich."
        ),
    )


def load_published_metadata():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    result = gh(
        "release",
        "download",
        RELEASE_TAG,
        "--pattern",
        "metadata.json",
        "--dir",
        str(CACHE_DIR),
        check=False,
    )

    if result.returncode != 0:
        print("No previous metadata found.")
        return {}

    try:
        with CACHED_METADATA_FILE.open(
            "r",
            encoding="utf-8",
        ) as file:
            metadata = json.load(file)

        if not isinstance(metadata, dict):
            raise ValueError("Metadata must be a JSON object.")

        feeds = metadata.get("feeds", {})

        if not isinstance(feeds, dict):
            raise ValueError("Invalid feeds metadata.")

        print("Previous metadata loaded.")

        return feeds

    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Could not read previous metadata: {error}")
        return {}


def upload_asset(filepath):
    print(f"Publishing {filepath.name}...")

    gh(
        "release",
        "upload",
        RELEASE_TAG,
        str(filepath),
        "--clobber",
    )


def save_metadata(metadata):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    document = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "feeds": metadata,
    }

    with METADATA_FILE.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            document,
            file,
            indent=2,
            ensure_ascii=False,
        )


# ============================================================
# AUSTRIAN API
# ============================================================

def authenticate():
    print("Authenticating with Austrian DBP...")

    response = requests.post(
        TOKEN_URL,
        data={
            "client_id": "dbp-public-ui",
            "grant_type": "password",
            "scope": "openid",
            "username": os.environ["DBP_USERNAME"],
            "password": os.environ["DBP_PASSWORD"],
        },
        timeout=60,
    )

    response.raise_for_status()

    token = response.json().get("access_token")

    if not token:
        raise RuntimeError(
            "Authentication returned no access token."
        )

    print("Authentication successful.")

    return token


def get_datasets(token):
    print("Retrieving dataset list...")

    response = requests.get(
        f"{API_URL}/data-sets",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        params={
            "tagFilterModeInclusive": "true",
        },
        timeout=120,
    )

    response.raise_for_status()

    datasets = response.json()

    if not isinstance(datasets, list):
        raise RuntimeError(
            "Unexpected dataset API response."
        )

    return {
        str(dataset["id"]): dataset
        for dataset in datasets
    }


def get_latest_version(dataset):
    versions = [
        version
        for version in dataset.get("activeVersions", [])
        if version.get("active") is True
        and version.get("dataSetVersion")
    ]

    if not versions:
        raise RuntimeError(
            "No active timetable versions available."
        )

    return max(
        versions,
        key=lambda version: (
            int(version.get("year", 0)),
            version["dataSetVersion"].get("created", ""),
        ),
    )


# ============================================================
# DOWNLOAD
# ============================================================

def download_gtfs(token, dataset_id, year, filename):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    filepath = OUTPUT_DIR / filename

    url = (
        f"{API_URL}/data-sets/"
        f"{dataset_id}/{year}/file"
    )
    # Obtain a fresh access token for each GTFS download.
    # Large downloads and GitHub uploads can exceed the
    # lifetime of the original token.

    token = authenticate()

    print(f"Downloading dataset {dataset_id}...")
    print(f"Source: {url}")

    with requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
        },
        stream=True,
        timeout=(30, 300),
    ) as response:

        response.raise_for_status()

        with filepath.open("wb") as file:
            for chunk in response.iter_content(
                chunk_size=1024 * 1024
            ):
                if chunk:
                    file.write(chunk)

    print(
        f"Downloaded {filename}: "
        f"{filepath.stat().st_size / 1024 / 1024:.2f} MiB"
    )

    return filepath


# ============================================================
# VALIDATION
# ============================================================

def validate_gtfs(filepath):
    print(f"Validating {filepath.name}...")

    if not zipfile.is_zipfile(filepath):
        raise RuntimeError(
            "Downloaded file is not a valid ZIP archive."
        )

    with zipfile.ZipFile(filepath) as archive:

        names = set(archive.namelist())

        missing = REQUIRED_GTFS_FILES - names

        if missing:
            raise RuntimeError(
                f"Missing GTFS files: {sorted(missing)}"
            )

        bad_file = archive.testzip()

        if bad_file:
            raise RuntimeError(
                f"Corrupt ZIP entry: {bad_file}"
            )

    print("GTFS validation successful.")


# ============================================================
# PROCESS FEEDS
# ============================================================

def process_feed(
    token,
    datasets,
    dataset_id,
    config,
    published_metadata,
):
    name = config["name"]
    filename = config["filename"]

    print("\n" + "=" * 60)
    print(f"Processing: {name}")
    print("=" * 60)

    dataset = datasets.get(dataset_id)

    if not dataset:
        raise RuntimeError(
            f"Dataset {dataset_id} not found in API."
        )

    version = get_latest_version(dataset)

    year = version["year"]

    version_info = version["dataSetVersion"]

    version_id = str(version_info["id"])

    previous = published_metadata.get(dataset_id, {})

    previous_version = str(
        previous.get("version_id", "")
    )

    print(f"Latest version: {version_id}")
    print(f"Published version: {previous_version or 'None'}")

    if version_id == previous_version:
        print("No update required.")
        return False

    print("New version detected.")

    filepath = download_gtfs(
        token,
        dataset_id,
        year,
        filename,
    )

    validate_gtfs(filepath)

    upload_asset(filepath)

    published_metadata[dataset_id] = {
        "name": name,
        "dataset_id": dataset_id,
        "year": year,
        "version_id": version_id,
        "source_updated": version_info.get("created"),
        "source_filename": (
            version_info.get("file") or {}
        ).get("originalName"),
        "filename": filename,
        "size_bytes": filepath.stat().st_size,
        "published_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "download_url": (
            "https://github.com/"
            f"{os.environ['GITHUB_REPOSITORY']}"
            f"/releases/download/{RELEASE_TAG}/{filename}"
        ),
    }

    # Save and publish metadata only after the ZIP upload
    # has succeeded.

    save_metadata(published_metadata)
    upload_asset(METADATA_FILE)

    print(f"Successfully published {filename}")

    return True


# ============================================================
# MAIN
# ============================================================

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    ensure_release()

    published_metadata = load_published_metadata()

    token = authenticate()

    datasets = get_datasets(token)

    updated = []
    unchanged = []
    failed = []

    for dataset_id, config in FEEDS.items():

        try:
            changed = process_feed(
                token,
                datasets,
                dataset_id,
                config,
                published_metadata,
            )

            if changed:
                updated.append(config["name"])
            else:
                unchanged.append(config["name"])

        except Exception as error:
            print(
                f"ERROR processing {config['name']}: {error}",
                file=sys.stderr,
            )

            failed.append(config["name"])

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    print(f"Updated: {len(updated)}")
    for name in updated:
        print(f"  UPDATED: {name}")

    print(f"Unchanged: {len(unchanged)}")
    for name in unchanged:
        print(f"  UNCHANGED: {name}")

    print(f"Failed: {len(failed)}")
    for name in failed:
        print(f"  FAILED: {name}")

    if failed:
        raise RuntimeError(
            f"{len(failed)} feeds failed."
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)