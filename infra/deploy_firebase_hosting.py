"""Deploy Studio's built files to Firebase Hosting (https://reroot-hack-2026.web.app) via the Hosting REST API.

    npm run build --prefix studio      # with VITE_API_BASE_URL / VITE_FIREBASE_CONFIG set
    CLOUDSDK_ACTIVE_CONFIG_NAME=reroot uv run python infra/deploy_firebase_hosting.py studio/dist

Uses the gcloud login (no Firebase CLI, so it can never pick up a different Firebase account).
"""

import gzip
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import requests

SITE = "reroot-hack-2026"
API = "https://firebasehosting.googleapis.com/v1beta1"


def token() -> str:
    return subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, text=True, check=True).stdout.strip()


def main(dist: str) -> None:
    root = Path(dist)
    h = {"Authorization": f"Bearer {token()}", "x-goog-user-project": SITE, "Content-Type": "application/json"}
    config = {
        "rewrites": [{"glob": "**", "path": "/index.html"}],  # single-page app
        "headers": [{"glob": "/assets/**", "headers": {"Cache-Control": "public, max-age=31536000, immutable"}},
                    {"glob": "/index.html", "headers": {"Cache-Control": "no-cache"}}],
    }
    v = requests.post(f"{API}/sites/{SITE}/versions", headers=h, json={"config": config}, timeout=60)
    v.raise_for_status()
    version = v.json()["name"]

    files, blobs = {}, {}
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        gz = gzip.compress(f.read_bytes(), mtime=0)
        digest = hashlib.sha256(gz).hexdigest()
        files["/" + f.relative_to(root).as_posix()] = digest
        blobs[digest] = gz
    pop = requests.post(f"{API}/{version}:populateFiles", headers=h, json={"files": files}, timeout=60)
    pop.raise_for_status()
    need, upload_url = pop.json().get("uploadRequiredHashes", []), pop.json().get("uploadUrl")
    for digest in need:
        r = requests.post(f"{upload_url}/{digest}", headers={"Authorization": h["Authorization"], "Content-Type": "application/octet-stream"},
                          data=blobs[digest], timeout=120)
        r.raise_for_status()
    fin = requests.patch(f"{API}/{version}?update_mask=status", headers=h, json={"status": "FINALIZED"}, timeout=60)
    fin.raise_for_status()
    rel = requests.post(f"{API}/sites/{SITE}/releases?versionName={version}", headers=h, json={"message": "Studio"}, timeout=60)
    rel.raise_for_status()
    print(json.dumps({"files": len(files), "uploaded": len(need), "release": rel.json().get("name"),
                      "url": f"https://{SITE}.web.app"}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "studio/dist")
