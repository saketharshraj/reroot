"""Field alerts: monitoring -> Pub/Sub topic `alerts` -> push to /pubsub/alerts -> Gemini writes
the message (English + caretaker's language) -> Cloud TTS voice note -> Cloud Storage +
BigQuery `alerts` -> Telegram, if a bot is configured. Contract: contracts/alert_message.schema.json.
"""

import base64
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from functools import lru_cache

import requests
from google.cloud import bigquery, pubsub_v1, storage, texttospeech

from reroot.api import monitor
from reroot.core import config
from reroot.core.auth import get_credentials
from reroot.core.clients import bq, firestore_db, gemini, gen_config

log = logging.getLogger(__name__)
DS = f"{config.PROJECT}.{config.BQ_DATASET}"
SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}

# What to do, per alert type. Fixed text so the advice is vetted, not generated.
ACTIONS = {
    "dry_spell": "Water the saplings twice this week, early in the morning, and mulch around each base.",
    "vegetation_decline": "Walk the block this week, count dead or dry saplings, and send a photo of the worst patch.",
}


@lru_cache
def _publisher():
    return pubsub_v1.PublisherClient(credentials=get_credentials())


@lru_cache
def _bucket():
    return storage.Client(project=config.PROJECT, credentials=get_credentials()).bucket(config.GCS_BUCKET)


@lru_cache
def _tts():
    return texttospeech.TextToSpeechClient(credentials=get_credentials())


def build_alerts(checks: list[dict]) -> list[dict]:
    out = []
    for c in checks:
        if not c["action_needed"]:
            continue
        primary = max(c["alerts"], key=lambda a: SEVERITY_ORDER[a["severity"]])
        w = c.get("weather", {})
        out.append({
            "alert_id": uuid.uuid4().hex[:16],
            "plantation_id": c["plantation_id"],
            "type": primary["type"],
            "severity": primary["severity"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "evidence": {
                "plantation_name": c["name"],
                "age_years": c["age_years"],
                "ndvi_now": c["ndvi_recent"],
                "ndvi_expected": c["ndvi_expected"],
                "establishment_index": c["establishment_index"],
                "precip_last_14d_mm": w.get("precip_last_14d_mm"),
                "precip_next_10d_mm": w.get("precip_next_10d_mm"),
                "soil_moisture_vs_normal_pct": w.get("soil_moisture_vs_normal_pct"),
                "also": [a["type"] for a in c["alerts"] if a["type"] != primary["type"]],
                "sources": c["sources"] + w.get("sources", []),
            },
            "recipient": {"chat_id": None, "language": c.get("language") or "hi-IN", "name": c.get("caretaker_name")},
        })
    return out


def evaluate_and_publish(plantation_id: str | None = None) -> dict:
    checks = monitor.check(plantation_id)
    alerts = build_alerts(checks)
    inline = os.getenv("ALERTS_INLINE") == "1"
    topic = _publisher().topic_path(config.PROJECT, config.PUBSUB_ALERTS_TOPIC)
    for a in alerts:
        if inline:  # local dev without a push subscription
            process(a)
        else:
            _publisher().publish(topic, json.dumps(a).encode()).result(timeout=30)
    return {"checked": [_summary(c) for c in checks], "alerts": alerts, "delivery": "inline" if inline else "pubsub"}


def _summary(c: dict) -> dict:
    return {k: c.get(k) for k in ("plantation_id", "name", "status", "establishment_index", "age_years",
                                   "alerts", "weather")}


def compose(alert: dict) -> dict:
    ev = alert["evidence"]
    lang = alert["recipient"].get("language") or "hi-IN"
    actions = " ".join(ACTIONS[t] for t in [alert["type"], *ev.get("also", [])] if t in ACTIONS)
    prompt = f"""Write a short field alert for the caretakers of a tree plantation in India.
Plantation: {ev['plantation_name']} (planted {ev.get('age_years')} years ago).
Facts you may use (do not invent any other numbers):
- greenness is {round((ev.get('establishment_index') or 0) * 100)}% of the target for its age
- rain in the last 14 days: {ev.get('precip_last_14d_mm')} mm
- rain expected in the next 10 days: {ev.get('precip_next_10d_mm')} mm
- soil moisture vs normal for this time of year: {ev.get('soil_moisture_vs_normal_pct')}%
Alert type: {alert['type']} ({alert['severity']}).
What they must do (keep this advice, rephrase simply): {actions}
Rules: plain spoken words a villager understands, no jargon (never say NDVI), at most 45 words
per message, start with the plantation name. message_local must be in the language for
locale {lang}, written in its native script."""
    resp = gemini().models.generate_content(
        model=config.GEMINI_MODEL_FAST,
        contents=prompt,
        config=gen_config(
            response_mime_type="application/json",
            response_schema={"type": "object", "properties": {
                "title_en": {"type": "string"}, "message_en": {"type": "string"},
                "message_local": {"type": "string"}}, "required": ["title_en", "message_en", "message_local"]},
        ),
    )
    return json.loads(resp.text)


def synthesize(text: str, language: str, encoding=texttospeech.AudioEncoding.MP3) -> bytes:
    voice_names = [f"{language}-Chirp3-HD-Kore", None]  # prefer the natural voice, fall back to default
    for name in voice_names:
        try:
            voice = texttospeech.VoiceSelectionParams(language_code=language, name=name) if name else \
                texttospeech.VoiceSelectionParams(language_code=language, ssml_gender=texttospeech.SsmlVoiceGender.FEMALE)
            return _tts().synthesize_speech(input=texttospeech.SynthesisInput(text=text), voice=voice,
                                            audio_config=texttospeech.AudioConfig(audio_encoding=encoding)).audio_content
        except Exception as e:  # noqa: BLE001
            log.warning("TTS voice %s failed: %s", name, e)
    raise RuntimeError("text-to-speech failed")


def process(alert: dict) -> dict:
    msg = compose(alert)
    lang = alert["recipient"].get("language") or "hi-IN"
    audio = synthesize(msg["message_local"], lang)
    blob = _bucket().blob(f"alerts/{alert['alert_id']}.mp3")
    blob.upload_from_string(audio, content_type="audio/mpeg")
    delivered = _send_telegram(alert, msg, lang)
    row = {
        "alert_id": alert["alert_id"], "plantation_id": alert["plantation_id"], "created_at": alert["created_at"],
        "type": alert["type"], "severity": alert["severity"], "evidence": json.dumps(alert["evidence"]),
        "title": msg["title_en"], "message_en": msg["message_en"], "message_local": msg["message_local"],
        "audio_uri": f"gs://{config.GCS_BUCKET}/{blob.name}",
        "delivered_at": datetime.now(timezone.utc).isoformat() if delivered else None,
    }
    errors = bq().insert_rows_json(f"{DS}.alerts", [row])
    if errors:
        raise RuntimeError(errors)
    mirror_to_firestore(row)
    return row


def mirror_to_firestore(row: dict) -> None:
    """Studio's caretaker phone subscribes to `alerts` in Firestore, so a new alert shows up the moment it's
    written. BigQuery stays the record for analysis; this is best effort and never blocks the alert."""
    try:
        firestore_db().collection("alerts").document(row["alert_id"]).set({
            "alert_id": row["alert_id"], "plantation_id": row["plantation_id"],
            "created_at": datetime.fromisoformat(row["created_at"]) if isinstance(row["created_at"], str) else row["created_at"],
            "type": row["type"], "severity": row["severity"], "title": row["title"],
            "message_en": row["message_en"], "message_local": row["message_local"],
            "has_audio": bool(row.get("audio_uri")), "delivered": row.get("delivered_at") is not None,
        })
    except Exception:  # noqa: BLE001
        log.exception("firestore mirror failed for alert %s", row.get("alert_id"))


def _send_telegram(alert: dict, msg: dict, lang: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = alert["recipient"].get("chat_id") or os.getenv("TELEGRAM_DEMO_CHAT_ID")
    if not (token and chat):
        return False
    base = f"https://api.telegram.org/bot{token}"
    try:
        requests.post(f"{base}/sendMessage", json={"chat_id": chat, "text": f"{msg['message_local']}\n\n{msg['message_en']}"}, timeout=15)
        ogg = synthesize(msg["message_local"], lang, texttospeech.AudioEncoding.OGG_OPUS)
        requests.post(f"{base}/sendVoice", data={"chat_id": chat}, files={"voice": ("alert.ogg", ogg, "audio/ogg")}, timeout=30)
        return True
    except requests.RequestException as e:
        log.warning("telegram send failed: %s", e)
        return False


def verify_push(authorization: str | None) -> None:
    """Accept only pushes signed by our Pub/Sub invoker account (when an audience is configured)."""
    audience = os.getenv("PUBSUB_PUSH_AUDIENCE")
    if not audience:
        return
    from google.auth.transport import requests as grequests
    from google.oauth2 import id_token
    if not authorization or not authorization.startswith("Bearer "):
        raise PermissionError("missing bearer token")
    claims = id_token.verify_oauth2_token(authorization.split(" ", 1)[1], grequests.Request(), audience=audience)
    if claims.get("email") != f"pubsub-invoker@{config.PROJECT}.iam.gserviceaccount.com":
        raise PermissionError("unexpected caller")


def handle_push(envelope: dict) -> dict:
    alert = json.loads(base64.b64decode(envelope["message"]["data"]))
    return process(alert)


def list_alerts(plantation_id: str | None = None, limit: int = 20) -> list[dict]:
    rows = bq().query(
        f"""SELECT alert_id, plantation_id, created_at, type, severity, title, message_en, message_local,
                   evidence, audio_uri, delivered_at
            FROM `{DS}.alerts` WHERE (@pid IS NULL OR plantation_id = @pid)
            ORDER BY created_at DESC LIMIT {int(limit)}""",
        job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("pid", "STRING", plantation_id)]),
    ).result()
    return [{
        "alert_id": r.alert_id, "plantation_id": r.plantation_id, "created_at": r.created_at.isoformat(),
        "type": r.type, "severity": r.severity, "title": r.title, "message_en": r.message_en,
        "message_local": r.message_local, "evidence": json.loads(r.evidence) if isinstance(r.evidence, str) else r.evidence,
        "has_audio": bool(r.audio_uri), "delivered": r.delivered_at is not None,
    } for r in rows]


def audio(alert_id: str) -> bytes:
    return _bucket().blob(f"alerts/{alert_id}.mp3").download_as_bytes()
