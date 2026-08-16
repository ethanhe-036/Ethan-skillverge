#!/usr/bin/env python3
import importlib.util
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "export_pptx.py"
SPEC = importlib.util.spec_from_file_location("export_pptx", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def content_types_xml(content_type=MODULE.PPTX_CONTENT_TYPE, slide_names=()):
    slide_overrides = "".join(
        f'<Override PartName="/{slide_name}" '
        f'ContentType="{MODULE.PRESENTATION_SLIDE_CONTENT_TYPE}"/>'
        for slide_name in slide_names
    )
    return (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Override PartName="/ppt/presentation.xml" '
        f'ContentType="{content_type}"/>{slide_overrides}</Types>'
    )


def presentation_xml(relationship_ids):
    slide_ids = "".join(
        f'<p:sldId id="{256 + index}" r:id="{relationship_id}"/>'
        for index, relationship_id in enumerate(relationship_ids)
    )
    return (
        '<p:presentation '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<p:sldIdLst>{slide_ids}</p:sldIdLst></p:presentation>"
    )


def presentation_relationships_xml(relationships):
    entries = []
    for relationship_id, relationship_type, target, target_mode in relationships:
        mode = f' TargetMode="{target_mode}"' if target_mode is not None else ""
        entries.append(
            '<Relationship '
            f'Id="{relationship_id}" Type="{relationship_type}" '
            f'Target="{target}"{mode}/>'
        )
    return (
        '<Relationships '
        'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{"".join(entries)}</Relationships>'
    )


def package_relationships_xml(
    target="ppt/presentation.xml",
    relationship_type=MODULE.OFFICE_DOCUMENT_RELATIONSHIP_TYPE,
    target_mode=None,
):
    return presentation_relationships_xml(
        [("rId1", relationship_type, target, target_mode)]
    )


def write_valid_pptx(archive, slides=()):
    slides = list(slides)
    archive.writestr(
        "[Content_Types].xml",
        content_types_xml(slide_names=[name for name, _xml in slides]),
    )
    archive.writestr("_rels/.rels", package_relationships_xml())
    relationship_ids = [f"rId{index + 1}" for index, _slide in enumerate(slides)]
    archive.writestr("ppt/presentation.xml", presentation_xml(relationship_ids))
    archive.writestr(
        "ppt/_rels/presentation.xml.rels",
        presentation_relationships_xml(
            [
                (
                    relationship_id,
                    MODULE.PRESENTATION_SLIDE_RELATIONSHIP_TYPE,
                    slide_name.removeprefix("ppt/"),
                    None,
                )
                for relationship_id, (slide_name, _slide_xml) in zip(
                    relationship_ids, slides
                )
            ]
        ),
    )
    for slide_name, slide_xml in slides:
        archive.writestr(slide_name, slide_xml)


def write_pptx_parts(
    archive,
    *,
    content_types,
    presentation,
    relationships,
    slides=(),
    package_relationships=package_relationships_xml(),
):
    if content_types is not None:
        archive.writestr("[Content_Types].xml", content_types)
    if presentation is not None:
        archive.writestr("ppt/presentation.xml", presentation)
    if package_relationships is not None:
        archive.writestr("_rels/.rels", package_relationships)
    if relationships is not None:
        archive.writestr("ppt/_rels/presentation.xml.rels", relationships)
    for slide_name, slide_xml in slides:
        archive.writestr(slide_name, slide_xml)


class ExportPptxTests(unittest.TestCase):
    def test_win32_file_rename_info_uses_root_handle_and_no_replace(self):
        class FakeFunction:
            def __init__(self, implementation=lambda *_args: 1):
                self.implementation = implementation
                self.argtypes = None
                self.restype = None

            def __call__(self, *args):
                return self.implementation(*args)

        observed = {}

        def set_information(source, io_status, buffer, size, information_class):
            header = MODULE._Win32RenameInfoHeader.from_buffer(buffer)
            name_offset = (
                MODULE._Win32RenameInfoHeader.file_name_length.offset
                + MODULE.ctypes.sizeof(MODULE.ctypes.c_uint32)
            )
            observed.update(
                source=source.value,
                information_class=information_class,
                flags=header.flags,
                root_directory=header.root_directory,
                name=MODULE.ctypes.string_at(
                    MODULE.ctypes.addressof(buffer) + name_offset,
                    header.file_name_length,
                ).decode("utf-16-le"),
                size=size,
            )
            return 1

        def nt_set_information(*args):
            set_information(*args)
            return 0

        class FakeKernel32:
            CreateFileW = FakeFunction()
            CloseHandle = FakeFunction()
            GetFileInformationByHandle = FakeFunction()
            GetFinalPathNameByHandleW = FakeFunction()

        class FakeNtdll:
            NtSetInformationFile = FakeFunction(nt_set_information)
            RtlNtStatusToDosError = FakeFunction(lambda status: status)

        api = MODULE._CtypesWin32DirectoryApi(FakeKernel32(), FakeNtdll())
        api.rename_noreplace(
            71,
            42,
            "deck.pptx",
            Path(r"C:\output\deck.pptx"),
        )

        self.assertEqual(observed["source"], 71)
        self.assertEqual(
            observed["information_class"],
            MODULE.WIN32_FILE_RENAME_INFORMATION,
        )
        self.assertEqual(observed["flags"], 0)
        self.assertEqual(observed["root_directory"], 42)
        self.assertEqual(observed["name"], "deck.pptx")
        self.assertGreater(observed["size"], len("deck.pptx".encode("utf-16-le")))

    def test_windows_handle_publication_targets_retained_directory(self):
        class FakeApi:
            def __init__(self):
                self.final_paths = {
                    10: r"\\?\C:\output",
                    20: r"\\?\C:\output\.deck.staging",
                }
                self.renames = []
                self.closed = []

            def open_directory(self, _path, *, writable):
                self.assert_writable = writable
                return 10

            def is_directory(self, _handle):
                return True

            def final_path(self, handle):
                return self.final_paths[handle]

            def open_rename_source(self, _path):
                return 20

            def rename_noreplace(self, *args):
                self.renames.append(args)

            def close(self, handle):
                self.closed.append(handle)

        api = FakeApi()
        capability = MODULE._WindowsDirectoryHandle.open(
            Path(r"C:\output"),
            api=api,
        )
        try:
            capability.rename_noreplace(
                Path(r"C:\output\.deck.staging"),
                "deck.pptx",
                Path(r"C:\output\deck.pptx"),
            )
        finally:
            capability.close()

        self.assertEqual(len(api.renames), 1)
        source_handle, root_handle, destination_name, destination_display = api.renames[0]
        self.assertEqual(source_handle, 20)
        self.assertEqual(root_handle, 10)
        self.assertEqual(destination_name, "deck.pptx")
        self.assertEqual(destination_display, Path(r"C:\output\deck.pptx"))
        self.assertEqual(api.closed, [20, 10])

    def test_windows_aba_source_open_cannot_publish_into_redirected_directory(self):
        class FakeApi:
            def __init__(self):
                self.final_paths = {
                    # The retained root is back at its original name, while the
                    # source HANDLE proves it was opened during a brief redirect.
                    10: r"\\?\C:\output",
                    20: r"\\?\C:\redirected\.deck.staging",
                }
                self.renames = []
                self.closed = []

            def final_path(self, handle):
                return self.final_paths[handle]

            def open_rename_source(self, _path):
                return 20

            def rename_noreplace(self, *args):
                self.renames.append(args)

            def close(self, handle):
                self.closed.append(handle)

        api = FakeApi()
        capability = MODULE._WindowsDirectoryHandle(
            api,
            10,
            api.final_paths[10],
            writable=True,
        )
        try:
            with self.assertRaisesRegex(MODULE.ExportError, "outside the anchored"):
                capability.rename_noreplace(
                    Path(r"C:\output\.deck.staging"),
                    "deck.pptx",
                    Path(r"C:\output\deck.pptx"),
                )
        finally:
            capability.close()

        self.assertEqual(api.renames, [])
        self.assertEqual(api.closed, [20, 10])

    def test_windows_open_file_rejects_final_handle_outside_root(self):
        class FakeApi:
            final_paths = {
                10: r"\\?\C:\project",
                20: r"\\?\C:\redirected\page.page",
            }

            def final_path(self, handle):
                return self.final_paths[handle]

            def close(self, _handle):
                pass

        windows_handle = MODULE._WindowsDirectoryHandle(
            FakeApi(),
            10,
            FakeApi.final_paths[10],
            writable=False,
        )
        capability = MODULE.AnchoredDirectory(
            Path(r"C:\project"),
            None,
            MODULE.PathSnapshot(True, 1, 1, MODULE.stat.S_IFDIR),
            windows_handle=windows_handle,
        )
        try:
            with patch.object(
                MODULE,
                "capture_path_snapshot",
                return_value=capability.identity,
            ), patch.object(MODULE.os, "open", return_value=77), patch.object(
                MODULE.os,
                "close",
            ) as close_descriptor, patch.object(
                MODULE,
                "_native_handle_from_descriptor",
                return_value=20,
            ):
                with self.assertRaisesRegex(MODULE.ExportError, "outside the anchored"):
                    capability.open_file("page.page", MODULE.os.O_RDONLY)
            close_descriptor.assert_called_once_with(77)
        finally:
            capability.close()

    def test_windows_handle_api_unavailable_fails_closed(self):
        with patch.object(MODULE.ctypes, "WinDLL", None, create=True):
            with self.assertRaisesRegex(
                MODULE.ExportError,
                "stable directory-handle APIs are unavailable",
            ):
                MODULE._load_win32_directory_api()

    def test_windows_filesystem_without_handle_relative_rename_fails_closed(self):
        class FakeFunction:
            def __init__(self, result=1):
                self.result = result
                self.argtypes = None
                self.restype = None

            def __call__(self, *_args):
                return self.result

        class FakeKernel32:
            CreateFileW = FakeFunction()
            CloseHandle = FakeFunction()
            GetFileInformationByHandle = FakeFunction()
            GetFinalPathNameByHandleW = FakeFunction()

        class FakeNtdll:
            NtSetInformationFile = FakeFunction(50)
            RtlNtStatusToDosError = FakeFunction(50)

        api = MODULE._CtypesWin32DirectoryApi(FakeKernel32(), FakeNtdll())
        with self.assertRaisesRegex(
            MODULE.ExportError,
            "cannot perform handle-relative no-replace publication",
        ):
            api.rename_noreplace(
                71,
                42,
                "deck.pptx",
                Path(r"C:\output\deck.pptx"),
            )

    @unittest.skipIf(os.name == "nt", "cannot emulate a foreign pathlib platform")
    def test_non_posix_platform_without_stable_handle_fails_closed(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            with patch.object(
                MODULE.os,
                "name",
                "unsupported",
            ), patch.object(MODULE.os, "supports_dir_fd", set()):
                with self.assertRaisesRegex(
                    MODULE.ExportError,
                    "lacks a stable directory descriptor or Windows HANDLE",
                ):
                    MODULE.AnchoredDirectory.open(directory)

    @unittest.skipIf(os.name == "nt", "cannot emulate a foreign pathlib platform")
    def test_posix_platform_without_nofollow_fails_closed(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            with patch.object(
                MODULE.os,
                "name",
                "posix",
            ), patch.object(
                MODULE.os,
                "supports_dir_fd",
                {MODULE.os.open, MODULE.os.stat},
            ), patch.object(
                MODULE.os,
                "O_NOFOLLOW",
                0,
                create=True,
            ):
                with self.assertRaisesRegex(
                    MODULE.ExportError,
                    "lacks O_NOFOLLOW",
                ):
                    MODULE.AnchoredDirectory.open(directory)

    def test_main_passes_the_manifest_used_for_default_output_to_export(self):
        manifest = Path("/project/deck.pptd")
        args = MODULE.argparse.Namespace(
            input=Path("/project"),
            output=None,
            transition="fade",
            embed_fonts=False,
            keep_browser_raw=False,
            force=False,
        )
        with patch.object(MODULE, "parse_args", return_value=args), \
                patch.object(MODULE, "find_manifest", return_value=manifest), \
                patch.object(MODULE, "export_pptx", return_value={}) as export:
            self.assertEqual(MODULE.main([]), 0)
        self.assertEqual(export.call_args.args[0], manifest)
        self.assertEqual(export.call_args.args[1], manifest.with_suffix(".pptx"))

    def test_directory_discovery_rejects_manifest_symlink_outside_project(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            project = root / "project"
            project.mkdir()
            outside = root / "outside.pptd"
            outside.write_text("pages: []\n", encoding="utf-8")
            link = project / "deck.pptd"
            try:
                link.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"symlinks are unavailable: {exc}")

            with self.assertRaisesRegex(MODULE.ExportError, "symbolic link"):
                MODULE.find_manifest(project)

    def test_directory_discovery_streams_with_entry_and_depth_limits(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            wide = root / "wide"
            wide.mkdir()
            (wide / "one.txt").write_text("x", encoding="utf-8")
            (wide / "two.txt").write_text("x", encoding="utf-8")
            with patch.object(MODULE, "MAX_MANIFEST_SCAN_ENTRIES", 1):
                with self.assertRaisesRegex(MODULE.ExportError, "entry limit"):
                    MODULE.find_manifest(wide)

            deep = root / "deep"
            (deep / "one" / "two").mkdir(parents=True)
            with patch.object(MODULE, "MAX_MANIFEST_SCAN_DEPTH", 1):
                with self.assertRaisesRegex(MODULE.ExportError, "depth limit"):
                    MODULE.find_manifest(deep)

    def test_dependency_hint_uses_the_active_python_executable(self):
        self.assertIn(
            str(Path(MODULE.sys.executable).resolve()),
            MODULE.pip_install_hint("pyyaml"),
        )

    def test_windows_dependency_hint_is_valid_for_powershell_and_cmd(self):
        with patch.object(MODULE, "Path") as path_cls, patch.object(
            MODULE.os, "name", "nt"
        ):
            path_cls.return_value.resolve.return_value = r"C:\Program Files\Python\python.exe"
            hint = MODULE.pip_install_hint("pyyaml")
        self.assertIn('PowerShell: & "', hint)
        self.assertIn('cmd.exe: "', hint)
        self.assertEqual(hint.count("-m pip install pyyaml"), 2)

    def test_missing_pyyaml_is_reported_without_installing(self):
        with patch.dict(MODULE.sys.modules, {"yaml": None}):
            with self.assertRaisesRegex(MODULE.ExportError, "PyYAML is required"):
                MODULE.ensure_pyyaml()

    def test_missing_websocket_fails_before_staging_or_browser_work(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name) / "project"
            pages = project / "pages"
            pages.mkdir(parents=True)
            manifest = project / "deck.pptd"
            manifest.write_text("manifest", encoding="utf-8")
            (pages / "01.page").write_text("page", encoding="utf-8")
            output = project / "deck.pptx"
            payload = {"pages": [{"path": "pages/01.page"}], "imageMap": {}}

            with patch.object(MODULE, "find_manifest", return_value=manifest), \
                    patch.object(MODULE, "verify_quality_gate", return_value={"passed": True}), \
                    patch.object(MODULE, "quality_gate_bound_inputs", return_value=()), \
                    patch.object(MODULE, "build_payload", return_value=payload), \
                    patch.object(MODULE, "ensure_agent_browser", return_value="agent-browser"), \
                    patch.object(
                        MODULE,
                        "ensure_websocket",
                        side_effect=MODULE.ExportError("websocket-client is required"),
                    ), \
                    patch.object(MODULE, "ensure_debug_chrome") as debug_chrome, \
                    patch.object(MODULE, "BrowserSession") as browser:
                with self.assertRaisesRegex(MODULE.ExportError, "websocket-client"):
                    MODULE.export_pptx(
                        project,
                        output,
                        transition="fade",
                        embed_fonts=False,
                        quality_report=manifest,
                    )

            debug_chrome.assert_not_called()
            browser.assert_not_called()
            self.assertFalse(output.exists())
            self.assertFalse(any(project.glob(".*.staging")))

    def test_quality_gate_rejection_happens_before_payload_or_browser_work(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name)
            manifest = project / "deck.pptd"
            manifest.write_text("version: v2\nsize: [960, 540]\npages: []\n", encoding="utf-8")
            report = project / "quality.json"
            report.write_text("{}", encoding="utf-8")
            output = project / "deck.pptx"
            with patch.object(MODULE, "build_payload") as payload, patch.object(
                MODULE, "ensure_agent_browser"
            ) as browser:
                with self.assertRaisesRegex(MODULE.ExportError, "quality gate rejected"):
                    MODULE.export_pptx(
                        manifest,
                        output,
                        transition="fade",
                        embed_fonts=False,
                        quality_report=report,
                    )
            payload.assert_not_called()
            browser.assert_not_called()

    def test_missing_quality_report_fails_before_payload_or_browser_work(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name)
            manifest = project / "deck.pptd"
            manifest.write_text("version: v2\nsize: [960, 540]\npages: []\n", encoding="utf-8")
            with patch.object(MODULE, "build_payload") as payload, patch.object(
                MODULE, "ensure_agent_browser"
            ) as browser:
                with self.assertRaisesRegex(MODULE.ExportError, "quality_report is required"):
                    MODULE.export_pptx(
                        manifest,
                        project / "deck.pptx",
                        transition="fade",
                        embed_fonts=False,
                    )
            payload.assert_not_called()
            browser.assert_not_called()

    def test_quality_gate_is_rechecked_after_payload_capture(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name)
            manifest = project / "deck.pptd"
            report = project / "quality.json"
            manifest.write_text("manifest", encoding="utf-8")
            with patch.object(MODULE, "find_manifest", return_value=manifest), patch.object(
                MODULE, "verify_quality_gate", side_effect=[{"passed": True}, MODULE.ExportError("stale")]
            ) as gate, patch.object(MODULE, "quality_gate_bound_inputs", return_value=()), patch.object(
                MODULE, "build_payload", return_value={"pages": [], "imageMap": {}}
            ), patch.object(
                MODULE, "ensure_agent_browser"
            ) as browser:
                with self.assertRaisesRegex(MODULE.ExportError, "stale"):
                    MODULE.export_pptx(
                        manifest,
                        project / "deck.pptx",
                        transition="fade",
                        embed_fonts=False,
                        quality_report=report,
                    )
            self.assertEqual(gate.call_count, 2)
            browser.assert_not_called()

    def test_pptd_text_and_structure_limits_fail_before_browser_work(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            oversized = root / "oversized.pptd"
            oversized.write_text("12345", encoding="utf-8")
            with patch.object(MODULE, "MAX_TEXT_FILE_BYTES", 4):
                with self.assertRaisesRegex(MODULE.ExportError, "exceeds 20 MiB"):
                    MODULE.read_yaml_mapping(oversized)

            manifest = root / "deck.pptd"
            manifest.write_text("manifest", encoding="utf-8")
            with patch.object(
                MODULE,
                "read_yaml_mapping",
                return_value=(
                    "manifest",
                    {"version": "v2", "pages": ["page.page"] * 501},
                ),
            ):
                with self.assertRaisesRegex(MODULE.ExportError, "more than 500 pages"):
                    MODULE.build_payload(manifest)

    def test_yaml_event_budget_rejects_before_object_construction(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "too-many.pptd"
            path.write_text("values: [a, b, c]\n", encoding="utf-8")
            yaml = MODULE.ensure_pyyaml()
            with patch.object(MODULE, "MAX_YAML_NODES", 3), \
                    patch.object(yaml, "load", wraps=yaml.load) as load:
                with self.assertRaisesRegex(MODULE.ExportError, "node-count"):
                    MODULE.read_yaml_mapping(path)
            load.assert_not_called()

            huge_number = Path(name) / "huge-number.pptd"
            huge_number.write_text(
                "version: " + "9" * 5000 + "\n", encoding="utf-8"
            )
            with patch.object(yaml, "load", wraps=yaml.load) as load:
                with self.assertRaisesRegex(MODULE.ExportError, "numeric scalar"):
                    MODULE.read_yaml_mapping(huge_number)
            load.assert_not_called()

            huge_sexagesimal = Path(name) / "huge-sexagesimal.pptd"
            huge_sexagesimal.write_text(
                "version: " + "9" * 5000 + ":00\n", encoding="utf-8"
            )
            with patch.object(yaml, "load", wraps=yaml.load) as load:
                with self.assertRaisesRegex(MODULE.ExportError, "numeric scalar"):
                    MODULE.read_yaml_mapping(huge_sexagesimal)
            load.assert_not_called()

            explicit_integer = Path(name) / "explicit-integer.pptd"
            explicit_integer.write_text(
                'version: !!int "' + "9" * 5000 + '"\n', encoding="utf-8"
            )
            with patch.object(yaml, "load", wraps=yaml.load) as load:
                with self.assertRaisesRegex(MODULE.ExportError, "numeric scalar"):
                    MODULE.read_yaml_mapping(explicit_integer)
            load.assert_not_called()

            long_text = Path(name) / "long-text.pptd"
            long_text.write_text("text: " + "a" * 5000 + "\n", encoding="utf-8")
            _text, parsed = MODULE.read_yaml_mapping(long_text)
            self.assertEqual(parsed["text"], "a" * 5000)

            alias = Path(name) / "alias.pptd"
            alias.write_text("base: &base [a]\ncopy: *base\n", encoding="utf-8")
            with patch.object(yaml, "load", wraps=yaml.load) as load:
                with self.assertRaisesRegex(MODULE.ExportError, "aliases/anchors"):
                    MODULE.read_yaml_mapping(alias)
            load.assert_not_called()

    def test_yaml_duplicate_mapping_keys_are_rejected_at_every_depth(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            fixtures = {
                "manifest": (
                    "version: v2\npages: [pages/a.page]\n"
                    "pages: [pages/b.page]\n"
                ),
                "nested": "layout:\n  src: media/a.png\n  src: media/b.png\n",
            }
            for label, content in fixtures.items():
                with self.subTest(label=label):
                    path = root / f"{label}.pptd"
                    path.write_text(content, encoding="utf-8")
                    with self.assertRaisesRegex(MODULE.ExportError, "duplicate YAML mapping key"):
                        MODULE.read_yaml_mapping(path)

    def test_user_path_expansion_and_symlink_loops_are_controlled_errors(self):
        with patch.object(
            MODULE.Path,
            "expanduser",
            side_effect=RuntimeError("unknown home"),
        ):
            with self.assertRaisesRegex(MODULE.ExportError, "invalid PPTD input path"):
                MODULE.find_manifest(Path("~missing-user/deck"))

        if os.name == "nt":
            return

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            first = root / "first"
            second = root / "second"
            try:
                first.symlink_to(second)
                second.symlink_to(first)
            except OSError as exc:
                self.skipTest(f"symlinks are unavailable: {exc}")
            with self.assertRaisesRegex(MODULE.ExportError, "invalid PPTD input path"):
                MODULE.find_manifest(first / "deck.pptd")

    def test_bounded_input_reader_rejects_fifo_and_detects_concurrent_change(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            fifo = root / "input.pptd"
            if not hasattr(MODULE.os, "mkfifo"):
                self.skipTest("FIFO creation is unavailable")
            MODULE.os.mkfifo(fifo)
            with self.assertRaisesRegex(MODULE.ExportError, "regular non-symlink"):
                MODULE.read_yaml_mapping(fifo)

            changing = root / "changing.pptd"
            changing.write_text("version: v2\npages: []\n", encoding="utf-8")
            real_read = MODULE.os.read
            changed = {"done": False}

            def mutate_after_read(descriptor, size):
                data = real_read(descriptor, size)
                if data and not changed["done"]:
                    changed["done"] = True
                    changing.write_text("version: v2\npages: [other.page]\n", encoding="utf-8")
                return data

            with patch.object(MODULE.os, "read", side_effect=mutate_after_read):
                with self.assertRaisesRegex(MODULE.InputChangedError, "changed while"):
                    MODULE.read_yaml_mapping(changing)

    def test_payload_capture_retries_the_whole_project_snapshot(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name)
            pages = project / "pages"
            pages.mkdir()
            manifest = project / "deck.pptd"
            page = pages / "01.page"
            manifest.write_text(
                "version: v2\ntitle: Snapshot\npages:\n  - pages/01.page\n",
                encoding="utf-8",
            )
            page.write_text("elements: []\n", encoding="utf-8")
            calls = {"count": 0}

            def mutate_between_files(_root, _references, **_kwargs):
                calls["count"] += 1
                if calls["count"] == 1:
                    page.write_text(
                        "elements:\n  - type: text\n",
                        encoding="utf-8",
                    )
                return {}

            def parse_fixture(path):
                text = path.read_text(encoding="utf-8")
                if path == manifest:
                    return text, {
                        "version": "v2",
                        "title": "Snapshot",
                        "pages": ["pages/01.page"],
                    }
                return text, {
                    "elements": ([{"type": "text"}] if "type: text" in text else [])
                }

            with patch.object(MODULE, "read_yaml_mapping", side_effect=parse_fixture), \
                    patch.object(
                        MODULE,
                        "build_image_map",
                        side_effect=mutate_between_files,
                    ):
                payload = MODULE.build_payload(manifest)

            self.assertEqual(calls["count"], 2)
            self.assertIn("type: text", payload["pages"][0]["content"])

    def test_image_reference_walk_is_cycle_safe_and_depth_limited(self):
        cycle = {"src": "media/a.png"}
        cycle["self"] = cycle
        self.assertEqual(MODULE.collect_image_references(cycle), ["media/a.png"])

        nested = {}
        cursor = nested
        for _ in range(MODULE.MAX_YAML_DEPTH + 2):
            child = {}
            cursor["child"] = child
            cursor = child
        with self.assertRaisesRegex(MODULE.ExportError, "nesting-depth"):
            MODULE.collect_image_references(nested)

    def test_parse_agent_browser_version(self):
        release = MODULE.parse_version("agent-browser 0.33.2")
        self.assertEqual(release.core, (0, 33, 2))
        self.assertTrue(release.is_plain_release)
        self.assertEqual(str(release), "0.33.2")

        beta = MODULE.parse_version("agent-browser 0.33.2-beta.1")
        self.assertEqual(beta.core, (0, 33, 2))
        self.assertEqual(beta.prerelease, ("beta", "1"))
        self.assertEqual(beta.build, ())
        self.assertFalse(beta.is_plain_release)

        release_candidate = MODULE.parse_version(
            "v0.33.2-rc.2+linux.arm64"
        )
        self.assertEqual(release_candidate.prerelease, ("rc", "2"))
        self.assertEqual(release_candidate.build, ("linux", "arm64"))
        self.assertEqual(str(release_candidate), "0.33.2-rc.2+linux.arm64")

        build = MODULE.parse_version("agent-browser 0.33.2+build.7")
        self.assertEqual(build.prerelease, ())
        self.assertEqual(build.build, ("build", "7"))
        self.assertFalse(build.is_plain_release)

    def test_parse_agent_browser_version_rejects_partial_or_ambiguous_semver(self):
        invalid_outputs = (
            "agent-browser 0.33.2-beta.01",
            "agent-browser 0.33.2-beta..1",
            "agent-browser 01.33.2",
            "agent-browser 0.33.2.1",
            "agent-browser 0.33.2 (runtime 24.0.0)",
        )
        for output in invalid_outputs:
            with self.subTest(output=output):
                with self.assertRaisesRegex(MODULE.ExportError, "complete.*semantic"):
                    MODULE.parse_version(output)

    def test_sanitized_writer_snapshot_matches_the_explicit_ui_contract(self):
        # Deliberately contains no document content, account data, or live URL.
        fixture = {
            "data": {
                "snapshot": '- button "导出" [ref=e1]\n- button "下载" [ref=e2]',
                "refs": {
                    "e1": {"name": "导出", "role": "button"},
                    "e2": {"name": "下载", "role": "button"},
                },
            }
        }
        self.assertEqual(MODULE.writer_control_ref(fixture, "export"), "e1")
        self.assertEqual(MODULE.writer_control_ref(fixture, "download"), "e2")
        self.assertEqual(MODULE.KIMI_WRITER_ORIGIN, "https://www.kimi.com")
        self.assertEqual(MODULE.KIMI_WRITER_PATH_PREFIX, "/neo-ppt")
        self.assertEqual(MODULE.KIMI_WRITER_UI.image_format_name, "图片")
        self.assertIn("字体", MODULE.KIMI_WRITER_UI.font_name_tokens)

    def test_writer_control_contract_rejects_duplicate_named_buttons(self):
        fixture = {
            "data": {
                "snapshot": "sanitized duplicate controls",
                "refs": {
                    "e1": {"name": "导出", "role": "button"},
                    "e2": {"name": "导出", "role": "button"},
                },
            }
        }
        with self.assertRaisesRegex(
            MODULE.AmbiguousUiContractError,
            "refusing to guess",
        ):
            MODULE.writer_control_ref(fixture, "export")

    @patch.object(MODULE, "run_command")
    @patch.object(MODULE.shutil, "which")
    def test_old_agent_browser_raises_pinned_install_guidance(self, which, run_command):
        which.side_effect = [
            "/bin/node",
            "/bin/npm",
            "/bin/agent-browser",
        ]
        run_command.side_effect = [
            MODULE.subprocess.CompletedProcess([], 0, "v24.14.0\n"),
            MODULE.subprocess.CompletedProcess([], 0, "agent-browser 0.17.1\n"),
        ]
        with self.assertRaisesRegex(
            MODULE.ExportError,
            r"npm install --global agent-browser@0\.33\.2",
        ):
            MODULE.ensure_agent_browser()
        self.assertEqual(len(run_command.call_args_list), 2)

    @patch.object(MODULE, "run_command")
    @patch.object(MODULE.shutil, "which")
    def test_missing_agent_browser_raises_pinned_install_guidance(
        self, which, run_command
    ):
        which.side_effect = ["/bin/node", "/bin/npm", None]
        run_command.return_value = MODULE.subprocess.CompletedProcess(
            [], 0, "v24.14.0\n"
        )
        with self.assertRaisesRegex(
            MODULE.ExportError,
            r"npm install --global agent-browser@0\.33\.2",
        ):
            MODULE.ensure_agent_browser()
        self.assertEqual(len(run_command.call_args_list), 1)

    @patch.object(MODULE, "run_command")
    @patch.object(MODULE.shutil, "which")
    def test_newer_agent_browser_is_rejected_outside_tested_window(
        self, which, run_command
    ):
        which.side_effect = ["/bin/node", "/bin/npm", "/bin/agent-browser"]
        run_command.side_effect = [
            MODULE.subprocess.CompletedProcess([], 0, "v24.14.0\n"),
            MODULE.subprocess.CompletedProcess([], 0, "agent-browser 0.34.0\n"),
        ]
        with patch.dict(
            MODULE.os.environ,
            {MODULE.ALLOW_UNTESTED_AGENT_BROWSER_ENV: ""},
        ):
            with self.assertRaisesRegex(MODULE.ExportError, "tested compatibility"):
                MODULE.ensure_agent_browser()

    def test_exact_tested_agent_browser_release_is_accepted(self):
        version = MODULE.parse_version("agent-browser 0.33.2")
        with patch.object(MODULE, "ensure_nodejs"), patch.object(
            MODULE.shutil, "which", return_value="/bin/agent-browser"
        ), patch.object(
            MODULE, "read_agent_browser_version", return_value=version
        ), patch.dict(
            MODULE.os.environ,
            {MODULE.ALLOW_UNTESTED_AGENT_BROWSER_ENV: ""},
        ):
            self.assertEqual(MODULE.ensure_agent_browser(), "/bin/agent-browser")

    def test_prerelease_and_build_agent_browser_require_override(self):
        for raw_version in (
            "0.33.2-beta.1",
            "0.33.2-rc.1",
            "0.33.2+vendor.7",
        ):
            version = MODULE.parse_version(f"agent-browser {raw_version}")
            with self.subTest(version=raw_version), patch.object(
                MODULE, "ensure_nodejs"
            ), patch.object(
                MODULE.shutil, "which", return_value="/bin/agent-browser"
            ), patch.object(
                MODULE, "read_agent_browser_version", return_value=version
            ), patch.dict(
                MODULE.os.environ,
                {MODULE.ALLOW_UNTESTED_AGENT_BROWSER_ENV: ""},
            ):
                with self.assertRaisesRegex(
                    MODULE.ExportError, "official releases 0.33.2..0.33.2 only"
                ):
                    MODULE.ensure_agent_browser()

    def test_override_allows_prerelease_and_build_with_warning(self):
        for raw_version in (
            "0.33.2-beta.1",
            "0.33.2-rc.1",
            "0.33.2+vendor.7",
        ):
            version = MODULE.parse_version(f"agent-browser {raw_version}")
            with self.subTest(version=raw_version), patch.object(
                MODULE, "ensure_nodejs"
            ), patch.object(
                MODULE.shutil, "which", return_value="/bin/agent-browser"
            ), patch.object(
                MODULE, "read_agent_browser_version", return_value=version
            ), patch.dict(
                MODULE.os.environ,
                {MODULE.ALLOW_UNTESTED_AGENT_BROWSER_ENV: "1"},
            ), patch.object(MODULE, "log") as mocked_log:
                self.assertEqual(
                    MODULE.ensure_agent_browser(), "/bin/agent-browser"
                )
                mocked_log.assert_any_call(
                    "warning: continuing with untested agent-browser version "
                    f"{raw_version} because "
                    f"{MODULE.ALLOW_UNTESTED_AGENT_BROWSER_ENV}=1"
                )

    def test_override_does_not_allow_agent_browser_below_minimum(self):
        version = MODULE.parse_version("agent-browser 0.33.1-rc.1")
        with patch.object(MODULE, "ensure_nodejs"), patch.object(
            MODULE.shutil, "which", return_value="/bin/agent-browser"
        ), patch.object(
            MODULE, "read_agent_browser_version", return_value=version
        ), patch.dict(
            MODULE.os.environ,
            {MODULE.ALLOW_UNTESTED_AGENT_BROWSER_ENV: "1"},
        ):
            with self.assertRaisesRegex(MODULE.ExportError, "below the required"):
                MODULE.ensure_agent_browser()

    @patch.object(MODULE, "run_command")
    @patch.object(MODULE.shutil, "which")
    def test_explicit_override_allows_unverified_agent_browser(
        self, which, run_command
    ):
        which.side_effect = ["/bin/node", "/bin/npm", "/bin/agent-browser"]
        run_command.side_effect = [
            MODULE.subprocess.CompletedProcess([], 0, "v24.14.0\n"),
            MODULE.subprocess.CompletedProcess([], 0, "agent-browser 0.34.0\n"),
        ]
        with patch.dict(
            MODULE.os.environ,
            {MODULE.ALLOW_UNTESTED_AGENT_BROWSER_ENV: "1"},
        ):
            self.assertEqual(MODULE.ensure_agent_browser(), "/bin/agent-browser")

    @patch.object(MODULE, "run_command")
    @patch.object(MODULE.shutil, "which")
    def test_missing_nodejs_raises_clear_error(self, which, run_command):
        which.return_value = None
        with self.assertRaisesRegex(MODULE.ExportError, "Node.js is not installed"):
            MODULE.ensure_nodejs()
        run_command.assert_not_called()

    @patch.object(MODULE, "run_command")
    @patch.object(MODULE.shutil, "which")
    def test_old_nodejs_raises_clear_error(self, which, run_command):
        which.return_value = "/bin/node"
        run_command.return_value = MODULE.subprocess.CompletedProcess([], 0, "v16.20.2\n")
        with self.assertRaisesRegex(MODULE.ExportError, "Node.js 24\\+ is required"):
            MODULE.ensure_nodejs()

    @patch.object(MODULE, "switch_state", return_value=None)
    def test_missing_font_switch_is_reported_as_unknown(self, _switch_state):
        dialog = {"role": "dialog"}
        returned_dialog, enabled = MODULE.configure_font_embedding(
            object(),
            dialog,
            True,
        )
        self.assertIs(returned_dialog, dialog)
        self.assertIsNone(enabled)
        with self.assertRaisesRegex(MODULE.ExportError, "no font switch"):
            MODULE.configure_font_embedding(object(), dialog, False)

    def test_switch_state_selects_named_font_switch_and_rejects_ambiguity(self):
        snapshot = {
            "data": {
                "snapshot": (
                    '- switch "Notifications" [checked=true ref=e1]\n'
                    '- switch "嵌入字体" [ref=e2]\n'
                ),
                "refs": {
                    "e1": {"role": "switch", "name": "Notifications"},
                    "e2": {"role": "switch", "name": "嵌入字体"},
                },
            }
        }
        self.assertEqual(MODULE.switch_state(snapshot), ("e2", False, False))

        snapshot["data"]["refs"]["e2"] = {"role": "switch", "name": ""}
        with self.assertRaisesRegex(MODULE.ExportError, "multiple unnamed switches"):
            MODULE.switch_state(snapshot)

        single = {
            "data": {
                "snapshot": "- switch [checked=true disabled ref=e7]\n",
                "refs": {"e7": {"role": "switch", "name": ""}},
            }
        }
        self.assertEqual(MODULE.switch_state(single), ("e7", True, True))

    @patch.object(MODULE, "run_command")
    @patch.object(MODULE.shutil, "which")
    def test_missing_npm_raises_clear_error(self, which, run_command):
        which.side_effect = ["/bin/node", None]
        run_command.return_value = MODULE.subprocess.CompletedProcess([], 0, "v24.14.0\n")
        with self.assertRaisesRegex(MODULE.ExportError, "npm is not installed"):
            MODULE.ensure_nodejs()

    def test_parse_node_version(self):
        self.assertEqual(MODULE.parse_node_version("v22.11.0"), (22, 11, 0))
        self.assertEqual(MODULE.parse_node_version("18.20.4"), (18, 20, 4))

    def test_fade_is_inserted_before_timing(self):
        source = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:extLst/>'
            b'</p:spTree></p:cSld><p:clrMapOvr/><p:timing/><p:extLst/></p:sld>'
        )
        result_bytes = MODULE.replace_transition(source, "fade")
        result = result_bytes.decode("utf-8")
        self.assertIn("<p:transition", result)
        self.assertIn("<p:fade/>", result)
        self.assertGreater(result.index("<p:transition"), result.index("<p:clrMapOvr"))
        self.assertLess(result.index("<p:transition"), result.index("<p:timing"))
        MODULE.validate_transition_order(result_bytes, "fade")

    def test_existing_transition_is_replaced_or_removed(self):
        source = (
            b'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree/></p:cSld>'
            b'<p:transition><p:wipe/></p:transition><p:extLst/></p:sld>'
        )
        faded = MODULE.replace_transition(source, "fade").decode("utf-8")
        self.assertNotIn("p:wipe", faded)
        self.assertEqual(faded.count("<p:transition"), 1)
        MODULE.validate_transition_order(faded.encode("utf-8"), "fade")
        cleared = MODULE.replace_transition(source, "none").decode("utf-8")
        self.assertNotIn("p:transition", cleared)
        MODULE.validate_transition_order(cleared.encode("utf-8"), "none")

    def test_nested_transition_is_relocated_to_slide_root(self):
        source = (
            b'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree>'
            b'<p:transition><p:fade/></p:transition><p:extLst/>'
            b'</p:spTree></p:cSld><p:clrMapOvr/><p:extLst/></p:sld>'
        )
        result = MODULE.replace_transition(source, "fade")
        MODULE.validate_transition_order(result, "fade")
        root = MODULE.ET.fromstring(result)
        transition_tag = f"{{{MODULE.PRESENTATION_NAMESPACE}}}transition"
        self.assertEqual(sum(node.tag == transition_tag for node in root.iter()), 1)
        self.assertEqual(MODULE.root_child_names(result), [
            "cSld", "clrMapOvr", "transition", "extLst"
        ])

    def test_transition_patch_uses_expanded_presentation_namespace(self):
        source = (
            b'<q:sld xmlns:q="http://schemas.openxmlformats.org/presentationml/2006/main" '
            b'xmlns:p="urn:not-presentation"><q:cSld><q:spTree/></q:cSld>'
            b'<p:cSld/><p:transition><p:fade/></p:transition></q:sld>'
        )
        result = MODULE.replace_transition(source, "fade")
        MODULE.validate_transition_order(result, "fade")
        root = MODULE.ET.fromstring(result)
        real_transition = f"{{{MODULE.PRESENTATION_NAMESPACE}}}transition"
        wrong_transition = "{urn:not-presentation}transition"
        self.assertEqual(sum(child.tag == real_transition for child in root), 1)
        self.assertEqual(sum(child.tag == wrong_transition for child in root), 1)

        cleared = MODULE.replace_transition(result, "none")
        MODULE.validate_transition_order(cleared, "none")
        cleared_root = MODULE.ET.fromstring(cleared)
        self.assertFalse(any(child.tag == real_transition for child in cleared_root))
        self.assertTrue(any(child.tag == wrong_transition for child in cleared_root))

    def test_transition_patch_preserves_ignorable_extension_prefixes_lexically(self):
        source = (
            b'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            b'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
            b'xmlns:p14="http://schemas.microsoft.com/office/powerpoint/2010/main" '
            b'mc:Ignorable="p14"><p:cSld><p:spTree><p14:creationId val="1"/>'
            b'</p:spTree></p:cSld></p:sld>'
        )
        result = MODULE.replace_transition(source, "fade")
        MODULE.validate_transition_order(result, "fade")
        self.assertIn(b'xmlns:p14="http://schemas.microsoft.com/office/powerpoint/2010/main"', result)
        self.assertIn(b'mc:Ignorable="p14"', result)
        self.assertIn(b'<p14:creationId val="1"/>', result)

    def test_transition_uses_the_root_prefix_not_an_anchor_local_prefix(self):
        source = (
            b'<q:sld xmlns:q="http://schemas.openxmlformats.org/presentationml/2006/main">'
            b'<p:cSld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            b'<p:spTree/></p:cSld></q:sld>'
        )
        result = MODULE.replace_transition(source, "fade")
        self.assertIn(b"<q:transition", result)
        self.assertNotIn(b"<p:transition", result)
        MODULE.ET.fromstring(result)
        MODULE.validate_transition_order(result, "fade")

    def test_patch_transitions_preserves_a_valid_zip(self):
        with tempfile.TemporaryDirectory() as name:
            deck = Path(name) / "test.pptx"
            with zipfile.ZipFile(deck, "w", zipfile.ZIP_DEFLATED) as archive:
                write_valid_pptx(
                    archive,
                    [
                        (
                            "ppt/slides/slide1.xml",
                            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree/></p:cSld></p:sld>',
                        )
                    ],
                )
            self.assertEqual(MODULE.patch_transitions(deck, "fade"), 1)
            with zipfile.ZipFile(deck) as archive:
                self.assertIsNone(archive.testzip())
                slide = archive.read("ppt/slides/slide1.xml")
                self.assertIn(b"<p:fade/>", slide)
            with self.assertRaisesRegex(MODULE.ExportError, "slide count"):
                MODULE.verify_output(
                    deck, "fade", expect_fonts=False, expected_slides=2
                )

    @unittest.skipIf(os.name == "nt", "Windows denies renaming an open file")
    def test_transition_patch_detects_path_swap_after_verified_open(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            deck = root / "test.pptx"
            with zipfile.ZipFile(deck, "w", zipfile.ZIP_DEFLATED) as archive:
                write_valid_pptx(
                    archive,
                    [
                        (
                            "ppt/slides/slide1.xml",
                            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree/></p:cSld></p:sld>',
                        )
                    ],
                )
            identity = MODULE.capture_path_snapshot(deck)
            displaced = root / "displaced.pptx"
            real_replace = MODULE.replace_transition
            injected = {"done": False}

            def swap_path(data, transition):
                if not injected["done"]:
                    injected["done"] = True
                    deck.replace(displaced)
                    deck.write_text("concurrent", encoding="utf-8")
                return real_replace(data, transition)

            with patch.object(MODULE, "replace_transition", side_effect=swap_path):
                with self.assertRaisesRegex(MODULE.ExportError, "path changed"):
                    MODULE.patch_transitions(
                        deck,
                        "fade",
                        expected_identity=identity,
                    )

            self.assertEqual(deck.read_text(encoding="utf-8"), "concurrent")
            with zipfile.ZipFile(displaced) as archive:
                self.assertIn(b"<p:fade/>", archive.read("ppt/slides/slide1.xml"))

    def test_slide_verification_reads_and_validates_one_page_at_a_time(self):
        events = []

        class FakeArchive:
            def read(self, name):
                events.append(("read", name))
                return name.encode("utf-8")

        def validate(data, transition):
            events.append(("validate", data.decode("utf-8"), transition))

        def has_fade(data):
            events.append(("fade", data.decode("utf-8")))
            return data.startswith(b"slide1")

        with patch.object(MODULE, "validate_transition_order", side_effect=validate), \
                patch.object(MODULE, "has_direct_fade_transition", side_effect=has_fade):
            hits = MODULE.verify_slide_transitions(
                FakeArchive(),
                ["slide1.xml", "slide2.xml", "slide3.xml"],
                "fade",
            )

        self.assertEqual(hits, 1)
        self.assertEqual(
            events,
            [
                ("read", "slide1.xml"),
                ("validate", "slide1.xml", "fade"),
                ("fade", "slide1.xml"),
                ("read", "slide2.xml"),
                ("validate", "slide2.xml", "fade"),
                ("fade", "slide2.xml"),
                ("read", "slide3.xml"),
                ("validate", "slide3.xml", "fade"),
                ("fade", "slide3.xml"),
            ],
        )

    def test_pptx_core_graph_preserves_presentation_order_and_manifest_count(self):
        with tempfile.TemporaryDirectory() as name:
            deck = Path(name) / "ordered.pptx"
            with zipfile.ZipFile(deck, "w") as archive:
                write_valid_pptx(
                    archive,
                    [
                        (
                            "ppt/slides/slide10.xml",
                            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree/></p:cSld></p:sld>',
                        ),
                        (
                            "ppt/slides/slide2.xml",
                            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree/></p:cSld></p:sld>',
                        ),
                    ],
                )

            self.assertEqual(
                MODULE.validate_pptx_archive(
                    deck,
                    require_slides=True,
                    expected_slides=2,
                ),
                ["ppt/slides/slide10.xml", "ppt/slides/slide2.xml"],
            )
            with self.assertRaisesRegex(MODULE.ExportError, "slide count"):
                MODULE.validate_pptx_archive(deck, expected_slides=3)

    def test_pptx_core_xml_parts_must_be_present_and_well_formed(self):
        slide = (
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree/></p:cSld></p:sld>',
        )
        valid_parts = {
            "content_types": content_types_xml(
                slide_names=["ppt/slides/slide1.xml"]
            ),
            "presentation": presentation_xml(["rId1"]),
            "relationships": presentation_relationships_xml(
                [
                    (
                        "rId1",
                        MODULE.PRESENTATION_SLIDE_RELATIONSHIP_TYPE,
                        "slides/slide1.xml",
                        None,
                    )
                ]
            ),
        }
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for key in valid_parts:
                with self.subTest(malformed=key):
                    deck = root / f"malformed-{key}.pptx"
                    parts = dict(valid_parts)
                    parts[key] = b"<garbage"
                    with zipfile.ZipFile(deck, "w") as archive:
                        write_pptx_parts(archive, slides=[slide], **parts)
                    with self.assertRaisesRegex(MODULE.ExportError, "malformed .* XML"):
                        MODULE.validate_pptx_archive(deck)

            missing_rels = root / "missing-relationships.pptx"
            with zipfile.ZipFile(missing_rels, "w") as archive:
                write_pptx_parts(
                    archive,
                    content_types=valid_parts["content_types"],
                    presentation=valid_parts["presentation"],
                    relationships=None,
                    slides=[slide],
                )
            with self.assertRaisesRegex(MODULE.ExportError, "missing required OOXML"):
                MODULE.validate_pptx_archive(missing_rels)

    def test_pptx_zip_filename_decode_errors_are_controlled(self):
        with tempfile.TemporaryDirectory() as name:
            deck = Path(name) / "malformed-name.pptx"
            deck.write_bytes(b"PK")
            with patch.object(
                MODULE.zipfile,
                "ZipFile",
                side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"),
            ):
                with self.assertRaisesRegex(MODULE.ExportError, "invalid ZIP"):
                    MODULE.validate_pptx_archive(deck)

    def test_ooxml_parser_rejects_excessive_elements_and_depth(self):
        with tempfile.TemporaryDirectory() as name:
            deck = Path(name) / "bounded-xml.pptx"
            with zipfile.ZipFile(deck, "w") as archive:
                archive.writestr("many.xml", "<root><a/><b/><c/></root>")
                archive.writestr("deep.xml", "<a><b><c/></b></a>")
            with zipfile.ZipFile(deck) as archive:
                with patch.object(MODULE, "MAX_PPTX_XML_NODES", 3):
                    with self.assertRaisesRegex(MODULE.ExportError, "too many elements"):
                        MODULE.parse_ooxml_root(archive, "many.xml", "test part")
                with patch.object(MODULE, "MAX_PPTX_XML_DEPTH", 2):
                    with self.assertRaisesRegex(MODULE.ExportError, "too deeply nested"):
                        MODULE.parse_ooxml_root(archive, "deep.xml", "test part")

    def test_ooxml_parser_rejects_dtd_entities_before_expansion(self):
        with tempfile.TemporaryDirectory() as name:
            deck = Path(name) / "entity.pptx"
            bomb = (
                '<!DOCTYPE root [<!ENTITY a "1234567890">'
                '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
                '<root>&b;</root>'
            )
            with zipfile.ZipFile(deck, "w") as archive:
                archive.writestr("entity.xml", bomb)
            with zipfile.ZipFile(deck) as archive:
                with self.assertRaisesRegex(MODULE.ExportError, "forbidden DTD/entity"):
                    MODULE.parse_ooxml_root(archive, "entity.xml", "test part")

            utf16_deck = Path(name) / "entity-utf16.pptx"
            with zipfile.ZipFile(utf16_deck, "w") as archive:
                archive.writestr("entity.xml", bomb.encode("utf-16"))
            with zipfile.ZipFile(utf16_deck) as archive:
                with self.assertRaisesRegex(MODULE.ExportError, "UTF-16/32 or NUL"):
                    MODULE.parse_ooxml_root(archive, "entity.xml", "test part")

    def test_pptx_core_requires_exact_namespaced_content_type_override(self):
        presentation = presentation_xml([])
        relationships = presentation_relationships_xml([])
        exact_mime_in_comment = (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            f"<!-- {MODULE.PPTX_CONTENT_TYPE} --></Types>"
        )
        duplicate_override = content_types_xml().replace(
            "</Types>",
            '<Override PartName="/ppt/presentation.xml" '
            f'ContentType="{MODULE.PPTX_CONTENT_TYPE}"/></Types>',
        )
        cases = [
            ("missing", exact_mime_in_comment, "Override is missing"),
            (
                "wrong-mime",
                content_types_xml("application/octet-stream"),
                "invalid MIME type",
            ),
            ("duplicate", duplicate_override, "Override is duplicated"),
            (
                "wrong-namespace",
                '<Types><Override PartName="/ppt/presentation.xml" '
                f'ContentType="{MODULE.PPTX_CONTENT_TYPE}"/></Types>',
                "unexpected root element",
            ),
        ]
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for label, content_types, error in cases:
                with self.subTest(case=label):
                    deck = root / f"content-types-{label}.pptx"
                    with zipfile.ZipFile(deck, "w") as archive:
                        write_pptx_parts(
                            archive,
                            content_types=content_types,
                            presentation=presentation,
                            relationships=relationships,
                        )
                    with self.assertRaisesRegex(MODULE.ExportError, error):
                        MODULE.validate_pptx_archive(deck)

    def test_pptx_core_requires_package_relationship_and_real_slide_parts(self):
        slide_name = "ppt/slides/slide1.xml"
        valid_slide = (
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/'
            'presentationml/2006/main"><p:cSld><p:spTree/></p:cSld></p:sld>'
        )
        presentation = presentation_xml(["rId1"])
        relationships = presentation_relationships_xml(
            [
                (
                    "rId1",
                    MODULE.PRESENTATION_SLIDE_RELATIONSHIP_TYPE,
                    "slides/slide1.xml",
                    None,
                )
            ]
        )
        valid_content_types = content_types_xml(slide_names=[slide_name])
        wrong_slide_mime = valid_content_types.replace(
            MODULE.PRESENTATION_SLIDE_CONTENT_TYPE,
            "application/octet-stream",
        )
        cases = [
            (
                "missing-package-rels",
                valid_content_types,
                valid_slide,
                None,
                "missing required OOXML",
            ),
            (
                "wrong-office-document-target",
                valid_content_types,
                valid_slide,
                package_relationships_xml("ppt/other.xml"),
                "does not target ppt/presentation.xml",
            ),
            (
                "missing-slide-content-type",
                content_types_xml(),
                valid_slide,
                package_relationships_xml(),
                "slide content-type Override is missing",
            ),
            (
                "wrong-slide-content-type",
                wrong_slide_mime,
                valid_slide,
                package_relationships_xml(),
                "slide content-type Override has an invalid MIME",
            ),
            (
                "wrong-slide-namespace",
                valid_content_types,
                '<p:sld xmlns:p="urn:not-presentationml"><p:cSld/></p:sld>',
                package_relationships_xml(),
                "invalid PresentationML root",
            ),
            (
                "missing-direct-common-slide",
                valid_content_types,
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/'
                'presentationml/2006/main"><p:extLst/></p:sld>',
                package_relationships_xml(),
                "exactly one direct p:cSld",
            ),
            (
                "missing-direct-shape-tree",
                valid_content_types,
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/'
                'presentationml/2006/main"><p:cSld/></p:sld>',
                package_relationships_xml(),
                "exactly one direct p:spTree",
            ),
            (
                "duplicate-direct-shape-tree",
                valid_content_types,
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/'
                'presentationml/2006/main"><p:cSld><p:spTree/><p:spTree/>'
                '</p:cSld></p:sld>',
                package_relationships_xml(),
                "exactly one direct p:spTree",
            ),
            (
                "wrong-namespace-shape-tree",
                valid_content_types,
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/'
                'presentationml/2006/main" xmlns:x="urn:not-presentationml">'
                '<p:cSld><x:spTree/></p:cSld></p:sld>',
                package_relationships_xml(),
                "exactly one direct p:spTree",
            ),
        ]
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for label, types, slide_xml, package_rels, error in cases:
                with self.subTest(case=label):
                    deck = root / f"{label}.pptx"
                    with zipfile.ZipFile(deck, "w") as archive:
                        write_pptx_parts(
                            archive,
                            content_types=types,
                            presentation=presentation,
                            relationships=relationships,
                            slides=[(slide_name, slide_xml)],
                            package_relationships=package_rels,
                        )
                    with self.assertRaisesRegex(MODULE.ExportError, error):
                        MODULE.validate_pptx_archive(deck)

    def test_pptx_core_rejects_broken_slide_relationship_graphs(self):
        slide_xml = '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree/></p:cSld></p:sld>'
        slide1 = ("ppt/slides/slide1.xml", slide_xml)
        slide2 = ("ppt/slides/slide2.xml", slide_xml)
        slide_type = MODULE.PRESENTATION_SLIDE_RELATIONSHIP_TYPE
        non_slide_type = (
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide"
        )
        missing_rid_presentation = (
            '<p:presentation '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<p:sldIdLst><p:sldId id="256"/></p:sldIdLst></p:presentation>'
        )
        duplicate_rid_presentation = (
            '<p:presentation '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<p:sldIdLst><p:sldId id="256" r:id="rId1"/>'
            '<p:sldId id="257" r:id="rId1"/></p:sldIdLst></p:presentation>'
        )
        valid_relationship = ("rId1", slide_type, "slides/slide1.xml", None)
        cases = [
            (
                "missing-rid",
                missing_rid_presentation,
                [valid_relationship],
                [slide1],
                "missing its relationship r:id",
            ),
            (
                "duplicate-presentation-rid",
                duplicate_rid_presentation,
                [valid_relationship],
                [slide1],
                "duplicate r:id",
            ),
            (
                "duplicate-relationship-rid",
                presentation_xml(["rId1"]),
                [valid_relationship, valid_relationship],
                [slide1],
                "duplicate r:id",
            ),
            (
                "broken-rid",
                presentation_xml(["rIdMissing"]),
                [valid_relationship],
                [slide1],
                "r:id is broken",
            ),
            (
                "external",
                presentation_xml(["rId1"]),
                [("rId1", slide_type, "https://example.invalid/slide.xml", "External")],
                [slide1],
                "external relationship",
            ),
            (
                "non-slide-type",
                presentation_xml(["rId1"]),
                [("rId1", non_slide_type, "slides/slide1.xml", None)],
                [slide1],
                "non-slide relationship",
            ),
            (
                "outside-presentation-directory",
                presentation_xml(["rId1"]),
                [("rId1", slide_type, "../slides/slide1.xml", None)],
                [slide1],
                "outside the presentation directory",
            ),
            (
                "non-slide-target",
                presentation_xml(["rId1"]),
                [("rId1", slide_type, "notesSlides/notesSlide1.xml", None)],
                [slide1],
                "not a canonical slideN.xml part",
            ),
            (
                "missing-target",
                presentation_xml(["rId1"]),
                [("rId1", slide_type, "slides/slide2.xml", None)],
                [slide1],
                "target is missing",
            ),
            (
                "duplicate-slide-target",
                presentation_xml(["rId1", "rId2"]),
                [
                    valid_relationship,
                    ("rId2", slide_type, "slides/slide1.xml", None),
                ],
                [slide1],
                "slide part more than once",
            ),
            (
                "unreferenced-slide-relationship",
                presentation_xml(["rId1"]),
                [
                    valid_relationship,
                    ("rId2", slide_type, "slides/slide2.xml", None),
                ],
                [slide1, slide2],
                "unreferenced slide relationship",
            ),
            (
                "unreferenced-slide-part",
                presentation_xml(["rId1"]),
                [valid_relationship],
                [slide1, slide2],
                "unreferenced slide part",
            ),
        ]
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for label, presentation, relationships, slides, error in cases:
                with self.subTest(case=label):
                    deck = root / f"broken-{label}.pptx"
                    with zipfile.ZipFile(deck, "w") as archive:
                        write_pptx_parts(
                            archive,
                            content_types=content_types_xml(
                                slide_names=[name for name, _xml in slides]
                            ),
                            presentation=presentation,
                            relationships=presentation_relationships_xml(relationships),
                            slides=slides,
                        )
                    with self.assertRaisesRegex(MODULE.ExportError, error):
                        MODULE.validate_pptx_archive(deck)

    def test_pptx_archive_rejects_duplicate_and_oversized_members(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            duplicate = root / "duplicate.pptx"
            with zipfile.ZipFile(duplicate, "w") as archive:
                archive.writestr(
                    "[Content_Types].xml",
                    '<Types><Override PartName="/ppt/presentation.xml" '
                    f'ContentType="{MODULE.PPTX_CONTENT_TYPE}"/></Types>',
                )
                archive.writestr("ppt/presentation.xml", "one")
                with self.assertWarns(UserWarning):
                    archive.writestr("ppt/presentation.xml", "two")
            with self.assertRaisesRegex(MODULE.ExportError, "duplicate member"):
                MODULE.validate_pptx_archive(duplicate)

            oversized = root / "oversized.pptx"
            with zipfile.ZipFile(oversized, "w") as archive:
                archive.writestr(
                    "[Content_Types].xml",
                    '<Types><Override PartName="/ppt/presentation.xml" '
                    f'ContentType="{MODULE.PPTX_CONTENT_TYPE}"/></Types>',
                )
                archive.writestr("ppt/presentation.xml", "12345")
            with patch.object(MODULE, "MAX_PPTX_MEMBER_BYTES", 4):
                with self.assertRaisesRegex(MODULE.ExportError, "member exceeds"):
                    MODULE.validate_pptx_archive(oversized)

            oversized_rels = root / "oversized-relationships.pptx"
            slide_name = "ppt/slides/slide1.xml"
            relationships = presentation_relationships_xml(
                [
                    (
                        "rId1",
                        MODULE.PRESENTATION_SLIDE_RELATIONSHIP_TYPE,
                        "slides/slide1.xml",
                        None,
                    )
                ]
            ).replace("</Relationships>", f"{' ' * 2000}</Relationships>")
            with zipfile.ZipFile(oversized_rels, "w") as archive:
                write_pptx_parts(
                    archive,
                    content_types=content_types_xml(slide_names=[slide_name]),
                    presentation=presentation_xml(["rId1"]),
                    relationships=relationships,
                    slides=[
                        (
                            slide_name,
                            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree/></p:cSld></p:sld>',
                        )
                    ],
                )
            with patch.object(MODULE, "MAX_PPTX_XML_MEMBER_BYTES", 1024):
                with self.assertRaisesRegex(MODULE.ExportError, "XML member exceeds"):
                    MODULE.validate_pptx_archive(oversized_rels)

    def test_zip_member_budget_is_enforced_before_zipfile_materializes_entries(self):
        with tempfile.TemporaryDirectory() as name:
            deck = Path(name) / "many-members.pptx"
            with zipfile.ZipFile(deck, "w") as archive:
                for index in range(3):
                    archive.writestr(f"part-{index}.bin", b"")
            with deck.open("rb") as stream:
                with self.assertRaisesRegex(MODULE.ExportError, "more than 2 members"):
                    MODULE.preflight_zip_central_directory(
                        stream,
                        deck.stat().st_size,
                        max_members=2,
                        max_directory_bytes=1024 * 1024,
                        display_name=str(deck),
                    )

    def test_pptx_rejects_noncanonical_package_member_names(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for label, unsafe in (
                ("traversal", "../../outside.exe"),
                ("absolute", "/outside.exe"),
                ("backslash", "ppt\\outside.exe"),
            ):
                with self.subTest(label=label):
                    deck = root / f"unsafe-{label}.pptx"
                    stored_name = "ppt/outside.exe" if label == "backslash" else unsafe
                    with zipfile.ZipFile(deck, "w") as archive:
                        write_valid_pptx(
                            archive,
                            [
                                (
                                    "ppt/slides/slide1.xml",
                                    '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree/></p:cSld></p:sld>',
                                )
                            ],
                        )
                        archive.writestr(stored_name, b"payload")
                    if label == "backslash":
                        raw = deck.read_bytes()
                        self.assertEqual(raw.count(b"ppt/outside.exe"), 2)
                        deck.write_bytes(
                            raw.replace(b"ppt/outside.exe", b"ppt\\outside.exe")
                        )
                    with self.assertRaisesRegex(MODULE.ExportError, "unsafe member name"):
                        MODULE.validate_pptx_archive(deck)

    def test_run_command_captures_utf8_via_bounded_temp_file(self):
        process = MODULE.run_command(
            [
                MODULE.sys.executable,
                "-c",
                "import sys; sys.stdout.buffer.write('版本 0.33.2\\n'.encode('utf-8'))",
            ],
            timeout=5,
        )
        self.assertEqual(process.returncode, 0)
        self.assertIn("0.33.2", process.stdout)
        self.assertIn("版本", process.stdout)

    def test_run_command_kills_output_overflow_before_reading_it_all(self):
        with patch.object(MODULE, "MAX_COMMAND_OUTPUT_BYTES", 1024):
            with self.assertRaisesRegex(MODULE.ExportError, "output exceeded"):
                MODULE.run_command(
                    [
                        MODULE.sys.executable,
                        "-c",
                        "import sys; sys.stdout.buffer.write(b'x' * 100000)",
                    ],
                    timeout=5,
                )

    def test_find_download_ignores_files_older_than_since(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            old = root / "old.pptx"
            new = root / "new.pptx"
            for path in (old, new):
                with zipfile.ZipFile(path, "w") as archive:
                    write_valid_pptx(archive)

            older = time.time() - 60
            os.utime(old, (older, older))
            since = time.time() - 5
            found = MODULE.find_download([root], timeout=2.0, since=since)
            self.assertEqual(found.resolve(), new.resolve())

    def test_find_download_survives_files_vanishing_mid_scan(self):
        # Chrome renames "*.crdownload" files away between directory listing
        # and stat(); a vanished file must be skipped, not crash the export.
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            deck = root / "deck.pptx"
            with zipfile.ZipFile(deck, "w") as archive:
                write_valid_pptx(archive)
            ghost = root / "ghost.crdownload"
            ghost.write_bytes(b"partial download")

            real_stat = Path.stat
            seen = {"count": 0}

            def racy_stat(self, **kwargs):
                if self.name == "ghost.crdownload":
                    seen["count"] += 1
                    if seen["count"] > 1:
                        raise FileNotFoundError(2, "vanished mid-scan", str(self))
                return real_stat(self, **kwargs)

            with patch.object(Path, "stat", racy_stat):
                found = MODULE.find_download([root], timeout=2.0)
            self.assertEqual(found.resolve(), deck.resolve())

    def test_find_download_rejects_oversized_observed_file_immediately(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            oversized = root / "download.zip"
            oversized.write_bytes(b"12345")
            with self.assertRaisesRegex(MODULE.ExportError, "safety limit"):
                MODULE.find_download(
                    [root],
                    timeout=2.0,
                    accept=lambda _path: False,
                    maximum_bytes=4,
                )

    def test_browser_open_does_not_pass_download_path(self):
        session = MODULE.BrowserSession(
            "/bin/agent-browser",
            "test-session",
            Path("."),
            Path("/tmp/downloads"),
        )
        with patch.object(session, "run") as run:
            session.open("http://127.0.0.1:9/export_host.html")
        run.assert_called_once_with(
            ["open", "http://127.0.0.1:9/export_host.html"],
            timeout=90,
        )
        self.assertEqual(
            session.env["AGENT_BROWSER_DOWNLOAD_PATH"],
            str(Path("/tmp/downloads").resolve()),
        )

    def test_prepare_host_assets_copies_local_penpal_module(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            payload = {"id": "test", "pages": []}
            MODULE.prepare_host_assets(root, payload)
            self.assertEqual(
                (root / MODULE.HOST_TEMPLATE.name).read_bytes(),
                MODULE.HOST_TEMPLATE.read_bytes(),
            )
            self.assertEqual(
                (root / MODULE.PENPAL_MODULE.name).read_bytes(),
                MODULE.PENPAL_MODULE.read_bytes(),
            )
            self.assertEqual(
                MODULE.json.loads((root / "payload.json").read_text(encoding="utf-8")),
                payload,
            )

    def test_local_host_sends_restrictive_csp(self):
        self.assertIn("text/javascript", MODULE.QuietHandler.extensions_map[".mjs"])
        handler = object.__new__(MODULE.QuietHandler)
        headers = {}
        handler.send_header = lambda name, value: headers.__setitem__(name, value)
        with patch.object(
            MODULE.SimpleHTTPRequestHandler, "end_headers", return_value=None
        ):
            handler.end_headers()
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertIn(
            "frame-src https://www.kimi.com", headers["Content-Security-Policy"]
        )
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Cross-Origin-Resource-Policy"], "same-origin")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Pragma"], "no-cache")

    def test_local_host_rejects_forged_hosts_and_unlisted_paths(self):
        handler = object.__new__(MODULE.QuietHandler)
        handler.server = type(
            "Server",
            (),
            {
                "allowed_host": "127.0.0.1:43210",
                "capability_prefix": "/cap-test",
            },
        )()
        errors = []
        handler.send_error = lambda status, message: errors.append((status, message))

        handler.headers = {"Host": "attacker.example"}
        handler.path = "/cap-test/payload.json"
        with patch.object(MODULE.SimpleHTTPRequestHandler, "do_GET") as serve_get:
            handler.do_GET()
        self.assertEqual(errors[-1][0], 403)
        serve_get.assert_not_called()

        with patch.object(MODULE.SimpleHTTPRequestHandler, "do_HEAD") as serve_head:
            handler.do_HEAD()
        self.assertEqual(errors[-1][0], 403)
        serve_head.assert_not_called()

        handler.headers = {"Host": "127.0.0.1:43210"}
        handler.path = "/cap-test/payload.json"
        with patch.object(MODULE.SimpleHTTPRequestHandler, "do_GET") as serve_get:
            handler.do_GET()
        serve_get.assert_called_once_with()

        for forbidden in (
            "/",
            "/payload.json",
            "/cap-test/payload.json?probe=1",
            "/cap-test/unlisted.txt",
        ):
            with self.subTest(forbidden=forbidden):
                handler.path = forbidden
                with patch.object(MODULE.SimpleHTTPRequestHandler, "do_GET") as serve_get:
                    handler.do_GET()
                self.assertEqual(errors[-1][0], 404)
                serve_get.assert_not_called()

    def test_export_host_bounds_untrusted_rpc_payloads(self):
        source = MODULE.HOST_TEMPLATE.read_text(encoding="utf-8")
        self.assertNotIn("clipboard-read", source)
        self.assertNotIn("clipboard-write", source)
        for contract in (
            "const MAX_IMAGE_REQUESTS = 100",
            "const MAX_IMAGE_RESPONSE_BYTES = 100 * 1024 * 1024",
            "const MAX_LOCAL_IMAGE_PATH_CHARS = 4096",
            "const MAX_EXTERNAL_IMAGE_REFERENCE_BYTES = 16 * 1024",
            "Object.hasOwn(imageMap, normalized)",
            "responseBytes += utf8Bytes(resolved)",
            "const MAX_SAVE_CHANGES = 600",
            "const MAX_TEXT_BYTES = 20 * 1024 * 1024",
            "const MAX_SAVE_BYTES = 100 * 1024 * 1024",
            "const fileContent = validateSaveEcho(savePayload)",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, source)
        self.assertLess(
            source.index("if (paths.length > MAX_IMAGE_REQUESTS)"),
            source.index("const images = paths.map"),
        )
        self.assertLess(
            source.index("if (responseBytes > MAX_IMAGE_RESPONSE_BYTES)"),
            source.index("return images"),
        )

    def test_export_host_rpc_guards_fail_before_returning_oversized_data(self):
        node = MODULE.shutil.which("node")
        if node is None:
            self.skipTest("Node.js is unavailable for the host behavior contract")
        source = MODULE.HOST_TEMPLATE.read_text(encoding="utf-8")
        module_script = source.split('<script type="module">', 1)[1].split(
            "</script>", 1
        )[0]
        module_script = MODULE.re.sub(
            r'import\s*\{.*?\}\s*from\s*"\.\/penpal\.mjs";',
            "",
            module_script,
            flags=MODULE.re.DOTALL,
        )
        helpers = module_script.split("\n      try {", 1)[0]
        helpers = helpers.replace(
            "const MAX_IMAGE_REQUESTS = 100;",
            "const MAX_IMAGE_REQUESTS = 2;",
        ).replace(
            "const MAX_IMAGE_RESPONSE_BYTES = 100 * 1024 * 1024;",
            "const MAX_IMAGE_RESPONSE_BYTES = 10;",
        ).replace(
            "const MAX_LOCAL_IMAGE_PATH_CHARS = 4096;",
            "const MAX_LOCAL_IMAGE_PATH_CHARS = 4;",
        ).replace(
            "const MAX_TEXT_BYTES = 20 * 1024 * 1024;",
            "const MAX_TEXT_BYTES = 4;",
        ).replace(
            "const MAX_SAVE_BYTES = 100 * 1024 * 1024;",
            "const MAX_SAVE_BYTES = 6;",
        ).replace(
            "const MAX_SAVE_CHANGES = 600;",
            "const MAX_SAVE_CHANGES = 2;",
        )
        probe = r"""
const outcomes = {};
const expectThrow = (name, operation) => {
  try { operation(); outcomes[name] = false; }
  catch (_) { outcomes[name] = true; }
};
expectThrow('count', () => getBoundedImages({filePath: ['a', 'a', 'a']}, {a: 'x'}));
expectThrow('path', () => getBoundedImages({filePath: ['abcde']}, {}));
expectThrow('total', () => getBoundedImages({filePath: ['a', 'a']}, {a: '123456'}));
const inherited = Object.create({a: 'secret'});
outcomes.ownOnly = getBoundedImages({filePath: ['a']}, inherited)[0] === '';
expectThrow('saveCount', () => validateSaveEcho({changes: [{}, {}, {}]}));
expectThrow('saveFile', () => validateSaveEcho({changes: [{path: 'a', operate: 'put', content: '12345'}]}));
expectThrow('saveTotal', () => validateSaveEcho({changes: [{path: 'a', operate: 'put', content: '1234'}], fileContent: '123'}));
const rawFileContent = [{path: 'a', content: '1'}];
const echoedFileContent = validateSaveEcho({fileContent: rawFileContent});
outcomes.saveString = validateSaveEcho({fileContent: '1234'}) === '1234';
outcomes.saveArray = Array.isArray(echoedFileContent)
  && echoedFileContent !== rawFileContent
  && echoedFileContent[0] !== rawFileContent[0]
  && JSON.stringify(echoedFileContent) === JSON.stringify(rawFileContent);
expectThrow('saveArrayCount', () => validateSaveEcho({fileContent: [
  {path: 'a', content: ''},
  {path: 'b', content: ''},
  {path: 'c', content: ''},
]}));
expectThrow('saveArrayHole', () => validateSaveEcho({fileContent: new Array(1)}));
const expandedFileContent = [];
expandedFileContent.hidden = 'not allowed';
expectThrow('saveArrayField', () => validateSaveEcho({fileContent: expandedFileContent}));
expectThrow('saveArrayEntryField', () => validateSaveEcho({fileContent: [
  {path: 'a', content: '1', hidden: 'not allowed'},
]}));
expectThrow('saveArrayPath', () => validateSaveEcho({fileContent: [
  {path: 'abcde', content: ''},
]}));
expectThrow('saveArrayFile', () => validateSaveEcho({fileContent: [
  {path: 'a', content: '12345'},
]}));
expectThrow('saveArrayTotal', () => validateSaveEcho({fileContent: [
  {path: 'a', content: '1234'},
  {path: 'b', content: '1'},
]}));
process.stdout.write(JSON.stringify(outcomes));
"""
        process = MODULE.subprocess.run(
            [node, "-e", "globalThis.document={querySelector:()=>({})};\n" + helpers + probe],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(
            MODULE.json.loads(process.stdout),
            {
                "count": True,
                "path": True,
                "total": True,
                "ownOnly": True,
                "saveCount": True,
                "saveFile": True,
                "saveTotal": True,
                "saveString": True,
                "saveArray": True,
                "saveArrayCount": True,
                "saveArrayHole": True,
                "saveArrayField": True,
                "saveArrayEntryField": True,
                "saveArrayPath": True,
                "saveArrayFile": True,
                "saveArrayTotal": True,
            },
        )

    def test_ensure_debug_chrome_is_windows_only(self):
        with patch.object(MODULE.sys, "platform", "linux"):
            self.assertIsNone(MODULE.ensure_debug_chrome())

    @patch.object(MODULE, "cdp_alive", return_value=True)
    def test_ensure_debug_chrome_prefers_working_explicit_port(self, cdp_alive):
        with patch.object(MODULE.sys, "platform", "win32"), \
                patch.dict(MODULE.os.environ, {"AGENT_BROWSER_CDP": "9444"}):
            self.assertEqual(MODULE.ensure_debug_chrome(), 9444)
        cdp_alive.assert_called_once_with(9444)

    def test_automatic_debug_chrome_reuses_only_owned_profile_endpoint(self):
        with patch.object(MODULE.sys, "platform", "win32"), \
                patch.dict(MODULE.os.environ, {}, clear=True), \
                patch.object(
                    MODULE,
                    "windows_debug_profile_argument",
                    return_value=r"C:\KimiAutomation\ChromeData",
                ), \
                patch.object(MODULE, "owned_debug_chrome_port", return_value=9555) as owned, \
                patch.object(MODULE, "start_owned_debug_chrome") as start:
            self.assertEqual(MODULE.ensure_debug_chrome(), 9555)
        owned.assert_called_once_with(r"C:\KimiAutomation\ChromeData")
        start.assert_not_called()

    def test_automatic_debug_chrome_never_reuses_unknown_live_default_port(self):
        with tempfile.TemporaryDirectory() as name:
            executable = Path(name) / "chrome.exe"
            executable.write_bytes(b"placeholder")
            with patch.object(MODULE.sys, "platform", "win32"), \
                    patch.dict(MODULE.os.environ, {}, clear=True), \
                    patch.object(
                        MODULE,
                        "windows_debug_profile_argument",
                        return_value=r"C:\KimiAutomation\ChromeData",
                    ), \
                    patch.object(MODULE, "CHROME_CANDIDATES", (str(executable),)), \
                    patch.object(MODULE, "owned_debug_chrome_port", return_value=None), \
                    patch.object(MODULE, "cdp_alive", return_value=True) as unknown_live, \
                    patch.object(MODULE, "start_owned_debug_chrome", return_value=9666) as start:
                self.assertEqual(MODULE.ensure_debug_chrome(), 9666)
        unknown_live.assert_not_called()
        start.assert_called_once_with(
            str(executable),
            r"C:\KimiAutomation\ChromeData",
            allow_unowned_nonempty=False,
        )

    def test_owned_debug_chrome_marker_binds_profile_port_and_endpoint(self):
        with tempfile.TemporaryDirectory() as name:
            profile = str(Path(name) / "dedicated-profile")
            endpoint = "ws://127.0.0.1:9777/devtools/browser/owned-instance"
            MODULE._write_profile_marker(profile, 9777, endpoint)
            with patch.object(MODULE, "cdp_endpoint_identity", return_value=endpoint):
                self.assertEqual(MODULE.owned_debug_chrome_port(profile), 9777)
            with patch.object(
                MODULE,
                "cdp_endpoint_identity",
                return_value="ws://127.0.0.1:9777/devtools/browser/other-instance",
            ):
                self.assertIsNone(MODULE.owned_debug_chrome_port(profile))

    def test_windows_debug_profile_requires_a_dedicated_absolute_path(self):
        self.assertEqual(
            MODULE.windows_debug_profile_argument(r"C:\KimiAutomation\ChromeData"),
            r"C:\KimiAutomation\ChromeData",
        )
        for invalid in (
            "Default",
            "Profile 1",
            r"C:\Chrome\Default",
            r"C:\Chrome\Profile 2",
            r"C:\Users\me\AppData\Local\Google\Chrome\User Data",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(MODULE.ExportError, "dedicated"):
                    MODULE.windows_debug_profile_argument(invalid)

    def test_debug_chrome_refuses_a_nonempty_unowned_profile_directory(self):
        with tempfile.TemporaryDirectory() as name:
            profile = Path(name) / "daily-user-data"
            profile.mkdir()
            (profile / "Cookies").write_text("daily", encoding="utf-8")
            with patch.object(MODULE.subprocess, "Popen") as launch:
                with self.assertRaisesRegex(MODULE.ExportError, "non-empty.*ownership"):
                    MODULE.start_owned_debug_chrome("chrome.exe", str(profile))
            launch.assert_not_called()

    def test_explicit_dedicated_profile_may_be_prepopulated_for_login(self):
        with tempfile.TemporaryDirectory() as name:
            executable = Path(name) / "chrome.exe"
            executable.write_bytes(b"placeholder")
            profile = r"C:\KimiAutomation\signed-in-dedicated"
            with patch.object(MODULE.sys, "platform", "win32"), \
                    patch.dict(
                        MODULE.os.environ,
                        {"AGENT_BROWSER_PROFILE": profile},
                        clear=True,
                    ), \
                    patch.object(MODULE, "CHROME_CANDIDATES", (str(executable),)), \
                    patch.object(MODULE, "owned_debug_chrome_port", return_value=None), \
                    patch.object(MODULE, "start_owned_debug_chrome", return_value=9888) as start:
                self.assertEqual(MODULE.ensure_debug_chrome(), 9888)
            start.assert_called_once_with(
                str(executable),
                profile,
                allow_unowned_nonempty=True,
            )

    def test_browser_session_exports_cdp_port_to_env(self):
        with patch.dict(MODULE.os.environ, {}, clear=False):
            MODULE.os.environ.pop("AGENT_BROWSER_CDP", None)
            with_port = MODULE.BrowserSession(
                "/bin/agent-browser", "s", Path("."), Path("/tmp/d"), cdp_port=9444
            )
            self.assertEqual(with_port.env["AGENT_BROWSER_CDP"], "9444")
            without_port = MODULE.BrowserSession(
                "/bin/agent-browser", "s", Path("."), Path("/tmp/d")
            )
            self.assertNotIn("AGENT_BROWSER_CDP", without_port.env)

    def test_browser_session_preserves_profile_and_state_auth_configuration(self):
        with patch.dict(
            MODULE.os.environ,
            {
                "AGENT_BROWSER_PROFILE": r"C:\KimiAutomation\ChromeData",
                "AGENT_BROWSER_STATE": r"C:\KimiAutomation\state.json",
            },
            clear=False,
        ):
            session = MODULE.BrowserSession(
                "/bin/agent-browser", "s", Path("."), Path("/tmp/d")
            )
        self.assertEqual(
            session.env["AGENT_BROWSER_PROFILE"],
            r"C:\KimiAutomation\ChromeData",
        )
        self.assertEqual(
            session.env["AGENT_BROWSER_STATE"],
            r"C:\KimiAutomation\state.json",
        )

    def test_iframe_evaluation_awaits_promises_without_exposing_proxy(self):
        class FakeConnection:
            def __init__(self):
                self.sent = []
                self.responses = [
                    {
                        "id": 1,
                        "result": {
                            "targetInfos": [
                                {
                                    "targetId": "target-1",
                                    "type": "iframe",
                                    "url": "https://www.kimi.com/neo-ppt/editor",
                                }
                            ]
                        },
                    },
                    {"id": 2, "result": {"sessionId": "session-1"}},
                    {
                        "id": 3,
                        "result": {"result": {"value": {"ready": True}}},
                    },
                ]
                self.closed = False

            def send(self, payload):
                self.sent.append(MODULE.json.loads(payload))

            def recv(self):
                return MODULE.json.dumps(self.responses.pop(0))

            def close(self):
                self.closed = True

        connection = FakeConnection()

        class FakeWebsocket:
            @staticmethod
            def create_connection(*_args, **_kwargs):
                self.assertNotIn("HTTPS_PROXY", MODULE.os.environ)
                return connection

        with patch.object(MODULE, "ensure_websocket", return_value=FakeWebsocket), \
                patch.dict(MODULE.os.environ, {"HTTPS_PROXY": "http://proxy"}):
            value = MODULE.evaluate_in_iframe(
                "ws://127.0.0.1/devtools/browser/test",
                MODULE.OOPIF_URL_HINT,
                "Promise.resolve({ready: true})",
            )
            self.assertEqual(MODULE.os.environ["HTTPS_PROXY"], "http://proxy")

        self.assertEqual(value, {"ready": True})
        runtime = connection.sent[-1]
        self.assertEqual(runtime["sessionId"], "session-1")
        self.assertTrue(runtime["params"]["awaitPromise"])
        self.assertTrue(runtime["params"]["returnByValue"])
        self.assertTrue(connection.closed)

    def test_iframe_evaluation_rejects_ambiguous_kimi_targets(self):
        class FakeConnection:
            def __init__(self):
                self.sent = []
                self.responses = [
                    {
                        "id": 1,
                        "result": {
                            "targetInfos": [
                                {
                                    "targetId": "target-1",
                                    "type": "iframe",
                                    "url": "https://www.kimi.com/neo-ppt/editor",
                                },
                                {
                                    "targetId": "target-2",
                                    "type": "iframe",
                                    "url": "https://www.kimi.com/neo-ppt/editor",
                                },
                            ]
                        },
                    }
                ]
                self.closed = False

            def send(self, payload):
                self.sent.append(MODULE.json.loads(payload))

            def recv(self):
                return MODULE.json.dumps(self.responses.pop(0))

            def close(self):
                self.closed = True

        connection = FakeConnection()

        class FakeWebsocket:
            @staticmethod
            def create_connection(*_args, **_kwargs):
                return connection

        with patch.object(MODULE, "ensure_websocket", return_value=FakeWebsocket):
            with self.assertRaisesRegex(MODULE.ExportError, "ambiguous CDP attachment"):
                MODULE.evaluate_in_iframe(
                    "ws://127.0.0.1/devtools/browser/test",
                    MODULE.OOPIF_URL_HINT,
                    "true",
                )

        self.assertEqual([request["method"] for request in connection.sent], ["Target.getTargets"])
        self.assertTrue(connection.closed)

    def test_iframe_evaluation_rejects_hostile_substring_target_urls(self):
        hostile_urls = (
            "https://evil.example/kimi.com/neo-ppt",
            "https://evil.example/frame?next=https://www.kimi.com/neo-ppt/editor",
            "http://www.kimi.com/neo-ppt/editor",
            "https://www.kimi.com/neo-pptish/editor",
            "https://www.kimi.com:444/neo-ppt/editor",
            "https://user@www.kimi.com/neo-ppt/editor",
        )

        for hostile_url in hostile_urls:
            with self.subTest(url=hostile_url):
                class FakeConnection:
                    def __init__(self):
                        self.sent = []
                        self.responses = [{
                            "id": 1,
                            "result": {"targetInfos": [{
                                "targetId": "hostile",
                                "type": "iframe",
                                "url": hostile_url,
                            }]},
                        }]
                        self.closed = False

                    def send(self, payload):
                        self.sent.append(MODULE.json.loads(payload))

                    def recv(self):
                        return MODULE.json.dumps(self.responses.pop(0))

                    def close(self):
                        self.closed = True

                connection = FakeConnection()

                class FakeWebsocket:
                    @staticmethod
                    def create_connection(*_args, **_kwargs):
                        return connection

                with patch.object(MODULE, "ensure_websocket", return_value=FakeWebsocket):
                    with self.assertRaisesRegex(MODULE.ExportError, "no browser target"):
                        MODULE.evaluate_in_iframe(
                            "ws://127.0.0.1/devtools/browser/test",
                            MODULE.OOPIF_URL_HINT,
                            "true",
                        )

                self.assertEqual(
                    [request["method"] for request in connection.sent],
                    ["Target.getTargets"],
                )
                self.assertTrue(connection.closed)

        self.assertTrue(
            MODULE.is_kimi_writer_target_url(
                "https://www.kimi.com:443/neo-ppt/editor"
            )
        )

    def test_iframe_evaluation_wraps_transport_errors_and_closes_cleanly(self):
        class BrokenWebsocket:
            @staticmethod
            def create_connection(*_args, **_kwargs):
                raise RuntimeError("socket disappeared")

        with patch.object(MODULE, "ensure_websocket", return_value=BrokenWebsocket):
            with self.assertRaisesRegex(MODULE.ExportError, "connect to the browser CDP"):
                MODULE.evaluate_in_iframe("ws://test", MODULE.OOPIF_URL_HINT, "true")

        class InvalidJsonConnection:
            closed = False

            def send(self, _payload):
                pass

            def recv(self):
                return "not-json"

            def close(self):
                self.closed = True

        connection = InvalidJsonConnection()

        class InvalidJsonWebsocket:
            @staticmethod
            def create_connection(*_args, **_kwargs):
                return connection

        with patch.object(MODULE, "ensure_websocket", return_value=InvalidJsonWebsocket):
            with self.assertRaisesRegex(MODULE.ExportError, "iframe communication failed"):
                MODULE.evaluate_in_iframe("ws://test", MODULE.OOPIF_URL_HINT, "true")
        self.assertTrue(connection.closed)

        class SimulatedWebsocketTimeout(Exception):
            pass

        class TimeoutConnection(InvalidJsonConnection):
            def recv(self):
                raise SimulatedWebsocketTimeout("writer main thread is busy")

        timeout_connection = TimeoutConnection()

        class TimeoutWebsocket:
            WebSocketTimeoutException = SimulatedWebsocketTimeout

            @staticmethod
            def create_connection(*_args, **_kwargs):
                return timeout_connection

        with patch.object(MODULE, "ensure_websocket", return_value=TimeoutWebsocket):
            with self.assertRaisesRegex(
                MODULE.CdpOperationTimeout,
                "operation timed out",
            ):
                MODULE.evaluate_in_iframe("ws://test", MODULE.OOPIF_URL_HINT, "true")
        self.assertTrue(timeout_connection.closed)

    def test_iframe_evaluation_never_reselects_a_replacement_target(self):
        class FakeConnection:
            def __init__(self):
                self.sent = []
                self.responses = [
                    {
                        "id": 1,
                        "result": {
                            "targetInfos": [
                                {
                                    "targetId": "other-task",
                                    "type": "iframe",
                                    "url": "https://www.kimi.com/neo-ppt/editor",
                                }
                            ]
                        },
                    }
                ]
                self.closed = False

            def send(self, payload):
                self.sent.append(MODULE.json.loads(payload))

            def recv(self):
                return MODULE.json.dumps(self.responses.pop(0))

            def close(self):
                self.closed = True

        connection = FakeConnection()

        class FakeWebsocket:
            @staticmethod
            def create_connection(*_args, **_kwargs):
                return connection

        with patch.object(MODULE, "ensure_websocket", return_value=FakeWebsocket):
            with self.assertRaisesRegex(MODULE.ExportError, "replacement iframe"):
                MODULE.evaluate_in_iframe(
                    "ws://127.0.0.1/devtools/browser/test",
                    MODULE.OOPIF_URL_HINT,
                    "true",
                    target_id="original-task",
                )

        self.assertEqual([request["method"] for request in connection.sent], ["Target.getTargets"])
        self.assertTrue(connection.closed)

    def test_pptx_hook_state_reports_auth_and_writer_failures_immediately(self):
        for status in (401, 403):
            with self.subTest(status=status):
                with self.assertRaisesRegex(
                    MODULE.ExportError,
                    rf"HTTP {status}.*AGENT_BROWSER_PROFILE.*AGENT_BROWSER_STATE",
                ):
                    MODULE.classify_pptx_hook_state(
                        {"installed": True, "signatureStatus": status}
                    )
        with self.assertRaisesRegex(MODULE.ExportError, "HTTP 500"):
            MODULE.classify_pptx_hook_state(
                {"installed": True, "signatureStatus": 500}
            )
        with self.assertRaisesRegex(MODULE.ExportError, "signature request was rejected"):
            MODULE.classify_pptx_hook_state(
                {"installed": True, "signatureRejected": True}
            )
        self.assertIsNone(
            MODULE.classify_pptx_hook_state(
                {"installed": True, "rejected": True}
            )
        )
        with self.assertRaisesRegex(MODULE.ExportError, "no longer available"):
            MODULE.classify_pptx_hook_state({"installed": False})

    def test_pptx_hook_state_validates_blob_metadata(self):
        self.assertIn("HTMLAnchorElement.prototype.click", MODULE.PPTX_BLOB_HOOK_INSTALL_JS)
        self.assertIn(
            "HTMLAnchorElement.prototype.dispatchEvent",
            MODULE.PPTX_BLOB_HOOK_INSTALL_JS,
        )
        self.assertIn(
            "captureAnchorDownload(this, 'dispatch-event')",
            MODULE.PPTX_BLOB_HOOK_INSTALL_JS,
        )
        self.assertIn("endsWith('.pptx')", MODULE.PPTX_BLOB_HOOK_INSTALL_JS)
        self.assertIn("blobsByUrl", MODULE.PPTX_BLOB_CLEANUP_JS)
        self.assertIn(
            "state.originalAnchorDispatchEvent",
            MODULE.PPTX_BLOB_CLEANUP_JS,
        )
        self.assertNotIn("unhandledrejection", MODULE.PPTX_BLOB_HOOK_INSTALL_JS)
        self.assertEqual(
            MODULE.classify_pptx_hook_state(
                {
                    "installed": True,
                    "signatureStatus": 200,
                    "blob": {
                        "size": 123,
                        "mime": MODULE.PPTX_PACKAGE_MIME + "; charset=binary",
                    },
                }
            ),
            (123, MODULE.PPTX_PACKAGE_MIME),
        )
        self.assertIsNone(MODULE.classify_pptx_hook_state({"installed": True}))
        for blob, message in (
            ({"size": 0, "mime": MODULE.PPTX_PACKAGE_MIME}, "empty"),
            (
                {
                    "size": MODULE.MAX_PPTX_ARCHIVE_BYTES + 1,
                    "mime": MODULE.PPTX_PACKAGE_MIME,
                },
                "safety limit",
            ),
            ({"size": 1, "mime": "text/html"}, "unsupported"),
            ({"size": "1", "mime": MODULE.PPTX_PACKAGE_MIME}, "size"),
        ):
            with self.subTest(blob=blob):
                with self.assertRaisesRegex(MODULE.ExportError, message):
                    MODULE.classify_pptx_hook_state(
                        {"installed": True, "blob": blob}
                    )

    def test_pptx_hook_diagnostics_are_bounded_and_credential_free(self):
        diagnostics = MODULE.pptx_hook_diagnostics(
            {
                "lastStage": "signature-completed",
                "progressText": "  正在导出   95%  ",
                "signatureStatus": 200,
                "signatureRequestCount": 1,
                "objectUrlCount": 3,
                "pptxBlobCandidateCount": 1,
                "downloadSignalCount": 1,
                "captureSignal": "dispatch-event",
                "accessToken": "must-not-appear",
            }
        )
        self.assertIn("stage=signature-completed", diagnostics)
        self.assertIn("signature=http-200", diagnostics)
        self.assertIn("pptxBlobCandidates=1", diagnostics)
        self.assertIn("captureSignal=dispatch-event", diagnostics)
        self.assertNotIn("must-not-appear", diagnostics)

        hostile = MODULE.pptx_hook_diagnostics(
            {
                "lastStage": "bad\nsecret",
                "progressText": "x" * 500,
                "signatureRequestCount": -1,
                "objectUrlCount": "many",
            }
        )
        self.assertIn("stage=unknown", hostile)
        self.assertIn("signatureRequests=unknown", hostile)
        self.assertIn("objectUrls=unknown", hostile)
        self.assertLess(len(hostile), 500)

    def test_wait_for_pptx_blob_handles_pending_ready_auth_and_timeout(self):
        target = MODULE.IframeTarget("ws://test", "target-1")
        pending = {"installed": True, "blob": None}
        ready = {
            "installed": True,
            "blob": {"size": 5, "mime": MODULE.PPTX_PACKAGE_MIME},
        }
        with patch.object(
            MODULE,
            "evaluate_in_iframe",
            side_effect=[pending, ready],
        ), patch.object(MODULE.time, "sleep"):
            self.assertEqual(MODULE.wait_for_pptx_blob(target, timeout=2), (5, MODULE.PPTX_PACKAGE_MIME))

        with patch.object(
            MODULE,
            "evaluate_in_iframe",
            side_effect=[MODULE.CdpOperationTimeout("busy"), ready],
        ), patch.object(MODULE.time, "sleep"):
            self.assertEqual(
                MODULE.wait_for_pptx_blob(target, timeout=2),
                (5, MODULE.PPTX_PACKAGE_MIME),
            )

        auth = {"installed": True, "signatureStatus": 401}
        with patch.object(MODULE, "evaluate_in_iframe", return_value=auth), \
                patch.object(MODULE.time, "sleep") as sleep:
            with self.assertRaisesRegex(MODULE.ExportError, "signed-in Kimi"):
                MODULE.wait_for_pptx_blob(target, timeout=2)
        sleep.assert_not_called()

        with patch.object(MODULE.time, "monotonic", side_effect=[0.0, 1.0]), \
                patch.object(MODULE, "evaluate_in_iframe") as evaluate:
            with self.assertRaisesRegex(MODULE.ExportError, "neither a Blob"):
                MODULE.wait_for_pptx_blob(target, timeout=0.5)
        evaluate.assert_not_called()

        stalled = {
            "installed": True,
            "lastStage": "signature-completed",
            "progressText": "正在导出 95%",
            "signatureStatus": 200,
            "signatureRequestCount": 1,
            "objectUrlCount": 0,
            "pptxBlobCandidateCount": 0,
            "downloadSignalCount": 0,
            "blob": None,
        }
        with patch.object(MODULE.time, "monotonic", side_effect=[0.0, 2.0]), \
                patch.object(MODULE.time, "sleep"), \
                patch.object(MODULE, "evaluate_in_iframe", return_value=stalled):
            with self.assertRaisesRegex(
                MODULE.PptxWriterTimeout,
                r"signature=http-200.*pptxBlobCandidates=0",
            ):
                MODULE.wait_for_pptx_blob(target, timeout=1.0, deadline=1.0)

    def test_blob_chunk_expression_enforces_fixed_chunk_bounds(self):
        self.assertEqual(MODULE.PPTX_BLOB_CHUNK_BYTES, 1024 * 1024)
        expression = MODULE.pptx_blob_chunk_expression(
            7,
            MODULE.PPTX_BLOB_CHUNK_BYTES,
        )
        self.assertIn(f"const offset = 7", expression)
        self.assertIn("await state.blob.slice", expression)
        for offset, length in (
            (-1, 1),
            (0, 0),
            (0, MODULE.PPTX_BLOB_CHUNK_BYTES + 1),
        ):
            with self.subTest(offset=offset, length=length):
                with self.assertRaisesRegex(MODULE.ExportError, "chunk request"):
                    MODULE.pptx_blob_chunk_expression(offset, length)

    def test_blob_capture_writes_exact_base64_chunks_to_existing_private_file(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            staged = root / ".deck.staging"
            identity = MODULE.create_private_staging_file(staged)
            chunks = [b"abcd", b"efg"]
            responses = [
                {
                    "offset": 0,
                    "length": 4,
                    "base64": MODULE.base64.b64encode(chunks[0]).decode("ascii"),
                },
                {
                    "offset": 4,
                    "length": 3,
                    "base64": MODULE.base64.b64encode(chunks[1]).decode("ascii"),
                },
            ]
            with patch.object(MODULE, "PPTX_BLOB_CHUNK_BYTES", 4), patch.object(
                MODULE,
                "evaluate_in_iframe",
                side_effect=responses,
            ) as evaluate:
                written = MODULE.write_pptx_blob_to_staged_file(
                    MODULE.IframeTarget("ws://test", "target-1"),
                    staged,
                    identity,
                    7,
                )

            self.assertEqual(written, 7)
            self.assertEqual(staged.read_bytes(), b"abcdefg")
            self.assertEqual(evaluate.call_count, 2)

    def test_blob_capture_retries_a_transient_cdp_read_timeout(self):
        with tempfile.TemporaryDirectory() as name:
            staged = Path(name) / ".deck.staging"
            identity = MODULE.create_private_staging_file(staged)
            response = {
                "offset": 0,
                "length": 1,
                "base64": MODULE.base64.b64encode(b"x").decode("ascii"),
            }
            with patch.object(
                MODULE,
                "evaluate_in_iframe",
                side_effect=[MODULE.CdpOperationTimeout("busy"), response],
            ) as evaluate:
                written = MODULE.write_pptx_blob_to_staged_file(
                    MODULE.IframeTarget("ws://test", "target-1"),
                    staged,
                    identity,
                    1,
                    deadline=MODULE.time.monotonic() + 2,
                )

            self.assertEqual(written, 1)
            self.assertEqual(staged.read_bytes(), b"x")
            self.assertEqual(evaluate.call_count, 2)

    def test_blob_capture_rejects_corruption_and_changed_staging_identity(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            staged = root / ".deck.staging"
            identity = MODULE.create_private_staging_file(staged)
            with patch.object(
                MODULE,
                "evaluate_in_iframe",
                return_value={"offset": 0, "length": 1, "base64": "%%%"},
            ):
                with self.assertRaisesRegex(MODULE.ExportError, "invalid base64"):
                    MODULE.write_pptx_blob_to_staged_file(
                        MODULE.IframeTarget("ws://test", "target-1"), staged, identity, 1
                    )

            staged.unlink()
            staged.write_text("concurrent", encoding="utf-8")
            with patch.object(MODULE, "evaluate_in_iframe") as evaluate:
                with self.assertRaisesRegex(MODULE.ExportError, "staging file changed"):
                    MODULE.write_pptx_blob_to_staged_file(
                        MODULE.IframeTarget("ws://test", "target-1"), staged, identity, 1
                    )
            evaluate.assert_not_called()
            self.assertEqual(staged.read_text(encoding="utf-8"), "concurrent")

    @unittest.skipIf(os.name == "nt", "Windows denies renaming an open file")
    def test_blob_capture_detects_path_swap_after_verified_descriptor_open(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            staged = root / ".deck.staging"
            identity = MODULE.create_private_staging_file(staged)
            displaced = root / "displaced"

            def swap_after_open(*_args, **_kwargs):
                staged.replace(displaced)
                staged.write_text("concurrent", encoding="utf-8")
                return {
                    "offset": 0,
                    "length": 1,
                    "base64": MODULE.base64.b64encode(b"x").decode("ascii"),
                }

            with patch.object(
                MODULE,
                "evaluate_in_iframe",
                side_effect=swap_after_open,
            ):
                with self.assertRaisesRegex(MODULE.ExportError, "path changed after"):
                    MODULE.write_pptx_blob_to_staged_file(
                        MODULE.IframeTarget("ws://test", "target-1"),
                        staged,
                        identity,
                        1,
                    )

            self.assertEqual(staged.read_text(encoding="utf-8"), "concurrent")
            self.assertEqual(displaced.read_bytes(), b"x")

    def test_partial_blob_attempt_can_be_safely_reset_with_a_fresh_snapshot(self):
        with tempfile.TemporaryDirectory() as name:
            staged = Path(name) / ".deck.staging"
            original = MODULE.create_private_staging_file(staged)
            staged.write_bytes(b"partial blob")
            partial = MODULE.capture_path_snapshot(staged)
            self.assertTrue(MODULE._same_file_identity(original, partial))

            reset = MODULE.reset_private_staging_file(staged, partial)
            self.assertEqual(staged.read_bytes(), b"")
            descriptor = MODULE._open_verified_private_file(
                staged,
                reset,
                writable=True,
                require_exact_snapshot=True,
            )
            MODULE.os.close(descriptor)

    def test_blob_capture_deadline_fails_before_mutating_private_staging(self):
        with tempfile.TemporaryDirectory() as name:
            staged = Path(name) / ".deck.staging"
            identity = MODULE.create_private_staging_file(staged)
            with patch.object(MODULE, "evaluate_in_iframe") as evaluate:
                with self.assertRaisesRegex(MODULE.PptxWriterTimeout, "deadline"):
                    MODULE.write_pptx_blob_to_staged_file(
                        MODULE.IframeTarget("ws://test", "target-1"),
                        staged,
                        identity,
                        1,
                        deadline=MODULE.time.monotonic() - 1,
                    )
            evaluate.assert_not_called()
            self.assertTrue(MODULE.snapshots_match(
                MODULE.capture_path_snapshot(staged), identity
            ))

    def test_install_blob_hook_falls_back_only_when_cdp_is_unavailable(self):
        browser = object()
        with patch.object(
            MODULE,
            "browser_cdp_url",
            side_effect=MODULE.ExportError("unavailable"),
        ), patch.object(MODULE, "evaluate_in_iframe") as evaluate:
            self.assertIsNone(MODULE.install_pptx_blob_hook(browser))
        evaluate.assert_not_called()

        external_browser = type(
            "ExternalBrowser", (), {"requires_cdp_capture": True}
        )()
        with patch.object(
            MODULE,
            "browser_cdp_url",
            side_effect=MODULE.ExportError("unavailable"),
        ), patch.object(MODULE, "evaluate_in_iframe") as evaluate:
            with self.assertRaisesRegex(MODULE.ExportError, "external CDP"):
                MODULE.install_pptx_blob_hook(external_browser)
        evaluate.assert_not_called()

        with patch.object(MODULE, "browser_cdp_url", return_value="ws://test"), \
                patch.object(
                    MODULE,
                    "evaluate_in_iframe",
                    return_value=("target-1", {"installed": True}),
                ) as evaluate:
            self.assertEqual(
                MODULE.install_pptx_blob_hook(browser),
                MODULE.IframeTarget("ws://test", "target-1"),
            )
        self.assertEqual(evaluate.call_count, 1)

        with patch.object(MODULE, "browser_cdp_url", return_value="ws://test"), \
                patch.object(
                    MODULE,
                    "evaluate_in_iframe",
                    side_effect=MODULE.ExportError("ambiguous CDP attachment"),
                ):
            with self.assertRaisesRegex(MODULE.ExportError, "ambiguous CDP attachment"):
                MODULE.install_pptx_blob_hook(browser)

    def test_build_image_map_rejects_symlink_outside_project(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            project = root / "project"
            project.mkdir()
            outside = root / "outside.txt"
            outside.write_text("secret", encoding="utf-8")
            link = project / "leak.png"
            try:
                link.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"symlinks are unavailable: {exc}")
            with self.assertRaisesRegex(MODULE.ExportError, "escapes the PPTD"):
                MODULE.build_image_map(project, ["leak.png"])

    def test_prepare_host_assets_ascii_escapes_surrogate_payload_values(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            MODULE.prepare_host_assets(directory, {"title": "\ud800"})
            raw = (directory / "payload.json").read_bytes()
            self.assertIn(b"\\ud800", raw)
            self.assertEqual(MODULE.json.loads(raw.decode("ascii"))["title"], "\ud800")

    def test_build_image_map_embeds_only_declared_dependencies(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name) / "project"
            media = project / "media"
            qa = project / ".qa-images"
            media.mkdir(parents=True)
            qa.mkdir()
            (media / "hero.png").write_bytes(b"hero")
            (media / "private.png").write_bytes(b"private")
            (qa / "overview.jpg").write_bytes(b"qa")

            image_map = MODULE.build_image_map(project, ["media/hero.png"])
            self.assertEqual(set(image_map), {"media/hero.png"})
            self.assertNotIn("media/private.png", image_map)
            self.assertNotIn(".qa-images/overview.jpg", image_map)

    def test_build_image_map_uses_browser_visible_aliases_and_rejects_parent_paths(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name) / "project"
            media = project / "media"
            media.mkdir(parents=True)
            (media / "hero.png").write_bytes(b"hero")

            image_map = MODULE.build_image_map(project, ["./media//hero.png"])
            self.assertEqual(set(image_map), {"media/hero.png"})
            with self.assertRaisesRegex(MODULE.ExportError, "parent traversal"):
                MODULE.build_image_map(project, ["pages/../media/hero.png"])
            for invalid in ("bad\0.png", "bad\ud800.png"):
                with self.subTest(invalid=repr(invalid)):
                    with self.assertRaisesRegex(MODULE.ExportError, "unsupported character"):
                        MODULE.build_image_map(project, [invalid])

    def test_safe_project_path_wraps_invalid_manifest_path_characters(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name)
            for invalid in ("pages/bad\0.page", "pages/bad\ud800.page"):
                with self.subTest(invalid=repr(invalid)):
                    with self.assertRaisesRegex(MODULE.ExportError, "unsupported character"):
                        MODULE.safe_project_path(project, invalid)

    def test_build_image_map_preserves_an_internal_symlink_alias(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name) / "project"
            media = project / "media"
            assets = project / "assets"
            media.mkdir(parents=True)
            assets.mkdir()
            (assets / "hero").write_bytes(b"hero")
            link = media / "hero.png"
            try:
                link.symlink_to(assets / "hero")
            except OSError as exc:
                self.skipTest(f"symlinks are unavailable: {exc}")

            image_map = MODULE.build_image_map(project, ["media/hero.png"])
            self.assertEqual(set(image_map), {"media/hero.png"})
            self.assertTrue(image_map["media/hero.png"].startswith("data:image/png;base64,"))

    def test_build_image_map_preserves_an_internal_directory_symlink(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name) / "project"
            assets = project / "assets"
            project.mkdir()
            assets.mkdir()
            (assets / "hero.png").write_bytes(b"hero")
            try:
                (project / "media").symlink_to(assets, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlinks are unavailable: {exc}")
            image_map = MODULE.build_image_map(project, ["media/hero.png"])
            self.assertEqual(set(image_map), {"media/hero.png"})

    def test_manifest_ancestor_swap_is_rejected_after_directory_is_anchored(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            project = root / "project"
            pages = project / "pages"
            pages.mkdir(parents=True)
            manifest = project / "deck.pptd"
            manifest.write_text(
                "version: v2\ntitle: safe\npages: [pages/01.page]\n",
                encoding="utf-8",
            )
            (pages / "01.page").write_text("elements: []\n", encoding="utf-8")
            outside = root / "outside"
            outside_pages = outside / "pages"
            outside_pages.mkdir(parents=True)
            (outside / "deck.pptd").write_text(
                "version: v2\ntitle: SECRET\npages: [pages/01.page]\n",
                encoding="utf-8",
            )
            (outside_pages / "01.page").write_text(
                "elements: [{type: text, value: SECRET}]\n",
                encoding="utf-8",
            )
            selected = MODULE.find_manifest(project)
            displaced = root / "project-displaced"
            real_read = MODULE.read_yaml_mapping
            swapped = {"done": False}

            def swap_before_manifest_read(path):
                if not swapped["done"]:
                    swapped["done"] = True
                    project.rename(displaced)
                    project.symlink_to(outside, target_is_directory=True)
                return real_read(path)

            try:
                with patch.object(
                    MODULE,
                    "read_yaml_mapping",
                    side_effect=swap_before_manifest_read,
                ):
                    with self.assertRaisesRegex(
                        MODULE.ExportError,
                        "directory path changed|escapes the PPTD directory",
                    ):
                        MODULE.build_payload(selected)
            except OSError as exc:
                self.skipTest(f"symlink ancestor exchange is unavailable: {exc}")

    def test_manifest_parent_swap_between_discovery_and_anchoring_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            project = root / "project"
            project.mkdir()
            manifest = project / "deck.pptd"
            manifest.write_text(
                "version: v2\npages: [page.page]\n",
                encoding="utf-8",
            )
            (project / "page.page").write_text("elements: []\n", encoding="utf-8")
            outside = root / "outside"
            outside.mkdir()
            (outside / "deck.pptd").write_text(
                "version: v2\ntitle: SECRET\npages: [page.page]\n",
                encoding="utf-8",
            )
            (outside / "page.page").write_text(
                "elements: [{type: text, value: SECRET}]\n",
                encoding="utf-8",
            )
            selected = MODULE.find_manifest(project)
            displaced = root / "project-displaced"
            try:
                project.rename(displaced)
                project.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlink ancestor exchange is unavailable: {exc}")
            with self.assertRaisesRegex(MODULE.ExportError, "after manifest discovery"):
                MODULE.build_payload(selected)

    def test_page_ancestor_swap_cannot_redirect_anchored_read(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            project = root / "project"
            pages = project / "pages"
            pages.mkdir(parents=True)
            manifest = project / "deck.pptd"
            manifest.write_text(
                "version: v2\npages: [pages/01.page]\n",
                encoding="utf-8",
            )
            (pages / "01.page").write_text("elements: []\n", encoding="utf-8")
            outside_pages = root / "outside-pages"
            outside_pages.mkdir()
            (outside_pages / "01.page").write_text(
                "elements: [{type: text, value: SECRET}]\n",
                encoding="utf-8",
            )
            displaced = project / "pages-displaced"
            real_safe_path = MODULE.safe_project_path
            swapped = {"done": False}

            def swap_after_resolution(project_root, relative):
                resolved = real_safe_path(project_root, relative)
                if relative == "pages/01.page" and not swapped["done"]:
                    swapped["done"] = True
                    pages.rename(displaced)
                    pages.symlink_to(outside_pages, target_is_directory=True)
                return resolved

            try:
                with patch.object(
                    MODULE,
                    "safe_project_path",
                    side_effect=swap_after_resolution,
                ):
                    with self.assertRaisesRegex(
                        MODULE.ExportError,
                        "anchored path|opened Windows handle is outside",
                    ):
                        MODULE.build_payload(manifest)
            except OSError as exc:
                self.skipTest(f"symlink ancestor exchange is unavailable: {exc}")

    def test_image_ancestor_swap_cannot_embed_outside_content(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            project = root / "project"
            media = project / "media"
            media.mkdir(parents=True)
            (media / "hero.png").write_bytes(b"safe")
            outside = root / "outside-media"
            outside.mkdir()
            (outside / "hero.png").write_bytes(b"SECRET-OUTSIDE-PROJECT")
            displaced = project / "media-displaced"
            real_safe_path = MODULE.safe_project_path
            swapped = {"done": False}

            def swap_after_resolution(project_root, relative):
                resolved = real_safe_path(project_root, relative)
                if relative == "media/hero.png" and not swapped["done"]:
                    swapped["done"] = True
                    media.rename(displaced)
                    media.symlink_to(outside, target_is_directory=True)
                return resolved

            try:
                with patch.object(
                    MODULE,
                    "safe_project_path",
                    side_effect=swap_after_resolution,
                ):
                    with self.assertRaisesRegex(
                        MODULE.ExportError,
                        "anchored path|opened Windows handle is outside",
                    ):
                        MODULE.build_image_map(project, ["media/hero.png"])
            except OSError as exc:
                self.skipTest(f"symlink ancestor exchange is unavailable: {exc}")

    def test_official_and_raw_publication_stay_on_anchored_parent(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            parent = root / "output-parent"
            parent.mkdir()
            redirected = root / "redirected"
            redirected.mkdir()
            displaced = root / "output-parent-displaced"
            output = parent / "deck.pptx"
            raw = parent / "deck.browser-raw.pptx"
            with MODULE.AnchoredDirectory.open(parent) as capability:
                staged_output = parent / ".official.staging"
                staged_raw = parent / ".raw.staging"
                official_identity = MODULE.create_capability_staging_file(
                    capability, staged_output.name
                )
                raw_identity = MODULE.create_capability_staging_file(
                    capability, staged_raw.name
                )
                for path, identity, content in (
                    (staged_output, official_identity, b"official"),
                    (staged_raw, raw_identity, b"raw"),
                ):
                    descriptor = capability.open_file(path.name, MODULE.os.O_WRONLY)
                    try:
                        MODULE.os.write(descriptor, content)
                        MODULE.os.fsync(descriptor)
                    finally:
                        MODULE.os.close(descriptor)
                official_identity = capability.capture(staged_output.name)
                raw_identity = capability.capture(staged_raw.name)
                try:
                    parent.rename(displaced)
                    parent.symlink_to(redirected, target_is_directory=True)
                except OSError as exc:
                    self.skipTest(f"symlink ancestor exchange is unavailable: {exc}")
                if os.name == "nt":
                    with self.assertRaisesRegex(
                        MODULE.ExportError, "directory path changed"
                    ):
                        MODULE.publish_export_outputs(
                            staged_output,
                            output,
                            staged_raw,
                            raw,
                            replace_existing=False,
                            expected_output=MODULE.ABSENT_PATH_SNAPSHOT,
                            expected_debug=MODULE.ABSENT_PATH_SNAPSHOT,
                            expected_staged_output=official_identity,
                            expected_staged_debug=raw_identity,
                            directory_capability=capability,
                        )
                else:
                    published_raw = MODULE.publish_export_outputs(
                        staged_output,
                        output,
                        staged_raw,
                        raw,
                        replace_existing=False,
                        expected_output=MODULE.ABSENT_PATH_SNAPSHOT,
                        expected_debug=MODULE.ABSENT_PATH_SNAPSHOT,
                        expected_staged_output=official_identity,
                        expected_staged_debug=raw_identity,
                        directory_capability=capability,
                    )
                    self.assertEqual(published_raw, raw)
                with self.assertRaisesRegex(MODULE.ExportError, "directory path changed"):
                    capability.assert_path_binding()
            self.assertFalse((redirected / "deck.pptx").exists())
            self.assertFalse((redirected / "deck.browser-raw.pptx").exists())
            if os.name == "nt":
                self.assertTrue((displaced / staged_output.name).is_file())
                self.assertTrue((displaced / staged_raw.name).is_file())
            else:
                self.assertEqual((displaced / "deck.pptx").read_bytes(), b"official")
                self.assertEqual(
                    (displaced / "deck.browser-raw.pptx").read_bytes(),
                    b"raw",
                )

    def test_validate_pptx_destination_requires_pptx_and_protects_inputs(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name) / "project"
            pages = project / "pages"
            pages.mkdir(parents=True)
            manifest = project / "deck.pptd"
            manifest.write_text("manifest", encoding="utf-8")
            page = pages / "source.pptx"
            page.write_text("page", encoding="utf-8")
            payload = {"pages": [{"path": "pages/source.pptx"}]}

            with self.assertRaisesRegex(MODULE.ExportError, r"\.pptx extension"):
                MODULE.validate_pptx_destination(
                    manifest, payload, project / "deck.bin", False, False
                )
            with self.assertRaisesRegex(MODULE.ExportError, "project input"):
                MODULE.validate_pptx_destination(
                    manifest, payload, page, False, True
                )

            output, debug, output_snapshot, debug_snapshot = MODULE.validate_pptx_destination(
                manifest, payload, project / "deck.pptx", True, False
            )
            self.assertEqual(output, (project / "deck.pptx").resolve())
            self.assertEqual(debug, (project / "deck.browser-raw.pptx").resolve())
            self.assertFalse(output_snapshot.exists)
            self.assertIsNotNone(debug_snapshot)
            self.assertFalse(debug_snapshot.exists)

            retained = project / ".deck.pptx.backup"
            retained.write_text("previous", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.ExportError, "previous output backup"):
                MODULE.validate_pptx_destination(
                    manifest, payload, project / "deck.pptx", False, True
                )
            retained.unlink()
            raw_retained = project / ".deck.browser-raw.pptx.backup"
            raw_retained.write_text("previous raw", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.ExportError, "previous raw-debug backup"):
                MODULE.validate_pptx_destination(
                    manifest, payload, project / "deck.pptx", True, True
                )

    def test_validate_pptx_destination_rejects_case_and_hardlink_input_aliases(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name) / "project"
            pages = project / "pages"
            media = project / "media"
            pages.mkdir(parents=True)
            media.mkdir()
            manifest = project / "deck.pptd"
            manifest.write_text("manifest", encoding="utf-8")
            page = pages / "source.pptx"
            page.write_text("page", encoding="utf-8")
            image = media / "raw.pptx"
            image.write_text("image", encoding="utf-8")
            payload = {
                "pages": [{"path": "pages/source.pptx"}],
                "imageMap": {"media/raw.pptx": "data:image/png;base64,eA=="},
            }

            with self.assertRaisesRegex(MODULE.ExportError, "project input"):
                MODULE.validate_pptx_destination(
                    manifest, payload, pages / "SOURCE.PPTX", False, True
                )

            linked = project / "linked.pptx"
            try:
                MODULE.os.link(page, linked)
            except OSError as exc:
                self.skipTest(f"hard links are unavailable: {exc}")
            with self.assertRaisesRegex(MODULE.ExportError, "project input"):
                MODULE.validate_pptx_destination(
                    manifest, payload, linked, False, True
                )

            metadata = project / "deck.meta.json"
            metadata.write_text("metadata", encoding="utf-8")
            metadata_alias = project / "metadata-alias.pptx"
            MODULE.os.link(metadata, metadata_alias)
            with self.assertRaisesRegex(MODULE.ExportError, "project input"):
                MODULE.validate_pptx_destination(
                    manifest,
                    payload,
                    metadata_alias,
                    False,
                    True,
                    additional_protected_inputs=(metadata,),
                )

            debug = project / "deck.browser-raw.pptx"
            MODULE.os.link(image, debug)
            with self.assertRaisesRegex(MODULE.ExportError, "project input"):
                MODULE.validate_pptx_destination(
                    manifest, payload, project / "deck.pptx", True, True
                )

    def test_validate_pptx_destination_rejects_source_ancestor(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name) / "source.pptx"
            project.mkdir()
            manifest = project / "deck.pptd"
            manifest.write_text("manifest", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.ExportError, "not a directory"):
                MODULE.validate_pptx_destination(
                    manifest, {"pages": []}, project, False, True
                )

    def test_force_commit_rejects_multi_file_transaction_before_publication(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            first = root / "first.pptx"
            second = root / "second.pptx"
            staged_first = root / ".first.staging"
            staged_second = root / ".second.staging"
            staged_first.write_text("new-first", encoding="utf-8")
            staged_second.write_text("new-second", encoding="utf-8")

            with self.assertRaisesRegex(MODULE.ExportError, "exactly one"):
                MODULE.commit_staged_files(
                    [(staged_first, first), (staged_second, second)],
                    replace_existing=True,
                    expected_snapshots=(
                        MODULE.ABSENT_PATH_SNAPSHOT,
                        MODULE.ABSENT_PATH_SNAPSHOT,
                    ),
                )
            self.assertFalse(first.exists())
            self.assertFalse(second.exists())

    def test_commit_without_force_never_overwrites_a_concurrent_file(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            destination = root / "deck.pptx"
            staged = root / ".deck.staging"
            staged.write_text("new export", encoding="utf-8")
            real_publish = MODULE.rename_path_noreplace

            def inject_concurrent_file(source, target):
                destination.write_text("concurrent output", encoding="utf-8")
                return real_publish(source, target)

            with patch.object(
                MODULE, "rename_path_noreplace", side_effect=inject_concurrent_file
            ):
                with self.assertRaises(FileExistsError):
                    MODULE.commit_staged_files(
                        [(staged, destination)], replace_existing=False
                    )

            self.assertEqual(
                destination.read_text(encoding="utf-8"), "concurrent output"
            )
            self.assertEqual(staged.read_text(encoding="utf-8"), "new export")

    def test_no_force_rejects_multi_file_commit_before_publication(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            first = root / "deck.pptx"
            second = root / "deck.browser-raw.pptx"
            staged_first = root / ".deck.staging"
            staged_second = root / ".raw.staging"
            staged_first.write_text("new deck", encoding="utf-8")
            staged_second.write_text("new raw", encoding="utf-8")

            with patch.object(MODULE, "rename_path_noreplace") as rename:
                with self.assertRaisesRegex(MODULE.ExportError, "exactly one"):
                    MODULE.commit_staged_files(
                        [(staged_first, first), (staged_second, second)],
                        replace_existing=False,
                    )

            rename.assert_not_called()
            self.assertFalse(first.exists())
            self.assertFalse(second.exists())
            self.assertTrue(staged_first.is_file())
            self.assertTrue(staged_second.is_file())

    def test_debug_late_conflict_uses_unique_path_without_public_cleanup(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            official = root / "deck.pptx"
            requested_debug = root / "deck.browser-raw.pptx"
            staged_official = root / ".deck.staging"
            staged_debug = root / ".raw.staging"
            staged_official.write_text("new deck", encoding="utf-8")
            staged_debug.write_text("new raw", encoding="utf-8")
            real_publish = MODULE.rename_path_noreplace
            real_unlink = Path.unlink
            injected = {"done": False}

            def inject_late_conflict(source, target):
                if target == requested_debug and not injected["done"]:
                    injected["done"] = True
                    replacement = root / ".concurrent"
                    replacement.write_text(
                        "concurrent official replacement", encoding="utf-8"
                    )
                    MODULE.os.replace(replacement, official)
                    requested_debug.write_text(
                        "concurrent debug blocker", encoding="utf-8"
                    )
                return real_publish(source, target)

            def forbid_public_unlink(path, *args, **kwargs):
                if path in {official, requested_debug}:
                    raise AssertionError(f"attempted public unlink: {path}")
                return real_unlink(path, *args, **kwargs)

            with patch.object(
                MODULE,
                "rename_path_noreplace",
                side_effect=inject_late_conflict,
            ), patch.object(
                Path,
                "unlink",
                forbid_public_unlink,
            ), patch.object(
                MODULE.os.path,
                "samestat",
                side_effect=AssertionError("must not check then unlink"),
            ) as samestat:
                actual_debug = MODULE.publish_export_outputs(
                    staged_official,
                    official,
                    staged_debug,
                    requested_debug,
                    replace_existing=False,
                )

            samestat.assert_not_called()
            self.assertEqual(
                official.read_text(encoding="utf-8"),
                "concurrent official replacement",
            )
            self.assertEqual(
                requested_debug.read_text(encoding="utf-8"),
                "concurrent debug blocker",
            )
            self.assertNotEqual(actual_debug, requested_debug)
            self.assertEqual(actual_debug.read_text(encoding="utf-8"), "new raw")
            self.assertFalse(staged_debug.exists())

    def test_optional_debug_failure_does_not_fail_official_publication(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = root / "deck.pptx"
            debug = root / "deck.browser-raw.pptx"
            staged_output = root / ".deck.staging"
            staged_debug = root / ".raw.staging"
            staged_output.write_text("new deck", encoding="utf-8")
            staged_debug.write_text("new raw", encoding="utf-8")

            with patch.object(
                MODULE,
                "publish_optional_debug_copy",
                side_effect=OSError("simulated diagnostic failure"),
            ):
                actual_debug = MODULE.publish_export_outputs(
                    staged_output,
                    output,
                    staged_debug,
                    debug,
                    replace_existing=False,
                )

            self.assertIsNone(actual_debug)
            self.assertEqual(output.read_text(encoding="utf-8"), "new deck")
            self.assertFalse(debug.exists())
            self.assertFalse(staged_output.exists())
            self.assertEqual(staged_debug.read_text(encoding="utf-8"), "new raw")

    def test_force_reports_official_and_optional_raw_backups(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = root / "deck.pptx"
            debug = root / "deck.browser-raw.pptx"
            staged_output = root / ".deck.staging"
            staged_debug = root / ".raw.staging"
            output.write_text("old deck", encoding="utf-8")
            debug.write_text("old raw", encoding="utf-8")
            staged_output.write_text("new deck", encoding="utf-8")
            staged_debug.write_text("new raw", encoding="utf-8")
            details = {}

            actual_debug = MODULE.publish_export_outputs(
                staged_output,
                output,
                staged_debug,
                debug,
                replace_existing=True,
                expected_output=MODULE.capture_path_snapshot(output),
                expected_debug=MODULE.capture_path_snapshot(debug),
                expected_staged_output=MODULE.capture_path_snapshot(staged_output),
                expected_staged_debug=MODULE.capture_path_snapshot(staged_debug),
                publication_details=details,
            )

            self.assertEqual(actual_debug, debug)
            self.assertEqual(output.read_text(encoding="utf-8"), "new deck")
            self.assertEqual(debug.read_text(encoding="utf-8"), "new raw")
            self.assertEqual(
                details,
                {
                    "previousOutputBackup": (root / ".deck.pptx.backup").resolve(),
                    "previousBrowserRawBackup": (
                        root / ".deck.browser-raw.pptx.backup"
                    ).resolve(),
                },
            )

    def test_no_force_publishes_one_official_file_atomically(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = root / "deck.pptx"
            staged = root / ".deck.staging"
            staged.write_text("new deck", encoding="utf-8")

            MODULE.commit_staged_files(
                [(staged, output)],
                replace_existing=False,
            )

            self.assertEqual(output.read_text(encoding="utf-8"), "new deck")
            self.assertFalse(staged.exists())

    def test_private_file_cleanup_refuses_symlink_swap(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            staged = root / ".deck.random.staging"
            expected = MODULE.create_private_staging_file(staged)
            displaced = root / "displaced"
            staged.replace(displaced)
            victim = root / "victim.pptx"
            victim.write_text("keep", encoding="utf-8")
            try:
                staged.symlink_to(victim)
            except OSError as exc:
                self.skipTest(f"symlinks are unavailable: {exc}")

            MODULE._remove_private_staging_file(staged, expected)

            self.assertTrue(staged.is_symlink())
            self.assertEqual(victim.read_text(encoding="utf-8"), "keep")
            self.assertTrue(displaced.is_file())

    def test_force_commit_refuses_target_changed_since_preexport_snapshot(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = root / "deck.pptx"
            output.write_text("old", encoding="utf-8")
            expected = MODULE.capture_path_snapshot(output)
            output.write_text("concurrent", encoding="utf-8")
            staged = root / ".deck.staging"
            staged.write_text("new", encoding="utf-8")

            with self.assertRaisesRegex(MODULE.ExportError, "changed during export"):
                MODULE.commit_staged_files(
                    [(staged, output)],
                    replace_existing=True,
                    expected_snapshots=(expected,),
                )

            self.assertEqual(output.read_text(encoding="utf-8"), "concurrent")
            self.assertEqual(staged.read_text(encoding="utf-8"), "new")

    def test_force_commit_replaces_the_snapshotted_file_and_retains_one_backup(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = root / "deck.pptx"
            output.write_text("old", encoding="utf-8")
            expected = MODULE.capture_path_snapshot(output)
            staged = root / ".deck.staging"
            staged.write_text("new", encoding="utf-8")

            backup = MODULE.commit_staged_files(
                [(staged, output)],
                replace_existing=True,
                expected_snapshots=(expected,),
            )

            self.assertEqual(output.read_text(encoding="utf-8"), "new")
            self.assertFalse(staged.exists())
            self.assertEqual(backup, (root / ".deck.pptx.backup").resolve(strict=False))
            self.assertEqual(backup.read_text(encoding="utf-8"), "old")

            second = root / ".deck.second.staging"
            second.write_text("newer", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                MODULE.commit_staged_files(
                    [(second, output)],
                    replace_existing=True,
                    expected_snapshots=(MODULE.capture_path_snapshot(output),),
                )
            self.assertEqual(output.read_text(encoding="utf-8"), "new")
            self.assertEqual(backup.read_text(encoding="utf-8"), "old")

    def test_force_commit_restores_late_replacement_moved_to_backup(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = root / "deck.pptx"
            output.write_text("old", encoding="utf-8")
            expected = MODULE.capture_path_snapshot(output)
            staged = root / ".deck.staging"
            staged.write_text("new", encoding="utf-8")
            real_publish = MODULE.rename_path_noreplace
            injected = {"done": False}

            def inject_before_backup(source, destination):
                if source == output and not injected["done"]:
                    injected["done"] = True
                    replacement = root / ".concurrent"
                    replacement.write_text("concurrent", encoding="utf-8")
                    MODULE.os.replace(replacement, output)
                return real_publish(source, destination)

            with patch.object(
                MODULE,
                "rename_path_noreplace",
                side_effect=inject_before_backup,
            ):
                with self.assertRaisesRegex(MODULE.ExportError, "safely restored"):
                    MODULE.commit_staged_files(
                        [(staged, output)],
                        replace_existing=True,
                        expected_snapshots=(expected,),
                    )

            self.assertEqual(output.read_text(encoding="utf-8"), "concurrent")
            self.assertEqual(staged.read_text(encoding="utf-8"), "new")
            self.assertEqual(list(root.glob(".*.backup")), [])

    def test_force_commit_retains_backup_when_concurrent_writer_occupies_target(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = root / "deck.pptx"
            output.write_text("old", encoding="utf-8")
            expected = MODULE.capture_path_snapshot(output)
            staged = root / ".deck.staging"
            staged.write_text("new", encoding="utf-8")
            real_publish = MODULE.rename_path_noreplace

            def inject_after_backup(source, destination):
                if source == staged and destination == output:
                    output.write_text("concurrent", encoding="utf-8")
                return real_publish(source, destination)

            with patch.object(
                MODULE,
                "rename_path_noreplace",
                side_effect=inject_after_backup,
            ):
                with self.assertRaisesRegex(
                    MODULE.ExportError, r"retained at .*\.backup"
                ) as raised:
                    MODULE.commit_staged_files(
                        [(staged, output)],
                        replace_existing=True,
                        expected_snapshots=(expected,),
                    )

            self.assertEqual(output.read_text(encoding="utf-8"), "concurrent")
            backups = list(root.glob(".*.backup"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "old")
            self.assertIn(str(backups[0].resolve()), str(raised.exception))
            self.assertEqual(staged.read_text(encoding="utf-8"), "new")

    def test_font_timeout_retries_in_a_clean_session_without_embedding(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name) / "project"
            pages = project / "pages"
            pages.mkdir(parents=True)
            manifest = project / "deck.pptd"
            manifest.write_text("manifest", encoding="utf-8")
            (pages / "01.page").write_text("page", encoding="utf-8")
            output = project / "deck.pptx"
            payload = {"pages": [{"path": "pages/01.page"}], "imageMap": {}}

            class FakeServer:
                def shutdown(self):
                    pass

                def server_close(self):
                    pass

            class FakeThread:
                def join(self, timeout=None):
                    pass

            with patch.object(MODULE, "find_manifest", return_value=manifest), \
                    patch.object(MODULE, "verify_quality_gate", return_value={"passed": True}), \
                    patch.object(MODULE, "quality_gate_bound_inputs", return_value=()), \
                    patch.object(MODULE, "build_payload", return_value=payload), \
                    patch.object(MODULE, "ensure_agent_browser", return_value="agent-browser"), \
                    patch.object(MODULE, "ensure_websocket", return_value=object()), \
                    patch.object(MODULE, "ensure_debug_chrome", return_value=None), \
                    patch.object(
                        MODULE,
                        "serve",
                        return_value=(FakeServer(), FakeThread(), "http://localhost/"),
                    ), \
                    patch.object(
                        MODULE,
                        "run_pptx_browser_attempt",
                        side_effect=[
                            MODULE.PptxWriterTimeout("font timeout"),
                            MODULE.PptxAttemptResult(False, True),
                        ],
                    ) as attempt, \
                    patch.object(
                        MODULE,
                        "reset_private_staging_file",
                        wraps=MODULE.reset_private_staging_file,
                    ) as reset, \
                    patch.object(MODULE, "patch_transitions", return_value=1), \
                    patch.object(
                        MODULE,
                        "verify_output",
                        return_value={
                            "slides": 1,
                            "fadeTransitions": 1,
                            "fontParts": 0,
                            "bytes": 100,
                        },
                    ) as verify, \
                    patch.object(MODULE, "publish_export_outputs", return_value=None):
                summary = MODULE.export_pptx(
                    project,
                    output,
                    transition="fade",
                    embed_fonts=True,
                    quality_report=manifest,
                )

            self.assertEqual(attempt.call_count, 2)
            first = attempt.call_args_list[0]
            second = attempt.call_args_list[1]
            self.assertTrue(first.kwargs["embed_fonts"])
            self.assertEqual(first.kwargs["blob_timeout"], MODULE.PPTX_FONT_ATTEMPT_SECONDS)
            self.assertFalse(second.kwargs["embed_fonts"])
            self.assertEqual(second.kwargs["blob_timeout"], MODULE.PPTX_BLOB_WAIT_SECONDS)
            self.assertNotEqual(first.args[3], second.args[3])
            reset.assert_called_once()
            self.assertFalse(verify.call_args.args[2])
            self.assertTrue(summary["fontEmbeddingRequested"])
            self.assertFalse(summary["fontEmbeddingEnabledForSuccessfulAttempt"])
            self.assertTrue(summary["fontEmbeddingControlObserved"])
            self.assertTrue(summary["fontEmbeddingFallback"])
            self.assertTrue(summary["cdpBlobCaptureUsed"])

    def test_auth_or_structure_errors_do_not_trigger_font_retry(self):
        for failure in (
            MODULE.ExportError("HTTP 401"),
            MODULE.ExportError("unsupported PPTX Blob MIME type"),
        ):
            with self.subTest(failure=str(failure)), tempfile.TemporaryDirectory() as name:
                project = Path(name) / "project"
                pages = project / "pages"
                pages.mkdir(parents=True)
                manifest = project / "deck.pptd"
                manifest.write_text("manifest", encoding="utf-8")
                (pages / "01.page").write_text("page", encoding="utf-8")
                output = project / "deck.pptx"
                payload = {"pages": [{"path": "pages/01.page"}], "imageMap": {}}

                class FakeServer:
                    def shutdown(self):
                        pass

                    def server_close(self):
                        pass

                class FakeThread:
                    def join(self, timeout=None):
                        pass

                with patch.object(MODULE, "find_manifest", return_value=manifest), \
                        patch.object(MODULE, "verify_quality_gate", return_value={"passed": True}), \
                        patch.object(MODULE, "quality_gate_bound_inputs", return_value=()), \
                        patch.object(MODULE, "build_payload", return_value=payload), \
                        patch.object(MODULE, "ensure_agent_browser", return_value="agent-browser"), \
                        patch.object(MODULE, "ensure_websocket", return_value=object()), \
                        patch.object(MODULE, "ensure_debug_chrome", return_value=None), \
                        patch.object(
                            MODULE,
                            "serve",
                            return_value=(FakeServer(), FakeThread(), "http://localhost/"),
                        ), \
                        patch.object(
                            MODULE,
                            "run_pptx_browser_attempt",
                            side_effect=failure,
                        ) as attempt:
                    with self.assertRaisesRegex(MODULE.ExportError, str(failure)):
                        MODULE.export_pptx(
                            project,
                            output,
                            transition="fade",
                            embed_fonts=True,
                            quality_report=manifest,
                        )
                self.assertEqual(attempt.call_count, 1)

    def test_export_failure_preserves_output_and_uses_only_session_downloads(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            project = root / "project"
            pages = project / "pages"
            pages.mkdir(parents=True)
            manifest = project / "deck.pptd"
            manifest.write_text("manifest", encoding="utf-8")
            (pages / "01.page").write_text("page", encoding="utf-8")
            output = project / "deck.pptx"
            output.write_text("known-good", encoding="utf-8")
            payload = {"pages": [{"path": "pages/01.page"}], "imageMap": {}}
            observed_roots = []
            observed_timeouts = []

            class FakeBrowser:
                def __init__(self, *_args, **_kwargs):
                    pass

                def open(self, _url):
                    pass

                def run(self, *_args, **_kwargs):
                    return None

                def snapshot(self):
                    return {}

                def close(self):
                    pass

            class FakeServer:
                def shutdown(self):
                    pass

                def server_close(self):
                    pass

            class FakeThread:
                def join(self, timeout=None):
                    pass

            def fake_find_download(search_roots, **kwargs):
                roots = tuple(search_roots)
                observed_roots.extend(roots)
                observed_timeouts.append(kwargs.get("timeout"))
                downloaded = roots[0] / "download.pptx"
                downloaded.write_bytes(b"browser output")
                return downloaded

            with patch.object(MODULE, "find_manifest", return_value=manifest), \
                    patch.object(MODULE, "verify_quality_gate", return_value={"passed": True}), \
                    patch.object(MODULE, "quality_gate_bound_inputs", return_value=()), \
                    patch.object(MODULE, "build_payload", return_value=payload), \
                    patch.object(MODULE, "ensure_agent_browser", return_value="agent-browser"), \
                    patch.object(MODULE, "ensure_websocket", return_value=object()), \
                    patch.object(MODULE, "ensure_debug_chrome", return_value=None), \
                    patch.object(
                        MODULE,
                        "serve",
                        return_value=(FakeServer(), FakeThread(), "http://localhost/"),
                    ), \
                    patch.object(MODULE, "BrowserSession", FakeBrowser), \
                    patch.object(MODULE, "ref_by_name", return_value="e1"), \
                    patch.object(MODULE, "wait_for_export_dialog", return_value={}), \
                    patch.object(MODULE, "switch_state", return_value=None), \
                    patch.object(
                        MODULE,
                        "configure_font_embedding",
                        return_value=({}, False),
                    ), \
                    patch.object(MODULE, "install_pptx_blob_hook", return_value=None), \
                    patch.object(MODULE, "find_download", side_effect=fake_find_download), \
                    patch.object(
                        MODULE,
                        "patch_transitions",
                        side_effect=MODULE.ExportError("simulated patch failure"),
                    ):
                with self.assertRaisesRegex(MODULE.ExportError, "patch failure"):
                    MODULE.export_pptx(
                        project,
                        output,
                        transition="fade",
                        embed_fonts=False,
                        force=True,
                        quality_report=manifest,
                    )

            self.assertEqual(output.read_text(encoding="utf-8"), "known-good")
            self.assertEqual(len(observed_roots), 1)
            self.assertEqual(observed_roots[0].name, "downloads-1")
            self.assertEqual(observed_timeouts, [MODULE.PPTX_BLOB_WAIT_SECONDS])
            self.assertFalse(any(project.glob(".*.staging")))


if __name__ == "__main__":
    unittest.main()
