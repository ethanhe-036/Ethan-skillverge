#!/usr/bin/env python3
"""Generate raster theme previews through gpt-image-2 and Codex proxy config."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import struct
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit
import zlib

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None


DEFAULT_MODEL = "gpt-image-2"
DEFAULT_SIZE = "1536x1024"
DEFAULT_QUALITY = "medium"


class NotReadyError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def fail(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))


def normalize_base_url(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    raw_value = value.strip()
    try:
        parsed = urlsplit(raw_value)
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise NotReadyError("invalid-base-url") from exc
    if parsed.scheme.lower() not in ("http", "https") or not hostname:
        raise NotReadyError("invalid-base-url")
    if any(character.isspace() for character in parsed.netloc) or "\\" in parsed.netloc:
        raise NotReadyError("invalid-base-url")
    normalized_path = parsed.path.rstrip("/")
    if not normalized_path:
        normalized_path = "/v1"
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, normalized_path, parsed.query, parsed.fragment)
    )


def read_api_key(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise NotReadyError("invalid-auth-json") from exc
    if not isinstance(data, dict):
        raise NotReadyError("invalid-auth-json")
    for field in ("OPENAI_API_KEY", "openai_api_key"):
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def read_provider_config(path: Path) -> tuple[bool, str | None]:
    if not path.is_file():
        return False, None
    if tomllib is None:
        raise NotReadyError("python-3.11-required")
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except Exception as exc:
        raise NotReadyError("invalid-config-toml") from exc

    provider_name = data.get("model_provider")
    if provider_name is None:
        return False, None
    if not isinstance(provider_name, str) or not provider_name.strip():
        raise NotReadyError("invalid-selected-provider")
    providers = data.get("model_providers")
    if not isinstance(providers, dict):
        raise NotReadyError("selected-provider-missing")
    selected = providers.get(provider_name)
    if not isinstance(selected, dict):
        raise NotReadyError("selected-provider-missing")
    value = selected.get("base_url")
    if value is None:
        return True, None
    if not isinstance(value, str) or not value.strip():
        raise NotReadyError("invalid-provider-base-url")
    return True, normalize_base_url(value)


def build_child_env(config_file: Path, auth_file: Path) -> tuple[dict[str, str], dict[str, str]]:
    environment = os.environ.copy()
    child_env = environment.copy()
    child_env.pop("OPENAI_API_KEY", None)
    child_env.pop("OPENAI_BASE_URL", None)
    sources: dict[str, str] = {}
    auth_api_key = read_api_key(auth_file)
    provider_selected, configured_base_url = read_provider_config(config_file)
    environment_api_key = environment.get("OPENAI_API_KEY", "").strip() or None
    environment_base_url = normalize_base_url(environment.get("OPENAI_BASE_URL"))

    if provider_selected:
        api_key = auth_api_key or environment_api_key
        base_url = configured_base_url
        if api_key:
            sources["apiKey"] = "auth.json" if auth_api_key else "environment"
        sources["baseUrl"] = "config.toml" if base_url else "selected-provider-default"
    elif environment_base_url:
        # Environment-only routing is a pair; never attach an auth-file key to an ambient endpoint.
        api_key = environment_api_key
        base_url = environment_base_url
        if api_key:
            sources["apiKey"] = "environment"
        sources["baseUrl"] = "environment"
    else:
        api_key = auth_api_key or environment_api_key
        base_url = None
        if api_key:
            sources["apiKey"] = "auth.json" if auth_api_key else "environment"

    if api_key:
        child_env["OPENAI_API_KEY"] = api_key
    if base_url:
        child_env["OPENAI_BASE_URL"] = base_url
    return child_env, sources


def build_probe_env(environment: dict[str, str]) -> dict[str, str]:
    probe_env = environment.copy()
    probe_env.pop("OPENAI_API_KEY", None)
    probe_env.pop("OPENAI_BASE_URL", None)
    return probe_env


def describe_base_url(value: str | None) -> str:
    if not value:
        return "default-openai"
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or "invalid-host"
        return f"{host}:{parsed.port}" if parsed.port else host
    except ValueError:
        return "invalid-host"


def resolve_imagegen_cli(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    return codex_home() / "skills" / ".system" / "imagegen" / "scripts" / "image_gen.py"


def python_runtime_status(path: Path, probe_env: dict[str, str]) -> str:
    if not path.is_file():
        return "missing-python-runtime"
    try:
        result = subprocess.run(
            [
                str(path),
                "-c",
                "import importlib.util,sys; "
                "raise SystemExit(3 if sys.version_info < (3,9) else "
                "(0 if importlib.util.find_spec('openai') else 4))",
            ],
            env=probe_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
            check=False,
        )
        return {
            0: "ready",
            3: "unsupported-python-runtime",
            4: "missing-openai",
        }.get(result.returncode, "unusable-python-runtime")
    except (OSError, subprocess.TimeoutExpired):
        return "unusable-python-runtime"


def resolve_imagegen_python(
    value: str | None, probe_env: dict[str, str]
) -> tuple[Path | None, str]:
    if value:
        candidate = Path(value).expanduser().resolve()
        status = python_runtime_status(candidate, probe_env)
        return (candidate, status) if status == "ready" else (None, status)
    candidates = [
        probe_env.get("CODEX_IMAGEGEN_PYTHON"),
        Path("tmp/imagegen/.venv/Scripts/python.exe"),
        Path("tmp/imagegen/.venv/bin/python"),
        Path(sys.executable),
    ]
    seen: set[str] = set()
    statuses: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser().resolve()
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        status = python_runtime_status(path, probe_env)
        if status == "ready":
            return path, status
        statuses.append(status)
    for status in ("missing-openai", "unsupported-python-runtime", "unusable-python-runtime"):
        if status in statuses:
            return None, status
    return None, "missing-python-runtime"


def probe_payload(
    child_env: dict[str, str],
    sources: dict[str, str],
    cli: Path,
    imagegen_python: Path | None,
    python_runtime: str,
) -> dict[str, object]:
    api_key = bool(child_env.get("OPENAI_API_KEY"))
    base_url = child_env.get("OPENAI_BASE_URL")
    reasons = []
    if not api_key:
        reasons.append("missing-api-key")
    if not cli.is_file():
        reasons.append("missing-imagegen-cli")
    if imagegen_python is None:
        reasons.append(python_runtime)
    return {
        "pass": not reasons,
        "status": "READY" if not reasons else "NOT_READY",
        "route": "proxy-api" if base_url else "openai-api",
        "model": DEFAULT_MODEL,
        "apiKey": "configured" if api_key else "missing",
        "baseUrlHost": describe_base_url(base_url),
        "apiKeySource": sources.get("apiKey", "missing"),
        "baseUrlSource": sources.get("baseUrl", "default"),
        "imagegenCliExists": cli.is_file(),
        "pythonRuntime": python_runtime,
        "notReadyReasons": reasons,
    }


def failed_probe_payload(reason: str) -> dict[str, object]:
    return {
        "pass": False,
        "status": "NOT_READY",
        "route": "unknown",
        "model": DEFAULT_MODEL,
        "apiKey": "unknown",
        "baseUrlHost": "unknown",
        "apiKeySource": "unknown",
        "baseUrlSource": "unknown",
        "imagegenCliExists": None,
        "pythonRuntime": "unknown",
        "notReadyReasons": [reason],
    }


def output_path(value: str | None, slug: str | None) -> Path:
    if value:
        return Path(value)
    safe_slug = re.sub(r"[^a-z0-9]+", "-", (slug or "theme-preview").lower()).strip("-")
    return Path("output") / "imagegen" / f"{safe_slug[:64] or 'theme-preview'}.png"


def build_command(
    args: argparse.Namespace, cli: Path, imagegen_python: Path, destination: Path
) -> list[str]:
    command = [
        str(imagegen_python),
        str(cli),
        "generate",
        "--model",
        args.model,
        "--size",
        args.size,
        "--quality",
        args.quality,
        "--output-format",
        "png",
        "--out",
        str(destination),
    ]
    if args.prompt_file:
        command.extend(["--prompt-file", args.prompt_file])
    else:
        command.extend(["--prompt", args.prompt])
    if args.force:
        command.append("--force")
    if args.dry_run:
        command.append("--dry-run")
    if args.no_augment:
        command.append("--no-augment")
    return command


def paeth_predictor(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left


def png_passes(width: int, height: int, interlace: int) -> list[tuple[int, int]]:
    if interlace == 0:
        return [(width, height)]
    passes = []
    for start_x, start_y, step_x, step_y in (
        (0, 0, 8, 8),
        (4, 0, 8, 8),
        (0, 4, 4, 8),
        (2, 0, 4, 4),
        (0, 2, 2, 4),
        (1, 0, 2, 2),
        (0, 1, 1, 2),
    ):
        pass_width = 0 if width <= start_x else (width - start_x + step_x - 1) // step_x
        pass_height = 0 if height <= start_y else (height - start_y + step_y - 1) // step_y
        if pass_width and pass_height:
            passes.append((pass_width, pass_height))
    return passes


def validate_png_scanlines(
    decoded: bytes,
    passes: list[tuple[int, int]],
    bits_per_pixel: int,
    color_type: int,
    bit_depth: int,
    palette_entries: int | None,
) -> None:
    offset = 0
    bytes_per_pixel = max(1, (bits_per_pixel + 7) // 8)
    for pass_width, pass_height in passes:
        row_bytes = (pass_width * bits_per_pixel + 7) // 8
        previous = bytearray(row_bytes)
        for _ in range(pass_height):
            if offset + row_bytes + 1 > len(decoded):
                raise ValueError("PNG scanline data is truncated")
            filter_type = decoded[offset]
            offset += 1
            if filter_type > 4:
                raise ValueError("PNG uses an invalid scanline filter")
            raw = decoded[offset : offset + row_bytes]
            offset += row_bytes
            reconstructed = bytearray(row_bytes)
            for index, value in enumerate(raw):
                left = reconstructed[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
                above = previous[index]
                upper_left = previous[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
                if filter_type == 0:
                    predictor = 0
                elif filter_type == 1:
                    predictor = left
                elif filter_type == 2:
                    predictor = above
                elif filter_type == 3:
                    predictor = (left + above) // 2
                else:
                    predictor = paeth_predictor(left, above, upper_left)
                reconstructed[index] = (value + predictor) & 0xFF
            if color_type == 3:
                assert palette_entries is not None
                for pixel in range(pass_width):
                    bit_offset = pixel * bit_depth
                    shift = 8 - bit_depth - (bit_offset % 8)
                    palette_index = (reconstructed[bit_offset // 8] >> shift) & ((1 << bit_depth) - 1)
                    if palette_index >= palette_entries:
                        raise ValueError("PNG references a missing palette entry")
            previous = reconstructed
    if offset != len(decoded):
        raise ValueError("PNG has unexpected decoded data")


def inspect_raster(path: Path) -> tuple[str, int, int]:
    data = path.read_bytes()
    if len(data) > 128 * 1024 * 1024:
        raise ValueError("PNG exceeds the validation size limit")
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("output is not a PNG image")

    offset = 8
    width = height = bit_depth = color_type = interlace = None
    palette_entries: int | None = None
    idat_parts: list[bytes] = []
    seen_ihdr = seen_idat = seen_iend = idat_closed = False
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("PNG chunk header is truncated")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_end = offset + 12 + length
        if chunk_end > len(data):
            raise ValueError("PNG chunk data is truncated")
        if len(chunk_type) != 4 or not all(
            65 <= value <= 90 or 97 <= value <= 122 for value in chunk_type
        ):
            raise ValueError("PNG chunk type is invalid")
        payload = data[offset + 8 : offset + 8 + length]
        stored_crc = struct.unpack(">I", data[offset + 8 + length : chunk_end])[0]
        actual_crc = zlib.crc32(payload, zlib.crc32(chunk_type)) & 0xFFFFFFFF
        if stored_crc != actual_crc:
            raise ValueError("PNG chunk CRC is invalid")
        offset = chunk_end

        if not seen_ihdr and chunk_type != b"IHDR":
            raise ValueError("PNG IHDR must be the first chunk")
        if chunk_type == b"IHDR":
            if seen_ihdr or length != 13:
                raise ValueError("PNG IHDR is invalid")
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
            valid_depths = {
                0: {1, 2, 4, 8, 16},
                2: {8, 16},
                3: {1, 2, 4, 8},
                4: {8, 16},
                6: {8, 16},
            }
            if width < 1 or height < 1 or width * height > 100_000_000:
                raise ValueError("PNG dimensions are invalid")
            if color_type not in valid_depths or bit_depth not in valid_depths[color_type]:
                raise ValueError("PNG color type or bit depth is invalid")
            if compression != 0 or filtering != 0 or interlace not in (0, 1):
                raise ValueError("PNG encoding method is unsupported")
            seen_ihdr = True
        elif chunk_type == b"PLTE":
            if seen_idat or length < 3 or length > 768 or length % 3:
                raise ValueError("PNG palette is invalid")
            palette_entries = length // 3
        elif chunk_type == b"IDAT":
            if idat_closed:
                raise ValueError("PNG IDAT chunks are not consecutive")
            seen_idat = True
            idat_parts.append(payload)
        elif chunk_type == b"IEND":
            if length != 0 or not seen_idat:
                raise ValueError("PNG IEND is invalid")
            seen_iend = True
            break
        else:
            if seen_idat:
                idat_closed = True
            if not (chunk_type[0] & 0x20):
                raise ValueError("PNG contains an unknown critical chunk")

    if not seen_ihdr or not seen_idat or not seen_iend:
        raise ValueError("PNG is missing required chunks")
    if offset != len(data):
        raise ValueError("PNG has trailing data")
    assert None not in (width, height, bit_depth, color_type, interlace)
    assert width is not None and height is not None
    assert bit_depth is not None and color_type is not None and interlace is not None
    if color_type == 3 and palette_entries is None:
        raise ValueError("indexed PNG is missing its palette")

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    bits_per_pixel = channels * bit_depth
    passes = png_passes(width, height, interlace)
    expected_size = sum(
        ((pass_width * bits_per_pixel + 7) // 8 + 1) * pass_height
        for pass_width, pass_height in passes
    )
    if expected_size > 512 * 1024 * 1024:
        raise ValueError("PNG decoded data exceeds the validation size limit")
    decoder = zlib.decompressobj()
    try:
        decoded = decoder.decompress(b"".join(idat_parts), expected_size + 1)
    except zlib.error as exc:
        raise ValueError("PNG pixel stream is invalid") from exc
    if (
        len(decoded) != expected_size
        or not decoder.eof
        or decoder.unconsumed_tail
        or decoder.unused_data
    ):
        raise ValueError("PNG pixel stream is incomplete or oversized")
    validate_png_scanlines(
        decoded, passes, bits_per_pixel, color_type, bit_depth, palette_entries
    )
    return "PNG", width, height


def staging_path(destination: Path) -> Path:
    return destination.with_name(
        f"{destination.stem}.{os.getpid()}.{secrets.token_hex(4)}.tmp{destination.suffix.lower()}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--prompt")
    source.add_argument("--prompt-file")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--out")
    parser.add_argument("--slug")
    parser.add_argument("--size", default=DEFAULT_SIZE)
    parser.add_argument("--quality", default=DEFAULT_QUALITY, choices=("low", "medium", "high", "auto"))
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=(DEFAULT_MODEL,))
    parser.add_argument("--config-file", default=str(codex_home() / "config.toml"))
    parser.add_argument("--auth-file", default=str(codex_home() / "auth.json"))
    parser.add_argument("--imagegen-cli")
    parser.add_argument("--imagegen-python")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-augment", action="store_true")
    args = parser.parse_args()

    if not args.probe and not (args.prompt or args.prompt_file):
        parser.error("use --prompt, --prompt-file, or --probe")
    cli = resolve_imagegen_cli(args.imagegen_cli)
    try:
        child_env, sources = build_child_env(
            Path(args.config_file).expanduser(), Path(args.auth_file).expanduser()
        )
    except NotReadyError as exc:
        if args.probe:
            print(json.dumps(failed_probe_payload(exc.reason), ensure_ascii=True))
            return 2
        fail(f"NOT_READY: {exc.reason}", 2)
    imagegen_python, python_runtime = resolve_imagegen_python(
        args.imagegen_python, build_probe_env(child_env)
    )
    probe = probe_payload(child_env, sources, cli, imagegen_python, python_runtime)
    if args.probe:
        print(json.dumps(probe, ensure_ascii=True))
        return 0 if probe["pass"] else 2
    if not probe["pass"]:
        reasons = ", ".join(str(reason) for reason in probe["notReadyReasons"])
        fail(f"NOT_READY: {reasons}", 2)

    destination = output_path(args.out, args.slug)
    if destination.suffix.lower() != ".png":
        fail("Theme previews generated through this wrapper must use a .png output path.")
    if destination.exists() and not args.force:
        fail(f"Output already exists; use --force only for an intentional replacement: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    assert imagegen_python is not None
    staged = destination if args.dry_run else staging_path(destination)
    try:
        result = subprocess.run(
            build_command(args, cli, imagegen_python, staged), env=child_env, check=False
        )
        if result.returncode != 0:
            return result.returncode
        if args.dry_run:
            print(f"IMAGE2_DRY_RUN_OUTPUT={destination.resolve()}")
            return 0
        if not staged.is_file():
            fail("Image generation returned success but produced no file.")
        try:
            image_format, width, height = inspect_raster(staged)
        except ValueError as exc:
            fail(f"Generated preview failed raster validation: {exc}")
        os.replace(staged, destination)
        print(f"IMAGE2_OUTPUT={destination.resolve()}")
        print(f"IMAGE2_FORMAT={image_format}")
        print(f"IMAGE2_DIMENSIONS={width}x{height}")
        return 0
    finally:
        if staged != destination and staged.exists():
            staged.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
