#!/usr/bin/env python3
"""Export a PPTD project through Kimi's public browser-side PPTX writer.

The temporary localhost host supplies the manifest, pages, and local-image data
URLs to the public Kimi iframe for execution. That iframe is a network trust
boundary even though the project is not uploaded through a document-upload API;
the official editor may also fetch referenced remote resources.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import contextvars
import ctypes
import errno
import importlib.util
import json
import mimetypes
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
import zipfile
import xml.etree.ElementTree as ET
import xml.parsers.expat as expat
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple
from urllib.parse import urlsplit

SKILL_DIR = Path(__file__).resolve().parent.parent
HOST_TEMPLATE = Path(__file__).with_name("export_host.html")
PENPAL_MODULE = Path(__file__).with_name("penpal.mjs")
IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_EMBEDDED_MEDIA_BYTES = 200 * 1024 * 1024
MAX_TEXT_FILE_BYTES = 20 * 1024 * 1024
MAX_DECK_TEXT_BYTES = 100 * 1024 * 1024
MAX_DECK_PAGES = 500
MAX_YAML_DEPTH = 100
MAX_YAML_NODES = 200_000
MAX_YAML_NUMERIC_SCALAR_DIGITS = 4096
YAML_IMPLICIT_INTEGER_RE = re.compile(
    r"[+-]?(?:0b[0-1_]+|0[0-7_]+|(?:0|[1-9][0-9_]*)|"
    r"0x[0-9A-Fa-f_]+|[1-9][0-9_]*(?::[0-5]?[0-9])+)$"
)
MAX_IMAGE_REFERENCES = 5_000
MAX_LOCAL_IMAGE_PATH_CHARS = 4096
MAX_MANIFEST_SCAN_ENTRIES = 10_000
MAX_MANIFEST_SCAN_DEPTH = 64
MAX_INPUT_SNAPSHOT_ATTEMPTS = 3
MAX_PPTX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_PPTX_ARCHIVE_MEMBERS = 20_000
MAX_PPTX_MEMBER_BYTES = 128 * 1024 * 1024
MAX_PPTX_XML_MEMBER_BYTES = 20 * 1024 * 1024
MAX_PPTX_XML_NODES = 250_000
MAX_PPTX_XML_DEPTH = 256
MAX_PPTX_TOTAL_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_CONTENT_TYPES_BYTES = 4 * 1024 * 1024
MAX_PPTX_CENTRAL_DIRECTORY_BYTES = 64 * 1024 * 1024
MAX_ZIP_MEMBER_NAME_BYTES = 65_535
MAX_PPTX_MEMBER_NAME_CHARS = 4096
MAX_COMMAND_OUTPUT_BYTES = 8 * 1024 * 1024
UNTRUSTED_ZIP_ERRORS = (
    OSError,
    KeyError,
    UnicodeError,
    ValueError,
    RuntimeError,
    NotImplementedError,
    zipfile.BadZipFile,
    zipfile.LargeZipFile,
)
PPTX_BLOB_CHUNK_BYTES = 1024 * 1024
PPTX_BLOB_WAIT_SECONDS = 180.0
PPTX_FONT_ATTEMPT_SECONDS = 75.0
KIMI_WRITER_ORIGIN = "https://www.kimi.com"
KIMI_WRITER_PATH_PREFIX = "/neo-ppt"
OOPIF_URL_HINT = f"kimi.com{KIMI_WRITER_PATH_PREFIX}"
KIMI_WRITER_HOSTS = frozenset({urlsplit(KIMI_WRITER_ORIGIN).hostname or ""})
PPTX_PACKAGE_MIME = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)
PPTX_BLOB_MIME_TYPES = {
    PPTX_PACKAGE_MIME,
    "application/octet-stream",
    "application/zip",
    "application/x-zip-compressed",
}
PPTX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
)
CONTENT_TYPES_NAMESPACE = (
    "http://schemas.openxmlformats.org/package/2006/content-types"
)
PRESENTATION_NAMESPACE = (
    "http://schemas.openxmlformats.org/presentationml/2006/main"
)
OFFICE_RELATIONSHIPS_NAMESPACE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
PACKAGE_RELATIONSHIPS_NAMESPACE = (
    "http://schemas.openxmlformats.org/package/2006/relationships"
)
PRESENTATION_SLIDE_RELATIONSHIP_TYPE = (
    f"{OFFICE_RELATIONSHIPS_NAMESPACE}/slide"
)
OFFICE_DOCUMENT_RELATIONSHIP_TYPE = (
    f"{OFFICE_RELATIONSHIPS_NAMESPACE}/officeDocument"
)
PRESENTATION_SLIDE_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"
)
FADE_TRANSITION_XML = (
    '<p:transition spd="fast" advClick="1"><p:fade/></p:transition>'
)
MIN_AGENT_BROWSER_VERSION = (0, 33, 2)
MAX_TESTED_AGENT_BROWSER_VERSION = (0, 33, 2)
ALLOW_UNTESTED_AGENT_BROWSER_ENV = "OPEN_KIMI_PPT_ALLOW_UNTESTED_AGENT_BROWSER"
MIN_NODE_MAJOR = 24
NODE_INSTALL_HINT = "Install Node.js 24+ from https://nodejs.org, then retry."
AGENT_BROWSER_INSTALL_HINT = (
    "Install the tested version with: npm install --global agent-browser@0.33.2"
)


class ExportError(RuntimeError):
    pass


def _quality_gate_module() -> Any:
    module_name = "_open_deck_export_pptd_quality"
    module = sys.modules.get(module_name)
    if module is None:
        path = Path(__file__).with_name("pptd_quality.py")
        specification = importlib.util.spec_from_file_location(module_name, path)
        if specification is None or specification.loader is None:
            raise ExportError("could not load the bundled PPTD quality gate")
        module = importlib.util.module_from_spec(specification)
        sys.modules[module_name] = module
        try:
            specification.loader.exec_module(module)
        except Exception as exc:
            sys.modules.pop(module_name, None)
            raise ExportError(f"could not load the bundled PPTD quality gate: {exc}") from exc
    return module


def verify_quality_gate(manifest: Path, report: Path) -> Dict[str, Any]:
    """Load the bundled linter and reject a weak, stale, or tampered report."""

    module = _quality_gate_module()
    try:
        receipt = module.verify_quality_report(
            manifest, report, minimum_fail_on="warning"
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise ExportError(f"PPTD quality gate rejected export: {exc}") from exc
    media = receipt.get("media", {})
    if media.get("referenced", 0) and (
        media.get("tracked") != media.get("referenced")
        or receipt.get("media_sources_sha256") is None
    ):
        raise ExportError(
            "PPTD quality gate rejected export: referenced local media must be "
            "fully tracked by a bound provenance manifest"
        )
    return receipt


def quality_gate_bound_inputs(manifest: Path, report: Path) -> Tuple[Path, ...]:
    """Resolve all source files bound by a quality report for overwrite checks."""

    module = _quality_gate_module()
    try:
        return module.quality_report_bound_inputs(report, manifest.parent)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ExportError(
            f"could not resolve quality-report source bindings: {exc}"
        ) from exc


class AmbiguousUiContractError(ExportError):
    pass


class SemanticVersion(NamedTuple):
    major: int
    minor: int
    patch: int
    prerelease: Tuple[str, ...] = ()
    build: Tuple[str, ...] = ()

    @property
    def core(self) -> Tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    @property
    def is_plain_release(self) -> bool:
        return not self.prerelease and not self.build

    def __str__(self) -> str:
        value = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            value += "-" + ".".join(self.prerelease)
        if self.build:
            value += "+" + ".".join(self.build)
        return value


class KimiWriterUiContract(NamedTuple):
    export_name: str
    download_name: str
    image_format_name: str
    font_name_tokens: Tuple[str, ...]
    format_selector: str
    active_format_selector: str
    ready_expression: str


KIMI_WRITER_UI = KimiWriterUiContract(
    export_name="导出",
    download_name="下载",
    image_format_name="图片",
    font_name_tokens=("字体", "font"),
    format_selector=".radio-group-item",
    active_format_selector=".radio-group-item.active",
    ready_expression='document.documentElement.dataset.deckStatus === "ready"',
)


class PptxWriterTimeout(ExportError):
    pass


class CdpOperationTimeout(ExportError):
    pass


def preflight_zip_central_directory(
    stream: Any,
    compressed_size: int,
    *,
    max_members: int,
    max_directory_bytes: int,
    display_name: str,
    require_canonical_member_names: bool = False,
) -> None:
    """Bound ZIP metadata before zipfile materializes every ZipInfo object."""
    eocd_size = 22
    maximum_comment = 65_535
    original_position = stream.tell()
    try:
        if compressed_size < eocd_size:
            raise ExportError(f"invalid ZIP end record: {display_name}")
        tail_size = min(compressed_size, eocd_size + maximum_comment)
        stream.seek(compressed_size - tail_size)
        tail = stream.read(tail_size)
        if not isinstance(tail, (bytes, bytearray)) or len(tail) != tail_size:
            raise ExportError(f"could not read ZIP end record: {display_name}")

        signature = b"PK\x05\x06"
        position = tail.rfind(signature)
        while position >= 0:
            if position + eocd_size <= len(tail):
                comment_length = int.from_bytes(
                    tail[position + 20 : position + 22], "little"
                )
                if position + eocd_size + comment_length == len(tail):
                    break
            position = tail.rfind(signature, 0, position)
        if position < 0:
            raise ExportError(f"invalid ZIP end record: {display_name}")

        record = tail[position : position + eocd_size]
        disk_number = int.from_bytes(record[4:6], "little")
        directory_disk = int.from_bytes(record[6:8], "little")
        entries_on_disk = int.from_bytes(record[8:10], "little")
        entry_count = int.from_bytes(record[10:12], "little")
        directory_size = int.from_bytes(record[12:16], "little")
        directory_offset = int.from_bytes(record[16:20], "little")
        if disk_number or directory_disk or entries_on_disk != entry_count:
            raise ExportError(f"multi-disk ZIP archives are not supported: {display_name}")
        if (
            entry_count == 0xFFFF
            or directory_size == 0xFFFFFFFF
            or directory_offset == 0xFFFFFFFF
        ):
            # Both exporters cap archives far below ZIP64's thresholds. Reject
            # the more complex metadata format rather than parsing an attacker-
            # controlled second end record before applying resource limits.
            raise ExportError(f"ZIP64 metadata is outside export limits: {display_name}")
        if entry_count > max_members:
            raise ExportError(
                f"ZIP contains more than {max_members} members: {display_name}"
            )
        if directory_size > max_directory_bytes:
            raise ExportError(
                "ZIP central directory exceeds the metadata safety limit: "
                f"{display_name}"
            )

        eocd_offset = compressed_size - tail_size + position
        if directory_offset + directory_size != eocd_offset:
            raise ExportError(
                f"ZIP central-directory offsets are inconsistent: {display_name}"
            )
        stream.seek(directory_offset)
        remaining = directory_size
        observed = 0
        while remaining:
            if remaining < 46:
                raise ExportError(f"truncated ZIP central directory: {display_name}")
            header = stream.read(46)
            if len(header) != 46 or header[:4] != b"PK\x01\x02":
                raise ExportError(f"invalid ZIP central directory: {display_name}")
            filename_length = int.from_bytes(header[28:30], "little")
            extra_length = int.from_bytes(header[30:32], "little")
            comment_length = int.from_bytes(header[32:34], "little")
            disk_start = int.from_bytes(header[34:36], "little")
            record_size = 46 + filename_length + extra_length + comment_length
            if (
                disk_start != 0
                or filename_length == 0
                or filename_length > MAX_ZIP_MEMBER_NAME_BYTES
                or record_size > remaining
            ):
                raise ExportError(f"invalid ZIP member metadata: {display_name}")
            observed += 1
            if observed > max_members:
                raise ExportError(
                    f"ZIP contains more than {max_members} members: {display_name}"
                )
            filename_bytes = stream.read(filename_length)
            if len(filename_bytes) != filename_length:
                raise ExportError(f"truncated ZIP member name: {display_name}")
            flags = int.from_bytes(header[8:10], "little")
            encoding = "utf-8" if flags & 0x800 else "cp437"
            try:
                filename = filename_bytes.decode(encoding)
            except UnicodeError as exc:
                raise ExportError(
                    f"invalid ZIP member name encoding: {display_name}"
                ) from exc
            if require_canonical_member_names:
                validate_zip_member_name(filename)
            stream.seek(extra_length + comment_length, os.SEEK_CUR)
            remaining -= record_size
        if observed != entry_count:
            raise ExportError(f"ZIP member count is inconsistent: {display_name}")
    finally:
        stream.seek(original_position)


def validate_zip_member_name(name: str) -> None:
    if (
        not isinstance(name, str)
        or not name
        or len(name) > MAX_PPTX_MEMBER_NAME_CHARS
        or "\0" in name
        or "\\" in name
        or name.startswith("/")
        or re.match(r"^[A-Za-z]:", name)
    ):
        raise ExportError(f"PPTX archive contains an unsafe member name: {name!r}")
    parts = name.split("/")
    if parts[-1] == "":
        parts.pop()
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ExportError(f"PPTX archive contains an unsafe member name: {name!r}")


class InputChangedError(ExportError):
    pass


class PptxAttemptResult(NamedTuple):
    effective_fonts: Optional[bool]
    cdp_blob_capture_used: bool


def pip_install_hint(package: str) -> str:
    executable = Path(sys.executable).resolve()
    if os.name == "nt":
        quoted = f'"{executable}"'
        return (
            f"PowerShell: & {quoted} -m pip install {package}; "
            f"cmd.exe: {quoted} -m pip install {package}"
        )
    return f'"{executable}" -m pip install {package}'


class QuietHandler(SimpleHTTPRequestHandler):
    allowed_paths = frozenset(
        {"/export_host.html", "/payload.json", "/penpal.mjs"}
    )
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".mjs": "text/javascript; charset=utf-8",
    }

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _host_is_allowed(self) -> bool:
        expected = getattr(self.server, "allowed_host", None)
        return isinstance(expected, str) and self.headers.get("Host") == expected

    def _resource_path(self, request_path: Optional[str] = None) -> Optional[str]:
        capability = getattr(self.server, "capability_prefix", None)
        path = self.path if request_path is None else request_path
        if not isinstance(capability, str) or not path.startswith(f"{capability}/"):
            return None
        resource = path[len(capability) :]
        return resource if resource in self.allowed_paths else None

    def _serve_if_allowed(self, method: Callable[[], None]) -> None:
        if not self._host_is_allowed():
            self.send_error(403, "invalid localhost Host header")
            return
        if self._resource_path() is None:
            self.send_error(404, "localhost export resource not found")
            return
        method()

    def translate_path(self, path: str) -> str:
        resource = self._resource_path(path)
        if resource is None:
            return str(Path(self.directory) / ".forbidden")
        return str(Path(self.directory) / resource.lstrip("/"))

    def do_GET(self) -> None:
        self._serve_if_allowed(super().do_GET)

    def do_HEAD(self) -> None:
        self._serve_if_allowed(super().do_HEAD)

    def end_headers(self) -> None:
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; "
            "frame-src https://www.kimi.com; "
            "object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'none'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        super().end_headers()


def log(message: str) -> None:
    print(f"[open-kimi-ppt] {message}", file=sys.stderr, flush=True)


def run_command(
    command: Sequence[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: int = 90,
) -> subprocess.CompletedProcess[str]:
    """Capture merged stdout/stderr via a temp file.

    On Windows, agent-browser's detached daemon can inherit a PIPE handle and
    prevent EOF, deadlocking ``subprocess.run(stdout=PIPE)``. Decoding with the
    system locale (GBK on zh-CN Windows) can also raise UnicodeDecodeError.
    Writing bytes to a bounded temporary file and decoding explicitly avoids
    both failures without retaining unbounded command output in memory.
    """
    handle, sink_path = tempfile.mkstemp(prefix="open-kimi-ppt-", suffix=".log")
    os.close(handle)
    sink = Path(sink_path)
    output = ""
    returncode = -1
    timed_out = False
    output_overflow = False
    try:
        with sink.open("wb") as out:
            process = subprocess.Popen(
                list(command),
                cwd=str(cwd) if cwd is not None else None,
                env=env,
                stdout=out,
                stderr=subprocess.STDOUT,
            )
            deadline = time.monotonic() + timeout
            while True:
                returncode = process.poll()
                try:
                    output_size = os.fstat(out.fileno()).st_size
                except OSError:
                    output_size = 0
                if output_size > MAX_COMMAND_OUTPUT_BYTES:
                    output_overflow = True
                if output_overflow:
                    if returncode is None:
                        process.kill()
                    try:
                        returncode = process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        returncode = -1
                    break
                if returncode is not None:
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    process.kill()
                    try:
                        returncode = process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        returncode = -1
                    break
                time.sleep(0.05)
        try:
            size = sink.stat().st_size
            with sink.open("rb") as source:
                if size > MAX_COMMAND_OUTPUT_BYTES:
                    source.seek(-MAX_COMMAND_OUTPUT_BYTES, os.SEEK_END)
                    prefix = "[earlier command output truncated]\n"
                else:
                    prefix = ""
                output = prefix + source.read(MAX_COMMAND_OUTPUT_BYTES).decode(
                    "utf-8", errors="replace"
                )
        except OSError:
            output = ""
        if output_overflow:
            raise ExportError(
                "agent-browser command output exceeded the 8 MiB safety limit"
            )
        if timed_out:
            raise subprocess.TimeoutExpired(
                cmd=list(command),
                timeout=timeout,
                output=output,
            )
    except subprocess.TimeoutExpired as exc:
        raise subprocess.TimeoutExpired(
            cmd=list(command),
            timeout=timeout,
            output=exc.output if exc.output is not None else output,
        ) from exc
    finally:
        try:
            sink.unlink(missing_ok=True)
        except OSError:
            # WinError 32: daemon may still hold the log file handle.
            pass
    return subprocess.CompletedProcess(list(command), returncode, output, None)


def temporary_directory(prefix: str) -> Any:
    # ignore_cleanup_errors avoids masking the real export error when a Windows
    # browser daemon still holds files under the temp tree (Python 3.10+).
    try:
        return tempfile.TemporaryDirectory(prefix=prefix, ignore_cleanup_errors=True)
    except TypeError:
        return tempfile.TemporaryDirectory(prefix=prefix)


def ensure_pyyaml() -> Any:
    try:
        import yaml
    except ImportError as exc:
        raise ExportError(
            "PyYAML is required for PPTD export. Install it in the active Python "
            f"environment with: {pip_install_hint('pyyaml')}"
        ) from exc
    return yaml


SEMVER_PRERELEASE_IDENTIFIER = (
    r"(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
)
SEMVER_PATTERN = re.compile(
    r"(?<![0-9A-Za-z.+-])v?"
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    rf"(?:-({SEMVER_PRERELEASE_IDENTIFIER}"
    rf"(?:\.{SEMVER_PRERELEASE_IDENTIFIER})*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?![0-9A-Za-z.+-])"
)


def parse_version(output: str) -> SemanticVersion:
    matches = list(SEMVER_PATTERN.finditer(output))
    if len(matches) != 1:
        raise ExportError(
            "could not parse exactly one complete agent-browser semantic version "
            f"from: {output.strip()}"
        )
    match = matches[0]
    prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
    build = tuple(match.group(5).split(".")) if match.group(5) else ()
    return SemanticVersion(
        *(int(match.group(index)) for index in range(1, 4)),
        prerelease=prerelease,
        build=build,
    )


def parse_node_version(output: str) -> Tuple[int, int, int]:
    match = re.search(r"v?(\d+)\.(\d+)\.(\d+)\b", output)
    if not match:
        raise ExportError(f"could not parse Node.js version from: {output.strip()}")
    return tuple(int(part) for part in match.groups())


def read_agent_browser_version(executable: str) -> SemanticVersion:
    process = run_command([executable, "--version"], timeout=30)
    if process.returncode != 0:
        raise ExportError(f"agent-browser --version failed:\n{process.stdout[-2000:]}")
    return parse_version(process.stdout)


def ensure_nodejs() -> str:
    executable = shutil.which("node")
    if not executable:
        raise ExportError(f"Node.js is not installed or not on PATH. {NODE_INSTALL_HINT}")

    process = run_command([executable, "--version"], timeout=30)
    if process.returncode != 0:
        raise ExportError(f"node --version failed:\n{process.stdout[-2000:]}")

    version = parse_node_version(process.stdout)
    if version[0] < MIN_NODE_MAJOR:
        raise ExportError(
            f"Node.js {MIN_NODE_MAJOR}+ is required; found "
            f"{'.'.join(map(str, version))} ({process.stdout.strip()}). {NODE_INSTALL_HINT}"
        )

    npm = shutil.which("npm")
    if not npm:
        raise ExportError(
            "npm is not installed or not on PATH. "
            f"npm ships with Node.js. {NODE_INSTALL_HINT}"
        )

    log(f"Node.js version: {'.'.join(map(str, version))}")
    return executable


def ensure_agent_browser() -> str:
    ensure_nodejs()

    executable = shutil.which("agent-browser")
    if not executable:
        raise ExportError(f"agent-browser is not installed. {AGENT_BROWSER_INSTALL_HINT}")
    version = read_agent_browser_version(executable)
    minimum = ".".join(map(str, MIN_AGENT_BROWSER_VERSION))
    maximum = ".".join(map(str, MAX_TESTED_AGENT_BROWSER_VERSION))
    if version.core < MIN_AGENT_BROWSER_VERSION:
        raise ExportError(
            "agent-browser is below the required version "
            f"{minimum}: {version}. "
            f"{AGENT_BROWSER_INSTALL_HINT}"
        )
    tested_release = (
        MIN_AGENT_BROWSER_VERSION
        <= version.core
        <= MAX_TESTED_AGENT_BROWSER_VERSION
        and version.is_plain_release
    )
    if not tested_release:
        override = os.environ.get(ALLOW_UNTESTED_AGENT_BROWSER_ENV, "").strip().casefold()
        if override not in {"1", "true", "yes"}:
            raise ExportError(
                "agent-browser is outside the tested compatibility contract "
                f"(official releases {minimum}..{maximum} only): {version}. "
                f"{AGENT_BROWSER_INSTALL_HINT}, or set "
                f"{ALLOW_UNTESTED_AGENT_BROWSER_ENV}=1 to explicitly accept the "
                "untested remote-UI contract risk"
            )
        log(
            "warning: continuing with untested agent-browser version "
            f"{version} because "
            f"{ALLOW_UNTESTED_AGENT_BROWSER_ENV}=1"
        )
    log(f"agent-browser version: {version}")
    return executable


CDP_OWNERSHIP_MARKER = ".open-kimi-ppt-cdp.json"
CDP_OWNERSHIP_MARKER_BYTES = 4096
DEVTOOLS_ACTIVE_PORT = "DevToolsActivePort"
CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    str(Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def cdp_endpoint_identity(port: int) -> Optional[str]:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version",
            timeout=2,
        ) as response:
            raw = response.read(CDP_OWNERSHIP_MARKER_BYTES + 1)
        if len(raw) > CDP_OWNERSHIP_MARKER_BYTES:
            return None
        payload = json.loads(raw.decode("utf-8"))
        endpoint = payload.get("webSocketDebuggerUrl") if isinstance(payload, dict) else None
        if not isinstance(endpoint, str):
            return None
        match = re.fullmatch(
            rf"ws://(?:127\.0\.0\.1|localhost):{port}(/devtools/browser/[A-Za-z0-9._-]+)",
            endpoint,
        )
        return endpoint if match else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def cdp_alive(port: int) -> bool:
    return cdp_endpoint_identity(port) is not None


def _read_bounded_regular_file(path: Path, maximum: int) -> Optional[bytes]:
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            return None
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_dev != before.st_dev
                or opened.st_ino != before.st_ino
                or opened.st_size > maximum
            ):
                return None
            chunks: List[bytes] = []
            remaining = maximum + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(remaining, 4096))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            if len(raw) > maximum:
                return None
            after = os.fstat(descriptor)
            if (
                after.st_size != opened.st_size
                or after.st_mtime_ns != opened.st_mtime_ns
                or after.st_ctime_ns != opened.st_ctime_ns
            ):
                return None
            return raw
        finally:
            os.close(descriptor)
    except OSError:
        return None


def _profile_marker_payload(profile: str, port: int, endpoint: str) -> Dict[str, Any]:
    return {
        "owner": "open-kimi-ppt",
        "version": 1,
        "profile": str(PureWindowsPath(profile)).casefold(),
        "port": port,
        "endpoint": endpoint,
    }


def _read_profile_marker(profile: str) -> Optional[Dict[str, Any]]:
    raw = _read_bounded_regular_file(
        Path(profile) / CDP_OWNERSHIP_MARKER,
        CDP_OWNERSHIP_MARKER_BYTES,
    )
    if raw is None:
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_profile_marker(profile: str, port: int, endpoint: str) -> None:
    marker = Path(profile) / CDP_OWNERSHIP_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        _profile_marker_payload(profile, port, endpoint),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > CDP_OWNERSHIP_MARKER_BYTES:
        raise ExportError("internal CDP ownership marker exceeds its safety limit")
    temporary = marker.with_name(f".{marker.name}.{uuid.uuid4().hex}.staging")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
        0o600,
    )
    try:
        view = memoryview(encoded)
        while view:
            count = os.write(descriptor, view)
            if count <= 0:
                raise ExportError("CDP ownership marker write made no progress")
            view = view[count:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, marker)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def owned_debug_chrome_port(profile: str) -> Optional[int]:
    marker = _read_profile_marker(profile)
    if marker is None:
        return None
    port = marker.get("port")
    endpoint = marker.get("endpoint")
    if (
        marker.get("owner") != "open-kimi-ppt"
        or marker.get("version") != 1
        or marker.get("profile") != str(PureWindowsPath(profile)).casefold()
        or isinstance(port, bool)
        or not isinstance(port, int)
        or not 1 <= port <= 65535
        or not isinstance(endpoint, str)
    ):
        return None
    return port if cdp_endpoint_identity(port) == endpoint else None


def profile_has_valid_ownership_marker(profile: str) -> bool:
    marker = _read_profile_marker(profile)
    return bool(
        marker is not None
        and marker.get("owner") == "open-kimi-ppt"
        and marker.get("version") == 1
        and marker.get("profile") == str(PureWindowsPath(profile)).casefold()
        and isinstance(marker.get("port"), int)
        and not isinstance(marker.get("port"), bool)
        and 1 <= marker["port"] <= 65535
        and isinstance(marker.get("endpoint"), str)
    )


def _devtools_active_port(profile: str) -> Optional[Tuple[int, str, Tuple[int, ...]]]:
    path = Path(profile) / DEVTOOLS_ACTIVE_PORT
    raw = _read_bounded_regular_file(path, CDP_OWNERSHIP_MARKER_BYTES)
    if raw is None:
        return None
    try:
        lines = raw.decode("utf-8").splitlines()
        port = int(lines[0])
        browser_path = lines[1]
        info = path.lstat()
    except (OSError, UnicodeError, ValueError, IndexError):
        return None
    if not 1 <= port <= 65535 or not re.fullmatch(
        r"/devtools/browser/[A-Za-z0-9._-]+", browser_path
    ):
        return None
    endpoint = cdp_endpoint_identity(port)
    if endpoint is None or not endpoint.endswith(browser_path):
        return None
    version = (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )
    return port, endpoint, version


def start_owned_debug_chrome(
    executable: str,
    profile: str,
    *,
    allow_unowned_nonempty: bool = False,
) -> int:
    profile_path = Path(profile)
    if profile_path.is_symlink():
        raise ExportError("dedicated Chrome profile must not be a symbolic link")
    if profile_path.exists():
        if not profile_path.is_dir():
            raise ExportError(f"dedicated Chrome profile is not a directory: {profile}")
        try:
            with os.scandir(profile_path) as entries:
                nonempty = next(entries, None) is not None
        except OSError as exc:
            raise ExportError(f"cannot inspect dedicated Chrome profile: {profile}") from exc
        if (
            nonempty
            and not allow_unowned_nonempty
            and not profile_has_valid_ownership_marker(profile)
        ):
            raise ExportError(
                "AGENT_BROWSER_PROFILE is non-empty but has no valid open-kimi-ppt "
                "ownership marker; choose a new empty dedicated directory instead "
                "of an everyday browser profile"
            )
    profile_path.mkdir(parents=True, exist_ok=True)
    if not profile_path.is_dir():
        raise ExportError(f"dedicated Chrome profile is not a directory: {profile}")
    active_before = _devtools_active_port(profile)
    previous_version = active_before[2] if active_before is not None else None
    log(f"starting a dedicated debug browser: {executable}")
    subprocess.Popen(
        [
            executable,
            f"--user-data-dir={profile}",
            "--remote-debugging-port=0",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-position=-2400,0",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=(
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        ),
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        active = _devtools_active_port(profile)
        if active is not None and active[2] != previous_version:
            port, endpoint, _version = active
            _write_profile_marker(profile, port, endpoint)
            return port
        time.sleep(0.5)
    raise ExportError("dedicated debug browser did not publish a verified CDP endpoint within 20s")


def windows_debug_profile_argument(raw_profile: Optional[str]) -> str:
    """Resolve only explicit, dedicated Windows profile directories."""
    if not raw_profile:
        return str(Path(tempfile.gettempdir()) / "okp-cdp-profile")
    profile = PureWindowsPath(raw_profile)
    if not profile.is_absolute():
        raise ExportError(
            "on Windows, AGENT_BROWSER_PROFILE must be an absolute path to a "
            "dedicated Chrome user-data directory; profile names such as Default "
            "or Profile 1 are intentionally not resolved"
        )
    leaf = profile.name.casefold()
    if (
        leaf in {"default", "user data"}
        or re.fullmatch(r"profile\s+\d+", leaf)
    ):
        raise ExportError(
            "AGENT_BROWSER_PROFILE must point to a dedicated user-data directory, "
            "not the User Data root or a Default/Profile N directory from a daily "
            "browser profile"
        )
    return str(profile)


def safe_expanduser_path(path: Path, *, description: str) -> Path:
    """Expand a user-supplied path without leaking platform path exceptions."""
    try:
        return path.expanduser()
    except (OSError, RuntimeError, KeyError, ValueError, UnicodeError) as exc:
        raise ExportError(f"invalid {description} path: {os.fspath(path)!r}") from exc


def safe_resolve_path(path: Path, *, description: str) -> Path:
    """Resolve a user-supplied path and normalize symlink-loop failures."""
    try:
        return path.resolve()
    except (OSError, RuntimeError, ValueError, UnicodeError) as exc:
        raise ExportError(f"invalid {description} path: {os.fspath(path)!r}") from exc


def ensure_debug_chrome() -> Optional[int]:
    """Return a CDP port for agent-browser to connect to (Windows only).

    On Windows agent-browser cannot launch Chrome itself: the Chrome launcher
    process hands off to a child and exits, which agent-browser mistakes for a
    crash ("Chrome exited early without writing DevToolsActivePort"). The
    export therefore always drives an externally started browser. An explicit
    working AGENT_BROWSER_CDP is treated as user authorization. Automatic mode
    reuses only an endpoint whose identity is recorded inside the dedicated
    profile; otherwise it starts a new browser on a Chrome-selected free port.
    A live but unowned common debugging port is never attached.
    """
    if sys.platform != "win32":
        return None

    explicit = os.environ.get("AGENT_BROWSER_CDP")
    if explicit:
        try:
            if cdp_alive(int(explicit)):
                return int(explicit)
        except ValueError:
            pass
        log(f"AGENT_BROWSER_CDP={explicit} is not answering; starting a debug browser instead")

    raw_profile = os.environ.get("AGENT_BROWSER_PROFILE")
    profile = windows_debug_profile_argument(raw_profile)
    owned_port = owned_debug_chrome_port(profile)
    if owned_port is not None:
        return owned_port

    executable = next((c for c in CHROME_CANDIDATES if Path(c).is_file()), None)
    if executable is None:
        raise ExportError(
            "no Chrome or Edge found to drive the export; install Google Chrome, "
            "or start a browser with --remote-debugging-port yourself and set "
            "AGENT_BROWSER_CDP to that port"
        )
    return start_owned_debug_chrome(
        executable,
        profile,
        allow_unowned_nonempty=bool(raw_profile),
    )


def find_manifest(source: Path) -> Path:
    source = safe_expanduser_path(source, description="PPTD input")
    if source.is_symlink():
        raise ExportError(f"PPTD manifest input must not be a symbolic link: {source}")
    source = safe_resolve_path(source, description="PPTD input")
    if source.is_file():
        if source.suffix.lower() != ".pptd":
            raise ExportError(f"input must be a .pptd file or project directory: {source}")
        _remember_manifest_parent(source)
        return source
    if not source.is_dir():
        raise ExportError(f"input does not exist: {source}")
    manifests: List[Path] = []
    scanned_entries = 0
    pending: List[Tuple[Path, int]] = [(source, 0)]
    while pending:
        directory, depth = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    scanned_entries += 1
                    if scanned_entries > MAX_MANIFEST_SCAN_ENTRIES:
                        raise ExportError(
                            "project scan exceeds the entry limit of "
                            f"{MAX_MANIFEST_SCAN_ENTRIES}"
                        )
                    entry_path = Path(entry.path)
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise ExportError(
                            f"project changed during manifest discovery: {entry.path}"
                        ) from exc
                    if stat.S_ISDIR(info.st_mode):
                        child_depth = depth + 1
                        if child_depth > MAX_MANIFEST_SCAN_DEPTH:
                            raise ExportError(
                                "project scan exceeds the directory depth limit of "
                                f"{MAX_MANIFEST_SCAN_DEPTH}"
                            )
                        pending.append((entry_path, child_depth))
                    elif entry_path.suffix.lower() == ".pptd" and (
                        stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                    ):
                        manifests.append(entry_path)
        except OSError as exc:
            raise ExportError(f"cannot scan project directory {directory}: {exc}") from exc
    manifests.sort()
    if not manifests:
        raise ExportError(f"no .pptd manifest found under: {source}")
    if len(manifests) > 1:
        choices = "\n  ".join(str(path) for path in manifests[:20])
        raise ExportError(
            "multiple .pptd manifests found; pass one manifest explicitly:\n  " + choices
        )
    if manifests[0].is_symlink():
        raise ExportError(
            f"discovered PPTD manifest must not be a symbolic link: {manifests[0]}"
        )
    manifest = manifests[0].resolve()
    try:
        manifest.relative_to(source)
    except ValueError as exc:
        raise ExportError(
            f"discovered PPTD manifest resolves outside the input project: {manifests[0]}"
        ) from exc
    _remember_manifest_parent(manifest)
    return manifest


def read_bounded_regular_bytes(
    path: Path,
    maximum_bytes: int,
    *,
    description: str,
    limit_label: Optional[str] = None,
) -> Tuple[bytes, PathSnapshot]:
    displayed_limit = limit_label or f"{maximum_bytes} bytes"
    capability = _ACTIVE_INPUT_CAPABILITY.get()
    relative_path: Optional[Path] = None
    if capability is not None:
        try:
            relative_path = capability.relative_path(path)
        except ExportError:
            # Standalone callers may read an unrelated trusted helper while a
            # project capture is active. Only project descendants use the
            # project capability.
            relative_path = None
    try:
        before = (
            capability.capture(relative_path)
            if capability is not None and relative_path is not None
            else capture_path_snapshot(path)
        )
        if (
            not before.exists
            or before.mode is None
            or not stat.S_ISREG(before.mode)
        ):
            raise ExportError(f"{description} must be a regular non-symlink file: {path}")
        if before.size is None or before.size > maximum_bytes:
            raise ExportError(f"{description} exceeds {displayed_limit}: {path}")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = (
            capability.open_file(relative_path, flags)
            if capability is not None and relative_path is not None
            else os.open(path, flags)
        )
    except OSError as exc:
        raise ExportError(f"cannot safely open {description} {path}: {exc}") from exc
    try:
        opened_info = os.fstat(descriptor)
        opened = PathSnapshot(
            True,
            opened_info.st_dev,
            opened_info.st_ino,
            opened_info.st_mode,
            opened_info.st_size,
            opened_info.st_mtime_ns,
            opened_info.st_ctime_ns,
        )
        if not snapshots_match(opened, before) or not stat.S_ISREG(opened_info.st_mode):
            raise InputChangedError(f"{description} changed before it could be read: {path}")
        chunks: List[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum_bytes:
                raise ExportError(
                    f"{description} exceeds {displayed_limit}: {path}"
                )
        after_info = os.fstat(descriptor)
        after = PathSnapshot(
            True,
            after_info.st_dev,
            after_info.st_ino,
            after_info.st_mode,
            after_info.st_size,
            after_info.st_mtime_ns,
            after_info.st_ctime_ns,
        )
        if total != opened_info.st_size or not snapshots_match(after, opened):
            raise InputChangedError(f"{description} changed while it was read: {path}")
    finally:
        os.close(descriptor)
    final_snapshot = (
        capability.capture(relative_path)
        if capability is not None and relative_path is not None
        else capture_path_snapshot(path)
    )
    if not snapshots_match(final_snapshot, after):
        raise InputChangedError(f"{description} path changed while it was read: {path}")
    return b"".join(chunks), after


def read_yaml_mapping(path: Path) -> Tuple[str, Dict[str, Any]]:
    raw, _snapshot = read_bounded_regular_bytes(
        path,
        MAX_TEXT_FILE_BYTES,
        description="PPTD text file",
        limit_label="20 MiB",
    )
    yaml = ensure_pyyaml()
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ExportError(f"cannot read UTF-8 PPTD file {path}: {exc}") from exc
    try:
        node_count = 0
        depth = 0
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            event_name = type(event).__name__
            if event_name == "AliasEvent" or getattr(event, "anchor", None) is not None:
                raise ExportError(f"PPTD YAML aliases/anchors are not supported: {path}")
            if event_name in ("MappingStartEvent", "SequenceStartEvent"):
                node_count += 1
                depth += 1
                if depth - 1 > MAX_YAML_DEPTH:
                    raise ExportError("PPTD YAML exceeds the nesting-depth safety limit")
            elif event_name in ("MappingEndEvent", "SequenceEndEvent"):
                depth = max(0, depth - 1)
            elif event_name == "ScalarEvent":
                node_count += 1
                scalar = getattr(event, "value", "")
                explicit_integer = getattr(event, "tag", None) == "tag:yaml.org,2002:int"
                implicit_integer = (
                    getattr(event, "tag", None) is None
                    and getattr(event, "style", None) is None
                    and isinstance(scalar, str)
                    and YAML_IMPLICIT_INTEGER_RE.fullmatch(scalar) is not None
                )
                if (
                    isinstance(scalar, str)
                    and len(scalar) > MAX_YAML_NUMERIC_SCALAR_DIGITS
                    and (explicit_integer or implicit_integer)
                ):
                    raise ExportError(
                        "PPTD YAML contains an overlong numeric scalar"
                    )
            if node_count > MAX_YAML_NODES:
                raise ExportError("PPTD YAML exceeds the node-count safety limit")
        class UniqueKeySafeLoader(yaml.SafeLoader):
            pass

        def construct_unique_mapping(loader: Any, node: Any, deep: bool = False) -> Dict[Any, Any]:
            loader.flatten_mapping(node)
            mapping: Dict[Any, Any] = {}
            for key_node, value_node in node.value:
                key = loader.construct_object(key_node, deep=deep)
                try:
                    duplicate = key in mapping
                except TypeError as exc:
                    raise ExportError(f"unhashable YAML mapping key in {path}") from exc
                if duplicate:
                    raise ExportError(f"duplicate YAML mapping key {key!r} in {path}")
                mapping[key] = loader.construct_object(value_node, deep=deep)
            return mapping

        UniqueKeySafeLoader.construct_mapping = construct_unique_mapping
        value = yaml.load(text, Loader=UniqueKeySafeLoader)
    except (yaml.YAMLError, RecursionError, ValueError, OverflowError) as exc:
        raise ExportError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ExportError(f"expected a YAML mapping in {path}")
    return text, value


def safe_project_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ExportError("page path must be a non-empty string")
    if "\0" in relative or any(0xD800 <= ord(character) <= 0xDFFF for character in relative):
        raise ExportError("project path contains an unsupported character")
    try:
        candidate = (root / relative).resolve()
    except (OSError, ValueError, UnicodeError, RuntimeError) as exc:
        raise ExportError(f"invalid project path: {relative!r}") from exc
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ExportError(f"project path escapes the PPTD directory: {relative}") from exc
    return candidate


def collect_image_references(value: Any) -> List[str]:
    references: List[str] = []
    visited: set[int] = set()
    node_count = 0

    def walk(node: Any, depth: int = 0) -> None:
        nonlocal node_count
        node_count += 1
        if node_count > MAX_YAML_NODES:
            raise ExportError("PPTD YAML exceeds the node-count safety limit")
        if depth > MAX_YAML_DEPTH:
            raise ExportError("PPTD YAML exceeds the nesting-depth safety limit")
        if isinstance(node, list):
            identity = id(node)
            if identity in visited:
                return
            visited.add(identity)
            for item in node:
                walk(item, depth + 1)
            return
        if not isinstance(node, dict):
            return
        identity = id(node)
        if identity in visited:
            return
        visited.add(identity)
        for key, child in node.items():
            if key == "src" and isinstance(child, str):
                source = child.strip().replace("\\", "/")
                if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", source):
                    continue
                if Path(source).suffix.lower() in IMAGE_MIME:
                    references.append(source)
                    if len(references) > MAX_IMAGE_REFERENCES:
                        raise ExportError("PPTD contains too many local image references")
            else:
                walk(child, depth + 1)

    walk(value)
    return references


def normalize_local_image_reference(reference: str) -> str:
    if not isinstance(reference, str):
        raise ExportError("local image reference must be a string")
    if len(reference) > MAX_LOCAL_IMAGE_PATH_CHARS:
        raise ExportError(
            f"local image reference exceeds {MAX_LOCAL_IMAGE_PATH_CHARS} characters"
        )
    if "\0" in reference or any(
        0xD800 <= ord(character) <= 0xDFFF for character in reference
    ):
        raise ExportError("local image reference contains an unsupported character")
    source = reference.strip().replace("\\", "/")
    if (
        not source
        or source.startswith("/")
        or re.match(r"^[A-Za-z]:/", source)
        or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", source)
    ):
        raise ExportError(f"invalid local image reference: {reference}")
    parts: List[str] = []
    for part in source.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            # The browser host deliberately rejects parent traversal. Reject it
            # here too instead of embedding an image under a canonical key that
            # the writer can never request.
            raise ExportError(
                f"local image reference contains parent traversal: {reference}"
            )
        parts.append(part)
    if not parts:
        raise ExportError(f"invalid local image reference: {reference}")
    return "/".join(parts)


def build_image_map(
    root: Path,
    references: Iterable[str],
    *,
    input_snapshots: Optional[Dict[Path, PathSnapshot]] = None,
) -> Dict[str, str]:
    root = safe_resolve_path(root, description="PPTD project root")
    active_capability = _ACTIVE_INPUT_CAPABILITY.get()
    owned_capability: Optional[AnchoredDirectory] = None
    capability_token: Optional[contextvars.Token[Optional[AnchoredDirectory]]] = None
    if active_capability is None or active_capability.root != root:
        owned_capability = AnchoredDirectory.open(root)
        capability_token = _ACTIVE_INPUT_CAPABILITY.set(owned_capability)
        active_capability = owned_capability
    image_map: Dict[str, str] = {}
    total = 0
    normalized_references: Dict[str, Path] = {}
    try:
        for reference in references:
            alias = normalize_local_image_reference(reference)
            resolved = safe_project_path(root, alias)
            # Keep the browser-visible canonical lexical alias as the map key.
            # Internal symlinks remain supported: safe_project_path resolves the
            # alias once to an in-root target, then the target is opened through
            # the already-anchored root instead of re-following the alias.
            normalized_references[alias] = resolved

        for alias, resolved in sorted(normalized_references.items()):
            suffix = Path(alias).suffix.lower()
            if suffix not in IMAGE_MIME:
                continue
            data_bytes, snapshot = read_bounded_regular_bytes(
                resolved,
                MAX_IMAGE_BYTES,
                description="local image",
                limit_label="20 MiB",
            )
            size = len(data_bytes)
            if total + size > MAX_EMBEDDED_MEDIA_BYTES:
                raise ExportError(
                    "local image payload exceeds 200 MiB; reduce media size or use remote URLs"
                )
            data = base64.b64encode(data_bytes).decode("ascii")
            image_map[alias] = f"data:{IMAGE_MIME[suffix]};base64,{data}"
            total += size
            if input_snapshots is not None:
                input_snapshots[resolved] = snapshot
        if image_map:
            log(f"prepared {len(image_map)} local image resource(s), {total} bytes")
        return image_map
    finally:
        if capability_token is not None:
            _ACTIVE_INPUT_CAPABILITY.reset(capability_token)
        if owned_capability is not None:
            owned_capability.close()


def _read_stable_yaml_mapping(
    path: Path,
    input_snapshots: Dict[Path, PathSnapshot],
) -> Tuple[str, Dict[str, Any]]:
    capability = _ACTIVE_INPUT_CAPABILITY.get()
    relative = capability.relative_path(path) if capability is not None else None
    before = (
        capability.capture(relative)
        if capability is not None and relative is not None
        else capture_path_snapshot(path)
    )
    if before.mode is None or not stat.S_ISREG(before.mode):
        raise ExportError(f"PPTD input must be a regular file: {path}")
    text, value = read_yaml_mapping(path)
    after = (
        capability.capture(relative)
        if capability is not None and relative is not None
        else capture_path_snapshot(path)
    )
    if not snapshots_match(after, before):
        raise InputChangedError(f"PPTD input changed during payload capture: {path}")
    input_snapshots[path] = after
    return text, value


def _build_payload_once(manifest: Path) -> Dict[str, Any]:
    input_snapshots: Dict[Path, PathSnapshot] = {}
    manifest_text, manifest_data = _read_stable_yaml_mapping(
        manifest,
        input_snapshots,
    )
    if manifest_data.get("version") != "v2":
        raise ExportError("local PPTX export currently requires PPTD version: v2")
    page_paths = manifest_data.get("pages")
    if not isinstance(page_paths, list) or not page_paths:
        raise ExportError("PPTD manifest must contain a non-empty pages list")
    if len(page_paths) > MAX_DECK_PAGES:
        raise ExportError(f"PPTD contains more than {MAX_DECK_PAGES} pages")

    capability = _ACTIVE_INPUT_CAPABILITY.get()
    root = capability.root if capability is not None else manifest.parent.resolve()
    pages: List[Dict[str, str]] = []
    image_references = collect_image_references(manifest_data)
    total_text_bytes = len(manifest_text.encode("utf-8"))
    seen_pages: set[str] = set()
    for entry in page_paths:
        page_path = safe_project_path(root, entry)
        page_key = str(page_path).casefold()
        if page_key in seen_pages:
            raise ExportError(f"PPTD manifest contains a duplicate page: {entry}")
        seen_pages.add(page_key)
        page_snapshot = (
            capability.capture(capability.relative_path(page_path))
            if capability is not None
            else capture_path_snapshot(page_path)
        )
        if page_snapshot.mode is None or not stat.S_ISREG(page_snapshot.mode):
            raise ExportError(f"missing page file: {entry}")
        page_text, page_data = _read_stable_yaml_mapping(
            page_path,
            input_snapshots,
        )
        total_text_bytes += len(page_text.encode("utf-8"))
        if total_text_bytes > MAX_DECK_TEXT_BYTES:
            raise ExportError("PPTD manifest and pages exceed the 100 MiB text limit")
        if not isinstance(page_data.get("elements"), list):
            raise ExportError(f"page elements must be an array: {entry}")
        pages.append({"path": str(entry), "content": page_text})
        image_references.extend(collect_image_references(page_data))
        if len(image_references) > MAX_IMAGE_REFERENCES:
            raise ExportError("PPTD contains too many local image references")

    title = str(manifest_data.get("title") or manifest.stem)
    image_map = build_image_map(
        root,
        image_references,
        input_snapshots=input_snapshots,
    )
    for input_path, expected in input_snapshots.items():
        current = (
            capability.capture(capability.relative_path(input_path))
            if capability is not None
            else capture_path_snapshot(input_path)
        )
        if not snapshots_match(current, expected):
            raise InputChangedError(
                f"project input changed during payload capture: {input_path}"
            )
    if capability is not None:
        capability.assert_path_binding()
    return {
        "id": f"local-export-{uuid.uuid4().hex}",
        "title": title,
        "manifestPath": manifest.name,
        "manifestContent": manifest_text,
        "pages": pages,
        "imageMap": image_map,
    }


def build_payload(manifest: Path) -> Dict[str, Any]:
    display_manifest = manifest
    discovered_parent = _consume_manifest_parent_identity(display_manifest)
    if discovered_parent is not None and not _same_file_identity(
        capture_path_snapshot(display_manifest.parent),
        discovered_parent,
    ):
        raise ExportError(
            f"PPTD project directory changed after manifest discovery: {display_manifest.parent}"
        )
    resolved_manifest = safe_resolve_path(manifest, description="PPTD manifest")
    last_error: Optional[InputChangedError] = None
    with AnchoredDirectory.open(resolved_manifest.parent) as capability:
        if discovered_parent is not None and not _same_file_identity(
            capability.identity,
            discovered_parent,
        ):
            raise ExportError(
                f"PPTD project directory changed while it was anchored: {display_manifest.parent}"
            )
        token = _ACTIVE_INPUT_CAPABILITY.set(capability)
        try:
            for _attempt in range(MAX_INPUT_SNAPSHOT_ATTEMPTS):
                try:
                    return _build_payload_once(display_manifest)
                except InputChangedError as exc:
                    last_error = exc
        finally:
            _ACTIVE_INPUT_CAPABILITY.reset(token)
    raise ExportError(
        "project inputs kept changing while a consistent export snapshot was "
        f"captured; retry after saves finish: {last_error}"
    ) from last_error


def json_result(output: str) -> Dict[str, Any]:
    for line in reversed(output.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ExportError(f"agent-browser returned no JSON object:\n{output[-2000:]}")


class BrowserSession:
    def __init__(
        self,
        executable: str,
        session: str,
        cwd: Path,
        download_dir: Path,
        cdp_port: Optional[int] = None,
    ):
        self.executable = executable
        self.session = session
        self.cwd = cwd
        # Kept as a search root fallback; not passed to agent-browser. On Windows,
        # --download-path can be rewritten to a \\?\ path that cancels Chrome downloads.
        self.download_dir = download_dir
        self.env = os.environ.copy()
        self.env.setdefault("AGENT_BROWSER_DEFAULT_TIMEOUT", "60000")
        self.env.setdefault("AGENT_BROWSER_IDLE_TIMEOUT_MS", "180000")
        self.env["AGENT_BROWSER_DOWNLOAD_PATH"] = str(download_dir.resolve())
        self.requires_cdp_capture = bool(
            cdp_port is not None or self.env.get("AGENT_BROWSER_CDP")
        )
        if cdp_port is not None:
            self.env["AGENT_BROWSER_CDP"] = str(cdp_port)

    def run(
        self,
        args: Sequence[str],
        *,
        timeout: int = 90,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = [self.executable, "--session", self.session, *args]
        process = run_command(command, cwd=self.cwd, env=self.env, timeout=timeout)
        if check and process.returncode != 0:
            raise ExportError(
                f"agent-browser command failed ({process.returncode}): "
                f"{' '.join(args)}\n{process.stdout[-4000:]}"
            )
        return process

    def open(self, url: str) -> None:
        # The per-session destination is carried in AGENT_BROWSER_DOWNLOAD_PATH.
        # Keeping it out of argv avoids the Windows path rewriting issue seen with
        # the older --download-path flag.
        self.run(["open", url], timeout=90)

    def snapshot(self) -> Dict[str, Any]:
        process = self.run(["snapshot", "-i", "-C", "--json"])
        return json_result(process.stdout)

    def close(self) -> None:
        self.run(["close"], timeout=20, check=False)


def snapshot_data(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    data = snapshot.get("data")
    if not isinstance(data, dict):
        raise ExportError(f"invalid agent-browser snapshot: {snapshot}")
    return data


def ref_by_name(snapshot: Dict[str, Any], name: str, role: Optional[str] = None) -> str:
    refs = snapshot_data(snapshot).get("refs")
    if not isinstance(refs, dict):
        raise ExportError("snapshot contains no interactive refs")
    matches = []
    for ref, metadata in refs.items():
        if not isinstance(metadata, dict) or metadata.get("name") != name:
            continue
        if role is not None and str(metadata.get("role", "")).lower() != role.lower():
            continue
        matches.append(ref)
    if not matches:
        raise ExportError(f"could not find {role or 'element'} named {name!r}")
    if len(matches) != 1:
        raise AmbiguousUiContractError(
            f"found {len(matches)} {role or 'element'} controls named {name!r}; "
            "refusing to guess which remote control to activate"
        )
    return matches[0]


def writer_control_ref(snapshot: Dict[str, Any], control: str) -> str:
    names = {
        "export": KIMI_WRITER_UI.export_name,
        "download": KIMI_WRITER_UI.download_name,
    }
    try:
        name = names[control]
    except KeyError as exc:
        raise ExportError(f"unknown Kimi writer control contract: {control}") from exc
    return ref_by_name(snapshot, name, "button")


def switch_state(snapshot: Dict[str, Any]) -> Optional[Tuple[str, bool, bool]]:
    data = snapshot_data(snapshot)
    text = str(data.get("snapshot") or "")
    matches: List[Tuple[str, str]] = []
    for match in re.finditer(
        r'(?m)^[^\r\n]*?\bswitch(?:\s+"[^"]*")?\s+\[(?P<attrs>[^\]]*)\]',
        text,
    ):
        attrs = match.group("attrs")
        ref_match = re.search(r"\bref=(e\d+)\b", attrs)
        if ref_match is not None:
            matches.append((ref_match.group(1), attrs))
    if not matches:
        return None

    refs = data.get("refs")
    named_font_refs: List[str] = []
    if isinstance(refs, dict):
        for ref, metadata in refs.items():
            if not isinstance(metadata, dict):
                continue
            if str(metadata.get("role", "")).casefold() != "switch":
                continue
            name = str(metadata.get("name", "")).casefold()
            if any(token.casefold() in name for token in KIMI_WRITER_UI.font_name_tokens):
                named_font_refs.append(str(ref))
    named_font_refs = list(dict.fromkeys(named_font_refs))
    if len(named_font_refs) > 1:
        raise ExportError("the Kimi export dialog exposes multiple font switches")
    if named_font_refs:
        selected = [item for item in matches if item[0] == named_font_refs[0]]
        if len(selected) != 1:
            raise ExportError("the named Kimi font switch has no unambiguous state")
        ref, attrs = selected[0]
    elif len(matches) == 1:
        # The current public dialog exposes one unnamed switch. Accept that
        # compatibility shape only while it remains the sole switch.
        ref, attrs = matches[0]
    else:
        raise ExportError(
            "the Kimi export dialog exposes multiple unnamed switches; refusing "
            "to guess which one controls font embedding"
        )
    return ref, "checked=true" in attrs, "disabled" in attrs


def wait_for_export_dialog(browser: BrowserSession, timeout: float = 20.0) -> Dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: Optional[Dict[str, Any]] = None
    while time.monotonic() < deadline:
        last = browser.snapshot()
        try:
            writer_control_ref(last, "download")
            return last
        except AmbiguousUiContractError:
            raise
        except ExportError:
            time.sleep(0.35)
    raise ExportError(f"export dialog did not become ready: {last}")


def ensure_websocket() -> Any:
    try:
        import websocket

        return websocket
    except ImportError as exc:
        raise ExportError(
            "websocket-client is required for CDP-assisted export. Install it in "
            f"the active Python environment with: {pip_install_hint('websocket-client')}"
        ) from exc


def browser_cdp_url(browser: BrowserSession) -> str:
    process = browser.run(["get", "cdp-url"], timeout=30)
    match = re.search(r"ws://\S+", process.stdout)
    if not match:
        raise ExportError(
            f"could not determine the browser CDP URL:\n{process.stdout[-500:]}"
        )
    return match.group(0)


class IframeTarget(NamedTuple):
    cdp_url: str
    target_id: str


def is_kimi_writer_target_url(raw_url: Any) -> bool:
    """Match only the official HTTPS Kimi writer origin and path."""
    try:
        parsed = urlsplit(str(raw_url))
        hostname = (parsed.hostname or "").casefold()
        port = parsed.port
    except (TypeError, ValueError, UnicodeError):
        return False
    return (
        parsed.scheme.casefold() == "https"
        and hostname in KIMI_WRITER_HOSTS
        and port in (None, 443)
        and parsed.username is None
        and parsed.password is None
        and (
            parsed.path == KIMI_WRITER_PATH_PREFIX
            or parsed.path.startswith(f"{KIMI_WRITER_PATH_PREFIX}/")
        )
    )


def evaluate_in_iframe(
    cdp_url: str,
    url_hint: str,
    expression: str,
    *,
    target_id: Optional[str] = None,
    return_target_id: bool = False,
    timeout: float = 30.0,
) -> Any:
    websocket = ensure_websocket()
    operation_deadline = time.monotonic() + timeout

    def remaining_timeout() -> float:
        remaining = operation_deadline - time.monotonic()
        if remaining <= 0:
            raise CdpOperationTimeout("browser CDP iframe operation timed out")
        return max(0.1, min(30.0, remaining))

    def call(
        connection: Any,
        request_id: int,
        method: str,
        params: Dict[str, Any],
        *,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        remaining_timeout()
        request: Dict[str, Any] = {
            "id": request_id,
            "method": method,
            "params": params,
        }
        if session_id is not None:
            request["sessionId"] = session_id
        if hasattr(connection, "settimeout"):
            connection.settimeout(remaining_timeout())
        connection.send(json.dumps(request))
        while True:
            if hasattr(connection, "settimeout"):
                connection.settimeout(remaining_timeout())
            else:
                remaining_timeout()
            message = json.loads(connection.recv())
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise ExportError(f"CDP {method} failed: {message['error']}")
            result = message.get("result", {})
            if not isinstance(result, dict):
                raise ExportError(f"CDP {method} returned an invalid result")
            return result

    # websocket-client honors proxy variables. The DevTools endpoint is local;
    # never send that connection (or browser authentication state) to a proxy.
    proxy_env = (
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
    )
    saved_proxy = {
        name: os.environ.pop(name) for name in proxy_env if name in os.environ
    }
    try:
        try:
            connection = websocket.create_connection(
                cdp_url,
                timeout=remaining_timeout(),
                suppress_origin=True,
            )
        except Exception as exc:
            raise ExportError("unable to connect to the browser CDP endpoint") from exc
    finally:
        os.environ.update(saved_proxy)
    try:
        try:
            targets = call(connection, 1, "Target.getTargets", {}).get("targetInfos", [])
            if not isinstance(targets, list):
                raise ExportError("CDP Target.getTargets returned no target list")
            if url_hint != OOPIF_URL_HINT:
                raise ExportError("unsupported browser writer target selector")
            matching_targets = [
                item
                for item in targets
                if (
                    isinstance(item, dict)
                    and item.get("type") == "iframe"
                    and is_kimi_writer_target_url(item.get("url", ""))
                )
            ]
            if target_id is not None:
                bound_targets = [
                    item for item in matching_targets if item.get("targetId") == target_id
                ]
                if len(bound_targets) != 1:
                    raise ExportError(
                        "the bound Kimi writer target disappeared or changed; refusing "
                        "to attach to a replacement iframe"
                    )
                target = bound_targets[0]
            elif not matching_targets:
                visible = ", ".join(
                    str(item.get("type", "unknown"))
                    for item in targets
                    if isinstance(item, dict)
                )
                raise ExportError(
                    f"no browser target matches the Kimi writer; observed target types: {visible}"
                )
            elif len(matching_targets) != 1:
                # A shared/persistent CDP browser may contain multiple Kimi writer
                # OOPIFs from other exports. URL-only selection cannot prove which
                # iframe belongs to this agent-browser session, so fail closed.
                visible_types = ", ".join(
                    str(item.get("type", "unknown")) for item in matching_targets
                )
                raise ExportError(
                    "multiple browser targets match the Kimi writer; refusing an "
                    "ambiguous CDP attachment (matching target types: "
                    f"{visible_types})"
                )
            else:
                target = matching_targets[0]
            resolved_target_id = target.get("targetId")
            if not isinstance(resolved_target_id, str) or not resolved_target_id:
                raise ExportError("Kimi writer target has no valid CDP target id")
            attached = call(
                connection,
                2,
                "Target.attachToTarget",
                {"targetId": resolved_target_id, "flatten": True},
            )
            session_id = attached.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                raise ExportError("CDP returned no iframe session id")
            evaluated = call(
                connection,
                3,
                "Runtime.evaluate",
                {
                    "expression": expression,
                    "returnByValue": True,
                    "awaitPromise": True,
                },
                session_id=session_id,
            )
            if evaluated.get("exceptionDetails"):
                details = evaluated["exceptionDetails"]
                text = details.get("text") if isinstance(details, dict) else None
                raise ExportError(f"iframe script failed: {text or 'JavaScript exception'}")
            remote = evaluated.get("result", {})
            if not isinstance(remote, dict):
                raise ExportError("CDP Runtime.evaluate returned no result object")
            value = remote.get("value")
            return (resolved_target_id, value) if return_target_id else value
        except CdpOperationTimeout:
            raise
        except ExportError:
            raise
        except Exception as exc:
            websocket_timeout = getattr(
                websocket,
                "WebSocketTimeoutException",
                None,
            )
            if isinstance(exc, TimeoutError) or (
                isinstance(websocket_timeout, type)
                and isinstance(exc, websocket_timeout)
            ):
                raise CdpOperationTimeout(
                    "browser CDP iframe operation timed out"
                ) from exc
            raise ExportError("browser CDP iframe communication failed") from exc
    finally:
        try:
            connection.close()
        except Exception:
            pass


PPTX_BLOB_HOOK_INSTALL_JS = r"""
(() => {
  const key = '__openKimiPptExportHookV1';
  const prior = globalThis[key];
  if (prior && prior.installed) return { installed: true };
  const state = {
    installed: false,
    blob: null,
    objectUrl: null,
    signatureStatus: null,
    signatureRejected: false,
    signatureRequestCount: 0,
    objectUrlCount: 0,
    pptxBlobCandidateCount: 0,
    downloadSignalCount: 0,
    captureSignal: null,
    lastStage: 'installing',
    blobsByUrl: new Map(),
  };
  const isSignature = (value) =>
    String(value || '').includes('/apiv2/utils/v1/signatures');
  const isPptxMime = (value) => {
    const mime = String(value || '').split(';', 1)[0].trim().toLowerCase();
    return mime === 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
      || mime === 'application/octet-stream'
      || mime === 'application/zip'
      || mime === 'application/x-zip-compressed';
  };
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  const originalFetch = globalThis.fetch || null;
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  const originalAnchorClick = HTMLAnchorElement.prototype.click;
  const originalAnchorDispatchEvent = HTMLAnchorElement.prototype.dispatchEvent;
  try {
    const patchedCreate = function(value) {
      const objectUrl = originalCreate.call(URL, value);
      if (value instanceof Blob) {
        state.objectUrlCount += 1;
        state.lastStage = 'blob-url-created';
        if (isPptxMime(value.type)) state.pptxBlobCandidateCount += 1;
        state.blobsByUrl.set(objectUrl, value);
        if (state.blobsByUrl.size > 512) {
          state.blobsByUrl.delete(state.blobsByUrl.keys().next().value);
        }
      }
      return objectUrl;
    };
    const patchedRevoke = function(objectUrl) {
      state.blobsByUrl.delete(String(objectUrl || ''));
      return originalRevoke.call(URL, objectUrl);
    };
    const captureAnchorDownload = (anchor, signal) => {
      const downloadName = String(anchor.download || '').toLowerCase();
      if (downloadName.endsWith('.pptx')) {
        state.downloadSignalCount += 1;
        state.lastStage = `download-${signal}`;
        const objectUrl = String(anchor.href || anchor.getAttribute('href') || '');
        const candidate = state.blobsByUrl.get(objectUrl);
        if (candidate) {
          state.blob = candidate;
          state.objectUrl = objectUrl;
          state.captureSignal = signal;
          state.lastStage = 'blob-captured';
        }
      }
    };
    const patchedAnchorClick = function(...args) {
      captureAnchorDownload(this, 'click');
      return originalAnchorClick.apply(this, args);
    };
    // FileSaver.js intentionally dispatches a synthetic MouseEvent rather than
    // calling anchor.click(). Kimi's current public writer uses that path for
    // its default PPTX download, so both activation mechanisms must be observed.
    const patchedAnchorDispatchEvent = function(event) {
      if (event && event.type === 'click') {
        captureAnchorDownload(this, 'dispatch-event');
      }
      return originalAnchorDispatchEvent.call(this, event);
    };
    const patchedFetch = originalFetch ? async function(...args) {
      const requestUrl = args[0] && args[0].url ? args[0].url : args[0];
      if (isSignature(requestUrl)) {
        state.signatureRequestCount += 1;
        state.lastStage = 'signature-requested';
      }
      try {
        const response = await originalFetch.apply(globalThis, args);
        if (isSignature(requestUrl)) {
          state.signatureStatus = Number(response.status);
          state.lastStage = 'signature-completed';
        }
        return response;
      } catch (error) {
        if (isSignature(requestUrl)) {
          state.signatureRejected = true;
          state.lastStage = 'signature-rejected';
        }
        throw error;
      }
    } : null;
    const patchedOpen = function(method, url, ...rest) {
      this.__openKimiPptSignatureRequest = isSignature(url);
      return originalOpen.call(this, method, url, ...rest);
    };
    const patchedSend = function(...args) {
      if (this.__openKimiPptSignatureRequest) {
        state.signatureRequestCount += 1;
        state.lastStage = 'signature-requested';
        this.addEventListener('loadend', () => {
          state.signatureStatus = Number(this.status);
          state.lastStage = 'signature-completed';
        }, { once: true });
        this.addEventListener('error', () => {
          state.signatureRejected = true;
          state.lastStage = 'signature-rejected';
        }, { once: true });
      }
      return originalSend.apply(this, args);
    };
    state.originalCreate = originalCreate;
    state.originalFetch = originalFetch;
    state.originalRevoke = originalRevoke;
    state.originalOpen = originalOpen;
    state.originalSend = originalSend;
    state.originalAnchorClick = originalAnchorClick;
    state.originalAnchorDispatchEvent = originalAnchorDispatchEvent;
    state.patchedCreate = patchedCreate;
    state.patchedFetch = patchedFetch;
    state.patchedRevoke = patchedRevoke;
    state.patchedOpen = patchedOpen;
    state.patchedSend = patchedSend;
    state.patchedAnchorClick = patchedAnchorClick;
    state.patchedAnchorDispatchEvent = patchedAnchorDispatchEvent;
    URL.createObjectURL = patchedCreate;
    URL.revokeObjectURL = patchedRevoke;
    if (patchedFetch) globalThis.fetch = patchedFetch;
    XMLHttpRequest.prototype.open = patchedOpen;
    XMLHttpRequest.prototype.send = patchedSend;
    HTMLAnchorElement.prototype.click = patchedAnchorClick;
    HTMLAnchorElement.prototype.dispatchEvent = patchedAnchorDispatchEvent;
    state.installed = true;
    state.lastStage = 'installed';
    globalThis[key] = state;
    return { installed: true };
  } catch (error) {
    try { URL.createObjectURL = originalCreate; } catch (_) {}
    try { URL.revokeObjectURL = originalRevoke; } catch (_) {}
    try { if (originalFetch) globalThis.fetch = originalFetch; } catch (_) {}
    try { XMLHttpRequest.prototype.open = originalOpen; } catch (_) {}
    try { XMLHttpRequest.prototype.send = originalSend; } catch (_) {}
    try { HTMLAnchorElement.prototype.click = originalAnchorClick; } catch (_) {}
    try { HTMLAnchorElement.prototype.dispatchEvent = originalAnchorDispatchEvent; } catch (_) {}
    return { installed: false };
  }
})()
""".strip()

PPTX_BLOB_STATUS_JS = r"""
(() => {
  const state = globalThis.__openKimiPptExportHookV1;
  if (!state || !state.installed) return { installed: false };
  const progressNode = document.querySelector('.export-dialog-loading-text');
  const progressText = progressNode && progressNode.textContent
    ? String(progressNode.textContent).trim().slice(0, 160)
    : null;
  return {
    installed: true,
    signatureStatus: state.signatureStatus,
    signatureRejected: Boolean(state.signatureRejected),
    signatureRequestCount: Number(state.signatureRequestCount || 0),
    objectUrlCount: Number(state.objectUrlCount || 0),
    pptxBlobCandidateCount: Number(state.pptxBlobCandidateCount || 0),
    downloadSignalCount: Number(state.downloadSignalCount || 0),
    captureSignal: state.captureSignal,
    lastStage: state.lastStage,
    progressText,
    blob: state.blob ? {
      size: Number(state.blob.size),
      mime: String(state.blob.type || ''),
    } : null,
  };
})()
""".strip()

PPTX_BLOB_CLEANUP_JS = r"""
(() => {
  const key = '__openKimiPptExportHookV1';
  const state = globalThis[key];
  if (!state) return true;
  try {
    if (URL.createObjectURL === state.patchedCreate) {
      URL.createObjectURL = state.originalCreate;
    }
    if (URL.revokeObjectURL === state.patchedRevoke) {
      URL.revokeObjectURL = state.originalRevoke;
    }
    if (state.patchedFetch && globalThis.fetch === state.patchedFetch) {
      globalThis.fetch = state.originalFetch;
    }
    if (XMLHttpRequest.prototype.open === state.patchedOpen) {
      XMLHttpRequest.prototype.open = state.originalOpen;
    }
    if (XMLHttpRequest.prototype.send === state.patchedSend) {
      XMLHttpRequest.prototype.send = state.originalSend;
    }
    if (HTMLAnchorElement.prototype.click === state.patchedAnchorClick) {
      HTMLAnchorElement.prototype.click = state.originalAnchorClick;
    }
    if (HTMLAnchorElement.prototype.dispatchEvent === state.patchedAnchorDispatchEvent) {
      HTMLAnchorElement.prototype.dispatchEvent = state.originalAnchorDispatchEvent;
    }
    if (state.objectUrl) state.originalRevoke.call(URL, state.objectUrl);
  } finally {
    state.blob = null;
    state.objectUrl = null;
    state.blobsByUrl.clear();
    delete globalThis[key];
  }
  return true;
})()
""".strip()


def pptx_blob_chunk_expression(offset: int, length: int) -> str:
    if offset < 0 or length <= 0 or length > PPTX_BLOB_CHUNK_BYTES:
        raise ExportError("invalid PPTX Blob chunk request")
    return f"""
(async () => {{
  const state = globalThis.__openKimiPptExportHookV1;
  if (!state || !state.blob) return {{ error: 'missing-blob' }};
  const offset = {offset};
  const end = Math.min(offset + {length}, state.blob.size);
  const bytes = new Uint8Array(await state.blob.slice(offset, end).arrayBuffer());
  let binary = '';
  for (let index = 0; index < bytes.length; index += 32768) {{
    binary += String.fromCharCode(...bytes.subarray(index, index + 32768));
  }}
  return {{ offset, length: bytes.length, base64: btoa(binary) }};
}})()
""".strip()


def validate_pptx_blob_metadata(blob: Any) -> Tuple[int, str]:
    if not isinstance(blob, dict):
        raise ExportError("Kimi writer returned invalid PPTX Blob metadata")
    size = blob.get("size")
    mime = blob.get("mime")
    if isinstance(size, bool) or not isinstance(size, (int, float)) or int(size) != size:
        raise ExportError("Kimi writer returned an invalid PPTX Blob size")
    size = int(size)
    if size <= 0:
        raise ExportError("Kimi writer returned an empty PPTX Blob")
    if size > MAX_PPTX_ARCHIVE_BYTES:
        raise ExportError("PPTX Blob exceeds the compressed-size safety limit")
    if not isinstance(mime, str):
        raise ExportError("Kimi writer returned invalid PPTX Blob MIME metadata")
    normalized_mime = mime.partition(";")[0].strip().lower()
    if normalized_mime not in PPTX_BLOB_MIME_TYPES:
        raise ExportError(
            f"Kimi writer returned an unsupported PPTX Blob MIME type: {mime!r}"
        )
    return size, normalized_mime


def classify_pptx_hook_state(state: Any) -> Optional[Tuple[int, str]]:
    if not isinstance(state, dict) or state.get("installed") is not True:
        raise ExportError("PPTX Blob capture hook is no longer available")
    status = state.get("signatureStatus")
    if isinstance(status, (int, float)) and not isinstance(status, bool):
        status_code = int(status)
        if status_code in (401, 403):
            raise ExportError(
                f"Kimi's PPTX writer returned HTTP {status_code} and requires a "
                "signed-in Kimi session. Sign in using a dedicated browser profile, "
                "then set AGENT_BROWSER_PROFILE or AGENT_BROWSER_STATE and retry; "
                "this exporter never logs in automatically."
            )
        if status_code and not 200 <= status_code < 300:
            raise ExportError(
                f"Kimi's PPTX signature service returned HTTP {status_code}"
            )
    if state.get("signatureRejected") is True:
        raise ExportError("Kimi's PPTX signature request was rejected")
    blob = state.get("blob")
    return validate_pptx_blob_metadata(blob) if blob is not None else None


def pptx_hook_diagnostics(state: Any) -> str:
    """Return a bounded, credential-free summary of the writer's last state."""
    if not isinstance(state, dict):
        return "state=unavailable"

    def count(name: str) -> str:
        value = state.get(name)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and int(value) == value
            and value >= 0
        ):
            return str(int(value))
        return "unknown"

    stage = state.get("lastStage")
    if not isinstance(stage, str) or not re.fullmatch(r"[a-z0-9-]{1,64}", stage):
        stage = "unknown"

    progress = state.get("progressText")
    if isinstance(progress, str):
        progress = " ".join(progress.split())[:160]
    if not progress:
        progress = "unavailable"

    signature_status = state.get("signatureStatus")
    signature_requests = state.get("signatureRequestCount")
    if (
        isinstance(signature_status, (int, float))
        and not isinstance(signature_status, bool)
        and int(signature_status) == signature_status
    ):
        signature = f"http-{int(signature_status)}"
    elif state.get("signatureRejected") is True:
        signature = "rejected"
    elif (
        isinstance(signature_requests, (int, float))
        and not isinstance(signature_requests, bool)
        and signature_requests > 0
    ):
        signature = "pending"
    else:
        signature = "not-requested"

    capture_signal = state.get("captureSignal")
    if capture_signal not in ("click", "dispatch-event"):
        capture_signal = "none"

    return ", ".join(
        (
            f"stage={stage}",
            f"progress={json.dumps(progress, ensure_ascii=True)}",
            f"signature={signature}",
            f"signatureRequests={count('signatureRequestCount')}",
            f"objectUrls={count('objectUrlCount')}",
            f"pptxBlobCandidates={count('pptxBlobCandidateCount')}",
            f"downloadSignals={count('downloadSignalCount')}",
            f"captureSignal={capture_signal}",
        )
    )


def wait_for_pptx_blob(
    target: IframeTarget,
    *,
    timeout: float = PPTX_BLOB_WAIT_SECONDS,
    deadline: Optional[float] = None,
) -> Tuple[int, str]:
    deadline = deadline if deadline is not None else time.monotonic() + timeout
    last_state: Any = None
    last_diagnostics: Optional[str] = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            state = evaluate_in_iframe(
                target.cdp_url,
                OOPIF_URL_HINT,
                PPTX_BLOB_STATUS_JS,
                target_id=target.target_id,
                timeout=min(30.0, remaining),
            )
        except CdpOperationTimeout as exc:
            if time.monotonic() >= deadline:
                raise PptxWriterTimeout(
                    "PPTX Blob status polling exceeded its attempt deadline; "
                    f"last observed writer state: {pptx_hook_diagnostics(last_state)}"
                ) from exc
            log(
                "PPTX writer main thread did not answer one CDP status poll; "
                "continuing within the bounded artifact deadline"
            )
            continue
        except ExportError:
            raise
        last_state = state
        diagnostics = pptx_hook_diagnostics(state)
        if diagnostics != last_diagnostics:
            log(f"PPTX writer state: {diagnostics}")
            last_diagnostics = diagnostics
        ready = classify_pptx_hook_state(state)
        if ready is not None:
            return ready
        time.sleep(0.25)
    raise PptxWriterTimeout(
        "Kimi's PPTX writer produced neither a Blob nor an explicit error within "
        f"{timeout:g} seconds; last observed writer state: "
        f"{pptx_hook_diagnostics(last_state)}"
    )


def write_pptx_blob_to_staged_file(
    target: IframeTarget,
    staged: Path,
    expected_identity: PathSnapshot,
    expected_size: int,
    *,
    deadline: Optional[float] = None,
) -> int:
    if expected_size <= 0 or expected_size > MAX_PPTX_ARCHIVE_BYTES:
        raise ExportError("invalid PPTX Blob size")
    if deadline is not None and time.monotonic() >= deadline:
        raise PptxWriterTimeout("PPTX Blob capture exceeded its attempt deadline")
    descriptor = _open_verified_private_file(
        staged,
        expected_identity,
        writable=True,
        require_exact_snapshot=True,
    )
    try:
        os.ftruncate(descriptor, 0)
        written = 0
        while written < expected_size:
            remaining_time = (
                deadline - time.monotonic() if deadline is not None else 30.0
            )
            if remaining_time <= 0:
                raise PptxWriterTimeout(
                    "PPTX Blob capture exceeded its attempt deadline"
                )
            requested = min(PPTX_BLOB_CHUNK_BYTES, expected_size - written)
            try:
                value = evaluate_in_iframe(
                    target.cdp_url,
                    OOPIF_URL_HINT,
                    pptx_blob_chunk_expression(written, requested),
                    target_id=target.target_id,
                    timeout=min(30.0, remaining_time),
                )
            except CdpOperationTimeout as exc:
                if deadline is None:
                    raise
                if time.monotonic() >= deadline:
                    raise PptxWriterTimeout(
                        "PPTX Blob capture exceeded its attempt deadline"
                    ) from exc
                log(
                    "PPTX Blob chunk read did not answer one CDP request; "
                    "retrying the same read within the bounded artifact deadline"
                )
                continue
            except ExportError:
                raise
            if not isinstance(value, dict) or value.get("error"):
                raise ExportError("PPTX Blob disappeared during capture")
            if value.get("offset") != written or value.get("length") != requested:
                raise ExportError("PPTX Blob chunk metadata was inconsistent")
            encoded = value.get("base64")
            if not isinstance(encoded, str):
                raise ExportError("PPTX Blob chunk did not contain base64 data")
            try:
                chunk = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ExportError("PPTX Blob chunk contained invalid base64 data") from exc
            if len(chunk) != requested:
                raise ExportError("PPTX Blob chunk byte count was inconsistent")
            view = memoryview(chunk)
            while view:
                count = os.write(descriptor, view)
                if count <= 0:
                    raise ExportError("PPTX Blob staging write made no progress")
                view = view[count:]
            written += len(chunk)
        os.fsync(descriptor)
        final_info = os.fstat(descriptor)
        if written != expected_size or final_info.st_size != expected_size:
            raise ExportError("PPTX Blob capture wrote an incomplete file")
        written_snapshot = PathSnapshot(
            True,
            final_info.st_dev,
            final_info.st_ino,
            final_info.st_mode,
            final_info.st_size,
            final_info.st_mtime_ns,
            final_info.st_ctime_ns,
        )
    finally:
        os.close(descriptor)
    _verify_closed_write_snapshot(
        capture_path_snapshot(staged),
        written_snapshot,
        written,
        "private PPTX staging path changed after Blob capture",
    )
    return written


def install_pptx_blob_hook(browser: BrowserSession) -> Optional[IframeTarget]:
    try:
        cdp_url = browser_cdp_url(browser)
    except Exception:
        if getattr(browser, "requires_cdp_capture", False):
            raise ExportError(
                "the configured external CDP browser did not expose a verifiable "
                "endpoint; refusing the shared-download compatibility fallback"
            )
        log(
            "warning: CDP PPTX Blob capture is unavailable; using the bounded "
            "filesystem download fallback"
        )
        return None

    target: Optional[IframeTarget] = None
    try:
        resolved_target_id, installed = evaluate_in_iframe(
            cdp_url,
            OOPIF_URL_HINT,
            PPTX_BLOB_HOOK_INSTALL_JS,
            return_target_id=True,
        )
        target = IframeTarget(cdp_url, resolved_target_id)
        if not isinstance(installed, dict) or installed.get("installed") is not True:
            raise ExportError("iframe rejected the PPTX Blob capture hook")
        return target
    except Exception:
        if target is not None:
            try:
                evaluate_in_iframe(
                    target.cdp_url,
                    OOPIF_URL_HINT,
                    PPTX_BLOB_CLEANUP_JS,
                    target_id=target.target_id,
                )
            except Exception:
                pass
        # Once a CDP endpoint exists, target ambiguity or hook failure is a
        # security boundary, not a compatibility signal. Falling back to a
        # shared browser download directory could publish another concurrent
        # export, so fail closed and let the caller start a clean session.
        raise


def cleanup_pptx_blob_hook(target: IframeTarget) -> None:
    try:
        evaluate_in_iframe(
            target.cdp_url,
            OOPIF_URL_HINT,
            PPTX_BLOB_CLEANUP_JS,
            target_id=target.target_id,
        )
    except Exception:
        log("warning: could not clear the PPTX Blob capture hook")


def configure_font_embedding(
    browser: BrowserSession,
    dialog: Dict[str, Any],
    desired: bool,
) -> Tuple[Dict[str, Any], Optional[bool]]:
    state = switch_state(dialog)
    if state is None:
        if not desired:
            raise ExportError(
                "the Kimi export dialog exposed no font switch, so the safe "
                "no-font retry cannot be enforced"
            )
        log("warning: the official export dialog exposed no font switch")
        return dialog, None
    switch_ref, checked, disabled = state
    if disabled:
        if checked != desired:
            raise ExportError(
                "the Kimi font switch is disabled and does not match the requested mode"
            )
        return dialog, checked
    if checked != desired:
        browser.run(["click", f"@{switch_ref}"])
        dialog = wait_for_export_dialog(browser)
        updated = switch_state(dialog)
        if updated is None or updated[1] != desired:
            raise ExportError("the Kimi font switch did not enter the requested state")
        checked = updated[1]
    return dialog, checked


def run_pptx_browser_attempt(
    agent_browser: str,
    url: str,
    temp_dir: Path,
    download_dir: Path,
    cdp_port: Optional[int],
    staged_output: Path,
    staged_identity: PathSnapshot,
    *,
    embed_fonts: bool,
    blob_timeout: float,
) -> PptxAttemptResult:
    session = f"open-kimi-ppt-export-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    browser = BrowserSession(
        agent_browser,
        session,
        temp_dir,
        download_dir,
        cdp_port,
    )
    cdp_target: Optional[IframeTarget] = None
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
        dialog, effective_fonts = configure_font_embedding(
            browser,
            dialog,
            embed_fonts,
        )
        download_ref = writer_control_ref(dialog, "download")
        cdp_target = install_pptx_blob_hook(browser)
        started_at = time.time() - 1.0
        artifact_deadline = time.monotonic() + blob_timeout
        log("generating PPTX in the browser")
        browser.run(["click", f"@{download_ref}"], timeout=30)
        if cdp_target is not None:
            blob_size, _blob_mime = wait_for_pptx_blob(
                cdp_target,
                timeout=blob_timeout,
                deadline=artifact_deadline,
            )
            write_pptx_blob_to_staged_file(
                cdp_target,
                staged_output,
                staged_identity,
                blob_size,
                deadline=artifact_deadline,
            )
        else:
            try:
                downloaded = find_download(
                    (download_dir,),
                    timeout=blob_timeout,
                    since=started_at,
                    maximum_bytes=MAX_PPTX_ARCHIVE_BYTES,
                )
            except ExportError as exc:
                if "timed out waiting for download" in str(exc):
                    raise PptxWriterTimeout(
                        "CDP Blob/signature monitoring was unavailable, and Kimi "
                        f"produced no filesystem PPTX within {blob_timeout:g} seconds. "
                        "An authentication failure cannot be classified on this "
                        "compatibility path; verify the dedicated Kimi session is "
                        "signed in and retry."
                    ) from exc
                raise
            copy_to_private_staging_file(
                downloaded,
                staged_output,
                staged_identity,
            )
        return PptxAttemptResult(effective_fonts, cdp_target is not None)
    finally:
        if cdp_target is not None:
            cleanup_pptx_blob_hook(cdp_target)
        try:
            browser.close()
        except Exception as exc:
            log(f"warning: failed to close browser: {exc}")


def parse_ooxml_root(
    archive: zipfile.ZipFile,
    member_name: str,
    description: str,
) -> ET.Element:
    try:
        data = archive.read(member_name)
    except (
        KeyError,
        OSError,
        RuntimeError,
        UnicodeError,
        ValueError,
        NotImplementedError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
    ) as exc:
        raise ExportError(f"unable to read {description}") from exc
    if b"\x00" in data or data.startswith(
        (b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff", b"\xff\xfe\x00\x00")
    ):
        # The official writer emits UTF-8 OOXML. Reject UTF-16/32 (and any NUL
        # byte, which is invalid in UTF-8 XML) so declaration scanning cannot
        # be bypassed by interleaving ASCII keywords with NULs.
        raise ExportError(f"{description} XML uses unsupported UTF-16/32 or NUL bytes")
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", data, re.IGNORECASE):
        # OOXML parts never require a DTD. ElementTree expands internal
        # entities, so even a tiny archive member could otherwise amplify into
        # a very large in-memory tree before the node/depth limits fire.
        raise ExportError(f"{description} XML contains a forbidden DTD/entity declaration")
    parser = ET.XMLPullParser(events=("start", "end"))
    root: Optional[ET.Element] = None
    node_count = 0
    depth = 0
    try:
        # Feed bounded chunks so an element-dense document is rejected before
        # ElementTree can materialize the rest of a hostile XML part.
        for offset in range(0, len(data), 64 * 1024):
            parser.feed(data[offset : offset + 64 * 1024])
            for event, element in parser.read_events():
                if event == "start":
                    if root is None:
                        root = element
                    node_count += 1
                    depth += 1
                    if node_count > MAX_PPTX_XML_NODES:
                        raise ExportError(
                            f"{description} XML has too many elements "
                            f"(limit {MAX_PPTX_XML_NODES})"
                        )
                    if depth > MAX_PPTX_XML_DEPTH:
                        raise ExportError(
                            f"{description} XML is too deeply nested "
                            f"(limit {MAX_PPTX_XML_DEPTH})"
                        )
                else:
                    depth -= 1
        parser.close()
    except ET.ParseError as exc:
        raise ExportError(f"malformed {description} XML: {exc}") from exc
    if root is None:
        raise ExportError(f"malformed {description} XML: document is empty")
    return root


def validate_presentation_content_type(root: ET.Element) -> None:
    types_tag = f"{{{CONTENT_TYPES_NAMESPACE}}}Types"
    override_tag = f"{{{CONTENT_TYPES_NAMESPACE}}}Override"
    if root.tag != types_tag:
        raise ExportError("malformed PPTX content-types part: unexpected root element")
    overrides = [
        child
        for child in root
        if child.tag == override_tag
        and child.get("PartName") == "/ppt/presentation.xml"
    ]
    if not overrides:
        raise ExportError(
            "PPTX presentation content-type Override is missing"
        )
    if len(overrides) != 1:
        raise ExportError(
            "PPTX presentation content-type Override is duplicated"
        )
    if overrides[0].get("ContentType") != PPTX_CONTENT_TYPE:
        raise ExportError(
            "PPTX presentation content-type Override has an invalid MIME type"
        )


def validate_slide_content_types(
    root: ET.Element,
    slide_names: Sequence[str],
) -> None:
    override_tag = f"{{{CONTENT_TYPES_NAMESPACE}}}Override"
    overrides: Dict[str, List[ET.Element]] = {}
    for child in root:
        if child.tag != override_tag:
            continue
        part_name = child.get("PartName")
        if isinstance(part_name, str):
            overrides.setdefault(part_name, []).append(child)
    for slide_name in slide_names:
        part_name = f"/{slide_name}"
        matches = overrides.get(part_name, [])
        if len(matches) != 1:
            raise ExportError(
                "PPTX slide content-type Override is missing or duplicated: "
                f"{part_name}"
            )
        if matches[0].get("ContentType") != PRESENTATION_SLIDE_CONTENT_TYPE:
            raise ExportError(
                f"PPTX slide content-type Override has an invalid MIME type: {part_name}"
            )


def presentation_slide_relationship_ids(root: ET.Element) -> List[str]:
    presentation_tag = f"{{{PRESENTATION_NAMESPACE}}}presentation"
    slide_list_tag = f"{{{PRESENTATION_NAMESPACE}}}sldIdLst"
    slide_id_tag = f"{{{PRESENTATION_NAMESPACE}}}sldId"
    relationship_id_attribute = f"{{{OFFICE_RELATIONSHIPS_NAMESPACE}}}id"
    if root.tag != presentation_tag:
        raise ExportError("malformed PPTX presentation part: unexpected root element")
    slide_lists = [child for child in root if child.tag == slide_list_tag]
    if len(slide_lists) > 1:
        raise ExportError("malformed PPTX presentation part: duplicate sldIdLst")
    if not slide_lists:
        return []
    relationship_ids: List[str] = []
    seen_ids: set[str] = set()
    for child in slide_lists[0]:
        if child.tag != slide_id_tag:
            raise ExportError(
                "malformed PPTX presentation part: unexpected sldIdLst child"
            )
        relationship_id = child.get(relationship_id_attribute)
        if not relationship_id:
            raise ExportError("PPTX sldId is missing its relationship r:id")
        if relationship_id in seen_ids:
            raise ExportError(
                f"PPTX presentation contains duplicate r:id: {relationship_id}"
            )
        seen_ids.add(relationship_id)
        relationship_ids.append(relationship_id)
    return relationship_ids


def presentation_relationships(root: ET.Element) -> Dict[str, Dict[str, str]]:
    relationships_tag = f"{{{PACKAGE_RELATIONSHIPS_NAMESPACE}}}Relationships"
    relationship_tag = f"{{{PACKAGE_RELATIONSHIPS_NAMESPACE}}}Relationship"
    if root.tag != relationships_tag:
        raise ExportError(
            "malformed PPTX presentation relationships part: unexpected root element"
        )
    relationships: Dict[str, Dict[str, str]] = {}
    for child in root:
        if child.tag != relationship_tag:
            raise ExportError(
                "malformed PPTX presentation relationships part: "
                "unexpected child element"
            )
        relationship_id = child.get("Id")
        relationship_type = child.get("Type")
        target = child.get("Target")
        if not relationship_id or not relationship_type or not target:
            raise ExportError(
                "malformed PPTX presentation relationship: Id, Type, and Target "
                "are required"
            )
        if relationship_id in relationships:
            raise ExportError(
                f"PPTX relationships contain duplicate r:id: {relationship_id}"
            )
        relationship = {
            "Type": relationship_type,
            "Target": target,
        }
        target_mode = child.get("TargetMode")
        if target_mode is not None:
            relationship["TargetMode"] = target_mode
        relationships[relationship_id] = relationship
    return relationships


def validate_package_office_document_relationship(root: ET.Element) -> None:
    relationships = presentation_relationships(root)
    candidates = [
        relationship
        for relationship in relationships.values()
        if relationship["Type"] == OFFICE_DOCUMENT_RELATIONSHIP_TYPE
    ]
    if len(candidates) != 1:
        raise ExportError(
            "PPTX package must contain exactly one officeDocument relationship"
        )
    relationship = candidates[0]
    if relationship.get("TargetMode") not in (None, "Internal"):
        raise ExportError("PPTX officeDocument relationship must be internal")
    if relationship["Target"] != "ppt/presentation.xml":
        raise ExportError(
            "PPTX officeDocument relationship does not target ppt/presentation.xml"
        )


def validate_slide_xml_parts(
    archive: zipfile.ZipFile,
    slide_names: Sequence[str],
) -> None:
    slide_tag = f"{{{PRESENTATION_NAMESPACE}}}sld"
    common_slide_tag = f"{{{PRESENTATION_NAMESPACE}}}cSld"
    shape_tree_tag = f"{{{PRESENTATION_NAMESPACE}}}spTree"
    for slide_name in slide_names:
        root = parse_ooxml_root(archive, slide_name, f"PPTX slide part {slide_name}")
        if root.tag != slide_tag:
            raise ExportError(
                f"PPTX slide has an invalid PresentationML root: {slide_name}"
            )
        common_slides = [child for child in root if child.tag == common_slide_tag]
        if len(common_slides) != 1:
            raise ExportError(
                f"PPTX slide must contain exactly one direct p:cSld: {slide_name}"
            )
        shape_trees = [
            child for child in common_slides[0] if child.tag == shape_tree_tag
        ]
        if len(shape_trees) != 1:
            raise ExportError(
                "PPTX slide p:cSld must contain exactly one direct p:spTree: "
                f"{slide_name}"
            )


def canonical_presentation_slide_target(target: str) -> str:
    match = re.fullmatch(r"slides/slide([1-9]\d*)\.xml", target)
    if not match:
        raise ExportError(
            "PPTX slide relationship target is outside the presentation directory "
            "or is not a canonical slideN.xml part"
        )
    return f"ppt/{target}"


def validate_presentation_slide_graph(
    presentation_root: ET.Element,
    relationships_root: ET.Element,
    archive_names: set[str],
) -> List[str]:
    slide_relationship_ids = presentation_slide_relationship_ids(presentation_root)
    relationships = presentation_relationships(relationships_root)
    ordered_slide_names: List[str] = []
    seen_slide_names: set[str] = set()
    used_relationship_ids: set[str] = set()

    for relationship_id in slide_relationship_ids:
        relationship = relationships.get(relationship_id)
        if relationship is None:
            raise ExportError(
                f"PPTX slide r:id is broken: {relationship_id} has no relationship"
            )
        used_relationship_ids.add(relationship_id)
        target_mode = relationship.get("TargetMode")
        if target_mode not in (None, "Internal"):
            raise ExportError(
                f"PPTX slide r:id uses an external relationship: {relationship_id}"
            )
        if relationship["Type"] != PRESENTATION_SLIDE_RELATIONSHIP_TYPE:
            raise ExportError(
                f"PPTX slide r:id maps to a non-slide relationship: {relationship_id}"
            )
        slide_name = canonical_presentation_slide_target(relationship["Target"])
        if slide_name not in archive_names:
            raise ExportError(
                f"PPTX slide relationship target is missing: {slide_name}"
            )
        if slide_name in seen_slide_names:
            raise ExportError(
                f"PPTX presentation references a slide part more than once: {slide_name}"
            )
        seen_slide_names.add(slide_name)
        ordered_slide_names.append(slide_name)

    unused_slide_relationships = sorted(
        relationship_id
        for relationship_id, relationship in relationships.items()
        if relationship["Type"] == PRESENTATION_SLIDE_RELATIONSHIP_TYPE
        and relationship_id not in used_relationship_ids
    )
    if unused_slide_relationships:
        raise ExportError(
            "PPTX contains an unreferenced slide relationship: "
            f"{unused_slide_relationships[0]}"
        )

    archived_slide_names = {
        name
        for name in archive_names
        if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
    }
    orphaned_slide_names = sorted(archived_slide_names - seen_slide_names)
    if orphaned_slide_names:
        raise ExportError(
            f"PPTX contains an unreferenced slide part: {orphaned_slide_names[0]}"
        )
    return ordered_slide_names


def validate_pptx_archive_stream(
    stream: Any,
    compressed_size: int,
    display_name: str,
    *,
    require_slides: bool = False,
    expected_slides: Optional[int] = None,
) -> List[str]:
    if compressed_size > MAX_PPTX_ARCHIVE_BYTES:
        raise ExportError("PPTX archive exceeds the compressed-size safety limit")
    try:
        preflight_zip_central_directory(
            stream,
            compressed_size,
            max_members=MAX_PPTX_ARCHIVE_MEMBERS,
            max_directory_bytes=MAX_PPTX_CENTRAL_DIRECTORY_BYTES,
            display_name=display_name,
            require_canonical_member_names=True,
        )
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_PPTX_ARCHIVE_MEMBERS:
                raise ExportError("PPTX archive contains too many members")
            names: set[str] = set()
            folded_names: set[str] = set()
            total_size = 0
            for info in infos:
                validate_zip_member_name(info.filename)
                folded = info.filename.casefold()
                if info.filename in names or folded in folded_names:
                    raise ExportError(f"PPTX archive contains duplicate member: {info.filename}")
                names.add(info.filename)
                folded_names.add(folded)
                if info.flag_bits & 0x1:
                    raise ExportError(f"PPTX archive contains encrypted member: {info.filename}")
                if info.file_size > MAX_PPTX_MEMBER_BYTES:
                    raise ExportError(f"PPTX member exceeds the safety limit: {info.filename}")
                if info.filename.lower().endswith((".xml", ".rels")) and info.file_size > MAX_PPTX_XML_MEMBER_BYTES:
                    raise ExportError(f"PPTX XML member exceeds the safety limit: {info.filename}")
                total_size += info.file_size
                if total_size > MAX_PPTX_TOTAL_UNCOMPRESSED_BYTES:
                    raise ExportError("PPTX archive exceeds the total uncompressed-size limit")

            required = {
                "[Content_Types].xml",
                "_rels/.rels",
                "ppt/presentation.xml",
                "ppt/_rels/presentation.xml.rels",
            }
            if not required.issubset(names):
                raise ExportError("PPTX archive is missing required OOXML members")
            content_info = archive.getinfo("[Content_Types].xml")
            if content_info.file_size > MAX_CONTENT_TYPES_BYTES:
                raise ExportError("PPTX content-types part exceeds the safety limit")
            content_types_root = parse_ooxml_root(
                archive,
                "[Content_Types].xml",
                "PPTX content-types part",
            )
            validate_presentation_content_type(content_types_root)
            package_relationships_root = parse_ooxml_root(
                archive,
                "_rels/.rels",
                "PPTX package relationships part",
            )
            validate_package_office_document_relationship(
                package_relationships_root
            )
            presentation_root = parse_ooxml_root(
                archive,
                "ppt/presentation.xml",
                "PPTX presentation part",
            )
            relationships_root = parse_ooxml_root(
                archive,
                "ppt/_rels/presentation.xml.rels",
                "PPTX presentation relationships part",
            )
            slide_names = validate_presentation_slide_graph(
                presentation_root,
                relationships_root,
                names,
            )
            validate_slide_content_types(content_types_root, slide_names)
            validate_slide_xml_parts(archive, slide_names)
            if require_slides and not slide_names:
                raise ExportError("exported PPTX contains no slide XML")
            if expected_slides is not None and len(slide_names) != expected_slides:
                raise ExportError(
                    "exported slide count does not match the PPTD manifest: "
                    f"expected {expected_slides}, received {len(slide_names)}"
                )
            return slide_names
    except ExportError:
        raise
    except UNTRUSTED_ZIP_ERRORS as exc:
        raise ExportError(f"invalid PPTX ZIP: {display_name}") from exc


def validate_pptx_archive(
    path: Path,
    *,
    require_slides: bool = False,
    expected_slides: Optional[int] = None,
) -> List[str]:
    if not path.is_file() or path.name.endswith(".crdownload"):
        raise ExportError(f"PPTX archive does not exist: {path}")
    try:
        with path.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            return validate_pptx_archive_stream(
                stream,
                size,
                str(path),
                require_slides=require_slides,
                expected_slides=expected_slides,
            )
    except OSError as exc:
        raise ExportError(f"invalid PPTX ZIP: {path}") from exc


def is_pptx(path: Path) -> bool:
    try:
        validate_pptx_archive(path)
        return True
    except ExportError:
        return False


def find_download(
    search_roots: Iterable[Path],
    timeout: float = 150.0,
    accept: Callable[[Path], bool] = is_pptx,
    *,
    since: Optional[float] = None,
    maximum_bytes: Optional[int] = None,
) -> Path:
    deadline = time.monotonic() + timeout
    last_sizes: Dict[Path, int] = {}
    stable: Dict[Path, int] = {}
    while time.monotonic() < deadline:
        # Snapshot stats while collecting and tolerate Chrome renaming a live
        # .crdownload file between directory listing and stat(). Callers pass
        # only the isolated per-session download directory.
        entries: List[Tuple[Path, float, int]] = []
        for root in search_roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    info = path.stat()
                except OSError:
                    continue
                entries.append((path, info.st_mtime, info.st_size))
        for path, mtime, size in sorted(entries, key=lambda entry: entry[1], reverse=True):
            if since is not None and mtime < since:
                continue
            if maximum_bytes is not None and size > maximum_bytes:
                raise ExportError(
                    f"browser download exceeds the {maximum_bytes}-byte safety limit: {path}"
                )
            if size == last_sizes.get(path) and size > 0:
                stable[path] = stable.get(path, 0) + 1
            else:
                stable[path] = 0
            last_sizes[path] = size
            if stable[path] >= 1 and accept(path):
                return path
        time.sleep(0.5)
    visible = "\n  ".join(str(path) for path in last_sizes) or "(none)"
    raise ExportError(f"timed out waiting for download; observed files:\n  {visible}")


def _xml_tag_end(data: bytes, start: int) -> int:
    quote: Optional[int] = None
    for index in range(start + 1, len(data)):
        value = data[index]
        if quote is not None:
            if value == quote:
                quote = None
            continue
        if value in (ord('"'), ord("'")):
            quote = value
        elif value == ord(">"):
            return index + 1
    raise ExportError("invalid slide XML: unterminated tag")


def _element_byte_ranges(
    slide_xml: bytes,
    root: ET.Element,
) -> List[Tuple[str, str, int, int, int]]:
    records: List[Dict[str, Any]] = []
    stack: List[Dict[str, Any]] = []
    parser = expat.ParserCreate()

    def start_element(name: str, _attributes: Dict[str, str]) -> None:
        start = parser.CurrentByteIndex
        end = _xml_tag_end(slide_xml, start)
        record: Dict[str, Any] = {
            "qname": name,
            "start": start,
            "end": end if slide_xml[start:end].rstrip().endswith(b"/>") else None,
            "depth": len(stack),
        }
        records.append(record)
        stack.append(record)

    def end_element(_name: str) -> None:
        if not stack:
            raise ExportError("invalid slide XML: unmatched end tag")
        record = stack.pop()
        if record["end"] is None:
            record["end"] = _xml_tag_end(slide_xml, parser.CurrentByteIndex)

    parser.StartElementHandler = start_element
    parser.EndElementHandler = end_element
    try:
        parser.Parse(slide_xml, True)
    except expat.ExpatError as exc:
        raise ExportError(f"invalid slide XML: {exc}") from exc
    elements = list(root.iter())
    if len(records) != len(elements) or any(record["end"] is None for record in records):
        raise ExportError("could not map slide XML elements to their original byte ranges")
    return [
        (
            element.tag,
            record["qname"],
            record["start"],
            record["end"],
            record["depth"],
        )
        for element, record in zip(elements, records)
    ]


def replace_transition(slide_xml: bytes, transition: str) -> bytes:
    try:
        root = ET.fromstring(slide_xml)
    except ET.ParseError as exc:
        raise ExportError(f"invalid slide XML: {exc}") from exc
    slide_tag = f"{{{PRESENTATION_NAMESPACE}}}sld"
    transition_tag = f"{{{PRESENTATION_NAMESPACE}}}transition"
    common_slide_tag = f"{{{PRESENTATION_NAMESPACE}}}cSld"
    color_map_tag = f"{{{PRESENTATION_NAMESPACE}}}clrMapOvr"
    fade_tag = f"{{{PRESENTATION_NAMESPACE}}}fade"
    if root.tag != slide_tag:
        raise ExportError("slide XML has an invalid PresentationML root")
    elements = _element_byte_ranges(slide_xml, root)
    raw_removals = [
        (start, end)
        for tag, _qname, start, end, _depth in elements
        if tag == transition_tag
    ]
    # Invalid decks occasionally contain p:transition below cSld/spTree. Remove
    # every PresentationML transition, not just direct root children, before
    # inserting the one schema-valid root transition. Collapse nested ranges so
    # byte splicing never applies overlapping deletions twice.
    removals: List[Tuple[int, int]] = []
    for start, end in sorted(raw_removals):
        if removals and start >= removals[-1][0] and end <= removals[-1][1]:
            continue
        removals.append((start, end))
    patched = slide_xml
    for start, end in sorted(removals, reverse=True):
        patched = patched[:start] + patched[end:]
    if transition == "none":
        return patched

    anchors = [
        record
        for record in elements
        if record[4] == 1 and record[0] in (common_slide_tag, color_map_tag)
    ]
    common = [record for record in anchors if record[0] == common_slide_tag]
    colors = [record for record in anchors if record[0] == color_map_tag]
    if len(common) != 1 or len(colors) > 1:
        raise ExportError("slide XML has no unique cSld/clrMapOvr insertion anchor")
    anchor = colors[0] if colors else common[0]
    insertion = anchor[3] - sum(
        end - start for start, end in removals if end <= anchor[3]
    )
    # The root's lexical namespace prefix is necessarily in scope for every
    # direct child. An anchor may introduce a prefix only on cSld itself; using
    # that local prefix after the closing tag would create unbound XML.
    root_record = elements[0]
    if root_record[0] != slide_tag or root_record[4] != 0:
        raise ExportError("slide XML root could not be mapped to its lexical tag")
    qname = root_record[1]
    prefix = qname.rsplit(":", 1)[0] if ":" in qname else ""
    qualified = (lambda local: f"{prefix}:{local}" if prefix else local)
    fade_xml = (
        f'<{qualified("transition")} spd="fast" advClick="1">'
        f'<{qualified("fade")}/></{qualified("transition")}>'
    ).encode("utf-8")
    return patched[:insertion] + fade_xml + patched[insertion:]


def root_child_names(slide_xml: bytes) -> List[str]:
    try:
        root = ET.fromstring(slide_xml)
    except ET.ParseError as exc:
        raise ExportError(f"invalid slide XML: {exc}") from exc
    if root.tag != f"{{{PRESENTATION_NAMESPACE}}}sld":
        raise ExportError("slide XML has an invalid PresentationML root")
    prefix = f"{{{PRESENTATION_NAMESPACE}}}"
    return [
        child.tag[len(prefix) :] if child.tag.startswith(prefix) else child.tag
        for child in root
    ]


def has_direct_fade_transition(slide_xml: bytes) -> bool:
    try:
        root = ET.fromstring(slide_xml)
    except ET.ParseError as exc:
        raise ExportError(f"invalid slide XML: {exc}") from exc
    transition_tag = f"{{{PRESENTATION_NAMESPACE}}}transition"
    fade_tag = f"{{{PRESENTATION_NAMESPACE}}}fade"
    transition = next((child for child in root if child.tag == transition_tag), None)
    if transition is None:
        return False
    return any(child.tag == fade_tag for child in transition)


def validate_transition_order(slide_xml: bytes, transition: str) -> None:
    names = root_child_names(slide_xml)
    transition_indexes = [index for index, name in enumerate(names) if name == "transition"]
    if transition == "none":
        if transition_indexes:
            raise ExportError("transition=none left a root-level transition")
        return
    if len(transition_indexes) != 1 or not has_direct_fade_transition(slide_xml):
        raise ExportError("slide does not contain exactly one root-level fade transition")
    transition_index = transition_indexes[0]
    for required_before in ("cSld", "clrMapOvr"):
        if required_before in names and names.index(required_before) > transition_index:
            raise ExportError(f"{required_before} appears after transition")
    for required_after in ("timing", "extLst"):
        if required_after in names and names.index(required_after) < transition_index:
            raise ExportError(f"{required_after} appears before transition")


def patch_transitions(
    pptx: Path,
    transition: str,
    expected_identity: Optional[PathSnapshot] = None,
) -> int:
    expected_identity = expected_identity or capture_path_snapshot(pptx)
    descriptor = _open_verified_private_file(
        pptx,
        expected_identity,
        writable=True,
        require_exact_snapshot=True,
    )
    slide_count = 0
    try:
        with os.fdopen(descriptor, "r+b") as original, tempfile.TemporaryFile(
            mode="w+b"
        ) as patched:
            descriptor = -1
            original_size = os.fstat(original.fileno()).st_size
            validate_pptx_archive_stream(
                original,
                original_size,
                str(pptx),
                require_slides=True,
            )
            original.seek(0)
            with zipfile.ZipFile(original, "r") as source, zipfile.ZipFile(
                patched, "w"
            ) as target:
                target.comment = source.comment
                for info in source.infolist():
                    data = source.read(info.filename)
                    if re.fullmatch(r"ppt/slides/slide\d+\.xml", info.filename):
                        data = replace_transition(data, transition)
                        slide_count += 1
                    target.writestr(info, data, compress_type=info.compress_type)
            if slide_count == 0:
                raise ExportError("exported PPTX contains no slide XML")
            patched_size = os.fstat(patched.fileno()).st_size
            validate_pptx_archive_stream(
                patched,
                patched_size,
                "patched PPTX staging stream",
                require_slides=True,
            )
            patched.seek(0)
            original.seek(0)
            original.truncate(0)
            shutil.copyfileobj(patched, original, length=1024 * 1024)
            original.flush()
            os.fsync(original.fileno())
            final_info = os.fstat(original.fileno())
            patched_snapshot = PathSnapshot(
                True,
                final_info.st_dev,
                final_info.st_ino,
                final_info.st_mode,
                final_info.st_size,
                final_info.st_mtime_ns,
                final_info.st_ctime_ns,
            )
    except ExportError:
        raise
    except UNTRUSTED_ZIP_ERRORS as exc:
        raise ExportError(f"invalid PPTX ZIP while patching transitions: {pptx}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    _verify_closed_write_snapshot(
        capture_path_snapshot(pptx),
        patched_snapshot,
        patched_snapshot.size or 0,
        "private PPTX staging path changed during transition patching",
    )
    return slide_count


def verify_slide_transitions(
    archive: zipfile.ZipFile,
    slide_names: Sequence[str],
    transition: str,
) -> int:
    transition_hits = 0
    for name in slide_names:
        data = archive.read(name)
        validate_transition_order(data, transition)
        if has_direct_fade_transition(data):
            transition_hits += 1
    return transition_hits


def verify_output(
    pptx: Path,
    transition: str,
    expect_fonts: bool,
    expected_slides: Optional[int] = None,
    expected_identity: Optional[PathSnapshot] = None,
) -> Dict[str, Any]:
    expected_identity = expected_identity or capture_path_snapshot(pptx)
    descriptor = _open_verified_private_file(
        pptx,
        expected_identity,
        writable=False,
        require_exact_snapshot=True,
    )
    try:
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            size = os.fstat(stream.fileno()).st_size
            validated_slides = validate_pptx_archive_stream(
                stream,
                size,
                str(pptx),
                require_slides=True,
                expected_slides=expected_slides,
            )
            stream.seek(0)
            with zipfile.ZipFile(stream) as archive:
                broken = archive.testzip()
                if broken:
                    raise ExportError(f"PPTX CRC check failed at: {broken}")
                slide_names = validated_slides
                transition_hits = verify_slide_transitions(
                    archive,
                    slide_names,
                    transition,
                )
                if transition == "fade" and transition_hits != len(slide_names):
                    raise ExportError("fade transition was not written to every slide")
                fonts = [
                    name
                    for name in archive.namelist()
                    if name.startswith("ppt/fonts/") and not name.endswith("/")
                ]
    except ExportError:
        raise
    except UNTRUSTED_ZIP_ERRORS as exc:
        raise ExportError(f"invalid PPTX ZIP while verifying output: {pptx}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not snapshots_match(capture_path_snapshot(pptx), expected_identity):
        raise ExportError("private PPTX staging path changed during verification")
    if expect_fonts and not fonts:
        log(
            "warning: embed-fonts was enabled, but the official writer produced no font part"
        )
    return {
        "slides": len(slide_names),
        "fadeTransitions": transition_hits,
        "fontParts": len(fonts),
        "bytes": size,
    }


def prepare_host_assets(directory: Path, payload: Dict[str, Any]) -> None:
    shutil.copy2(HOST_TEMPLATE, directory / HOST_TEMPLATE.name)
    shutil.copy2(PENPAL_MODULE, directory / PENPAL_MODULE.name)
    (directory / "payload.json").write_text(
        # ASCII escaping preserves all JSON string values, including a lone
        # surrogate produced by a permissive YAML decoder, without asking the
        # filesystem UTF-8 encoder to represent an invalid Unicode scalar.
        json.dumps(payload, ensure_ascii=True), encoding="utf-8"
    )


def serve(directory: Path) -> Tuple[ThreadingHTTPServer, threading.Thread, str]:
    handler = lambda *args, **kwargs: QuietHandler(  # noqa: E731
        *args, directory=str(directory), **kwargs
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    host, port = server.server_address
    server.allowed_host = f"{host}:{port}"  # type: ignore[attr-defined]
    capability = f"/{uuid.uuid4().hex}"
    server.capability_prefix = capability  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://{host}:{port}{capability}/export_host.html"


def is_same_or_ancestor(candidate: Path, target: Path) -> bool:
    """Return whether candidate is target or one of target's ancestors."""
    try:
        target.relative_to(candidate)
        return True
    except ValueError:
        return False


def path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


class PathSnapshot(NamedTuple):
    exists: bool
    device: Optional[int] = None
    inode: Optional[int] = None
    mode: Optional[int] = None
    size: Optional[int] = None
    modified_ns: Optional[int] = None
    changed_ns: Optional[int] = None


ABSENT_PATH_SNAPSHOT = PathSnapshot(False)
_MANIFEST_PARENT_IDENTITIES: Dict[str, PathSnapshot] = {}
_MANIFEST_PARENT_IDENTITIES_LOCK = threading.Lock()


# Win32 directory capabilities -------------------------------------------------
#
# CreateFileW is path based, but a directory HANDLE returned by it remains bound
# to the opened directory when its display path is renamed or temporarily
# redirected.  FILE_RENAME_INFO accepts that HANDLE as RootDirectory, so the
# publication target can be resolved by the kernel without re-walking the output
# pathname.  Source/input handles are additionally checked with
# GetFinalPathNameByHandleW before they are trusted.  This closes the ABA hole in
# the former "verify path, rename, verify path" fallback.
WIN32_ERROR_ACCESS_DENIED = 5
WIN32_ERROR_FILE_EXISTS = 80
WIN32_ERROR_ALREADY_EXISTS = 183
WIN32_UNSUPPORTED_ERRORS = frozenset({1, 50, 87, 120})
WIN32_FILE_RENAME_INFORMATION = 10
WIN32_DELETE = 0x00010000
WIN32_SYNCHRONIZE = 0x00100000
WIN32_FILE_LIST_DIRECTORY = 0x00000001
WIN32_FILE_ADD_FILE = 0x00000002
WIN32_FILE_ADD_SUBDIRECTORY = 0x00000004
WIN32_FILE_TRAVERSE = 0x00000020
WIN32_FILE_READ_ATTRIBUTES = 0x00000080
WIN32_FILE_SHARE_READ = 0x00000001
WIN32_FILE_SHARE_WRITE = 0x00000002
WIN32_FILE_SHARE_DELETE = 0x00000004
WIN32_OPEN_EXISTING = 3
WIN32_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
WIN32_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
WIN32_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
WIN32_MAX_FINAL_PATH_CHARS = 32_768


class _Win32FileInformation(ctypes.Structure):
    _fields_ = [
        ("file_attributes", ctypes.c_uint32),
        ("creation_time_low", ctypes.c_uint32),
        ("creation_time_high", ctypes.c_uint32),
        ("access_time_low", ctypes.c_uint32),
        ("access_time_high", ctypes.c_uint32),
        ("write_time_low", ctypes.c_uint32),
        ("write_time_high", ctypes.c_uint32),
        ("volume_serial_number", ctypes.c_uint32),
        ("file_size_high", ctypes.c_uint32),
        ("file_size_low", ctypes.c_uint32),
        ("number_of_links", ctypes.c_uint32),
        ("file_index_high", ctypes.c_uint32),
        ("file_index_low", ctypes.c_uint32),
    ]


class _Win32RenameInfoHeader(ctypes.Structure):
    # The first FILE_RENAME_INFO member is a BOOLEAN/DWORD union.  Reserving the
    # full DWORD gives RootDirectory the same alignment as the Windows header on
    # both 32-bit and 64-bit runtimes.
    _fields_ = [
        ("flags", ctypes.c_uint32),
        ("root_directory", ctypes.c_void_p),
        ("file_name_length", ctypes.c_uint32),
    ]


class _Win32IoStatusBlock(ctypes.Structure):
    _fields_ = [
        ("status_or_pointer", ctypes.c_void_p),
        ("information", ctypes.c_size_t),
    ]


def _win32_error_number(exc: BaseException) -> Optional[int]:
    value = getattr(exc, "winerror", None)
    if isinstance(value, int):
        return value
    value = getattr(exc, "errno", None)
    return value if isinstance(value, int) else None


def _raise_win32_api_error(error_number: int, path: Path) -> None:
    if error_number in {WIN32_ERROR_FILE_EXISTS, WIN32_ERROR_ALREADY_EXISTS}:
        raise FileExistsError(errno.EEXIST, "destination already exists", path)
    if error_number in WIN32_UNSUPPORTED_ERRORS:
        raise ExportError(
            "Windows runtime or filesystem cannot perform handle-relative "
            "no-replace publication"
        )
    formatter = getattr(ctypes, "FormatError", None)
    detail = formatter(error_number) if callable(formatter) else "Win32 API error"
    error = OSError(error_number, detail, os.fspath(path))
    try:
        error.winerror = error_number  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        pass
    raise error


def _normalized_win32_parts(value: os.PathLike[str] | str) -> Tuple[str, ...]:
    text = os.fspath(value).replace("/", "\\")
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    elif text.startswith("\\??\\"):
        text = text[4:]
    return tuple(part.casefold() for part in PureWindowsPath(text).parts)


def _win32_paths_equal(
    first: os.PathLike[str] | str,
    second: os.PathLike[str] | str,
) -> bool:
    return _normalized_win32_parts(first) == _normalized_win32_parts(second)


def _win32_path_is_within(
    candidate: os.PathLike[str] | str,
    root: os.PathLike[str] | str,
    *,
    allow_root: bool = False,
) -> bool:
    candidate_parts = _normalized_win32_parts(candidate)
    root_parts = _normalized_win32_parts(root)
    return (
        len(candidate_parts) >= len(root_parts) + (0 if allow_root else 1)
        and candidate_parts[: len(root_parts)] == root_parts
    )


class _CtypesWin32DirectoryApi:
    """Small, mockable wrapper around the Win32 HANDLE operations we require."""

    def __init__(self, kernel32: Any, ntdll: Any) -> None:
        required = (
            "CreateFileW",
            "CloseHandle",
            "GetFileInformationByHandle",
            "GetFinalPathNameByHandleW",
        )
        missing = [name for name in required if not hasattr(kernel32, name)]
        if missing:
            raise ExportError(
                "Windows runtime lacks required stable-handle APIs: "
                + ", ".join(missing)
            )
        self.kernel32 = kernel32
        required_nt = ("NtSetInformationFile", "RtlNtStatusToDosError")
        missing_nt = [name for name in required_nt if not hasattr(ntdll, name)]
        if missing_nt:
            raise ExportError(
                "Windows runtime lacks required handle-relative rename APIs: "
                + ", ".join(missing_nt)
            )
        self.ntdll = ntdll
        kernel32.CreateFileW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        kernel32.CreateFileW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.GetFileInformationByHandle.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_Win32FileInformation),
        ]
        kernel32.GetFileInformationByHandle.restype = ctypes.c_int
        kernel32.GetFinalPathNameByHandleW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        kernel32.GetFinalPathNameByHandleW.restype = ctypes.c_uint32
        ntdll.NtSetInformationFile.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_Win32IoStatusBlock),
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_int,
        ]
        ntdll.NtSetInformationFile.restype = ctypes.c_long
        ntdll.RtlNtStatusToDosError.argtypes = [ctypes.c_long]
        ntdll.RtlNtStatusToDosError.restype = ctypes.c_uint32

    @staticmethod
    def _last_error() -> int:
        getter = getattr(ctypes, "get_last_error", None)
        return int(getter()) if callable(getter) else 0

    @staticmethod
    def _invalid_handle() -> int:
        return int(ctypes.c_void_p(-1).value or -1)

    def _create_file(self, path: Path, access: int, flags: int) -> int:
        handle = self.kernel32.CreateFileW(
            os.fspath(path),
            access,
            WIN32_FILE_SHARE_READ | WIN32_FILE_SHARE_WRITE | WIN32_FILE_SHARE_DELETE,
            None,
            WIN32_OPEN_EXISTING,
            flags,
            None,
        )
        numeric = int(handle or 0)
        if not numeric or numeric == self._invalid_handle():
            error_number = self._last_error()
            error = OSError(error_number, "CreateFileW failed", os.fspath(path))
            try:
                error.winerror = error_number  # type: ignore[attr-defined]
            except (AttributeError, TypeError):
                pass
            raise error
        return numeric

    def open_directory(self, path: Path, *, writable: bool) -> int:
        access = (
            WIN32_FILE_LIST_DIRECTORY
            | WIN32_FILE_TRAVERSE
            | WIN32_FILE_READ_ATTRIBUTES
            | WIN32_SYNCHRONIZE
        )
        if writable:
            access |= WIN32_FILE_ADD_FILE | WIN32_FILE_ADD_SUBDIRECTORY
        return self._create_file(
            path,
            access,
            WIN32_FILE_FLAG_BACKUP_SEMANTICS | WIN32_FILE_FLAG_OPEN_REPARSE_POINT,
        )

    def open_rename_source(self, path: Path) -> int:
        return self._create_file(
            path,
            WIN32_DELETE | WIN32_FILE_READ_ATTRIBUTES | WIN32_SYNCHRONIZE,
            WIN32_FILE_FLAG_BACKUP_SEMANTICS | WIN32_FILE_FLAG_OPEN_REPARSE_POINT,
        )

    def close(self, handle: int) -> None:
        self.kernel32.CloseHandle(ctypes.c_void_p(handle))

    def is_directory(self, handle: int) -> bool:
        information = _Win32FileInformation()
        if not self.kernel32.GetFileInformationByHandle(
            ctypes.c_void_p(handle),
            ctypes.byref(information),
        ):
            _raise_win32_api_error(self._last_error(), Path("<directory handle>"))
        return bool(information.file_attributes & WIN32_FILE_ATTRIBUTE_DIRECTORY)

    def final_path(self, handle: int) -> str:
        size = 512
        while size <= WIN32_MAX_FINAL_PATH_CHARS:
            buffer = ctypes.create_unicode_buffer(size)
            result = int(
                self.kernel32.GetFinalPathNameByHandleW(
                    ctypes.c_void_p(handle),
                    buffer,
                    size,
                    0,
                )
            )
            if result == 0:
                _raise_win32_api_error(
                    self._last_error(),
                    Path("<opened handle>"),
                )
            if result < size:
                return buffer.value
            size = result + 1
        raise ExportError("opened Windows path exceeds the supported length limit")

    def rename_noreplace(
        self,
        source_handle: int,
        destination_directory_handle: int,
        destination_name: str,
        destination_display: Path,
    ) -> None:
        encoded_name = destination_name.encode("utf-16-le")
        name_offset = _Win32RenameInfoHeader.file_name_length.offset + ctypes.sizeof(
            ctypes.c_uint32
        )
        buffer_size = max(
            ctypes.sizeof(_Win32RenameInfoHeader),
            name_offset + len(encoded_name),
        )
        buffer = ctypes.create_string_buffer(buffer_size)
        header = _Win32RenameInfoHeader.from_buffer(buffer)
        header.flags = 0  # ReplaceIfExists == FALSE: kernel-enforced no-replace.
        header.root_directory = destination_directory_handle
        header.file_name_length = len(encoded_name)
        if encoded_name:
            ctypes.memmove(
                ctypes.addressof(buffer) + name_offset,
                encoded_name,
                len(encoded_name),
            )
        io_status = _Win32IoStatusBlock()
        status = int(self.ntdll.NtSetInformationFile(
            ctypes.c_void_p(source_handle),
            ctypes.byref(io_status),
            buffer,
            buffer_size,
            WIN32_FILE_RENAME_INFORMATION,
        ))
        if status != 0:
            error_number = int(self.ntdll.RtlNtStatusToDosError(status))
            _raise_win32_api_error(error_number, destination_display)


def _load_win32_directory_api() -> _CtypesWin32DirectoryApi:
    loader = getattr(ctypes, "WinDLL", None)
    if not callable(loader):
        raise ExportError(
            "Windows stable directory-handle APIs are unavailable; refusing "
            "path-based publication"
        )
    try:
        kernel32 = loader("kernel32", use_last_error=True)
        ntdll = loader("ntdll", use_last_error=True)
    except (OSError, TypeError) as exc:
        raise ExportError(
            "Windows stable directory-handle APIs are unavailable; refusing "
            "path-based publication"
        ) from exc
    return _CtypesWin32DirectoryApi(kernel32, ntdll)


def _native_handle_from_descriptor(descriptor: int) -> int:
    try:
        import msvcrt
    except ImportError as exc:
        raise ExportError(
            "Windows descriptor-to-HANDLE validation is unavailable"
        ) from exc
    try:
        return int(msvcrt.get_osfhandle(descriptor))
    except (OSError, ValueError) as exc:
        raise ExportError("cannot validate the opened Windows file handle") from exc


class _WindowsDirectoryHandle:
    def __init__(
        self,
        api: Any,
        handle: int,
        final_path: str,
        *,
        writable: bool,
    ) -> None:
        self.api = api
        self.handle = handle
        self.opened_final_path = final_path
        self.writable = writable
        self.closed = False

    @classmethod
    def open(
        cls,
        canonical: Path,
        *,
        api: Optional[Any] = None,
    ) -> "_WindowsDirectoryHandle":
        selected_api = api if api is not None else _load_win32_directory_api()
        writable = True
        try:
            handle = selected_api.open_directory(canonical, writable=True)
        except OSError as exc:
            if _win32_error_number(exc) != WIN32_ERROR_ACCESS_DENIED:
                raise
            writable = False
            handle = selected_api.open_directory(canonical, writable=False)
        try:
            if not selected_api.is_directory(handle):
                raise ExportError(
                    f"Windows directory capability is not a directory: {canonical}"
                )
            final_path = selected_api.final_path(handle)
            if not _win32_paths_equal(final_path, canonical):
                raise ExportError(
                    "Windows directory binding changed while the stable handle "
                    f"was opened: {canonical}"
                )
            return cls(selected_api, handle, final_path, writable=writable)
        except Exception:
            selected_api.close(handle)
            raise

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.api.close(self.handle)

    def _ensure_open(self) -> None:
        if self.closed:
            raise ExportError("Windows directory capability is already closed")

    def final_path(self) -> str:
        self._ensure_open()
        return self.api.final_path(self.handle)

    def verify_native_handle(
        self,
        opened_handle: int,
        display_path: Path,
        *,
        allow_root: bool = False,
    ) -> None:
        self._ensure_open()
        opened_final_path = self.api.final_path(opened_handle)
        root_final_path = self.final_path()
        if not _win32_path_is_within(
            opened_final_path,
            root_final_path,
            allow_root=allow_root,
        ):
            raise ExportError(
                "opened Windows handle is outside the anchored directory: "
                f"{display_path}"
            )

    def rename_noreplace(
        self,
        source_display: Path,
        destination_name: str,
        destination_display: Path,
    ) -> None:
        self._ensure_open()
        if not self.writable:
            raise ExportError(
                "Windows directory handle lacks publication rights; refusing "
                "path-based rename fallback"
            )
        source_handle = self.api.open_rename_source(source_display)
        try:
            self.verify_native_handle(source_handle, source_display)
            # FILE_RENAME_INFO resolves the simple destination name relative to
            # the retained directory HANDLE.  No destination pathname is walked
            # between validation and publication.
            self.api.rename_noreplace(
                source_handle,
                self.handle,
                destination_name,
                destination_display,
            )
        finally:
            self.api.close(source_handle)


def capture_path_snapshot(path: Path) -> PathSnapshot:
    """Capture enough lstat identity to reject a changed publication target."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return ABSENT_PATH_SNAPSHOT
    if os.name == "nt" and stat.S_ISREG(info.st_mode):
        # CPython's Windows path-stat and descriptor-fstat implementations can
        # expose different file-index representations for the same file. Use a
        # descriptor snapshot for regular files so later verified opens compare
        # like with like. The preceding lstat still rejects an existing reparse
        # point instead of silently treating its target as the named entry.
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise ExportError(f"file type changed during snapshot: {path}")
            return PathSnapshot(
                True,
                opened.st_dev,
                opened.st_ino,
                opened.st_mode,
                opened.st_size,
                opened.st_mtime_ns,
                opened.st_ctime_ns,
            )
        finally:
            os.close(descriptor)
    return PathSnapshot(
        True,
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _manifest_identity_key(path: Path) -> str:
    try:
        return os.path.normcase(os.path.abspath(os.fspath(path)))
    except (OSError, ValueError, UnicodeError):
        return os.fspath(path)


def _remember_manifest_parent(manifest: Path) -> None:
    parent = capture_path_snapshot(manifest.parent)
    if parent.mode is None or not stat.S_ISDIR(parent.mode):
        raise ExportError(f"PPTD project directory changed during discovery: {manifest.parent}")
    with _MANIFEST_PARENT_IDENTITIES_LOCK:
        _MANIFEST_PARENT_IDENTITIES[_manifest_identity_key(manifest)] = parent


def _consume_manifest_parent_identity(manifest: Path) -> Optional[PathSnapshot]:
    with _MANIFEST_PARENT_IDENTITIES_LOCK:
        return _MANIFEST_PARENT_IDENTITIES.pop(
            _manifest_identity_key(manifest),
            None,
        )


def _snapshot_from_stat(info: os.stat_result) -> PathSnapshot:
    return PathSnapshot(
        True,
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


class AnchoredDirectory:
    """A stable directory capability for input reads and output publication.

    ``Path.resolve()`` is used only to choose the initial, canonical directory.
    Every POSIX path component is then opened relative to an already-open parent
    with ``O_NOFOLLOW``.  Once constructed, child opens and renames are relative
    to the retained directory descriptor, so renaming/replacing an ancestor
    cannot redirect an export.  Windows retains a directory HANDLE, validates
    every path-opened input by its final HANDLE path, and publishes with
    FILE_RENAME_INFO relative to that directory HANDLE.  Platforms providing
    neither mechanism fail closed instead of claiming path snapshots are an
    equivalent publication boundary.
    """

    def __init__(
        self,
        root: Path,
        descriptor: Optional[int],
        identity: PathSnapshot,
        fallback_ancestors: Tuple[Tuple[Path, PathSnapshot], ...] = (),
        windows_handle: Optional[_WindowsDirectoryHandle] = None,
    ) -> None:
        self.root = root
        self._descriptor = descriptor
        self.identity = identity
        self._fallback_ancestors = fallback_ancestors
        self._windows_handle = windows_handle
        self._closed = False

    @classmethod
    def open(cls, path: Path, *, create: bool = False) -> "AnchoredDirectory":
        canonical = safe_resolve_path(path, description="directory capability")
        if os.name == "nt":
            try:
                if create:
                    canonical.mkdir(parents=True, exist_ok=True)
                identity = capture_path_snapshot(canonical)
                if identity.mode is None or not stat.S_ISDIR(identity.mode):
                    raise ExportError(
                        f"directory capability is not a directory: {canonical}"
                    )
                windows_handle = _WindowsDirectoryHandle.open(canonical)
                return cls(
                    canonical,
                    None,
                    identity,
                    windows_handle=windows_handle,
                )
            except (OSError, RuntimeError, ValueError, UnicodeError) as exc:
                if isinstance(exc, ExportError):
                    raise
                raise ExportError(
                    f"cannot securely open Windows directory capability "
                    f"{canonical}: {exc}"
                ) from exc
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        supports_nofollow = isinstance(nofollow, int) and nofollow != 0
        supports_dir_fd = (
            os.name == "posix"
            and os.open in getattr(os, "supports_dir_fd", set())
            and os.stat in getattr(os, "supports_dir_fd", set())
            and supports_nofollow
        )
        if supports_dir_fd:
            flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            anchor = Path(canonical.anchor)
            try:
                descriptor = os.open(anchor, flags | nofollow)
                consumed = len(anchor.parts)
                for component in canonical.parts[consumed:]:
                    if create:
                        try:
                            os.mkdir(component, 0o700, dir_fd=descriptor)
                        except FileExistsError:
                            pass
                    child = os.open(
                        component,
                        flags | nofollow,
                        dir_fd=descriptor,
                    )
                    os.close(descriptor)
                    descriptor = child
                info = os.fstat(descriptor)
                if not stat.S_ISDIR(info.st_mode):
                    raise ExportError(
                        f"directory capability is not a directory: {canonical}"
                    )
                return cls(canonical, descriptor, _snapshot_from_stat(info))
            except Exception as exc:
                if "descriptor" in locals():
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                if isinstance(exc, ExportError):
                    raise
                raise ExportError(
                    f"cannot securely open directory capability {canonical}: {exc}"
                ) from exc

        if os.name == "posix" and not supports_nofollow:
            raise ExportError(
                "platform lacks O_NOFOLLOW required for a stable directory "
                "descriptor; refusing path-based input access and publication"
            )
        raise ExportError(
            "platform lacks a stable directory descriptor or Windows HANDLE; "
            "refusing path-based input access and publication"
        )

    def __enter__(self) -> "AnchoredDirectory":
        return self

    @property
    def uses_dir_fd(self) -> bool:
        return self._descriptor is not None

    @property
    def uses_windows_handle(self) -> bool:
        return self._windows_handle is not None

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._descriptor is not None:
            os.close(self._descriptor)
        if self._windows_handle is not None:
            self._windows_handle.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise ExportError("directory capability is already closed")

    def _relative_parts(self, relative: os.PathLike[str] | str) -> Tuple[str, ...]:
        value = Path(relative)
        if value.is_absolute():
            raise ExportError(f"capability path must be relative: {relative}")
        parts = tuple(part for part in value.parts if part not in ("", "."))
        if any(part == ".." for part in parts):
            raise ExportError(f"capability path contains parent traversal: {relative}")
        if any("\0" in part for part in parts):
            raise ExportError("capability path contains an unsupported character")
        return parts

    def relative_path(self, path: Path) -> Path:
        try:
            return path.relative_to(self.root)
        except ValueError:
            # macOS commonly spells the same temporary tree as both /var and
            # /private/var. Resolve only to normalize that display alias; child
            # access still occurs through the retained root descriptor.
            try:
                return path.resolve().relative_to(self.root)
            except (OSError, RuntimeError, ValueError, UnicodeError) as exc:
                raise ExportError(
                    f"path is outside the anchored directory {self.root}: {path}"
                ) from exc

    def display_path(self, relative: os.PathLike[str] | str) -> Path:
        return self.root.joinpath(*self._relative_parts(relative))

    def _verify_fallback(self) -> None:
        self._ensure_open()
        if self._windows_handle is not None:
            # Keep an instantaneous binding check around path-only helper
            # operations, but never treat it as the capability boundary:
            # opened files are verified by HANDLE and publication is relative
            # to the retained RootDirectory HANDLE.
            actual = capture_path_snapshot(self.root)
            if not _same_file_identity(actual, self.identity):
                raise ExportError(
                    f"directory path changed after validation: {self.root}"
                )
            return
        for path, expected in self._fallback_ancestors:
            actual = capture_path_snapshot(path)
            if not _same_file_identity(actual, expected):
                raise ExportError(
                    f"directory ancestor changed after validation: {path}"
                )

    def assert_path_binding(self) -> None:
        """Reject a renamed/replaced display path without abandoning the handle."""
        self._ensure_open()
        actual = capture_path_snapshot(self.root)
        if not _same_file_identity(actual, self.identity):
            raise ExportError(
                f"directory path changed after validation: {self.root}"
            )
        if self._descriptor is None and self._windows_handle is None:
            self._verify_fallback()

    def _open_parent_fd(
        self,
        parts: Tuple[str, ...],
        *,
        create_parents: bool = False,
    ) -> Tuple[int, str]:
        if self._descriptor is None:
            raise ExportError("directory descriptors are unavailable on this platform")
        if not parts:
            raise ExportError("operation requires a child path")
        descriptor = os.dup(self._descriptor)
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            for component in parts[:-1]:
                if create_parents:
                    try:
                        os.mkdir(component, 0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                child = os.open(component, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            return descriptor, parts[-1]
        except Exception:
            os.close(descriptor)
            raise

    def open_directory(self, relative: os.PathLike[str] | str = ".") -> int:
        parts = self._relative_parts(relative)
        if self._descriptor is None:
            self._verify_fallback()
            path = self.display_path(relative)
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
            descriptor = os.open(path, flags)
            try:
                if self._windows_handle is None:
                    raise ExportError(
                        "stable directory handle is unavailable for directory open"
                    )
                self._windows_handle.verify_native_handle(
                    _native_handle_from_descriptor(descriptor),
                    path,
                    allow_root=not parts,
                )
                return descriptor
            except Exception:
                os.close(descriptor)
                raise
        if not parts:
            return os.dup(self._descriptor)
        parent, leaf = self._open_parent_fd(parts)
        try:
            return os.open(
                leaf,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent,
            )
        finally:
            os.close(parent)

    def open_file(
        self,
        relative: os.PathLike[str] | str,
        flags: int,
        mode: int = 0o600,
        *,
        create_parents: bool = False,
    ) -> int:
        parts = self._relative_parts(relative)
        secured_flags = flags | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            secured_flags |= os.O_NOFOLLOW
        if self._descriptor is None:
            self._verify_fallback()
            path = self.display_path(relative)
            if create_parents:
                path.parent.mkdir(parents=True, exist_ok=True)
                self._verify_fallback()
            descriptor = os.open(path, secured_flags, mode)
            try:
                if self._windows_handle is None:
                    raise ExportError(
                        "stable directory handle is unavailable for file open"
                    )
                self._windows_handle.verify_native_handle(
                    _native_handle_from_descriptor(descriptor),
                    path,
                )
                return descriptor
            except Exception:
                os.close(descriptor)
                raise
        parent, leaf = self._open_parent_fd(
            parts,
            create_parents=create_parents,
        )
        try:
            return os.open(leaf, secured_flags, mode, dir_fd=parent)
        finally:
            os.close(parent)

    def capture(self, relative: os.PathLike[str] | str) -> PathSnapshot:
        parts = self._relative_parts(relative)
        if not parts:
            if self._descriptor is None:
                self._verify_fallback()
                return capture_path_snapshot(self.root)
            return _snapshot_from_stat(os.fstat(self._descriptor))
        if self._descriptor is None:
            self._verify_fallback()
            snapshot = capture_path_snapshot(self.display_path(relative))
            self._verify_fallback()
            return snapshot
        try:
            parent, leaf = self._open_parent_fd(parts)
        except FileNotFoundError:
            return ABSENT_PATH_SNAPSHOT
        except OSError as exc:
            raise ExportError(
                f"cannot traverse anchored path {self.display_path(relative)}: {exc}"
            ) from exc
        try:
            try:
                return _snapshot_from_stat(
                    os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                )
            except FileNotFoundError:
                return ABSENT_PATH_SNAPSHOT
            except OSError as exc:
                raise ExportError(
                    f"cannot inspect anchored path {self.display_path(relative)}: {exc}"
                ) from exc
        finally:
            os.close(parent)

    def mkdir(
        self,
        relative: os.PathLike[str] | str,
        mode: int = 0o700,
        *,
        parents: bool = False,
    ) -> None:
        parts = self._relative_parts(relative)
        if self._descriptor is None:
            self._verify_fallback()
            self.display_path(relative).mkdir(mode=mode, parents=parents)
            self._verify_fallback()
            return
        parent, leaf = self._open_parent_fd(parts, create_parents=parents)
        try:
            os.mkdir(leaf, mode, dir_fd=parent)
        finally:
            os.close(parent)

    def unlink(self, relative: os.PathLike[str] | str) -> None:
        parts = self._relative_parts(relative)
        if self._descriptor is None:
            self._verify_fallback()
            self.display_path(relative).unlink()
            self._verify_fallback()
            return
        parent, leaf = self._open_parent_fd(parts)
        try:
            os.unlink(leaf, dir_fd=parent)
        finally:
            os.close(parent)

    def rmdir(self, relative: os.PathLike[str] | str) -> None:
        parts = self._relative_parts(relative)
        if self._descriptor is None:
            self._verify_fallback()
            self.display_path(relative).rmdir()
            self._verify_fallback()
            return
        parent, leaf = self._open_parent_fd(parts)
        try:
            os.rmdir(leaf, dir_fd=parent)
        finally:
            os.close(parent)

    def rename_noreplace(
        self,
        source: os.PathLike[str] | str,
        destination: os.PathLike[str] | str,
    ) -> None:
        source_parts = self._relative_parts(source)
        destination_parts = self._relative_parts(destination)
        if self._descriptor is None:
            if self._windows_handle is None:
                raise ExportError(
                    "stable handle-relative rename is unavailable on this platform"
                )
            if len(source_parts) != 1 or len(destination_parts) != 1:
                raise ExportError(
                    "Windows handle-relative publication requires sibling names"
                )
            self._windows_handle.rename_noreplace(
                self.display_path(source),
                destination_parts[0],
                self.display_path(destination),
            )
            return
        source_parent, source_leaf = self._open_parent_fd(source_parts)
        destination_parent, destination_leaf = self._open_parent_fd(destination_parts)
        try:
            _renameat_noreplace(
                source_parent,
                source_leaf,
                destination_parent,
                destination_leaf,
                self.display_path(destination),
            )
        finally:
            os.close(source_parent)
            os.close(destination_parent)


_ACTIVE_INPUT_CAPABILITY: contextvars.ContextVar[Optional[AnchoredDirectory]] = (
    contextvars.ContextVar("open_kimi_ppt_input_capability", default=None)
)


def snapshots_match(
    actual: PathSnapshot,
    expected: PathSnapshot,
    *,
    after_rename: bool = False,
) -> bool:
    if actual.exists != expected.exists:
        return False
    if not actual.exists:
        return True
    if (
        actual.mode is None
        or expected.mode is None
        or stat.S_IFMT(actual.mode) != stat.S_IFMT(expected.mode)
    ):
        return False
    fields = ("device", "inode", "size", "modified_ns")
    if os.name == "posix":
        fields += ("mode",)
    if not after_rename:
        fields += ("changed_ns",)
    return all(getattr(actual, field) == getattr(expected, field) for field in fields)


def _verify_closed_write_snapshot(
    actual: PathSnapshot,
    opened: PathSnapshot,
    expected_size: int,
    message: str,
) -> PathSnapshot:
    """Verify a just-closed private file without trusting Windows close-time stamps.

    Windows may finalize timestamps only when the last writable HANDLE closes, so
    an fstat snapshot taken before close is not byte-for-byte comparable with a
    subsequent path stat. The stable file identity, type, and exact byte count
    remain the security boundary there. POSIX keeps the stronger exact snapshot
    comparison because its rename-while-open semantics make that check useful.
    """
    if not _same_file_identity(actual, opened) or actual.size != expected_size:
        raise ExportError(message)
    if os.name == "posix" and not snapshots_match(actual, opened):
        raise ExportError(message)
    return actual


def _casefold_parts(path: Path) -> Tuple[str, ...]:
    resolved = path.resolve(strict=False)
    return tuple(part.casefold() for part in resolved.parts)


def paths_alias(first: Path, second: Path) -> bool:
    """Reject lexical, case-only, symlink, and hard-link aliases."""
    first_resolved = first.resolve(strict=False)
    second_resolved = second.resolve(strict=False)
    if first_resolved == second_resolved:
        return True
    if _casefold_parts(first_resolved) == _casefold_parts(second_resolved):
        return True
    if path_exists(first_resolved) and path_exists(second_resolved):
        try:
            return os.path.samefile(first_resolved, second_resolved)
        except OSError:
            return False
    return False


def is_same_or_ancestor_alias(candidate: Path, target: Path) -> bool:
    """Alias-aware form of is_same_or_ancestor for destructive destinations."""
    candidate = candidate.resolve(strict=False)
    target = target.resolve(strict=False)
    if is_same_or_ancestor(candidate, target):
        return True
    candidate_parts = _casefold_parts(candidate)
    target_parts = _casefold_parts(target)
    if target_parts[: len(candidate_parts)] == candidate_parts:
        return True
    if path_exists(candidate):
        for ancestor in (target, *target.parents):
            if not path_exists(ancestor):
                continue
            try:
                if os.path.samefile(candidate, ancestor):
                    return True
            except OSError:
                continue
    return False


def snapshot_aliases_path(snapshot: PathSnapshot, path: Path) -> bool:
    if not snapshot.exists or not path_exists(path):
        return False
    try:
        info = path.stat()
    except OSError:
        return False
    return snapshot.device == info.st_dev and snapshot.inode == info.st_ino


def validate_pptx_destination(
    manifest: Path,
    payload: Dict[str, Any],
    requested_output: Path,
    keep_download: bool,
    force: bool,
    *,
    directory_capability: Optional[AnchoredDirectory] = None,
    additional_protected_inputs: Sequence[Path] = (),
) -> Tuple[Path, Optional[Path], PathSnapshot, Optional[PathSnapshot]]:
    requested = safe_expanduser_path(requested_output, description="PPTX output")
    if requested.is_symlink():
        raise ExportError(f"output must not be a symbolic link: {requested}")
    output = safe_resolve_path(requested, description="PPTX output")
    if (
        directory_capability is not None
        and output.parent != directory_capability.root
    ):
        raise ExportError("PPTX output does not belong to its directory capability")

    def destination_snapshot(path: Path) -> PathSnapshot:
        if directory_capability is None:
            return capture_path_snapshot(path)
        return directory_capability.capture(path.name)

    def destination_exists(path: Path) -> bool:
        return destination_snapshot(path).exists

    if output.suffix.lower() != ".pptx":
        raise ExportError(f"output must use the .pptx extension: {output}")
    initial_output = destination_snapshot(output)
    if (
        initial_output.exists
        and initial_output.mode is not None
        and stat.S_ISDIR(initial_output.mode)
    ):
        raise ExportError(f"PPTX output must be a file, not a directory: {output}")
    previous_backup = output.with_name(f".{output.name}.backup")
    if force and destination_exists(previous_backup):
        raise ExportError(
            "a previous output backup is still retained; inspect and move or "
            f"remove it before another --force export: {previous_backup}"
        )

    manifest = safe_resolve_path(manifest, description="PPTD manifest")
    project_root = manifest.parent
    if is_same_or_ancestor_alias(output, project_root):
        raise ExportError(f"PPTX output must not contain the source project: {output}")

    protected_inputs = {manifest}
    protected_inputs.update(
        safe_resolve_path(path, description="protected export input")
        for path in additional_protected_inputs
    )
    pages = payload.get("pages")
    if not isinstance(pages, list):
        raise ExportError("invalid export payload: pages must be an array")
    for page in pages:
        if not isinstance(page, dict):
            raise ExportError("invalid export payload: page must be an object")
        protected_inputs.add(safe_project_path(project_root, page.get("path")))
    image_map = payload.get("imageMap", {})
    if not isinstance(image_map, dict):
        raise ExportError("invalid export payload: imageMap must be an object")
    for image_path in image_map:
        protected_inputs.add(safe_project_path(project_root, image_path))
    if any(paths_alias(output, path) for path in protected_inputs):
        raise ExportError(f"PPTX output would overwrite a project input: {output}")
    if initial_output.exists and not force:
        raise ExportError(f"output already exists (pass --force to replace it): {output}")

    debug_copy = (
        output.with_name(f"{output.stem}.browser-raw.pptx")
        if keep_download
        else None
    )
    if debug_copy is not None:
        debug_backup = debug_copy.with_name(f".{debug_copy.name}.backup")
        if force and destination_exists(debug_backup):
            raise ExportError(
                "a previous raw-debug backup is still retained; inspect and move "
                f"or remove it before another --force export: {debug_backup}"
            )
        if any(paths_alias(debug_copy, path) for path in protected_inputs):
            raise ExportError(
                f"raw debug output would overwrite a project input: {debug_copy}"
            )
        if paths_alias(debug_copy, output):
            raise ExportError("raw debug output aliases the official PPTX output")
        initial_debug = destination_snapshot(debug_copy)
        if (
            initial_debug.exists
            and initial_debug.mode is not None
            and stat.S_ISDIR(initial_debug.mode)
        ):
            raise ExportError(
                f"raw debug output must be a file, not a directory: {debug_copy}"
            )
        if initial_debug.exists and not force:
            raise ExportError(
                f"raw debug output already exists (pass --force): {debug_copy}"
            )
    output_snapshot = destination_snapshot(output)
    debug_snapshot = (
        destination_snapshot(debug_copy) if debug_copy is not None else None
    )
    for label, destination, snapshot in (
        ("output", output, output_snapshot),
        ("raw debug output", debug_copy, debug_snapshot),
    ):
        if destination is None or snapshot is None or not snapshot.exists:
            continue
        if snapshot.mode is None or not stat.S_ISREG(snapshot.mode):
            raise ExportError(f"{label} must be a regular file: {destination}")
        if any(snapshot_aliases_path(snapshot, path) for path in protected_inputs):
            raise ExportError(f"{label} is a hard-link alias of a project input: {destination}")
    return output, debug_copy, output_snapshot, debug_snapshot


def sibling_staging_file(destination: Path) -> Path:
    return destination.with_name(
        f".{destination.name}.{uuid.uuid4().hex}.staging"
    )


def create_private_staging_file(path: Path) -> PathSnapshot:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
        0o600,
    )
    os.close(descriptor)
    return capture_path_snapshot(path)


def create_capability_staging_file(
    capability: AnchoredDirectory,
    relative: os.PathLike[str] | str,
) -> PathSnapshot:
    descriptor = capability.open_file(
        relative,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        snapshot = _snapshot_from_stat(os.fstat(descriptor))
        if not stat.S_ISREG(snapshot.mode or 0):
            raise ExportError("private capability staging output is not a regular file")
        return snapshot
    finally:
        os.close(descriptor)


def copy_file_into_capability(
    source: Path,
    capability: AnchoredDirectory,
    destination_relative: os.PathLike[str] | str,
    destination_identity: PathSnapshot,
    *,
    maximum_bytes: int,
) -> PathSnapshot:
    """Copy a verified regular file into a pre-created capability child."""
    source_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        source_flags |= os.O_NOFOLLOW
    source_descriptor = os.open(source, source_flags)
    destination_descriptor = -1
    try:
        source_before = os.fstat(source_descriptor)
        if not stat.S_ISREG(source_before.st_mode):
            raise ExportError(f"staging import source is not a regular file: {source}")
        if source_before.st_size > maximum_bytes:
            raise ExportError("staging import exceeds its size limit")
        destination_descriptor = capability.open_file(
            destination_relative,
            os.O_RDWR,
        )
        destination_before = _snapshot_from_stat(os.fstat(destination_descriptor))
        if not snapshots_match(destination_before, destination_identity):
            raise ExportError("private capability staging file changed before import")
        os.ftruncate(destination_descriptor, 0)
        copied = 0
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            copied += len(chunk)
            if copied > maximum_bytes:
                raise ExportError("staging import grew beyond its size limit")
            view = memoryview(chunk)
            while view:
                count = os.write(destination_descriptor, view)
                if count <= 0:
                    raise ExportError("staging import made no progress")
                view = view[count:]
        os.fsync(destination_descriptor)
        source_after = os.fstat(source_descriptor)
        if (
            source_after.st_dev != source_before.st_dev
            or source_after.st_ino != source_before.st_ino
            or source_after.st_size != source_before.st_size
            or source_after.st_mtime_ns != source_before.st_mtime_ns
            or source_after.st_ctime_ns != source_before.st_ctime_ns
            or copied != source_before.st_size
        ):
            raise ExportError("staging import source changed during copy")
        imported = _snapshot_from_stat(os.fstat(destination_descriptor))
    finally:
        os.close(source_descriptor)
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
    return _verify_closed_write_snapshot(
        capability.capture(destination_relative),
        imported,
        copied,
        "private capability staging path changed after import",
    )


def remove_capability_staging_file(
    capability: AnchoredDirectory,
    relative: os.PathLike[str] | str,
    expected: Optional[PathSnapshot],
) -> None:
    current = capability.capture(relative)
    if not current.exists:
        return
    if expected is None or not _same_file_identity(current, expected):
        log(
            "warning: refusing to remove changed capability staging output "
            f"{capability.display_path(relative)}"
        )
        return
    try:
        capability.unlink(relative)
    except OSError as exc:
        log(
            "warning: could not remove capability staging output "
            f"{capability.display_path(relative)}: {exc}"
        )


def _same_file_identity(actual: PathSnapshot, expected: PathSnapshot) -> bool:
    return (
        actual.exists
        and expected.exists
        and actual.device == expected.device
        and actual.inode == expected.inode
        and actual.mode is not None
        and expected.mode is not None
        and stat.S_IFMT(actual.mode) == stat.S_IFMT(expected.mode)
    )


def _open_verified_private_file(
    path: Path,
    expected: PathSnapshot,
    *,
    writable: bool,
    require_exact_snapshot: bool = False,
) -> int:
    flags = (os.O_RDWR if writable else os.O_RDONLY) | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        opened = PathSnapshot(
            True,
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )
        if (
            not _same_file_identity(opened, expected)
            or (require_exact_snapshot and not snapshots_match(opened, expected))
            or not stat.S_ISREG(info.st_mode)
        ):
            raise ExportError(f"private staging file changed: {path}")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def copy_to_private_staging_file(
    source: Path,
    destination: Path,
    destination_identity: PathSnapshot,
    *,
    source_identity: Optional[PathSnapshot] = None,
) -> int:
    source_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        source_flags |= os.O_NOFOLLOW
    source_descriptor = os.open(source, source_flags)
    destination_descriptor = -1
    try:
        source_info = os.fstat(source_descriptor)
        if not stat.S_ISREG(source_info.st_mode):
            raise ExportError(f"PPTX copy source is not a regular file: {source}")
        if source_info.st_size <= 0 or source_info.st_size > MAX_PPTX_ARCHIVE_BYTES:
            raise ExportError("PPTX copy source exceeds the compressed-size safety limit")
        opened_source = PathSnapshot(
            True,
            source_info.st_dev,
            source_info.st_ino,
            source_info.st_mode,
            source_info.st_size,
            source_info.st_mtime_ns,
            source_info.st_ctime_ns,
        )
        if source_identity is not None and not snapshots_match(
            opened_source, source_identity
        ):
            raise ExportError("private PPTX copy source changed")
        destination_descriptor = _open_verified_private_file(
            destination,
            destination_identity,
            writable=True,
            require_exact_snapshot=True,
        )
        os.ftruncate(destination_descriptor, 0)
        copied = 0
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            copied += len(chunk)
            if copied > MAX_PPTX_ARCHIVE_BYTES:
                raise ExportError("PPTX copy exceeded the compressed-size safety limit")
            view = memoryview(chunk)
            while view:
                count = os.write(destination_descriptor, view)
                if count <= 0:
                    raise ExportError("PPTX staging copy made no progress")
                view = view[count:]
        if copied != source_info.st_size:
            raise ExportError("PPTX copy source changed size during capture")
        os.fsync(destination_descriptor)
        destination_info = os.fstat(destination_descriptor)
        if destination_info.st_size != copied:
            raise ExportError("PPTX staging copy was incomplete")
        copied_snapshot = PathSnapshot(
            True,
            destination_info.st_dev,
            destination_info.st_ino,
            destination_info.st_mode,
            destination_info.st_size,
            destination_info.st_mtime_ns,
            destination_info.st_ctime_ns,
        )
        source_after_info = os.fstat(source_descriptor)
        source_after = PathSnapshot(
            True,
            source_after_info.st_dev,
            source_after_info.st_ino,
            source_after_info.st_mode,
            source_after_info.st_size,
            source_after_info.st_mtime_ns,
            source_after_info.st_ctime_ns,
        )
        if not snapshots_match(source_after, opened_source):
            raise ExportError("PPTX copy source changed during capture")
    finally:
        os.close(source_descriptor)
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
    _verify_closed_write_snapshot(
        capture_path_snapshot(destination),
        copied_snapshot,
        copied,
        "private PPTX staging path changed after copy",
    )
    if not snapshots_match(capture_path_snapshot(source), source_after):
        raise ExportError("private PPTX copy source path changed")
    return copied


def reset_private_staging_file(path: Path, expected: PathSnapshot) -> PathSnapshot:
    descriptor = _open_verified_private_file(
        path,
        expected,
        writable=True,
        require_exact_snapshot=True,
    )
    try:
        os.ftruncate(descriptor, 0)
        os.fsync(descriptor)
        info = os.fstat(descriptor)
        reset_snapshot = PathSnapshot(
            True,
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )
    finally:
        os.close(descriptor)
    return _verify_closed_write_snapshot(
        capture_path_snapshot(path),
        reset_snapshot,
        0,
        "private PPTX staging path changed during reset",
    )


def _remove_private_staging_file(
    staged: Path,
    expected: Optional[PathSnapshot],
) -> None:
    current = capture_path_snapshot(staged)
    if not current.exists:
        return
    if expected is None or not _same_file_identity(current, expected):
        log(f"warning: refusing to remove changed staged output {staged.absolute()}")
        return
    try:
        staged.unlink()
    except OSError as exc:
        log(f"warning: could not remove staged output {staged}: {exc}")


def _raise_noreplace_rename_error(destination: Path) -> None:
    error_number = ctypes.get_errno()
    if error_number in {
        errno.ENOSYS,
        errno.EINVAL,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }:
        raise ExportError(
            "this platform or filesystem does not support atomic no-replace "
            "publication"
        )
    raise OSError(error_number, os.strerror(error_number), os.fspath(destination))


def _renameat_noreplace(
    source_dir_fd: int,
    source_name: str,
    destination_dir_fd: int,
    destination_name: str,
    destination_display: Path,
) -> None:
    """Descriptor-relative atomic no-replace rename for POSIX capabilities."""
    source_bytes = os.fsencode(source_name)
    destination_bytes = os.fsencode(destination_name)
    libc = ctypes.CDLL(None, use_errno=True)
    ctypes.set_errno(0)
    if sys.platform == "darwin":
        renameatx_np = getattr(libc, "renameatx_np", None)
        if renameatx_np is None:
            raise ExportError(
                "this macOS runtime lacks descriptor-relative atomic publication"
            )
        renameatx_np.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameatx_np.restype = ctypes.c_int
        result = renameatx_np(
            source_dir_fd,
            source_bytes,
            destination_dir_fd,
            destination_bytes,
            0x00000004,
        )
        if result != 0:
            _raise_noreplace_rename_error(destination_display)
        return
    if sys.platform.startswith("linux"):
        renameat2 = getattr(libc, "renameat2", None)
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
                source_dir_fd,
                source_bytes,
                destination_dir_fd,
                destination_bytes,
                0x00000001,
            )
        else:
            machine = os.uname().machine.lower()
            syscall_number = {
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
            }.get(machine)
            if syscall_number is None:
                raise ExportError(
                    "this Linux architecture lacks descriptor-relative atomic "
                    "publication"
                )
            syscall = libc.syscall
            syscall.restype = ctypes.c_long
            result = syscall(
                ctypes.c_long(syscall_number),
                ctypes.c_int(source_dir_fd),
                ctypes.c_char_p(source_bytes),
                ctypes.c_int(destination_dir_fd),
                ctypes.c_char_p(destination_bytes),
                ctypes.c_uint(0x00000001),
            )
        if result != 0:
            _raise_noreplace_rename_error(destination_display)
        return
    raise ExportError(
        "this POSIX platform lacks descriptor-relative atomic publication"
    )


def rename_path_noreplace(source: Path, destination: Path) -> None:
    """Atomically rename a sibling path only when destination is absent."""
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        renamex_np = libc.renamex_np
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        ctypes.set_errno(0)
        if renamex_np(source_bytes, destination_bytes, 0x00000004) != 0:
            _raise_noreplace_rename_error(destination)
        return
    if sys.platform.startswith("linux"):
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
            syscall_number = {
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
            }.get(machine)
            if syscall_number is None:
                raise ExportError(
                    "this Linux architecture lacks a configured atomic "
                    "no-replace publication primitive"
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
            _raise_noreplace_rename_error(destination)
        return
    if os.name == "nt":
        source_parent = safe_resolve_path(
            source.parent,
            description="Windows publication parent",
        )
        destination_parent = safe_resolve_path(
            destination.parent,
            description="Windows publication parent",
        )
        if not _win32_paths_equal(source_parent, destination_parent):
            raise ExportError(
                "Windows handle-relative publication requires sibling paths"
            )
        with AnchoredDirectory.open(source_parent) as capability:
            capability.rename_noreplace(source.name, destination.name)
        return
    raise ExportError("this platform lacks atomic no-replace publication")


def publish_staged_file_noreplace(
    staged: Path,
    destination: Path,
    *,
    directory_capability: Optional[AnchoredDirectory] = None,
) -> None:
    """Atomically publish one regular file without touching an existing name."""
    staged_snapshot = (
        directory_capability.capture(staged.name)
        if directory_capability is not None
        else capture_path_snapshot(staged)
    )
    if (
        not staged_snapshot.exists
        or staged_snapshot.mode is None
        or not stat.S_ISREG(staged_snapshot.mode)
    ):
        raise ExportError(f"staged output is missing: {staged}")
    if directory_capability is not None:
        directory_capability.rename_noreplace(staged.name, destination.name)
    else:
        rename_path_noreplace(staged, destination)


def _restore_file_backup_noreplace(
    backup: Path,
    destination: Path,
    *,
    directory_capability: Optional[AnchoredDirectory] = None,
) -> None:
    try:
        if directory_capability is not None:
            directory_capability.rename_noreplace(backup.name, destination.name)
        else:
            rename_path_noreplace(backup, destination)
    except Exception as exc:
        retained = backup.resolve(strict=False)
        raise ExportError(
            "destination was occupied during safe recovery; no concurrent path "
            f"was overwritten and the displaced file was retained at {retained}"
        ) from exc


def _publish_staged_file_force(
    staged: Path,
    destination: Path,
    expected: PathSnapshot,
    *,
    directory_capability: Optional[AnchoredDirectory] = None,
) -> Optional[Path]:
    """Replace exactly the pre-export target without deleting a late writer."""
    snapshot = (
        (lambda path: directory_capability.capture(path.name))
        if directory_capability is not None
        else capture_path_snapshot
    )
    current = snapshot(destination)
    if not snapshots_match(current, expected):
        raise ExportError(f"output changed during export; refusing to replace it: {destination}")
    if not expected.exists:
        publish_staged_file_noreplace(
            staged,
            destination,
            directory_capability=directory_capability,
        )
        return None
    if expected.mode is None or not stat.S_ISREG(expected.mode):
        raise ExportError(f"expected output is not a regular file: {destination}")

    staged_snapshot = snapshot(staged)
    if (
        not staged_snapshot.exists
        or staged_snapshot.mode is None
        or not stat.S_ISREG(staged_snapshot.mode)
    ):
        raise ExportError(f"staged output is missing: {staged}")
    # A deterministic single backup prevents unbounded accumulation. A later
    # --force run fails closed until the user moves or removes this recovery
    # artifact explicitly.
    backup = destination.with_name(f".{destination.name}.backup")
    if directory_capability is not None:
        directory_capability.rename_noreplace(destination.name, backup.name)
    else:
        rename_path_noreplace(destination, backup)
    moved_snapshot = snapshot(backup)
    if not snapshots_match(moved_snapshot, expected, after_rename=True):
        _restore_file_backup_noreplace(
            backup,
            destination,
            directory_capability=directory_capability,
        )
        raise ExportError(
            f"output changed while publication began and was safely restored: {destination}"
        )

    try:
        publish_staged_file_noreplace(
            staged,
            destination,
            directory_capability=directory_capability,
        )
    except Exception as exc:
        _restore_file_backup_noreplace(
            backup,
            destination,
            directory_capability=directory_capability,
        )
        raise exc

    published_snapshot = snapshot(destination)
    if not snapshots_match(published_snapshot, staged_snapshot, after_rename=True):
        retained = backup.resolve(strict=False)
        raise ExportError(
            "published output was changed concurrently; it was not overwritten or "
            f"deleted, and the previous output was retained at {retained}"
        )
    if not snapshots_match(snapshot(backup), moved_snapshot):
        retained = backup.resolve(strict=False)
        raise ExportError(
            "the previous output changed during publication; the changed file was "
            f"retained at {retained}"
        )
    # Another process may retain this displaced inode and write after any
    # snapshot check. A check-then-unlink cannot be made atomic, so preserving
    # the backup is the only way to avoid deleting that late writer's result.
    retained = backup.resolve(strict=False)
    log(f"previous output retained at {retained}")
    return retained


def publish_optional_debug_copy(
    staged: Path,
    requested_destination: Path,
    *,
    replace_existing: bool,
    expected_snapshot: Optional[PathSnapshot] = None,
    expected_staged_snapshot: Optional[PathSnapshot] = None,
    publication_details: Optional[Dict[str, Path]] = None,
    directory_capability: Optional[AnchoredDirectory] = None,
) -> Path:
    """Publish a diagnostic copy without jeopardizing the official output."""
    if expected_staged_snapshot is not None and not snapshots_match(
        (
            directory_capability.capture(staged.name)
            if directory_capability is not None
            else capture_path_snapshot(staged)
        ),
        expected_staged_snapshot,
    ):
        raise ExportError("private staged debug output changed before publication")
    if replace_existing:
        retained = commit_staged_files(
            [(staged, requested_destination)],
            replace_existing=True,
            expected_snapshots=(expected_snapshot,),
            expected_staged_snapshots=(
                (expected_staged_snapshot,)
                if expected_staged_snapshot is not None
                else None
            ),
            directory_capability=directory_capability,
        )
        if retained is not None:
            log(f"previous raw debug output retained at {retained}")
            if publication_details is not None:
                publication_details["previousBrowserRawBackup"] = retained
        return requested_destination

    destination = requested_destination
    for _ in range(32):
        try:
            publish_staged_file_noreplace(
                staged,
                destination,
                directory_capability=directory_capability,
            )
            if destination != requested_destination:
                log(
                    "warning: raw debug output appeared concurrently; "
                    f"saved this export as {destination}"
                )
            return destination
        except FileExistsError:
            destination = requested_destination.with_name(
                f"{requested_destination.stem}.late-{uuid.uuid4().hex}"
                f"{requested_destination.suffix}"
            )
    raise ExportError(
        "could not allocate a unique path for the optional raw debug output"
    )


def commit_staged_files(
    pairs: Sequence[Tuple[Path, Path]],
    *,
    replace_existing: bool,
    expected_snapshots: Optional[Sequence[Optional[PathSnapshot]]] = None,
    expected_staged_snapshots: Optional[Sequence[Optional[PathSnapshot]]] = None,
    directory_capability: Optional[AnchoredDirectory] = None,
) -> Optional[Path]:
    """Publish staged siblings without overwriting unexpected concurrent files.

    Force mode uses backups and rollback. No-force mode intentionally accepts
    one official output only: ordinary filesystems have no atomic multi-name
    commit, and rollback via check-then-unlink can delete a concurrent writer.
    """
    if len(pairs) != 1:
        raise ExportError(
            "publication accepts exactly one output; diagnostic copies must be "
            "published as independent transactions"
        )
    staged, destination = pairs[0]
    staged_snapshot = (
        directory_capability.capture(staged.name)
        if directory_capability is not None
        else capture_path_snapshot(staged)
    )
    if (
        not staged_snapshot.exists
        or staged_snapshot.mode is None
        or not stat.S_ISREG(staged_snapshot.mode)
    ):
        raise ExportError(f"staged output is missing: {staged}")
    if expected_staged_snapshots is not None:
        if len(expected_staged_snapshots) != 1:
            raise ExportError("publication requires one staged-file snapshot")
        expected_staged = expected_staged_snapshots[0]
        if expected_staged is None or not snapshots_match(
            staged_snapshot, expected_staged
        ):
            raise ExportError("private staged output changed before publication")
    destination_snapshot = (
        directory_capability.capture(destination.name)
        if directory_capability is not None
        else capture_path_snapshot(destination)
    )
    if (
        destination_snapshot.exists
        and destination_snapshot.mode is not None
        and stat.S_ISDIR(destination_snapshot.mode)
    ):
        raise ExportError(f"output destination is a directory: {destination}")

    if not replace_existing:
        publish_staged_file_noreplace(
            staged,
            destination,
            directory_capability=directory_capability,
        )
        return None
    if expected_snapshots is None or len(expected_snapshots) != 1:
        raise ExportError("force publication requires one pre-export target snapshot")
    expected = expected_snapshots[0]
    if expected is None:
        raise ExportError("force publication is missing its target snapshot")
    return _publish_staged_file_force(
        staged,
        destination,
        expected,
        directory_capability=directory_capability,
    )


def publish_export_outputs(
    staged_output: Path,
    output: Path,
    staged_debug: Optional[Path],
    debug_copy: Optional[Path],
    *,
    replace_existing: bool,
    expected_output: Optional[PathSnapshot] = None,
    expected_debug: Optional[PathSnapshot] = None,
    expected_staged_output: Optional[PathSnapshot] = None,
    expected_staged_debug: Optional[PathSnapshot] = None,
    publication_details: Optional[Dict[str, Path]] = None,
    directory_capability: Optional[AnchoredDirectory] = None,
) -> Optional[Path]:
    """Publish the official artifact, then best-effort diagnostic output."""
    previous_backup = commit_staged_files(
        [(staged_output, output)],
        replace_existing=replace_existing,
        expected_snapshots=(expected_output,),
        expected_staged_snapshots=(
            (expected_staged_output,) if expected_staged_output is not None else None
        ),
        directory_capability=directory_capability,
    )
    if previous_backup is not None and publication_details is not None:
        publication_details["previousOutputBackup"] = previous_backup
    if staged_debug is None or debug_copy is None:
        return None
    try:
        return publish_optional_debug_copy(
            staged_debug,
            debug_copy,
            replace_existing=replace_existing,
            expected_snapshot=expected_debug,
            expected_staged_snapshot=expected_staged_debug,
            publication_details=publication_details,
            directory_capability=directory_capability,
        )
    except Exception as exc:
        log(f"warning: could not publish optional raw debug output: {exc}")
        return None


def export_pptx(
    source: Path,
    output: Path,
    transition: str,
    embed_fonts: bool,
    keep_download: bool = False,
    force: bool = False,
    quality_report: Optional[Path] = None,
) -> Dict[str, Any]:
    manifest = find_manifest(source)
    if quality_report is None:
        raise ExportError(
            "quality_report is required; generate a current report with "
            "pptd_quality.py --fail-on warning"
        )
    quality_receipt = verify_quality_gate(manifest, quality_report)
    quality_bound_inputs = quality_gate_bound_inputs(manifest, quality_report)
    payload = build_payload(manifest)
    # Bind the captured browser payload to the same source snapshot that passed
    # the gate.  A project edit between the first gate and payload capture must
    # not authorize an artifact from different bytes.
    confirmed_receipt = verify_quality_gate(manifest, quality_report)
    if confirmed_receipt != quality_receipt:
        raise ExportError("PPTD quality receipt changed during payload capture")
    # Perform the non-mutating policy validation first. The parent identity is
    # then pinned before any staging or publication operation is allowed.
    output, debug_copy, output_snapshot, debug_snapshot = validate_pptx_destination(
        manifest,
        payload,
        output,
        keep_download,
        force,
        additional_protected_inputs=(*quality_bound_inputs, quality_report),
    )
    prevalidated_parent = capture_path_snapshot(output.parent)
    agent_browser = ensure_agent_browser()
    ensure_websocket()
    cdp_port = ensure_debug_chrome()
    published_debug: Optional[Path] = None
    actual_embed_fonts: Optional[bool] = embed_fonts
    font_embedding_fallback = False
    cdp_blob_capture_used = False
    publication_details: Dict[str, Path] = {}

    log(f"manifest: {manifest}")
    log(
        f"defaults: transition={transition}, embed_fonts={'on' if embed_fonts else 'off'}"
    )

    with AnchoredDirectory.open(output.parent, create=True) as output_capability:
        if prevalidated_parent.exists and not _same_file_identity(
            output_capability.identity,
            prevalidated_parent,
        ):
            raise ExportError(
                f"output parent changed after validation: {output.parent}"
            )
        output, debug_copy, output_snapshot, debug_snapshot = validate_pptx_destination(
            manifest,
            payload,
            output,
            keep_download,
            force,
            directory_capability=output_capability,
            additional_protected_inputs=(*quality_bound_inputs, quality_report),
        )
        capability_staged_output = sibling_staging_file(output)
        capability_staged_debug = (
            sibling_staging_file(debug_copy) if debug_copy is not None else None
        )
        capability_output_identity: Optional[PathSnapshot] = None
        capability_debug_identity: Optional[PathSnapshot] = None

        with temporary_directory(prefix="open-kimi-ppt-export-") as temp_name:
            temp_dir = Path(temp_name)
            staged_output = temp_dir / "writer-output.pptx"
            staged_debug = (
                temp_dir / "writer-browser-raw.pptx"
                if debug_copy is not None
                else None
            )
            staged_output_identity: Optional[PathSnapshot] = (
                create_private_staging_file(staged_output)
            )
            staged_debug_identity: Optional[PathSnapshot] = None
            if staged_debug is not None:
                staged_debug_identity = create_private_staging_file(staged_debug)
            prepare_host_assets(temp_dir, payload)
            server, thread, url = serve(temp_dir)
            try:
                first_download_dir = temp_dir / "downloads-1"
                first_download_dir.mkdir()
                try:
                    attempt_result = run_pptx_browser_attempt(
                        agent_browser,
                        url,
                        temp_dir,
                        first_download_dir,
                        cdp_port,
                        staged_output,
                        staged_output_identity,
                        embed_fonts=embed_fonts,
                        blob_timeout=(
                            PPTX_FONT_ATTEMPT_SECONDS
                            if embed_fonts
                            else PPTX_BLOB_WAIT_SECONDS
                        ),
                    )
                    actual_embed_fonts = attempt_result.effective_fonts
                    cdp_blob_capture_used = attempt_result.cdp_blob_capture_used
                except PptxWriterTimeout as exc:
                    if not embed_fonts:
                        raise
                    font_embedding_fallback = True
                    actual_embed_fonts = False
                    log(
                        "warning: the font-embedding attempt did not yield a PPTX; "
                        "starting a clean browser session and retrying with font "
                        f"embedding disabled. Reason: {exc}"
                    )
                    failed_attempt_snapshot = capture_path_snapshot(staged_output)
                    if (
                        staged_output_identity is None
                        or not _same_file_identity(
                            failed_attempt_snapshot,
                            staged_output_identity,
                        )
                    ):
                        raise ExportError(
                            "private PPTX staging path changed during the failed attempt"
                        )
                    staged_output_identity = reset_private_staging_file(
                        staged_output,
                        failed_attempt_snapshot,
                    )
                    retry_download_dir = temp_dir / "downloads-2"
                    retry_download_dir.mkdir()
                    attempt_result = run_pptx_browser_attempt(
                        agent_browser,
                        url,
                        temp_dir,
                        retry_download_dir,
                        cdp_port,
                        staged_output,
                        staged_output_identity,
                        embed_fonts=False,
                        blob_timeout=PPTX_BLOB_WAIT_SECONDS,
                    )
                    actual_embed_fonts = attempt_result.effective_fonts
                    cdp_blob_capture_used = attempt_result.cdp_blob_capture_used
                completed_output_snapshot = capture_path_snapshot(staged_output)
                if not _same_file_identity(
                    completed_output_snapshot,
                    staged_output_identity,
                ):
                    raise ExportError("private PPTX staging path changed after export")
                staged_output_identity = completed_output_snapshot
                if staged_debug is not None:
                    copy_to_private_staging_file(
                        staged_output,
                        staged_debug,
                        staged_debug_identity,
                        source_identity=staged_output_identity,
                    )
            finally:
                for name, cleanup in (
                    ("HTTP server", server.shutdown),
                    ("HTTP socket", server.server_close),
                    ("HTTP thread", lambda: thread.join(timeout=2)),
                ):
                    try:
                        cleanup()
                    except Exception as exc:
                        log(f"warning: failed to close {name}: {exc}")

            slide_count = patch_transitions(
                staged_output,
                transition,
                expected_identity=staged_output_identity,
            )
            staged_output_identity = capture_path_snapshot(staged_output)
            if staged_debug is not None:
                staged_debug_identity = capture_path_snapshot(staged_debug)
            summary = verify_output(
                staged_output,
                transition,
                actual_embed_fonts is not False,
                expected_slides=len(payload["pages"]),
                expected_identity=staged_output_identity,
            )

            final_receipt = verify_quality_gate(manifest, quality_report)
            if final_receipt != quality_receipt:
                raise ExportError("PPTD quality receipt changed before publication")

            try:
                capability_output_identity = create_capability_staging_file(
                    output_capability,
                    capability_staged_output.name,
                )
                capability_output_identity = copy_file_into_capability(
                    staged_output,
                    output_capability,
                    capability_staged_output.name,
                    capability_output_identity,
                    maximum_bytes=MAX_PPTX_ARCHIVE_BYTES,
                )
                if staged_debug is not None and capability_staged_debug is not None:
                    capability_debug_identity = create_capability_staging_file(
                        output_capability,
                        capability_staged_debug.name,
                    )
                    capability_debug_identity = copy_file_into_capability(
                        staged_debug,
                        output_capability,
                        capability_staged_debug.name,
                        capability_debug_identity,
                        maximum_bytes=MAX_PPTX_ARCHIVE_BYTES,
                    )

                # Do not publish into a directory that no longer occupies the
                # user-requested pathname. Descriptor-relative operations would
                # remain safe, but the result would otherwise be stranded under
                # a renamed directory and the reported path would be false.
                output_capability.assert_path_binding()
                published_debug = publish_export_outputs(
                    capability_staged_output,
                    output,
                    capability_staged_debug,
                    debug_copy,
                    replace_existing=force,
                    expected_output=output_snapshot,
                    expected_debug=debug_snapshot,
                    expected_staged_output=capability_output_identity,
                    expected_staged_debug=capability_debug_identity,
                    publication_details=publication_details,
                    directory_capability=output_capability,
                )
                output_capability.assert_path_binding()
            finally:
                remove_capability_staging_file(
                    output_capability,
                    capability_staged_output.name,
                    capability_output_identity,
                )
                if capability_staged_debug is not None:
                    remove_capability_staging_file(
                        output_capability,
                        capability_staged_debug.name,
                        capability_debug_identity,
                    )

    summary["transitionPatchedSlides"] = slide_count
    summary["output"] = str(output)
    summary["fontEmbeddingRequested"] = embed_fonts
    summary["fontEmbeddingAttempted"] = embed_fonts
    summary["fontEmbeddingEnabledForSuccessfulAttempt"] = actual_embed_fonts
    summary["fontEmbeddingControlObserved"] = actual_embed_fonts is not None
    summary["fontEmbeddingFallback"] = font_embedding_fallback
    summary["cdpBlobCaptureUsed"] = cdp_blob_capture_used
    summary["qualityGate"] = quality_receipt
    if published_debug is not None:
        summary["browserRawOutput"] = str(published_debug)
    if "previousOutputBackup" in publication_details:
        summary["previousOutputBackup"] = str(
            publication_details["previousOutputBackup"]
        )
    if "previousBrowserRawBackup" in publication_details:
        summary["previousBrowserRawBackup"] = str(
            publication_details["previousBrowserRawBackup"]
        )
    return summary


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export a PPTD project to PPTX using Kimi's public browser-side writer. "
            "Defaults: fade transition and embedded fonts."
        )
    )
    parser.add_argument("input", type=Path, help=".pptd manifest or project directory")
    parser.add_argument("--output", "-o", type=Path, help="output .pptx path")
    parser.add_argument(
        "--transition",
        choices=("fade", "none"),
        default="fade",
        help="slide transition written to every slide (default: fade)",
    )
    font_group = parser.add_mutually_exclusive_group()
    font_group.add_argument(
        "--embed-fonts",
        dest="embed_fonts",
        action="store_true",
        default=True,
        help="embed fonts when available (default)",
    )
    font_group.add_argument(
        "--no-embed-fonts",
        dest="embed_fonts",
        action="store_false",
        help="disable font embedding",
    )
    parser.add_argument(
        "--keep-browser-raw",
        action="store_true",
        help="also keep the unpatched browser download beside the output",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "replace an existing output file while retaining one reported sibling "
            "backup; move/remove that backup before the next forced export"
        ),
    )
    parser.add_argument(
        "--quality-report",
        type=Path,
        required=True,
        help=(
            "require a current source-bound report from pptd_quality.py "
            "generated with --fail-on warning (or stricter)"
        ),
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        manifest = find_manifest(args.input)
        output = args.output or manifest.with_suffix(".pptx")
        summary = export_pptx(
            manifest,
            output,
            args.transition,
            args.embed_fonts,
            args.keep_browser_raw,
            args.force,
            getattr(args, "quality_report", None),
        )
    except (ExportError, OSError, subprocess.SubprocessError) as exc:
        print(f"open-kimi-ppt export failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
