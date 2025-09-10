import os, uuid
from .settings import settings

_s3 = None

def _s3_client():
    global _s3
    if _s3 is None:
        import boto3
        _s3 = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint or None,
            region_name=settings.s3_region or None,
            aws_access_key_id=settings.s3_access_key or None,
            aws_secret_access_key=settings.s3_secret_key or None,
        )
    return _s3

def save_file(content: bytes, content_type: str):
    ext = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(content_type, "")
    key = f"du-photos/{uuid.uuid4().hex}{ext}"

    if settings.storage_driver == "s3":
        s3 = _s3_client()
        s3.put_object(Bucket=settings.s3_bucket, Key=key, Body=content, ContentType=content_type, ACL="public-read")
        base = settings.storage_base_url or settings.s3_endpoint.rstrip("/") + "/" + settings.s3_bucket
        return f"{base}/{key}"
    else:
        base_dir = settings.storage_disk_path
        os.makedirs(os.path.join(base_dir, os.path.dirname(key)), exist_ok=True)
        path = os.path.join(base_dir, key)
        with open(path, "wb") as f:
            f.write(content)
        return f"/uploads/{key}"
