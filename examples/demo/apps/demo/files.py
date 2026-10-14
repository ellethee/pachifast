"""Upload, list, download and delete files for pachi-uploader (multipart field ``file``, one part per file)."""

import json
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select

from pachifast.auth.dependencies import CurrentUserDep
from pachifast.auth.schemas import UserRead
from pachifast.database import AliasedBaseModel
from pachifast.database.engine import CoreSessionDep
from pachifast.database.pagination import paginate
from pachifast.i18n import api_error
from pachifast.routing.router_factory import get_router
from pachifast.schemas.responses import PageParams, PaginatedResponse

from .models import DemoFile
from .settings import ALLOWED_UPLOAD_TYPES, settings, upload_dir

router = get_router(name="demo/files")


class FileRead(AliasedBaseModel):
    """A stored file as returned by the API."""

    id: uuid.UUID
    name: str
    content_type: str
    size: int
    uploaded_by: uuid.UUID | None = None
    created_at: datetime


class FilePageParams(PageParams):
    search_fields = ["name"]


@router.post("/upload", status_code=201, permissions=["demo.upload"], summary="Upload one or more files")
async def upload(
    session: CoreSessionDep,
    user: Annotated[UserRead, CurrentUserDep],
    file: Annotated[list[UploadFile], File(description="One part per file")],
    metadata: Annotated[str | None, Form(description="JSON {filename: meta} sent by pachi-uploader")] = None,
):
    """Store the files. Limits: ``demo_max_upload_bytes`` each, types png, jpeg, pdf and plain text.

    Nothing is stored if any of the files is refused. The name on disk is the generated id, never the client's.
    """
    meta = json.loads(metadata) if metadata else {}
    saved: list[DemoFile] = []
    contents: list[bytes] = []
    for part in file:
        if part.content_type not in ALLOWED_UPLOAD_TYPES:
            raise api_error(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                "demo.file_type_not_allowed",
                loc=("body", "file"),
                types=", ".join(sorted(ALLOWED_UPLOAD_TYPES.values())),
            )
        content = await part.read(settings.demo_max_upload_bytes + 1)
        if len(content) > settings.demo_max_upload_bytes:
            raise api_error(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "demo.file_too_large",
                loc=("body", "file"),
                max_mb=settings.demo_max_upload_bytes // (1024 * 1024),
            )
        contents.append(content)
        saved.append(
            DemoFile(
                name=(part.filename or "file")[:255],
                content_type=part.content_type or "application/octet-stream",
                size=len(content),
                uploaded_by=user.id,
            )
        )
    for row, content in zip(saved, contents, strict=True):
        (upload_dir() / str(row.id)).write_bytes(content)
        session.add(row)
    await session.commit()
    return {
        "files": [{"id": str(row.id), "name": row.name, "size": row.size, "meta": meta.get(row.name)} for row in saved]
    }


@router.get("", response_model=PaginatedResponse[FileRead], permissions=["demo.view"], summary="List files")
async def list_files(session: CoreSessionDep, params: Annotated[FilePageParams, Query()]):
    if not params.orderBy:
        params.orderBy = "-created_at"
    return await paginate(session, DemoFile, params)


async def _get(session: CoreSessionDep, file_id: uuid.UUID) -> DemoFile:
    row = (await session.execute(select(DemoFile).where(DemoFile.id == file_id))).scalars().first()
    if row is None:
        raise api_error(status.HTTP_404_NOT_FOUND, "demo.file_not_found", loc=("path", "file_id"))
    return row


@router.get("/{file_id}/download", permissions=["demo.view"], summary="Download a file")
async def download(file_id: uuid.UUID, session: CoreSessionDep):
    """The file with its original name in ``Content-Disposition``."""
    row = await _get(session, file_id)
    return FileResponse(upload_dir() / str(row.id), media_type=row.content_type, filename=row.name)


@router.delete("/{file_id}", status_code=204, skip_envelope=True, permissions=["demo.upload"], summary="Delete a file")
async def delete_file(file_id: uuid.UUID, session: CoreSessionDep):
    row = await _get(session, file_id)
    (upload_dir() / str(row.id)).unlink(missing_ok=True)
    await session.delete(row)
    await session.commit()
