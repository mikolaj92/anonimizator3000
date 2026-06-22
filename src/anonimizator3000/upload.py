from dataclasses import dataclass
from email import policy
from email.parser import BytesParser

from starlette.requests import Request


@dataclass(frozen=True)
class UploadedDocument:
    filename: str
    content_type: str
    data: bytes
    replacement_style: str | None = None


class UploadError(ValueError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


async def read_multipart_document(
    request: Request, *, max_file_bytes: int, max_body_bytes: int
) -> UploadedDocument:
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("multipart/form-data"):
        raise UploadError("Wyślij plik przez formularz multipart.")

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            content_length_value = int(content_length)
        except ValueError as error:
            raise UploadError("Nieprawidłowy nagłówek Content-Length.") from error
        if content_length_value < 0:
            raise UploadError("Nieprawidłowy nagłówek Content-Length.")
        if content_length_value > max_body_bytes:
            raise UploadError("Upload jest za duży.", status_code=413)

    body = await _read_limited_body(request, max_body_bytes)
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + content_type.encode("utf-8") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    )

    if not message.is_multipart():
        raise UploadError("Nieprawidłowy multipart.")

    document: UploadedDocument | None = None
    replacement_style: str | None = None
    for part in message.iter_parts():
        disposition = part.get("content-disposition", "")
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        if "form-data" not in disposition:
            continue
        if name == "style" and not filename:
            replacement_style = _decode_text_field(part)
            continue
        if name != "document" or not filename:
            continue

        data = part.get_payload(decode=True) or b""
        if len(data) > max_file_bytes:
            raise UploadError("Plik jest za duży.", status_code=413)
        document = UploadedDocument(
            filename=filename,
            content_type=part.get_content_type() or "application/octet-stream",
            data=data,
        )

    if document is None:
        raise UploadError("Brak pliku w polu document.")

    return UploadedDocument(
        filename=document.filename,
        content_type=document.content_type,
        data=document.data,
        replacement_style=replacement_style,
    )


def _decode_text_field(part: object) -> str | None:
    payload = part.get_payload(decode=True) if hasattr(part, "get_payload") else None
    if not isinstance(payload, bytes):
        return None
    return payload.decode("utf-8", errors="replace").strip() or None


async def _read_limited_body(request: Request, max_body_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_body_bytes:
            raise UploadError("Upload jest za duży.", status_code=413)
        chunks.append(chunk)
    return b"".join(chunks)
