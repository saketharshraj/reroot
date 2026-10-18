"""Initialised clients for Earth Engine, Gemini on Vertex AI and BigQuery."""

from functools import lru_cache

import ee
from google import genai
from google.genai import types
from google.cloud import bigquery

from . import config
from .auth import get_credentials

_ee_ready = False


def init_ee(high_volume: bool = False) -> None:
    """Initialise Earth Engine once per process.

    Use high_volume=True for request-serving code (many small parallel calls). Batch
    exports must use the standard endpoint.
    """
    global _ee_ready
    if _ee_ready:
        return
    url = "https://earthengine-highvolume.googleapis.com" if high_volume else None
    ee.Initialize(get_credentials(), project=config.PROJECT, opt_url=url)
    _ee_ready = True


@lru_cache
def gemini() -> genai.Client:
    return genai.Client(
        vertexai=True,
        project=config.PROJECT,
        location=config.GEMINI_LOCATION,
        credentials=get_credentials(),
    )


def gen_config(**kw) -> types.GenerateContentConfig:
    """GenerateContentConfig with the project's default thinking level."""
    return types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_level=config.GEMINI_THINKING), **kw)


@lru_cache
def firestore_db():
    """Firestore (Native, us-central1): the real-time feed Studio's caretaker phone listens to."""
    from google.cloud import firestore
    return firestore.Client(project=config.PROJECT, credentials=get_credentials())


@lru_cache
def bq() -> bigquery.Client:
    return bigquery.Client(project=config.PROJECT, credentials=get_credentials(), location=config.BQ_LOCATION)
