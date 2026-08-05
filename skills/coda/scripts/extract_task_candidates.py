"""Extract actionable task candidates from a Markdown-like task file."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path


LOGGER = logging.getLogger("coda.extract_task_candidates")

CHECKBOX_RE = re.compile(
    r"^\s*[-*+]\s+\[(?P<mark>[ xX])\]\s+(?P<text>.+?)\s*$"
)
STATUS_RE = re.compile(
    r"^\s{0,3}(?P<status>TODO|NEXT|DONE)\s*[:\-]\s*(?P<text>.+?)\s*$",
    re.IGNORECASE,
)
NUMBERED_RE = re.compile(
    r"^\s{0,3}(?P<number>\d+)[\.\)]\s+(?P<text>.+?)\s*$"
)
HEADING_RE = re.compile(
    r"^\s{0,3}(?P<marks>#{1,6})\s+(?P<title>.+?)\s*#*\s*$"
)
FENCE_OPEN_RE = re.compile(r"^\s{0,3}(?P<fence>`{3,}|~{3,}).*$")

TASK_SECTION_TITLES = {
    "task",
    "tasks",
    "step",
    "steps",
    "action item",
    "action items",
    "work item",
    "work items",
    "todo",
    "implementation plan",
    "implementation steps",
    "execution plan",
    "execution steps",
    "任务",
    "步骤",
    "待办",
    "行动项",
    "实施计划",
    "实施步骤",
    "执行计划",
    "执行步骤",
}


def configure_logging(verbose: bool) -> None:
    """Configure stderr logging for manual debugging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


def load_text(path: Path) -> str:
    """Load UTF-8 text from the task source file."""
    LOGGER.debug("Loading task source from %s", path)
    return path.read_text(encoding="utf-8")


def iter_content_lines(text: str) -> list[tuple[int, str]]:
    """Return lines outside backtick or tilde code fences."""
    content_lines: list[tuple[int, str]] = []
    fence_char: str | None = None
    fence_length = 0

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()

        if fence_char is not None:
            if (
                len(stripped) >= fence_length
                and stripped
                and set(stripped) == {fence_char}
            ):
                LOGGER.debug("Closed code fence at line %s", line_number)
                fence_char = None
                fence_length = 0
            continue

        fence_match = FENCE_OPEN_RE.match(raw_line)
        if fence_match:
            fence = fence_match.group("fence")
            fence_char = fence[0]
            fence_length = len(fence)
            LOGGER.debug(
                "Opened %s code fence at line %s", fence_char, line_number
            )
            continue

        content_lines.append((line_number, raw_line))

    return content_lines


def build_candidate(
    *,
    line_number: int,
    kind: str,
    text: str,
    raw: str,
    status: str | None = None,
) -> dict[str, object]:
    """Build a normalized candidate payload."""
    candidate: dict[str, object] = {
        "line_number": line_number,
        "kind": kind,
        "text": text.strip(),
        "raw": raw.rstrip(),
    }
    if status is not None:
        candidate["status"] = status
    return candidate


def normalize_heading(title: str) -> str:
    """Normalize a Markdown heading for exact task-section matching."""
    normalized = title.strip().strip("*_`").strip()
    normalized = re.sub(r"\s*[:：]\s*$", "", normalized)
    return re.sub(r"\s+", " ", normalized).casefold()


def is_task_section(title: str) -> bool:
    """Return whether the heading explicitly denotes actionable work."""
    return normalize_heading(title) in TASK_SECTION_TITLES


def extract_candidates(text: str) -> list[dict[str, object]]:
    """Extract actionable checkbox, status, and scoped numbered candidates."""
    candidates: list[dict[str, object]] = []
    task_section_level: int | None = None

    for line_number, raw_line in iter_content_lines(text):
        heading_match = HEADING_RE.match(raw_line)
        if heading_match:
            heading_level = len(heading_match.group("marks"))
            if is_task_section(heading_match.group("title")):
                task_section_level = heading_level
                LOGGER.debug("Entered task section at line %s", line_number)
            elif (
                task_section_level is not None
                and heading_level <= task_section_level
            ):
                task_section_level = None
                LOGGER.debug("Left task section at line %s", line_number)
            continue

        checkbox_match = CHECKBOX_RE.match(raw_line)
        if checkbox_match:
            mark = checkbox_match.group("mark")
            if mark.lower() == "x":
                LOGGER.debug("Ignored completed checkbox at line %s", line_number)
                continue
            candidates.append(
                build_candidate(
                    line_number=line_number,
                    kind="checkbox",
                    status="todo",
                    text=checkbox_match.group("text"),
                    raw=raw_line,
                )
            )
            continue

        status_match = STATUS_RE.match(raw_line)
        if status_match:
            status = status_match.group("status").lower()
            if status == "done":
                LOGGER.debug("Ignored DONE marker at line %s", line_number)
                continue
            candidates.append(
                build_candidate(
                    line_number=line_number,
                    kind="status",
                    status=status,
                    text=status_match.group("text"),
                    raw=raw_line,
                )
            )
            continue

        numbered_match = NUMBERED_RE.match(raw_line)
        if numbered_match and task_section_level is not None:
            candidates.append(
                build_candidate(
                    line_number=line_number,
                    kind="numbered",
                    status="todo",
                    text=numbered_match.group("text"),
                    raw=raw_line,
                )
            )
        elif numbered_match:
            LOGGER.debug("Ignored numbered prose at line %s", line_number)

    LOGGER.info("Extracted %s actionable task candidates", len(candidates))
    return candidates


def choose_next_candidate(
    candidates: list[dict[str, object]],
) -> dict[str, object] | None:
    """Prefer NEXT, then TODO markers, then other actionable source items."""
    if not candidates:
        return None

    def candidate_priority(candidate: dict[str, object]) -> tuple[int, int]:
        status = str(candidate.get("status"))
        kind = str(candidate.get("kind"))
        if status == "next":
            rank = 0
        elif status == "todo" and kind == "status":
            rank = 1
        else:
            rank = 2
        return rank, int(candidate["line_number"])

    return min(
        candidates,
        key=candidate_priority,
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for the task-candidate extractor."""
    parser = argparse.ArgumentParser(
        description="Extract actionable candidates from an explicit task source."
    )
    parser.add_argument("path", help="Path to the task source file")
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser


def main() -> int:
    """Run the CLI entrypoint and print extracted candidates as JSON."""
    parser = build_parser()
    args = parser.parse_args()
    configure_logging(verbose=args.verbose)

    path = Path(args.path).expanduser().resolve()
    if not path.exists():
        LOGGER.error("Task source does not exist: %s", path)
        return 1

    if not path.is_file():
        LOGGER.error("Task source is not a file: %s", path)
        return 1

    try:
        text = load_text(path)
    except (OSError, UnicodeError) as exc:
        LOGGER.error("Cannot read task source %s: %s", path, exc)
        return 1

    candidates = extract_candidates(text)
    status = "READY" if candidates else "NO_ACTIONABLE_TASK"

    payload = {
        "path": str(path),
        "status": status,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "next_candidate": choose_next_candidate(candidates),
    }

    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0 if candidates else 2


if __name__ == "__main__":
    raise SystemExit(main())
