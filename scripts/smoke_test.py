"""Check every Google Cloud dependency from this machine and print PASS/FAIL.

    uv run python scripts/smoke_test.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from reroot.core import config  # noqa: E402
from reroot.core.auth import get_credentials  # noqa: E402
from reroot.core.clients import bq, gemini, init_ee  # noqa: E402


def check(name, fn):
    try:
        print(f"PASS  {name}: {fn()}")
        return True
    except Exception as e:
        print(f"FAIL  {name}: {str(e).splitlines()[0][:220]}")
        return False


def earth_engine():
    import ee
    init_ee()
    return f"Sentinel-2 scenes over demo AOI in Sep 2026 = " + str(
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(ee.Geometry.Rectangle(config.DEMO_AOI_BBOX))
        .filterDate("2026-09-01", "2026-10-01").size().getInfo())


def bigquery():
    from google.cloud import bigquery as b
    ds = b.Dataset(f"{config.PROJECT}.{config.BQ_DATASET}")
    ds.location = config.BQ_LOCATION
    bq().create_dataset(ds, exists_ok=True)
    return list(bq().query("SELECT 1 AS ok").result())[0].ok and f"dataset {config.BQ_DATASET} ready"


def gemini_call(model):
    def run():
        r = gemini().models.generate_content(model=model, contents="Reply with exactly: pong")
        return f"{model} -> {r.text.strip()!r}"
    return run


def pubsub():
    from google.api_core.exceptions import AlreadyExists
    from google.cloud import pubsub_v1
    pub = pubsub_v1.PublisherClient(credentials=get_credentials())
    topic = pub.topic_path(config.PROJECT, config.PUBSUB_ALERTS_TOPIC)
    try:
        pub.create_topic(name=topic)
    except AlreadyExists:
        pass
    return f"topic {config.PUBSUB_ALERTS_TOPIC} ready"


if __name__ == "__main__":
    print(f"project={config.PROJECT}\n")
    results = [
        check("Earth Engine", earth_engine),
        check("BigQuery", bigquery),
        check("Gemini (fast)", gemini_call(config.GEMINI_MODEL_FAST)),
        check("Gemini (pro)", gemini_call(config.GEMINI_MODEL_PRO)),
        check("Pub/Sub", pubsub),
    ]
    sys.exit(0 if all(results) else 1)
