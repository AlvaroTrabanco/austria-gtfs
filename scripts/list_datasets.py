import json
import os
import sys
from pathlib import Path

import requests


TOKEN_URL = (
    "https://user.mobilitaetsverbuende.at/auth/realms/"
    "dbp-public/protocol/openid-connect/token"
)

DATASETS_URL = (
    "https://data.mobilitaetsverbuende.at/api/public/v1/data-sets"
)

OUTPUT_DIR = Path("site")
OUTPUT_FILE = OUTPUT_DIR / "datasets.json"


def authenticate():
    username = os.environ["DBP_USERNAME"]
    password = os.environ["DBP_PASSWORD"]

    response = requests.post(
        TOKEN_URL,
        data={
            "client_id": "dbp-public-ui",
            "grant_type": "password",
            "scope": "openid",
            "username": username,
            "password": password,
        },
        timeout=60,
    )

    response.raise_for_status()

    token = response.json().get("access_token")

    if not token:
        raise RuntimeError("Authentication succeeded but no token was returned.")

    return token


def get_datasets(token):
    response = requests.get(
        DATASETS_URL,
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

    return response.json()


def main():
    print("Authenticating with Austrian DBP...")

    token = authenticate()

    print("Authentication successful.")

    print("Retrieving datasets...")

    datasets = get_datasets(token)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(
            datasets,
            file,
            ensure_ascii=False,
            indent=2,
        )

    print(f"Dataset response saved to {OUTPUT_FILE}")

    if isinstance(datasets, list):
        print(f"Found {len(datasets)} datasets.")
    else:
        print("API returned a JSON object. Inspect datasets.json for details.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)