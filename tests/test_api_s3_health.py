from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.api import routes


def _make_settings(
    endpoint: str = "http://minio:9000",
    access_key: str = "test-access-key",
    secret_key: SecretStr | str = SecretStr("secret"),
    bucket_name: str = "media-bucket",
):
    return SimpleNamespace(
        s3=SimpleNamespace(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            bucket_name=bucket_name,
        )
    )


@pytest.mark.asyncio
async def test_check_s3_health_returns_true_when_bucket_exists(monkeypatch):
    monkeypatch.setattr(routes, "_check_s3_bucket_exists", lambda *args, **kwargs: True)

    settings = _make_settings()
    result = await routes._check_s3_health(settings)

    assert result is True


@pytest.mark.asyncio
async def test_check_s3_health_returns_false_when_config_missing():
    settings = _make_settings(endpoint="", access_key="", secret_key="", bucket_name="")

    result = await routes._check_s3_health(settings)

    assert result is False


@pytest.mark.asyncio
async def test_check_s3_health_returns_false_on_exception(monkeypatch):
    def _raise(*_args, **_kwargs):
        raise RuntimeError("s3 unavailable")

    monkeypatch.setattr(routes, "_check_s3_bucket_exists", _raise)

    settings = _make_settings()
    result = await routes._check_s3_health(settings)

    assert result is False


@pytest.mark.asyncio
async def test_check_s3_health_returns_false_on_timeout(monkeypatch):
    def _slow(*_args, **_kwargs):
        import time

        time.sleep(0.2)
        return True

    monkeypatch.setattr(routes, "_check_s3_bucket_exists", _slow)
    monkeypatch.setattr(routes, "S3_HEALTH_TIMEOUT_SECONDS", 0.05)

    settings = _make_settings()
    result = await routes._check_s3_health(settings)

    assert result is False
