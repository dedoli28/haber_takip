import os
import sys

import pytest

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if KOK not in sys.path:
    sys.path.insert(0, KOK)

import market_data_service  # noqa: E402
import redis_store  # noqa: E402


@pytest.fixture(autouse=True)
def temiz_ortam(monkeypatch):
    """Her test: bos surec ici onbellek, Redis YOK (bellek yedegi), piyasa
    ortam degiskenleri temiz."""
    for k in (
        "MARKET_DATA_PROVIDER",
        "MARKET_DATA_API_KEY",
        "MARKET_DATA_ALLOW_MOCK",
        "MARKET_DATA_MAX_CALLS_PER_MIN",
        "VERCEL_ENV",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(redis_store, "_BASE_URL", "")
    monkeypatch.setattr(redis_store, "_TOKEN", "")
    market_data_service.sifirla()
    yield
    market_data_service.sifirla()


@pytest.fixture
def istemci():
    from fastapi.testclient import TestClient

    import app as uygulama

    uygulama._hisse_kumesi_onbellek.update(zaman=0.0, kume=frozenset())
    return TestClient(uygulama.app)
