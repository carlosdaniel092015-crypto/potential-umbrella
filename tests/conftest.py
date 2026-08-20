import hashlib
import hmac
import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
TEST_WEBHOOK_SECRET = "test-webhook-secret"


def sign_payload(raw_body: bytes, secret: str = TEST_WEBHOOK_SECRET) -> str:
    """Misma firma que genera YCloud (HMAC-SHA256 hex sobre el body crudo)."""
    return hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / "ycloud" / name).read_text(encoding="utf-8"))


@pytest.fixture
def ycloud_inbound_text() -> dict:
    return load_fixture("inbound_text.json")


@pytest.fixture
def ycloud_inbound_audio() -> dict:
    return load_fixture("inbound_audio.json")


@pytest.fixture
def ycloud_status_update() -> dict:
    return load_fixture("status_update.json")
