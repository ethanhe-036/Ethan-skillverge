#!/usr/bin/env python3
"""Export a PPTD project as page images through Kimi's public editor for visual QA.

Reuses the same localhost SDK host and agent-browser flow as export_pptx.py, but
chooses 图片 in the export dialog, captures the images ZIP, unzips it, and stitches
all pages into a single overview image that a multimodal model can review.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

from export_pptx import (
    AnchoredDirectory,
    BrowserSession,
    ExportError,
    KIMI_WRITER_UI,
    OOPIF_URL_HINT,
    PathSnapshot,
    browser_cdp_url,
    build_payload,
    capture_path_snapshot,
    ensure_agent_browser,
    ensure_debug_chrome,
    ensure_websocket,
    evaluate_in_iframe,
    find_download,
    find_manifest,
    is_same_or_ancestor_alias,
    log,
    path_exists,
    pip_install_hint,
    preflight_zip_central_directory,
    prepare_host_assets,
    ref_by_name,
    safe_project_path,
    safe_expanduser_path,
    safe_resolve_path,
    serve,
    snapshots_match,
    temporary_directory,
    wait_for_export_dialog,
    writer_control_ref,
)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
OVERVIEW_COLUMNS = 3
OVERVIEW_THUMB_WIDTH = 640
OVERVIEW_LABEL_HEIGHT = 32
OVERVIEW_GAP = 12
MIN_OVERVIEW_THUMB_WIDTH = 160
MAX_OVERVIEW_DIMENSION = 65_000
MAX_OVERVIEW_PIXELS = 48_000_000
MAX_SOURCE_IMAGE_PIXELS = 40_000_000
MAX_TOTAL_SOURCE_IMAGE_PIXELS = 1_100_000_000
MAX_IMAGE_ARCHIVE_ENTRIES = 1000
MAX_IMAGE_ARCHIVE_MEMBERS = 2000
MAX_IMAGE_ARCHIVE_BYTES = 500 * 1024 * 1024
MAX_IMAGE_ARCHIVE_ENTRY_BYTES = 50 * 1024 * 1024
MAX_IMAGE_ARCHIVE_TOTAL_BYTES = 500 * 1024 * 1024
MAX_IMAGE_CENTRAL_DIRECTORY_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_SNAPSHOT_ENTRIES = 10_000
MAX_OUTPUT_SNAPSHOT_DEPTH = 64
COPY_CHUNK_BYTES = 1024 * 1024
OUTPUT_MARKER = ".open-kimi-ppt-qa-output.json"
MAX_OUTPUT_MARKER_BYTES = 4096
OUTPUT_MARKER_CONTENT = {
    "owner": "open-kimi-ppt",
    "kind": "page-image-qa",
    "version": 1,
}
UNTRUSTED_ZIP_ERRORS = (
    OSError,
    UnicodeError,
    ValueError,
    RuntimeError,
    NotImplementedError,
    zipfile.BadZipFile,
    zipfile.LargeZipFile,
)


def is_owned_output_directory(path: Path) -> bool:
    marker = path / OUTPUT_MARKER
    try:
        before = marker.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_OUTPUT_MARKER_BYTES:
            return False
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(marker, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_dev != before.st_dev
                or opened.st_ino != before.st_ino
                or opened.st_size > MAX_OUTPUT_MARKER_BYTES
            ):
                return False
            raw = os.read(descriptor, MAX_OUTPUT_MARKER_BYTES + 1)
            if len(raw) > MAX_OUTPUT_MARKER_BYTES:
                return False
        finally:
            os.close(descriptor)
        content = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return content == OUTPUT_MARKER_CONTENT


def ensure_pillow() -> Tuple[Any, Any, Any]:
    try:
        from PIL import Image, ImageDraw, ImageFont

        return Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise ExportError(
            "Pillow is required for image export. Install it in the active Python "
            f"environment with: {pip_install_hint('pillow')}"
        ) from exc


def archive_member_name(filename: str) -> str:
    normalized = filename.replace("\\", "/")
    return PurePosixPath(normalized).name


def validated_image_entries(
    archive: zipfile.ZipFile,
) -> List[Tuple[zipfile.ZipInfo, str]]:
    members = archive.infolist()
    if len(members) > MAX_IMAGE_ARCHIVE_MEMBERS:
        raise ExportError(
            f"image archive contains more than {MAX_IMAGE_ARCHIVE_MEMBERS} members"
        )
    normalized_names = {
        info.filename.replace("\\", "/").lstrip("./").casefold()
        for info in members
    }
    if "[content_types].xml" in normalized_names or any(
        name.startswith("ppt/") for name in normalized_names
    ):
        raise ExportError("image archive is an OOXML document, not a page-images ZIP")

    entries: List[Tuple[zipfile.ZipInfo, str]] = []
    seen: Dict[str, str] = {}
    total_size = 0
    for info in members:
        if info.flag_bits & 0x1:
            raise ExportError(f"image archive contains encrypted member: {info.filename}")
        total_size += info.file_size
        if total_size > MAX_IMAGE_ARCHIVE_TOTAL_BYTES:
            raise ExportError(
                "image archive expands beyond "
                f"{MAX_IMAGE_ARCHIVE_TOTAL_BYTES} bytes"
            )
        if info.is_dir() or Path(info.filename).suffix.lower() not in IMAGE_SUFFIXES:
            continue
        name = archive_member_name(info.filename)
        if not name or name in (".", ".."):
            raise ExportError(f"invalid image archive member: {info.filename!r}")
        folded = name.casefold()
        if folded in seen:
            raise ExportError(
                "image archive contains colliding filenames: "
                f"{seen[folded]!r} and {info.filename!r}"
            )
        if info.file_size > MAX_IMAGE_ARCHIVE_ENTRY_BYTES:
            raise ExportError(
                f"image archive member exceeds {MAX_IMAGE_ARCHIVE_ENTRY_BYTES} bytes: "
                f"{info.filename}"
            )
        seen[folded] = info.filename
        entries.append((info, PurePosixPath(name).suffix.lower()))
        if len(entries) > MAX_IMAGE_ARCHIVE_ENTRIES:
            raise ExportError(
                f"image archive contains more than {MAX_IMAGE_ARCHIVE_ENTRIES} images"
            )
    if not entries:
        raise ExportError("image archive contains no page images")
    return entries


def is_image_zip(path: Path) -> bool:
    if (
        not path.is_file()
        or path.name.endswith(".crdownload")
        or path.suffix.lower() != ".zip"
    ):
        return False
    try:
        archive_size = path.stat().st_size
        if archive_size > MAX_IMAGE_ARCHIVE_BYTES:
            return False
        with path.open("rb") as stream:
            preflight_zip_central_directory(
                stream,
                archive_size,
                max_members=MAX_IMAGE_ARCHIVE_MEMBERS,
                max_directory_bytes=MAX_IMAGE_CENTRAL_DIRECTORY_BYTES,
                display_name=str(path),
            )
            stream.seek(0)
            with zipfile.ZipFile(stream) as archive:
                validated_image_entries(archive)
                return True
    except (ExportError, *UNTRUSTED_ZIP_ERRORS):
        return False


def page_sort_key(path: Path) -> Tuple[int, str]:
    match = re.match(r"(\d+)", path.stem)
    return (int(match.group(1)) if match else sys.maxsize, path.name)


def archive_image_sort_key(
    entry: Tuple[zipfile.ZipInfo, str],
) -> Tuple[int, int, str, str]:
    name = archive_member_name(entry[0].filename)
    match = re.match(r"(\d+)", PurePosixPath(name).stem)
    if not match:
        return (1, 0, "", name.casefold())
    # Avoid int() on an archive-controlled, potentially 65 KiB digit prefix:
    # Python rejects >4300 digits on modern versions and older versions spend
    # disproportionate CPU constructing a huge integer. Length+lexicographic
    # order is an exact natural-number order after leading-zero normalization.
    digits = match.group(1).lstrip("0") or "0"
    return (0, len(digits), digits, name.casefold())


def unzip_images(archive_path: Path, pages_dir: Path) -> List[Path]:
    try:
        return _unzip_images_checked(archive_path, pages_dir)
    except ExportError:
        raise
    except UNTRUSTED_ZIP_ERRORS as exc:
        raise ExportError(f"invalid page-images ZIP: {archive_path}") from exc


def _unzip_images_checked(archive_path: Path, pages_dir: Path) -> List[Path]:
    try:
        archive_size = archive_path.stat().st_size
    except OSError as exc:
        raise ExportError(f"cannot stat image archive: {archive_path}") from exc
    if archive_size > MAX_IMAGE_ARCHIVE_BYTES:
        raise ExportError(
            f"image archive exceeds the {MAX_IMAGE_ARCHIVE_BYTES}-byte compressed-size limit"
        )
    pages_dir.mkdir(parents=True, exist_ok=True)
    images: List[Path] = []
    with archive_path.open("rb") as stream:
        preflight_zip_central_directory(
            stream,
            archive_size,
            max_members=MAX_IMAGE_ARCHIVE_MEMBERS,
            max_directory_bytes=MAX_IMAGE_CENTRAL_DIRECTORY_BYTES,
            display_name=str(archive_path),
        )
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            entries = sorted(validated_image_entries(archive), key=archive_image_sort_key)
            total_written = 0
            for index, (info, suffix) in enumerate(entries, start=1):
                # Never use an archive-controlled basename as a Windows path. A
                # name such as C:evil.png is drive-relative and foo:bar.png names
                # an NTFS alternate data stream; fixed local names remove both
                # classes as well as reserved-device and trailing-dot hazards.
                target = pages_dir / f"page-{index:04d}{suffix}"
                if path_exists(target):
                    raise ExportError(f"image extraction target already exists: {target}")
                with archive.open(info) as source, target.open("wb") as out:
                    written = 0
                    while True:
                        chunk = source.read(COPY_CHUNK_BYTES)
                        if not chunk:
                            break
                        written += len(chunk)
                        total_written += len(chunk)
                        if written > MAX_IMAGE_ARCHIVE_ENTRY_BYTES:
                            raise ExportError(
                                f"image archive member expanded beyond its limit: {info.filename}"
                            )
                        if total_written > MAX_IMAGE_ARCHIVE_TOTAL_BYTES:
                            raise ExportError("image archive exceeded its total extraction limit")
                        out.write(chunk)
                if written != info.file_size:
                    raise ExportError(
                        f"image archive member size mismatch: {info.filename}"
                    )
                images.append(target)
    return images


def copy_file_bounded(source: Path, destination: Path, maximum_bytes: int) -> int:
    source_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        source_flags |= os.O_NOFOLLOW
    source_descriptor = os.open(source, source_flags)
    destination_descriptor = -1
    created = False
    try:
        before = os.fstat(source_descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ExportError(f"browser download is not a regular file: {source}")
        if before.st_size > maximum_bytes:
            raise ExportError(
                f"browser download exceeds the {maximum_bytes}-byte safety limit"
            )
        destination_descriptor = os.open(
            destination,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0),
            0o600,
        )
        created = True
        copied = 0
        while True:
            chunk = os.read(source_descriptor, COPY_CHUNK_BYTES)
            if not chunk:
                break
            copied += len(chunk)
            if copied > maximum_bytes:
                raise ExportError(
                    f"browser download grew beyond the {maximum_bytes}-byte safety limit"
                )
            view = memoryview(chunk)
            while view:
                count = os.write(destination_descriptor, view)
                if count <= 0:
                    raise ExportError("browser-raw copy made no progress")
                view = view[count:]
        os.fsync(destination_descriptor)
        after = os.fstat(source_descriptor)
        if (
            copied != before.st_size
            or after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
        ):
            raise ExportError("browser download changed during bounded copy")
        return copied
    except Exception:
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
            destination_descriptor = -1
        if created:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    finally:
        os.close(source_descriptor)
        if destination_descriptor >= 0:
            os.close(destination_descriptor)


def label_font(image_font: Any) -> Any:
    try:
        return image_font.load_default(size=18)
    except TypeError:  # older Pillow without the size argument
        return image_font.load_default()


def overview_layout(image_sizes: Sequence[Tuple[int, int]]) -> Dict[str, int]:
    if not image_sizes:
        raise ExportError("cannot stitch an empty page-image set")
    ratios: List[float] = []
    total_source_pixels = 0
    for width, height in image_sizes:
        if width <= 0 or height <= 0:
            raise ExportError(f"invalid page image dimensions: {width}x{height}")
        if width * height > MAX_SOURCE_IMAGE_PIXELS:
            raise ExportError(
                f"page image exceeds the {MAX_SOURCE_IMAGE_PIXELS:,}-pixel safety limit: "
                f"{width}x{height}"
            )
        total_source_pixels += width * height
        if total_source_pixels > MAX_TOTAL_SOURCE_IMAGE_PIXELS:
            raise ExportError(
                "page images exceed the aggregate "
                f"{MAX_TOTAL_SOURCE_IMAGE_PIXELS:,}-pixel decode safety limit"
            )
        ratios.append(height / width)

    columns = OVERVIEW_COLUMNS
    rows = math.ceil(len(image_sizes) / columns)
    for thumb_width in range(OVERVIEW_THUMB_WIDTH, MIN_OVERVIEW_THUMB_WIDTH - 1, -1):
        max_thumb_height = max(max(1, round(thumb_width * ratio)) for ratio in ratios)
        cell_height = OVERVIEW_LABEL_HEIGHT + max_thumb_height
        width = columns * thumb_width + (columns + 1) * OVERVIEW_GAP
        height = rows * cell_height + (rows + 1) * OVERVIEW_GAP
        if (
            width <= MAX_OVERVIEW_DIMENSION
            and height <= MAX_OVERVIEW_DIMENSION
            and width * height <= MAX_OVERVIEW_PIXELS
        ):
            return {
                "columns": columns,
                "rows": rows,
                "thumbWidth": thumb_width,
                "cellHeight": cell_height,
                "width": width,
                "height": height,
            }
    raise ExportError(
        "overview would exceed image dimension/memory safety limits even at the "
        f"minimum {MIN_OVERVIEW_THUMB_WIDTH}px thumbnail width"
    )


def stitch_overview(
    images: Sequence[Path],
    output: Path,
    image_cls: Any,
    draw_cls: Any,
    image_font: Any,
) -> Path:
    try:
        return _stitch_overview_checked(
            images, output, image_cls, draw_cls, image_font
        )
    except ExportError:
        raise
    except Exception as exc:
        # Pillow's DecompressionBombError deliberately does not inherit from
        # OSError. Normalize all decoder/encoder failures at this untrusted
        # image boundary so the CLI fails cleanly without publishing staging.
        raise ExportError("could not safely decode or stitch page images") from exc


def _stitch_overview_checked(
    images: Sequence[Path],
    output: Path,
    image_cls: Any,
    draw_cls: Any,
    image_font: Any,
) -> Path:
    image_sizes: List[Tuple[int, int]] = []
    for path in images:
        with image_cls.open(path) as opened:
            image_sizes.append(tuple(opened.size))

    layout = overview_layout(image_sizes)
    overview = image_cls.new(
        "RGB",
        (layout["width"], layout["height"]),
        "#e5e7eb",
    )
    try:
        draw = draw_cls.Draw(overview)
        font = label_font(image_font)
        for position, path in enumerate(images):
            source_width, source_height = image_sizes[position]
            thumb_height = max(
                1,
                round(layout["thumbWidth"] * source_height / source_width),
            )
            with image_cls.open(path) as opened:
                frame = opened.convert("RGB")
                try:
                    thumb = frame.resize((layout["thumbWidth"], thumb_height))
                finally:
                    frame.close()
            column = position % layout["columns"]
            row = position // layout["columns"]
            x = OVERVIEW_GAP + column * (layout["thumbWidth"] + OVERVIEW_GAP)
            y = OVERVIEW_GAP + row * (layout["cellHeight"] + OVERVIEW_GAP)
            draw.rectangle(
                (x, y, x + layout["thumbWidth"], y + OVERVIEW_LABEL_HEIGHT - 4),
                fill="#111827",
            )
            draw.text((x + 8, y + 5), f"P{position + 1}", fill="#ffffff", font=font)
            overview.paste(thumb, (x, y + OVERVIEW_LABEL_HEIGHT))
            thumb.close()

        overview.save(output, "JPEG", quality=85)
    finally:
        overview.close()
    return output


# The export dialog's 图片 format option is a plain <div class="radio-group-item">
# without an ARIA role, so agent-browser's interactive snapshot never exposes it
# and cross-origin iframe rules block page-level eval. Clicking it requires CDP.
IMAGE_FORMAT_CLICK_JS = """
(() => {
  const items = [...document.querySelectorAll(__FORMAT_SELECTOR__)];
  const pool = items.length
    ? items
    : [...document.querySelectorAll('div,span,label,button')].filter(
        (el) => el.children.length === 0
      );
  const matches = pool.filter(
    (el) => el.textContent.trim() === __IMAGE_FORMAT_NAME__
  );
  if (matches.length !== 1) {
    return { status: matches.length ? 'ambiguous' : 'missing', count: matches.length };
  }
  matches[0].click();
  return { status: 'clicked', count: 1 };
})()
""".replace(
    "__FORMAT_SELECTOR__", json.dumps(KIMI_WRITER_UI.format_selector)
).replace(
    "__IMAGE_FORMAT_NAME__", json.dumps(KIMI_WRITER_UI.image_format_name)
).strip()

ACTIVE_FORMAT_JS = """
(() => {
  const active = [...document.querySelectorAll(
    __ACTIVE_FORMAT_SELECTOR__
  )];
  return {
    count: active.length,
    text: active.length === 1 ? active[0].textContent.trim() : null,
  };
})()
""".replace(
    "__ACTIVE_FORMAT_SELECTOR__",
    json.dumps(KIMI_WRITER_UI.active_format_selector),
).strip()


def select_image_format(browser: BrowserSession) -> None:
    cdp_url = browser_cdp_url(browser)
    target_id, value = evaluate_in_iframe(
        cdp_url,
        OOPIF_URL_HINT,
        IMAGE_FORMAT_CLICK_JS,
        return_target_id=True,
    )
    if not isinstance(value, dict) or value.get("status") != "clicked":
        raise ExportError(
            "Kimi image-format control is missing or ambiguous: "
            f"{value!r}"
        )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        active = evaluate_in_iframe(
            cdp_url,
            OOPIF_URL_HINT,
            ACTIVE_FORMAT_JS,
            target_id=target_id,
        )
        if (
            isinstance(active, dict)
            and active.get("count") == 1
            and active.get("text") == KIMI_WRITER_UI.image_format_name
        ):
            return
        time.sleep(0.3)
    raise ExportError(f"image format was not activated unambiguously: {active!r}")


class DirectoryEntrySnapshot(NamedTuple):
    relative_path: str
    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int
    changed_ns: int


class DirectorySnapshot(NamedTuple):
    root: PathSnapshot
    entries: Tuple[DirectoryEntrySnapshot, ...]


def capture_directory_snapshot(path: Path) -> DirectorySnapshot:
    root = capture_path_snapshot(path)
    if not root.exists:
        return DirectorySnapshot(root, ())
    if root.mode is None or not stat.S_ISDIR(root.mode):
        return DirectorySnapshot(root, ())

    entries: List[DirectoryEntrySnapshot] = []
    pending: List[Tuple[Path, Path, int]] = [(path, Path("."), 0)]
    while pending:
        directory, relative_directory, depth = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                for child in iterator:
                    if len(entries) >= MAX_OUTPUT_SNAPSHOT_ENTRIES:
                        raise ExportError(
                            "image output contains more than "
                            f"{MAX_OUTPUT_SNAPSHOT_ENTRIES} snapshot entries"
                        )
                    try:
                        info = child.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise ExportError(
                            "image output changed while it was being snapshotted: "
                            f"{child.path}"
                        ) from exc
                    relative = relative_directory / child.name
                    entries.append(
                        DirectoryEntrySnapshot(
                            relative.as_posix(),
                            info.st_dev,
                            info.st_ino,
                            info.st_mode,
                            info.st_size,
                            info.st_mtime_ns,
                            info.st_ctime_ns,
                        )
                    )
                    if stat.S_ISDIR(info.st_mode):
                        child_depth = depth + 1
                        if child_depth > MAX_OUTPUT_SNAPSHOT_DEPTH:
                            raise ExportError(
                                "image output exceeds the snapshot depth limit of "
                                f"{MAX_OUTPUT_SNAPSHOT_DEPTH}"
                            )
                        pending.append((Path(child.path), relative, child_depth))
        except OSError as exc:
            raise ExportError(
                f"could not snapshot image output directory {directory}: {exc}"
            ) from exc

    after = capture_path_snapshot(path)
    if root != after:
        raise ExportError(f"image output changed while it was being snapshotted: {path}")
    entries.sort(key=lambda entry: entry.relative_path)
    return DirectorySnapshot(root, tuple(entries))


def _path_snapshot_from_stat(info: os.stat_result) -> PathSnapshot:
    return PathSnapshot(
        True,
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def capture_capability_directory_snapshot(
    capability: AnchoredDirectory,
    relative: os.PathLike[str] | str,
) -> DirectorySnapshot:
    """Snapshot a tree without following the capability's display pathname."""
    root = capability.capture(relative)
    if not root.exists or root.mode is None or not stat.S_ISDIR(root.mode):
        return DirectorySnapshot(root, ())
    if not capability.uses_dir_fd:
        capability.assert_path_binding()
        snapshot = capture_directory_snapshot(capability.display_path(relative))
        capability.assert_path_binding()
        return snapshot

    entries: List[DirectoryEntrySnapshot] = []
    pending: List[Tuple[Path, int]] = [(Path(relative), 0)]
    while pending:
        relative_directory, depth = pending.pop()
        descriptor = capability.open_directory(relative_directory)
        try:
            with os.scandir(descriptor) as iterator:
                children = list(iterator)
            for child in children:
                if len(entries) >= MAX_OUTPUT_SNAPSHOT_ENTRIES:
                    raise ExportError(
                        "image output contains more than "
                        f"{MAX_OUTPUT_SNAPSHOT_ENTRIES} snapshot entries"
                    )
                info = child.stat(follow_symlinks=False)
                child_relative = relative_directory / child.name
                root_relative = Path(relative)
                displayed_relative = child_relative.relative_to(root_relative)
                entries.append(
                    DirectoryEntrySnapshot(
                        displayed_relative.as_posix(),
                        info.st_dev,
                        info.st_ino,
                        info.st_mode,
                        info.st_size,
                        info.st_mtime_ns,
                        info.st_ctime_ns,
                    )
                )
                if stat.S_ISDIR(info.st_mode):
                    child_depth = depth + 1
                    if child_depth > MAX_OUTPUT_SNAPSHOT_DEPTH:
                        raise ExportError(
                            "image output exceeds the snapshot depth limit of "
                            f"{MAX_OUTPUT_SNAPSHOT_DEPTH}"
                        )
                    pending.append((child_relative, child_depth))
        except OSError as exc:
            raise ExportError(
                "could not snapshot anchored image output directory "
                f"{capability.display_path(relative_directory)}: {exc}"
            ) from exc
        finally:
            os.close(descriptor)
    after = capability.capture(relative)
    if root != after:
        raise ExportError("anchored image output changed while it was snapshotted")
    entries.sort(key=lambda entry: entry.relative_path)
    return DirectorySnapshot(root, tuple(entries))


def is_owned_capability_directory(
    capability: AnchoredDirectory,
    relative: os.PathLike[str] | str,
) -> bool:
    marker_relative = Path(relative) / OUTPUT_MARKER
    descriptor = -1
    try:
        marker = capability.capture(marker_relative)
        if (
            not marker.exists
            or marker.mode is None
            or not stat.S_ISREG(marker.mode)
            or marker.size is None
            or marker.size > MAX_OUTPUT_MARKER_BYTES
        ):
            return False
        descriptor = capability.open_file(marker_relative, os.O_RDONLY)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != marker.device
            or opened.st_ino != marker.inode
            or opened.st_size > MAX_OUTPUT_MARKER_BYTES
        ):
            return False
        raw = os.read(descriptor, MAX_OUTPUT_MARKER_BYTES + 1)
        if len(raw) > MAX_OUTPUT_MARKER_BYTES:
            return False
        return json.loads(raw.decode("utf-8")) == OUTPUT_MARKER_CONTENT
    except (OSError, UnicodeError, json.JSONDecodeError, ExportError):
        return False
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def copy_tree_into_capability(
    source: Path,
    capability: AnchoredDirectory,
    destination_relative: os.PathLike[str] | str,
) -> PathSnapshot:
    """Import a private tree through anchored mkdir/openat operations."""
    root_before = source.lstat()
    if not stat.S_ISDIR(root_before.st_mode):
        raise ExportError(f"staged image tree is not a directory: {source}")
    capability.mkdir(destination_relative, 0o700)
    created_root = capability.capture(destination_relative)
    if created_root.mode is None or not stat.S_ISDIR(created_root.mode):
        raise ExportError("could not establish private image staging directory")
    pending: List[Tuple[Path, Path]] = [(source, Path(destination_relative))]
    copied_entries = 0
    copied_bytes = 0
    try:
        while pending:
            source_directory, destination_directory = pending.pop()
            with os.scandir(source_directory) as iterator:
                children = sorted(iterator, key=lambda entry: entry.name)
            for child in children:
                copied_entries += 1
                if copied_entries > MAX_OUTPUT_SNAPSHOT_ENTRIES:
                    raise ExportError("staged image tree exceeds the entry limit")
                source_child = source_directory / child.name
                # DirEntry.stat() may expose only the directory-enumeration
                # file index on Windows, while fstat() returns the stable file
                # ID. A direct lstat/open pair uses comparable identities.
                info = source_child.lstat()
                destination_child = destination_directory / child.name
                if stat.S_ISLNK(info.st_mode):
                    raise ExportError(
                        f"staged image tree contains a symlink: {source_child}"
                    )
                if stat.S_ISDIR(info.st_mode):
                    capability.mkdir(destination_child, 0o700)
                    pending.append((source_child, destination_child))
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise ExportError(
                        f"staged image tree contains an unsupported entry: {source_child}"
                    )
                source_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
                if hasattr(os, "O_NOFOLLOW"):
                    source_flags |= os.O_NOFOLLOW
                source_descriptor = os.open(source_child, source_flags)
                destination_descriptor = -1
                try:
                    opened = os.fstat(source_descriptor)
                    if (
                        opened.st_dev != info.st_dev
                        or opened.st_ino != info.st_ino
                        or not stat.S_ISREG(opened.st_mode)
                    ):
                        raise ExportError(
                            f"staged image file changed before import: {source_child}"
                        )
                    copied_bytes += opened.st_size
                    if copied_bytes > MAX_IMAGE_ARCHIVE_TOTAL_BYTES * 2:
                        raise ExportError("staged image tree exceeds the import size limit")
                    destination_descriptor = capability.open_file(
                        destination_child,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                    )
                    copied = 0
                    while True:
                        chunk = os.read(source_descriptor, COPY_CHUNK_BYTES)
                        if not chunk:
                            break
                        copied += len(chunk)
                        view = memoryview(chunk)
                        while view:
                            written = os.write(destination_descriptor, view)
                            if written <= 0:
                                raise ExportError("image staging import made no progress")
                            view = view[written:]
                    os.fsync(destination_descriptor)
                    after = os.fstat(source_descriptor)
                    if (
                        copied != opened.st_size
                        or after.st_dev != opened.st_dev
                        or after.st_ino != opened.st_ino
                        or after.st_size != opened.st_size
                        or after.st_mtime_ns != opened.st_mtime_ns
                        or after.st_ctime_ns != opened.st_ctime_ns
                    ):
                        raise ExportError(
                            f"staged image file changed during import: {source_child}"
                        )
                finally:
                    os.close(source_descriptor)
                    if destination_descriptor >= 0:
                        os.close(destination_descriptor)
        source_after = source.lstat()
        if (
            source_after.st_dev != root_before.st_dev
            or source_after.st_ino != root_before.st_ino
        ):
            raise ExportError("staged image tree changed during import")
        final_root = capability.capture(destination_relative)
        if not _same_root_identity(final_root, created_root):
            raise ExportError("private image staging directory changed during import")
        return final_root
    except Exception:
        cleanup_capability_directory(
            capability,
            destination_relative,
            created_root,
            label="incomplete image staging directory",
        )
        raise


def cleanup_capability_directory(
    capability: AnchoredDirectory,
    relative: os.PathLike[str] | str,
    expected_root: Optional[PathSnapshot],
    *,
    label: str,
) -> None:
    current = capability.capture(relative)
    if not current.exists:
        return
    if (
        expected_root is None
        or not _same_root_identity(current, expected_root)
        or current.mode is None
        or not stat.S_ISDIR(current.mode)
    ):
        log(
            f"warning: refusing to clean replaced {label}: "
            f"{capability.display_path(relative)}"
        )
        return
    if not capability.uses_dir_fd:
        capability.assert_path_binding()
        cleanup_private_directory(
            capability.display_path(relative),
            expected_root,
            label=label,
        )
        capability.assert_path_binding()
        return
    pending: List[Tuple[Path, bool]] = [(Path(relative), False)]
    try:
        while pending:
            current_relative, visited = pending.pop()
            if visited:
                capability.rmdir(current_relative)
                continue
            pending.append((current_relative, True))
            descriptor = capability.open_directory(current_relative)
            try:
                with os.scandir(descriptor) as iterator:
                    children = [
                        (child.name, child.stat(follow_symlinks=False))
                        for child in iterator
                    ]
            finally:
                os.close(descriptor)
            for child_name, info in children:
                child_relative = current_relative / child_name
                if stat.S_ISDIR(info.st_mode):
                    pending.append((child_relative, False))
                else:
                    capability.unlink(child_relative)
    except (OSError, ExportError) as exc:
        log(
            f"warning: could not remove {label} "
            f"{capability.display_path(relative)}: {exc}"
        )


def directory_snapshots_match(
    actual: DirectorySnapshot,
    expected: DirectorySnapshot,
    *,
    after_rename: bool = False,
) -> bool:
    return snapshots_match(
        actual.root, expected.root, after_rename=after_rename
    ) and actual.entries == expected.entries


def _same_root_identity(actual: PathSnapshot, expected: PathSnapshot) -> bool:
    return (
        actual.exists
        and expected.exists
        and actual.device == expected.device
        and actual.inode == expected.inode
        and actual.mode is not None
        and expected.mode is not None
        and stat.S_IFMT(actual.mode) == stat.S_IFMT(expected.mode)
    )


def validate_image_destination(
    manifest: Path,
    payload: Dict[str, Any],
    requested_output: Path,
    force: bool,
    *,
    directory_capability: Optional[AnchoredDirectory] = None,
) -> Tuple[Path, bool, DirectorySnapshot]:
    requested = safe_expanduser_path(requested_output, description="image output")
    if requested.is_symlink():
        raise ExportError(f"image output must not be a symbolic link: {requested}")
    output = safe_resolve_path(requested, description="image output")
    if (
        directory_capability is not None
        and output.parent != directory_capability.root
    ):
        raise ExportError("image output does not belong to its directory capability")

    def output_snapshot() -> DirectorySnapshot:
        if directory_capability is None:
            return capture_directory_snapshot(output)
        return capture_capability_directory_snapshot(
            directory_capability,
            output.name,
        )

    def child_exists(path: Path) -> bool:
        if directory_capability is None:
            return path_exists(path)
        return directory_capability.capture(path.name).exists

    def output_is_owned() -> bool:
        if directory_capability is None:
            return is_owned_output_directory(output)
        return is_owned_capability_directory(directory_capability, output.name)

    root = safe_resolve_path(Path(output.anchor), description="filesystem root")
    if output == root:
        raise ExportError(f"refusing to use a filesystem root as image output: {output}")
    previous_backup = output.with_name(f".{output.name}.backup")
    if force and child_exists(previous_backup):
        raise ExportError(
            "a previous image-output backup is still retained; inspect and move "
            f"or remove it before another --force export: {previous_backup}"
        )

    manifest = safe_resolve_path(manifest, description="PPTD manifest")
    project_root = manifest.parent
    try:
        current_directory = Path.cwd()
    except (OSError, RuntimeError, ValueError, UnicodeError) as exc:
        raise ExportError("cannot determine the current working directory safely") from exc
    home_directory = safe_expanduser_path(Path("~"), description="home directory")
    dangerous_targets = (
        project_root,
        safe_resolve_path(current_directory, description="current working directory"),
        safe_resolve_path(home_directory, description="home directory"),
    )
    if any(is_same_or_ancestor_alias(output, target) for target in dangerous_targets):
        raise ExportError(
            f"image output must not be a project/workspace/home ancestor: {output}"
        )

    protected_inputs = {manifest}
    pages = payload.get("pages")
    if not isinstance(pages, list):
        raise ExportError("invalid export payload: pages must be an array")
    for page in pages:
        if not isinstance(page, dict):
            raise ExportError("invalid export payload: page must be an object")
        protected_inputs.add(safe_project_path(project_root, page.get("path")))
    image_map = payload.get("imageMap")
    if not isinstance(image_map, dict):
        raise ExportError("invalid export payload: imageMap must be an object")
    for image_path in image_map:
        protected_inputs.add(safe_project_path(project_root, image_path))
    if any(is_same_or_ancestor_alias(output, path) for path in protected_inputs):
        raise ExportError(f"image output would remove a project input: {output}")

    initial_snapshot = output_snapshot()
    existed = initial_snapshot.root.exists
    if (
        existed
        and initial_snapshot.root.mode is not None
        and not stat.S_ISDIR(initial_snapshot.root.mode)
    ):
        raise ExportError(f"image output must be a directory: {output}")
    if existed:
        if not output_is_owned():
            raise ExportError(
                "refusing to replace an unowned directory; choose a new output path "
                f"instead: {output}"
            )
        if not force:
            raise ExportError(
                f"output directory already exists (pass --force to replace it): {output}"
            )
    snapshot = output_snapshot()
    if snapshot.root.exists:
        if snapshot.root.mode is None or not stat.S_ISDIR(snapshot.root.mode):
            raise ExportError(f"image output must be a directory: {output}")
        if not output_is_owned():
            raise ExportError(f"image output changed during validation: {output}")
    return output, existed, snapshot


def validate_page_count(images: Sequence[Path], payload: Dict[str, Any]) -> List[str]:
    pages = payload.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ExportError("invalid export payload: pages must be a non-empty array")
    page_paths: List[str] = []
    for entry in pages:
        path = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(path, str) or not path.strip():
            raise ExportError("invalid export payload: every page needs a path")
        page_paths.append(path)
    if len(images) != len(page_paths):
        raise ExportError(
            "page image count does not match the PPTD manifest: "
            f"expected {len(page_paths)}, received {len(images)}"
        )
    return page_paths


def sibling_staging_directory(destination: Path) -> Path:
    return destination.with_name(
        f".{destination.name}.{uuid.uuid4().hex}.staging"
    )


def _raise_atomic_rename_error(destination: Path) -> None:
    error_number = ctypes.get_errno()
    if error_number in {
        errno.ENOSYS,
        errno.EINVAL,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }:
        raise ExportError(
            "this platform or filesystem does not support atomic no-replace "
            "directory publication"
        )
    raise OSError(
        error_number,
        os.strerror(error_number),
        os.fspath(destination),
    )


def rename_directory_noreplace(source: Path, destination: Path) -> None:
    """Atomically publish a sibling directory only if destination is absent."""
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)

    if sys.platform == "darwin":
        # macOS renamex_np(2): RENAME_EXCL rejects every existing destination,
        # including an empty directory (which ordinary rename would replace).
        libc = ctypes.CDLL(None, use_errno=True)
        renamex_np = libc.renamex_np
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        ctypes.set_errno(0)
        if renamex_np(source_bytes, destination_bytes, 0x00000004) != 0:
            _raise_atomic_rename_error(destination)
        return

    if sys.platform.startswith("linux"):
        # Linux renameat2(2): RENAME_NOREPLACE is atomic in the VFS. Use the
        # libc wrapper when available and a syscall fallback for common arches.
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        ctypes.set_errno(0)
        if renameat2 is not None:
            renameat2.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            renameat2.restype = ctypes.c_int
            result = renameat2(
                -100, source_bytes, -100, destination_bytes, 0x00000001
            )
        else:
            machine = os.uname().machine.lower()
            syscall_numbers = {
                "x86_64": 316,
                "amd64": 316,
                "i386": 353,
                "i686": 353,
                "aarch64": 276,
                "arm64": 276,
                "armv7l": 382,
                "riscv64": 276,
                "ppc64": 357,
                "ppc64le": 357,
                "s390x": 347,
            }
            syscall_number = syscall_numbers.get(machine)
            if syscall_number is None:
                raise ExportError(
                    "this Linux architecture lacks a configured atomic no-replace "
                    "directory publication primitive"
                )
            syscall = libc.syscall
            syscall.restype = ctypes.c_long
            result = syscall(
                ctypes.c_long(syscall_number),
                ctypes.c_int(-100),
                ctypes.c_char_p(source_bytes),
                ctypes.c_int(-100),
                ctypes.c_char_p(destination_bytes),
                ctypes.c_uint(0x00000001),
            )
        if result != 0:
            _raise_atomic_rename_error(destination)
        return

    if os.name == "nt":
        # A path-based MoveFile/rename can be redirected if an ancestor binding
        # changes and then returns between identity checks.  Re-open the common
        # parent as a stable Windows directory HANDLE and publish relative to it.
        source_parent = safe_resolve_path(
            source.parent,
            description="Windows image publication parent",
        )
        destination_parent = safe_resolve_path(
            destination.parent,
            description="Windows image publication parent",
        )
        if source_parent != destination_parent:
            raise ExportError(
                "Windows handle-relative image publication requires sibling paths"
            )
        with AnchoredDirectory.open(source_parent) as capability:
            capability.rename_noreplace(source.name, destination.name)
        return

    raise ExportError(
        "this platform lacks atomic no-replace directory publication"
    )


def _validate_staged_directory(
    staged: Path,
    *,
    directory_capability: Optional[AnchoredDirectory] = None,
) -> DirectorySnapshot:
    snapshot = (
        capture_capability_directory_snapshot(directory_capability, staged.name)
        if directory_capability is not None
        else capture_directory_snapshot(staged)
    )
    if snapshot.root.mode is None or not stat.S_ISDIR(snapshot.root.mode):
        raise ExportError(f"staged image output is missing: {staged}")
    owned = (
        is_owned_capability_directory(directory_capability, staged.name)
        if directory_capability is not None
        else is_owned_output_directory(staged)
    )
    if not owned:
        raise ExportError(f"staged image output is missing its ownership marker: {staged}")
    for entry in snapshot.entries:
        if stat.S_ISLNK(entry.mode):
            raise ExportError(
                f"staged image output contains a symlink: {staged / entry.relative_path}"
            )
        if not stat.S_ISDIR(entry.mode) and not stat.S_ISREG(entry.mode):
            raise ExportError(
                f"unsupported staged output entry: {staged / entry.relative_path}"
            )
    return snapshot


def _restore_directory_backup_noreplace(backup: Path, destination: Path) -> None:
    try:
        rename_directory_noreplace(backup, destination)
    except Exception as exc:
        retained = backup.resolve(strict=False)
        raise ExportError(
            "destination was occupied during safe directory recovery; no "
            "concurrent directory was overwritten and the displaced directory "
            f"was retained at {retained}"
        ) from exc


def _restore_capability_directory_backup_noreplace(
    capability: AnchoredDirectory,
    backup: Path,
    destination: Path,
) -> None:
    try:
        capability.rename_noreplace(backup.name, destination.name)
    except Exception as exc:
        raise ExportError(
            "destination was occupied during anchored directory recovery; the "
            f"displaced directory was retained at {backup}"
        ) from exc


def cleanup_private_directory(
    path: Path,
    expected_root: Optional[PathSnapshot],
    *,
    label: str,
) -> None:
    current = capture_path_snapshot(path)
    if not current.exists:
        return
    if current.mode is None or not stat.S_ISDIR(current.mode):
        log(f"warning: refusing to clean changed {label}: {path.absolute()}")
        return
    if expected_root is not None and not _same_root_identity(current, expected_root):
        log(f"warning: refusing to clean replaced {label}: {path.absolute()}")
        return
    try:
        shutil.rmtree(path)
    except OSError as exc:
        log(f"warning: could not remove {label} {path.absolute()}: {exc}")


def commit_staged_directory(
    staged: Path,
    destination: Path,
    *,
    replace_existing: bool,
    expected_snapshot: Optional[DirectorySnapshot] = None,
    directory_capability: Optional[AnchoredDirectory] = None,
) -> Optional[Path]:
    staged_snapshot = _validate_staged_directory(
        staged,
        directory_capability=directory_capability,
    )

    def snapshot(path: Path) -> DirectorySnapshot:
        if directory_capability is None:
            return capture_directory_snapshot(path)
        return capture_capability_directory_snapshot(
            directory_capability,
            path.name,
        )

    def rename(source: Path, target: Path) -> None:
        if directory_capability is None:
            rename_directory_noreplace(source, target)
        else:
            directory_capability.rename_noreplace(source.name, target.name)

    if not replace_existing:
        # The complete staging tree becomes visible in one kernel operation.
        # On failure, the source tree remains staged and no destination partial
        # can exist; an independently-created destination is never replaced.
        rename(staged, destination)
        return None

    if expected_snapshot is None:
        raise ExportError("force publication requires a pre-export directory snapshot")
    current_snapshot = snapshot(destination)
    if not directory_snapshots_match(current_snapshot, expected_snapshot):
        raise ExportError(
            f"image output changed during export; refusing to replace it: {destination}"
        )
    if not expected_snapshot.root.exists:
        rename(staged, destination)
        return None
    if (
        expected_snapshot.root.mode is None
        or not stat.S_ISDIR(expected_snapshot.root.mode)
    ):
        raise ExportError(f"expected image output is not a directory: {destination}")

    # Keep at most one explicit recovery tree. A later --force run refuses to
    # replace it until the user deliberately moves or removes the backup.
    backup = destination.with_name(f".{destination.name}.backup")
    rename(destination, backup)
    moved_snapshot = snapshot(backup)
    if not directory_snapshots_match(
        moved_snapshot, expected_snapshot, after_rename=True
    ):
        if directory_capability is None:
            _restore_directory_backup_noreplace(backup, destination)
        else:
            _restore_capability_directory_backup_noreplace(
                directory_capability,
                backup,
                destination,
            )
        raise ExportError(
            "image output changed while publication began and was safely restored: "
            f"{destination}"
        )

    try:
        rename(staged, destination)
    except Exception as exc:
        if directory_capability is None:
            _restore_directory_backup_noreplace(backup, destination)
        else:
            _restore_capability_directory_backup_noreplace(
                directory_capability,
                backup,
                destination,
            )
        raise exc

    published_snapshot = snapshot(destination)
    if not directory_snapshots_match(
        published_snapshot, staged_snapshot, after_rename=True
    ):
        retained = backup.resolve(strict=False)
        raise ExportError(
            "published image output changed concurrently; it was not overwritten "
            f"or deleted, and the previous output was retained at {retained}"
        )
    if not directory_snapshots_match(
        snapshot(backup), moved_snapshot
    ):
        retained = backup.resolve(strict=False)
        raise ExportError(
            "the previous image output changed during publication; the changed "
            f"directory was retained at {retained}"
        )
    # A process may retain an open file within the displaced tree and write
    # after any snapshot. Recursive check-then-delete cannot be made atomic, so
    # retain and report the previous output rather than risk deleting it.
    retained = backup.resolve(strict=False)
    log(f"previous image output retained at {retained}")
    return retained


def export_images(
    source: Path,
    output: Path,
    keep_download: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    manifest = find_manifest(source)
    payload = build_payload(manifest)
    output, _output_existed, output_snapshot = validate_image_destination(
        manifest, payload, output, force
    )
    prevalidated_parent = capture_path_snapshot(output.parent)
    agent_browser = ensure_agent_browser()
    image_cls, draw_cls, image_font = ensure_pillow()
    # Fail before starting a browser or creating staging output. The CDP helper
    # imports this lazily as well, but dependency checks belong in preflight.
    ensure_websocket()
    cdp_port = ensure_debug_chrome()
    previous_output_backup: Optional[Path] = None
    images: List[Path] = []
    page_paths: List[str] = []

    log(f"manifest: {manifest}")
    with AnchoredDirectory.open(output.parent, create=True) as output_capability:
        if prevalidated_parent.exists and not _same_root_identity(
            output_capability.identity,
            prevalidated_parent,
        ):
            raise ExportError(
                f"image output parent changed after validation: {output.parent}"
            )
        output, _output_existed, output_snapshot = validate_image_destination(
            manifest,
            payload,
            output,
            force,
            directory_capability=output_capability,
        )
        capability_staged_output = sibling_staging_directory(output)
        capability_staged_root: Optional[PathSnapshot] = None
        with temporary_directory(prefix="open-kimi-ppt-images-") as temp_name:
            temp_dir = Path(temp_name)
            staged_output = temp_dir / "staged-images"
            staged_output.mkdir()
            (staged_output / OUTPUT_MARKER).write_text(
                json.dumps(OUTPUT_MARKER_CONTENT, sort_keys=True),
                encoding="utf-8",
            )
            download_dir = temp_dir / "downloads"
            download_dir.mkdir()
            prepare_host_assets(temp_dir, payload)
            server, thread, url = serve(temp_dir)
            session = f"open-kimi-ppt-images-{os.getpid()}-{uuid.uuid4().hex[:8]}"
            browser = BrowserSession(
                agent_browser,
                session,
                temp_dir,
                download_dir,
                cdp_port,
            )
            try:
                log("opening the public Kimi slide editor")
                browser.open(url)
                browser.run(
                    [
                        "wait",
                        "--fn",
                        KIMI_WRITER_UI.ready_expression,
                    ],
                    timeout=120,
                )
                browser.run(["set", "viewport", "1280", "720"])
                snapshot = browser.snapshot()
                export_ref = writer_control_ref(snapshot, "export")
                browser.run(["click", f"@{export_ref}"])
                dialog = wait_for_export_dialog(browser)

                select_image_format(browser)
                dialog = wait_for_export_dialog(browser)

                started_at = time.time() - 1.0
                download_ref = writer_control_ref(dialog, "download")
                log("rendering page images in the browser")
                browser.run(["click", f"@{download_ref}"], timeout=300)
                downloaded = find_download(
                    (download_dir,),
                    timeout=240,
                    accept=is_image_zip,
                    since=started_at,
                    maximum_bytes=MAX_IMAGE_ARCHIVE_BYTES,
                )
            finally:
                for name, cleanup in (
                    ("browser", browser.close),
                    ("HTTP server", server.shutdown),
                    ("HTTP socket", server.server_close),
                    ("HTTP thread", lambda: thread.join(timeout=2)),
                ):
                    try:
                        cleanup()
                    except Exception as exc:
                        log(f"warning: failed to close {name}: {exc}")

            images = unzip_images(downloaded, staged_output / "pages")
            page_paths = validate_page_count(images, payload)
            if keep_download:
                copy_file_bounded(
                    downloaded,
                    staged_output / "browser-raw.zip",
                    MAX_IMAGE_ARCHIVE_BYTES,
                )
            staged_overview = stitch_overview(
                images,
                staged_output / "overview.jpg",
                image_cls,
                draw_cls,
                image_font,
            )
            if not staged_overview.is_file():
                raise ExportError(
                    f"overview stitching produced no output: {staged_overview}"
                )

            try:
                capability_staged_root = copy_tree_into_capability(
                    staged_output,
                    output_capability,
                    capability_staged_output.name,
                )
                output_capability.assert_path_binding()
                previous_output_backup = commit_staged_directory(
                    capability_staged_output,
                    output,
                    replace_existing=force,
                    expected_snapshot=output_snapshot,
                    directory_capability=output_capability,
                )
                output_capability.assert_path_binding()
            finally:
                cleanup_capability_directory(
                    output_capability,
                    capability_staged_output.name,
                    capability_staged_root,
                    label="image staging directory",
                )

    mapping = [
        {
            "index": index,
            "image": f"pages/{path.name}",
            "page": page_paths[index - 1],
        }
        for index, path in enumerate(images, start=1)
    ]
    summary = {
        "pages": len(images),
        "overview": str(output / "overview.jpg"),
        "output": str(output),
        "images": mapping,
    }
    if keep_download:
        summary["browserRawOutput"] = str(output / "browser-raw.zip")
    if previous_output_backup is not None:
        summary["previousOutputBackup"] = str(previous_output_backup)
    return summary


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export a PPTD project as page images via Kimi's public editor, unzip "
            "them, and stitch an overview image for visual QA."
        )
    )
    parser.add_argument("input", type=Path, help=".pptd manifest or project directory")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="output directory (default: <project>/.qa-images)",
    )
    parser.add_argument(
        "--keep-browser-raw",
        action="store_true",
        help="also keep the downloaded images ZIP beside the overview",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "replace an existing output directory while retaining one reported "
            "sibling backup; move/remove it before the next forced export"
        ),
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        manifest = find_manifest(args.input)
        output = args.output or manifest.parent / ".qa-images"
        summary = export_images(manifest, output, args.keep_browser_raw, args.force)
    except (ExportError, OSError, subprocess.SubprocessError) as exc:
        print(f"open-kimi-ppt image export failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
