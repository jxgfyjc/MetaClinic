"""Artifact storage: validated uploads of audio / lab images / tongue images.

Security + correctness rules
  * Type decided from CONTENT (magic bytes), never from the client's filename/Content-Type.
  * Size and duration limits enforced; images are decoded with Pillow (a file that is not a real image is
    rejected), EXIF-rotated, and a ≤1280px JPEG thumbnail is generated for model input / fast display.
  * Stored under random keys in `objects_v3/` — never served by path; access goes through authenticated
    endpoints that check the artifact's owner (patient intake session or staff permission).
  * Content hash dedup per owner: re-uploading the same file returns the existing artifact (no duplicate jobs).
"""
from __future__ import annotations

import io
import secrets
import struct
import wave
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.config import get_settings
from server.db.models import Artifact
from server.domain.enums import ArtifactKind, ProcStatus
from server.domain.ids import sha256_hex
from server.errors import Unprocessable

IMAGE_KINDS = {"lab_image", "tongue_image", "image"}
_EXT = {"wav": ".wav", "mp3": ".mp3", "flac": ".flac", "mp4": ".m4a", "webm": ".webm", "ogg": ".ogg",
        "jpeg": ".jpg", "png": ".png", "webp": ".webp"}


def sniff(b: bytes) -> str | None:
    """Container/format from magic bytes."""
    if len(b) < 12:
        return None
    if b[:4] == b"RIFF" and b[8:12] == b"WAVE":
        return "wav"
    if b[:4] == b"fLaC":
        return "flac"
    if b[:3] == b"ID3" or (b[0] == 0xFF and (b[1] & 0xE0) == 0xE0):
        return "mp3"
    if b[4:8] == b"ftyp":
        return "mp4"
    if b[:4] == b"\x1aE\xdf\xa3":
        return "webm"
    if b[:4] == b"OggS":
        return "ogg"
    if b[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp"
    return None


def _probe_wav(b: bytes) -> float | None:
    try:
        with wave.open(io.BytesIO(b), "rb") as w:
            return w.getnframes() / float(w.getframerate()) if w.getframerate() else None
    except Exception:
        return None


def _probe_av(b: bytes) -> float | None:
    try:
        import av  # type: ignore
        with av.open(io.BytesIO(b)) as c:
            if c.duration:
                return c.duration / 1_000_000
    except Exception:
        return None
    return None


def store(db: Session, *, kind: str, data: bytes, original_name: str, owner: dict, purpose: str = "") -> tuple[Artifact, bool]:
    """Validate + persist. `owner` = {intake_id?, encounter_id?, patient_id?, by_kind, by_id}.
    Returns (artifact, created). Does NOT enqueue processing (caller decides)."""
    s = get_settings()
    if kind not in ArtifactKind.values():
        raise Unprocessable("不支持的文件类别", code="bad_kind")
    if not data:
        raise Unprocessable("文件内容为空", code="empty_file")
    fmt = sniff(data)
    is_image = kind in IMAGE_KINDS
    limit = (s.max_image_mb if is_image else s.max_audio_mb) * 1024 * 1024
    if len(data) > limit:
        raise Unprocessable(f"文件过大（上限 {limit // 1024 // 1024} MB）", code="file_too_large")
    if is_image and fmt not in ("jpeg", "png", "webp"):
        raise Unprocessable("无法识别为图片，请上传 JPG / PNG / WebP", code="not_an_image")
    if not is_image and fmt not in ("wav", "mp3", "flac", "mp4", "webm", "ogg"):
        raise Unprocessable("无法识别为音频，请上传 WAV / MP3 / M4A / WebM / OGG / FLAC", code="not_audio")

    digest = sha256_hex(data)
    scope = [Artifact.sha256 == digest, Artifact.kind == kind, Artifact.deleted_at.is_(None)]
    if owner.get("intake_id"):
        scope.append(Artifact.intake_id == owner["intake_id"])
    elif owner.get("encounter_id"):
        scope.append(Artifact.encounter_id == owner["encounter_id"])
    existing = db.scalar(select(Artifact).where(*scope).limit(1))
    if existing is not None:
        return existing, False

    key = f"{kind[:4]}_{secrets.token_hex(10)}{_EXT.get(fmt, '.bin')}"
    root = s.objects_dir
    root.mkdir(parents=True, exist_ok=True)
    a = Artifact(kind=kind, intake_id=owner.get("intake_id"), encounter_id=owner.get("encounter_id"), patient_id=owner.get("patient_id"),
                 storage_key=key, original_name=(original_name or "")[:200], mime=_mime(fmt), size_bytes=len(data), sha256=digest,
                 uploaded_by_kind=owner.get("by_kind", "staff"), uploaded_by_id=owner.get("by_id", ""), purpose=purpose[:24],
                 proc_status=ProcStatus.PENDING.value)
    if is_image:
        try:
            from PIL import Image, ImageOps
            with Image.open(io.BytesIO(data)) as im:
                im.load()
                im = ImageOps.exif_transpose(im)
                a.width, a.height = im.size
                if max(im.size) < 64:
                    raise Unprocessable("图片尺寸过小，无法识别", code="image_too_small")
                (root / key).write_bytes(data)
                thumb = im.convert("RGB")
                if max(thumb.size) > 1280:
                    thumb.thumbnail((1280, 1280))
                tkey = f"thumb_{secrets.token_hex(8)}.jpg"
                buf = io.BytesIO()
                thumb.save(buf, "JPEG", quality=85, optimize=True)
                (root / tkey).write_bytes(buf.getvalue())
                a.thumb_key = tkey
        except Unprocessable:
            raise
        except Exception as e:                                           # noqa: BLE001
            raise Unprocessable(f"图片损坏或格式不受支持：{type(e).__name__}", code="bad_image") from e
    else:
        dur = _probe_wav(data) if fmt == "wav" else _probe_av(data)
        a.duration_s = dur
        if dur is not None and dur > s.max_audio_seconds:
            raise Unprocessable(f"录音过长（{int(dur)} 秒，上限 {s.max_audio_seconds} 秒），请分段录制", code="audio_too_long")
        if dur is not None and dur < 0.4:
            raise Unprocessable("录音过短，请重新录制", code="audio_too_short")
        (root / key).write_bytes(data)
    db.add(a)
    db.flush()
    return a, True


def _mime(fmt: str | None) -> str:
    return {"wav": "audio/wav", "mp3": "audio/mpeg", "flac": "audio/flac", "mp4": "audio/mp4", "webm": "audio/webm", "ogg": "audio/ogg",
            "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(fmt or "", "application/octet-stream")


def path_of(a: Artifact, thumb: bool = False) -> Path:
    return get_settings().objects_dir / ((a.thumb_key if thumb and a.thumb_key else a.storage_key))


def processing_job_kind(kind: str) -> str:
    return {"audio": "asr", "lab_image": "ocr", "tongue_image": "tongue"}.get(kind, "")
