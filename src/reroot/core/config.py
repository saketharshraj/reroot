"""Runtime settings, read from the environment with hackathon defaults."""

import os

PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "reroot-hack-2026")
REGION = os.getenv("REGION", "us-central1")

# Earth Engine image assets only work with BigQuery queries run in the US location.
BQ_DATASET = os.getenv("BQ_DATASET", "reroot")
BQ_LOCATION = "US"

GCS_BUCKET = os.getenv("GCS_BUCKET", f"{PROJECT}-data")

GEMINI_LOCATION = os.getenv("GEMINI_LOCATION", "global")
GEMINI_MODEL_FAST = os.getenv("GEMINI_MODEL_FAST", "gemini-3-flash-preview")
GEMINI_MODEL_PRO = os.getenv("GEMINI_MODEL_PRO", "gemini-3.1-pro-preview")

PUBSUB_ALERTS_TOPIC = os.getenv("PUBSUB_ALERTS_TOPIC", "alerts")

# Demo area: Aravalli belt between Gurugram and Faridabad (degraded scrub, mining and
# development pressure, Mangar Bani sacred grove). [west, south, east, north]
DEMO_AOI_BBOX = [77.05, 28.30, 77.35, 28.55]
# A known intact forest patch inside the AOI, used as the "good site" reference.
DEMO_REFERENCE_POINT = [77.180, 28.410]  # Mangar Bani
