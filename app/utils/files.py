"""文件上传与存储的辅助方法。"""
from __future__ import annotations

from typing import Protocol

from fastapi import UploadFile

from ..storage import save_file


class _FileSaver(Protocol):
    """定义文件保存回调的协议，便于在测试中替换实现。"""

    def __call__(self, content: bytes, content_type: str) -> str:
        """保存文件内容并返回可公开访问的地址。"""
        ...


def save_upload_file(
    upload: UploadFile | None,
    *,
    saver: _FileSaver = save_file,
) -> str | None:
    """在接收到上传文件时写入存储并返回路径。

    该函数统一处理 ``UploadFile`` 的存在性检查、内容读取以及委托
    :func:`app.storage.save_file` 真正落盘的流程。在测试中可以注入自定义
    ``saver``，避免访问真实的存储后端。
    """

    if upload is None or not getattr(upload, "filename", None):
        return None

    content = upload.file.read()
    content_type = upload.content_type or "application/octet-stream"
    return saver(content, content_type)

