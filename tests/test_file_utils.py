import os
from io import BytesIO

os.environ.setdefault("DATABASE_URL", "sqlite:///test.db")

from app.utils.files import save_upload_file


class DummyUpload:
    def __init__(self, *, content: bytes, filename: str, content_type: str | None = None):
        self.file = BytesIO(content)
        self.filename = filename
        self.content_type = content_type


def test_save_upload_file_returns_none_when_missing_upload():
    assert save_upload_file(None) is None

    empty_filename = DummyUpload(content=b"data", filename="")
    assert save_upload_file(empty_filename) is None


def test_save_upload_file_reads_content_and_uses_saver():
    saved: dict[str, bytes | str] = {}

    def fake_saver(content: bytes, content_type: str) -> str:
        saved["content"] = content
        saved["content_type"] = content_type
        return "stored/path"

    upload = DummyUpload(content=b"binary", filename="photo.png", content_type="image/png")

    result = save_upload_file(upload, saver=fake_saver)

    assert result == "stored/path"
    assert saved["content"] == b"binary"
    assert saved["content_type"] == "image/png"
