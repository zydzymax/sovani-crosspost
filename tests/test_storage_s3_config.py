from app.adapters.storage_s3 import S3Storage


def test_s3_bucket_prefers_s3_bucket_name(monkeypatch):
    monkeypatch.setenv("S3_BUCKET_NAME", "bucket-from-name")
    monkeypatch.setenv("S3_BUCKET", "legacy-bucket")

    storage = S3Storage()

    assert storage.bucket == "bucket-from-name"


def test_s3_bucket_falls_back_to_legacy_env(monkeypatch):
    monkeypatch.delenv("S3_BUCKET_NAME", raising=False)
    monkeypatch.setenv("S3_BUCKET", "legacy-bucket")

    storage = S3Storage()

    assert storage.bucket == "legacy-bucket"


def test_s3_bucket_uses_default_when_env_absent(monkeypatch):
    monkeypatch.delenv("S3_BUCKET_NAME", raising=False)
    monkeypatch.delenv("S3_BUCKET", raising=False)
    monkeypatch.delenv("S3_ENDPOINT", raising=False)
    monkeypatch.delenv("S3_ACCESS_KEY", raising=False)
    monkeypatch.delenv("S3_SECRET_KEY", raising=False)

    storage = S3Storage()

    assert storage.bucket == "crosspost-media"
