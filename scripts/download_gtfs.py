import json
import os
import sys
import zipfile
from pathlib import Path

import requests


TOKEN_URL = (
    "https://user.mobilitaetsverbuende.at/auth/realms/"
    "dbp-public/protocol/openid-connect/token"
)

API_URL = "https://data.mobilitaetsverbuende.at/api/public/v1"

DATASET_ID = "69"
OUTPUT_DIR = Path("output")
OUTPUT_FILE = OUTPUT_DIR / "linz.zip"


def authenticate():
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
        raise RuntimeError("Authentication returned no access token.")

    return token


def get_dataset(token):
    response = requests.get(
        f"{API_URL}/data-sets",
        headers={"Authorization": f"Bearer {token}"},
        params={"tagFilterModeInclusive": "true"},
        timeout=120,
    )

    response.raise_for_status()

    datasets = response.json()

    for dataset in datasets:
        if str(dataset.get("id")) == DATASET_ID:
            return dataset

    raise RuntimeError(f"Dataset {DATASET_ID} not found.")


def get_latest_version(dataset):
    versions = dataset.get("activeVersions", [])

    if not versions:
        raise RuntimeError("No active versions found.")

    return max(
        versions,
        key=lambda version: (
            int(version.get("year", 0)),
            version.get("dataSetVersion", {}).get("created", ""),
        ),
    )


def download_gtfs(token, year):
    url = f"{API_URL}/data-sets/{DATASET_ID}/{year}/file"

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Downloading: {url}")

    with requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        stream=True,
        timeout=(30, 300),
    ) as response:

        response.raise_for_status()

        with OUTPUT_FILE.open("wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    file.write(chunk)


def validate_gtfs():
    if not zipfile.is_zipfile(OUTPUT_FILE):
        raise RuntimeError("Downloaded file is not a valid ZIP.")

    required_files = {
        "agency.txt",
        "stops.txt",
        "routes.txt",
        "trips.txt",
        "stop_times.txt",
    }

    with zipfile.ZipFile(OUTPUT_FILE) as archive:
        names = {
            Path(name).name
            for name in archive.namelist()
        }

        missing = required_files - names

        if missing:
            raise RuntimeError(
                f"Missing required GTFS files: {sorted(missing)}"
            )

        bad_file = archive.testzip()

        if bad_file:
            raise RuntimeError(f"Corrupt ZIP entry: {bad_file}")

    print("GTFS validation successful.")


def main():
    print("Authenticating...")

    token = authenticate()

    print("Retrieving dataset metadata...")

    dataset = get_dataset(token)

    version = get_latest_version(dataset)

    year = version["year"]
    version_info = version["dataSetVersion"]

    print(f"Dataset: {dataset['nameEn']}")
    print(f"Year: {year}")
    print(f"Version: {version_info['id']}")
    print(f"Source file: {version_info['file']['originalName']}")

    download_gtfs(token, year)

    validate_gtfs()

    metadata = {
        "dataset_id": DATASET_ID,
        "dataset_name": dataset["nameEn"],
        "year": year,
        "version_id": version_info["id"],
        "source_updated": version_info["created"],
        "source_filename": version_info["file"]["originalName"],
        "filename": "linz.zip",
    }

    with (OUTPUT_DIR / "metadata.json").open(
        "w", encoding="utf-8"
    ) as file:
        json.dump(metadata, file, indent=2)

    print(f"Successfully downloaded {OUTPUT_FILE}")
    print(f"Size: {OUTPUT_FILE.stat().st_size:,} bytes")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)