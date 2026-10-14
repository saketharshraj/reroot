"""One way to get Google credentials, wherever the code runs.

Order of preference:
1. Application Default Credentials: the attached service account on Cloud Run, or
   `gcloud auth application-default login` on a laptop.
2. Dev fallback: borrow the gcloud CLI's access token. It expires after about an hour,
   so long-running local servers should use ADC instead.
"""

import logging
import subprocess

import google.auth
from google.auth.exceptions import DefaultCredentialsError
from google.oauth2.credentials import Credentials

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/earthengine",
]


def get_credentials():
    try:
        creds, _ = google.auth.default(scopes=SCOPES)
        return creds
    except DefaultCredentialsError:
        pass
    log.warning("No ADC found; falling back to the gcloud CLI token (valid ~1h).")
    token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
    return Credentials(token)
