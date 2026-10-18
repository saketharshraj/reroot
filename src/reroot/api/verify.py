"""Caretaker photo check: GPS (EXIF or supplied) vs plantation polygon in BigQuery, plus
Gemini vision for sapling counts and condition. Photo -> Cloud Storage, result -> BigQuery."""

import io
import json
import uuid
from datetime import datetime, timezone

from google.cloud import bigquery
from google.genai import types
from PIL import Image

from reroot.api.alerts import _bucket
from reroot.core import config
from reroot.core.clients import bq, gemini, gen_config

DS = f"{config.PROJECT}.{config.BQ_DATASET}"
LOCATION_TOLERANCE_M = 150

SCHEMA = {
    "type": "object",
    "properties": {
        "is_plantation_photo": {"type": "boolean", "description": "outdoor photo of planted saplings or young trees"},
        "live_saplings_est": {"type": "integer"},
        "dead_or_dry_est": {"type": "integer"},
        "health": {"type": "string", "enum": ["good", "fair", "poor", "unclear"]},
        "signs": {"type": "array", "items": {"type": "string"}, "description": "visible evidence, e.g. tree guards, watering basins, wilting"},
        "summary": {"type": "string", "description": "one or two plain sentences for the field coordinator"},
        "confidence": {"type": "number"},
    },
    "required": ["is_plantation_photo", "live_saplings_est", "dead_or_dry_est", "health", "summary", "confidence"],
}

PROMPT = """You are checking a field photo sent by a caretaker of a tree plantation in the Aravalli
hills, India. Count the saplings / young trees you can actually see and judge whether they are
alive (green leaves) or dead/dry. Only report what is visible; if the photo is not of a
plantation, say so. Keep the summary factual and short."""


def _exif_gps(data: bytes) -> tuple[float, float] | None:
    try:
        gps = Image.open(io.BytesIO(data)).getexif().get_ifd(0x8825)
        if not gps or 2 not in gps or 4 not in gps:
            return None

        def deg(v, ref):
            d = float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
            return -d if ref in ("S", "W") else d

        return deg(gps[4], gps.get(3, "E")), deg(gps[2], gps.get(1, "N"))  # lon, lat
    except Exception:  # noqa: BLE001 - no usable EXIF is a normal case
        return None


def verify(data: bytes, mime: str, plantation_id: str, lon: float | None = None, lat: float | None = None) -> dict:
    gps = (lon, lat) if lon is not None and lat is not None else _exif_gps(data)
    location = {"source": "none", "match": None, "distance_m": None}
    if gps:
        row = list(bq().query(
            f"""SELECT ST_DISTANCE(ST_GEOGPOINT(@lon, @lat), geom) AS d, name
                FROM `{DS}.plantations` WHERE plantation_id = @pid""",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("lon", "FLOAT64", gps[0]),
                bigquery.ScalarQueryParameter("lat", "FLOAT64", gps[1]),
                bigquery.ScalarQueryParameter("pid", "STRING", plantation_id)])).result())
        if row:
            location = {"source": "exif" if lon is None else "device", "match": row[0].d <= LOCATION_TOLERANCE_M,
                        "distance_m": round(row[0].d)}

    resp = gemini().models.generate_content(
        model=config.GEMINI_MODEL_FAST,
        contents=[types.Part.from_bytes(data=data, mime_type=mime), PROMPT],
        config=gen_config(response_mime_type="application/json", response_schema=SCHEMA),
    )
    vision = json.loads(resp.text)

    vid = uuid.uuid4().hex[:16]
    ext = {"image/png": "png", "image/webp": "webp"}.get(mime, "jpg")
    blob = _bucket().blob(f"photos/{plantation_id}/{vid}.{ext}")
    blob.upload_from_string(data, content_type=mime)
    now = datetime.now(timezone.utc).isoformat()
    bq().insert_rows_json(f"{DS}.photo_verifications", [{
        "verification_id": vid, "plantation_id": plantation_id, "gcs_uri": f"gs://{config.GCS_BUCKET}/{blob.name}",
        "photo_lat": gps[1] if gps else None, "photo_lon": gps[0] if gps else None, "taken_at": now,
        "location_match": location["match"], "live_saplings_est": vision["live_saplings_est"],
        "dead_saplings_est": vision["dead_or_dry_est"], "confidence": vision["confidence"],
        "notes": vision["summary"], "model": config.GEMINI_MODEL_FAST, "created_at": now,
    }])
    try:  # field evidence log in Firestore, next to the alerts it answers
        from reroot.core.clients import firestore_db
        firestore_db().collection("photo_verifications").document(vid).set({
            "plantation_id": plantation_id, "created_at": datetime.now(timezone.utc), "location_match": location["match"],
            "live_saplings_est": vision["live_saplings_est"], "dead_or_dry_est": vision["dead_or_dry_est"],
            "health": vision.get("health"), "summary": vision["summary"], "confidence": vision["confidence"]})
    except Exception:  # noqa: BLE001 - never fail a verification over the mirror
        pass
    return {"verification_id": vid, "plantation_id": plantation_id, "location": location, **vision,
            "sources": [f"{DS}.plantations", "Gemini vision", "Firestore photo_verifications"]}
