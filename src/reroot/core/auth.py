"""One way to get Google credentials, wherever the code runs.

Order of preference:
1. GOOGLE_APPLICATION_CREDENTIALS_JSON: a service-account key as raw JSON or base64, for
   hosts outside Google Cloud (Render). Set it as a secret env var, never commit it.
2. Application Default Credentials: the attached service account on Cloud Run, or
   `gcloud auth application-default login` on a laptop.
3. Dev fallback: borrow the gcloud CLI's access token, re-fetched from gcloud whenever it
   nears expiry, so long-running local jobs keep working.
"""

import base64
import datetime as dt
import json
import logging
import os
import subprocess

import google.auth
from google.auth import credentials as ga_credentials
from google.auth.exceptions import DefaultCredentialsError
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/earthengine",
]


def _key_from_env():
    raw = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip()
    if not raw:
        return None
    info = json.loads(raw if raw.startswith("{") else base64.b64decode(raw))
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


def get_credentials():
    creds = _key_from_env()
    if creds:
        return creds
    try:
        creds, _ = google.auth.default(scopes=SCOPES)
        # User credentials need a quota project for some APIs (e.g. Text-to-Speech).
        if isinstance(creds, Credentials) and not creds.quota_project_id:
            creds = creds.with_quota_project(_project())
        return creds
    except DefaultCredentialsError:
        pass
    log.warning("No ADC found; falling back to the gcloud CLI token.")
    return GcloudCredentials(_project())


def _project() -> str:
    from reroot.core import config
    return config.PROJECT


class GcloudCredentials(ga_credentials.CredentialsWithQuotaProject):
    """Access tokens borrowed from the gcloud CLI, re-fetched when they near expiry.
    (oauth2 Credentials' refresh_handler is lost when copied by with_quota_project.)"""

    def __init__(self, quota_project_id: str | None = None):
        super().__init__()
        self._quota_project_id = quota_project_id

    def refresh(self, request):
        self.token, self.expiry = _gcloud_token()

    def with_quota_project(self, quota_project_id):
        return GcloudCredentials(quota_project_id)


def _gcloud_token(request=None, scopes=None):
    token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
    # gcloud tokens last ~60 min; refresh well before that. google-auth wants naive UTC.
    expiry = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(minutes=45)
    return token, expiry
