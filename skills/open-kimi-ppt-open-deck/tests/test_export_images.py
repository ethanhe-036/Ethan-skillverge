#!/usr/bin/env python3
import importlib.util
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

SPEC = importlib.util.spec_from_file_location("export_images", SCRIPTS_DIR / "export_images.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def make_images_zip(path: Path, names=("1.jpeg", "10.jpeg", "2.jpeg")) -> None:
    # 1x1 white JPEG, the smallest valid payload Pillow can open.
    import base64

    pixel = base64.b64decode(
        "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////"
        "////////////////////////////////////////////2wBDAf//////////////////"
        "////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB"
        "/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMB"
        "AAIQAxAAAAGf/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAA"
        "AAAAAAAAAAAAAAAAAP/aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgB"
        "AgEBPwF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAA"
        "AAAAAAAAAAAAAP/aAAgBAQABPyF//9oADAMBAAIAAwAAABCf/8QAFBEBAAAAAAAAAAAA"
        "AAAAAAAAAP/aAAgBAwEPEBB//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEPEBB/"
        "/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxB//9k="
    )
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, pixel)


class ExportImagesTests(unittest.TestCase):
    def test_windows_directory_publish_delegates_to_stable_parent_handle(self):
        class FakeCapability:
            def __init__(self):
                self.renames = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def rename_noreplace(self, source, destination):
                self.renames.append((source, destination))

        capability = FakeCapability()
        source = Path("output") / ".qa.staging"
        destination = Path("output") / "qa"
        with patch.object(MODULE.sys, "platform", "win32"), patch.object(
            MODULE.os,
            "name",
            "nt",
        ), patch.object(
            MODULE,
            "safe_resolve_path",
            side_effect=lambda path, **_kwargs: path,
        ), patch.object(
            MODULE.AnchoredDirectory,
            "open",
            return_value=capability,
        ) as open_capability, patch.object(
            MODULE.os,
            "rename",
            side_effect=AssertionError("path-based rename must not be used"),
        ):
            MODULE.rename_directory_noreplace(source, destination)

        open_capability.assert_called_once_with(source.parent)
        self.assertEqual(capability.renames, [(source.name, destination.name)])

    def test_main_passes_the_manifest_used_for_default_output_to_export(self):
        manifest = Path("/project/deck.pptd")
        args = MODULE.argparse.Namespace(
            input=Path("/project"),
            output=None,
            keep_browser_raw=False,
            force=False,
        )
        with patch.object(MODULE, "parse_args", return_value=args), \
                patch.object(MODULE, "find_manifest", return_value=manifest), \
                patch.object(MODULE, "export_images", return_value={}) as export:
            self.assertEqual(MODULE.main([]), 0)
        self.assertEqual(export.call_args.args[0], manifest)
        self.assertEqual(export.call_args.args[1], manifest.parent / ".qa-images")

    def test_image_export_reuses_shared_cdp_iframe_helper(self):
        self.assertEqual(MODULE.evaluate_in_iframe.__module__, "export_pptx")
        self.assertEqual(MODULE.OOPIF_URL_HINT, "kimi.com/neo-ppt")
        self.assertEqual(MODULE.ensure_debug_chrome.__module__, "export_pptx")

    def test_image_format_selection_pins_the_initial_iframe_target(self):
        browser = object()
        with patch.object(MODULE, "browser_cdp_url", return_value="ws://test"), \
                patch.object(
                    MODULE,
                    "evaluate_in_iframe",
                    side_effect=[
                        ("target-1", {"status": "clicked", "count": 1}),
                        {"count": 1, "text": "图片"},
                    ],
                ) as evaluate:
            MODULE.select_image_format(browser)
        self.assertTrue(evaluate.call_args_list[0].kwargs["return_target_id"])
        self.assertEqual(evaluate.call_args_list[1].kwargs["target_id"], "target-1")

    def test_image_format_contract_rejects_ambiguous_remote_control(self):
        with patch.object(MODULE, "browser_cdp_url", return_value="ws://test"), \
                patch.object(
                    MODULE,
                    "evaluate_in_iframe",
                    return_value=(
                        "target-1",
                        {"status": "ambiguous", "count": 2},
                    ),
                ):
            with self.assertRaisesRegex(MODULE.ExportError, "missing or ambiguous"):
                MODULE.select_image_format(object())

    def test_missing_pillow_is_reported_without_installing(self):
        real_import = __import__

        def missing_pillow(name, *args, **kwargs):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("simulated missing Pillow")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=missing_pillow):
            with self.assertRaisesRegex(MODULE.ExportError, "Pillow is required"):
                MODULE.ensure_pillow()

    def test_page_sort_key_orders_numeric_stems(self):
        paths = [Path("10.jpeg"), Path("2.jpeg"), Path("cover.jpeg"), Path("1.jpeg")]
        ordered = sorted(paths, key=MODULE.page_sort_key)
        self.assertEqual(
            [path.name for path in ordered],
            ["1.jpeg", "2.jpeg", "10.jpeg", "cover.jpeg"],
        )

    def test_is_image_zip_accepts_image_entries_only(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            good = root / "images.zip"
            make_images_zip(good)
            self.assertTrue(MODULE.is_image_zip(good))

            bad = root / "text.zip"
            with zipfile.ZipFile(bad, "w") as archive:
                archive.writestr("readme.txt", "hello")
            self.assertFalse(MODULE.is_image_zip(bad))
            self.assertFalse(MODULE.is_image_zip(root / "missing.zip"))

    def test_untrusted_zip_decode_and_crc_errors_are_controlled(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            archive_path = root / "images.zip"
            make_images_zip(archive_path, names=("1.jpeg",))

            with patch.object(
                MODULE.zipfile,
                "ZipFile",
                side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"),
            ):
                self.assertFalse(MODULE.is_image_zip(archive_path))
                with self.assertRaisesRegex(MODULE.ExportError, "invalid page-images ZIP"):
                    MODULE.unzip_images(archive_path, root / "decode-pages")

            data = bytearray(archive_path.read_bytes())
            local = data.index(b"PK\x03\x04")
            name_length = int.from_bytes(data[local + 26 : local + 28], "little")
            extra_length = int.from_bytes(data[local + 28 : local + 30], "little")
            content_start = local + 30 + name_length + extra_length
            data[content_start] ^= 0xFF
            archive_path.write_bytes(data)
            self.assertTrue(MODULE.is_image_zip(archive_path))
            with self.assertRaisesRegex(MODULE.ExportError, "invalid page-images ZIP"):
                MODULE.unzip_images(archive_path, root / "crc-pages")

    def test_unzip_images_flattens_and_sorts(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            archive_path = root / "images.zip"
            make_images_zip(archive_path, names=("1.jpeg", "10.jpeg", "2.jpeg", "note.txt"))
            images = MODULE.unzip_images(archive_path, root / "pages")
            self.assertEqual(
                [path.name for path in images],
                ["page-0001.jpeg", "page-0002.jpeg", "page-0003.jpeg"],
            )

    def test_unzip_images_never_uses_windows_special_archive_names_as_paths(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            archive_path = root / "images.zip"
            make_images_zip(
                archive_path,
                names=("C:evil.png", "foo:stream.jpeg", "CON.webp"),
            )
            images = MODULE.unzip_images(archive_path, root / "pages")
            self.assertEqual(
                [path.name for path in images],
                ["page-0001.png", "page-0002.webp", "page-0003.jpeg"],
            )
            self.assertTrue(all(path.parent == root / "pages" for path in images))

    def test_archive_sort_handles_an_extreme_numeric_prefix_without_int_conversion(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            archive_path = root / "images.zip"
            make_images_zip(
                archive_path,
                names=("2.png", f"{'1' * 5000}.png"),
            )
            images = MODULE.unzip_images(archive_path, root / "pages")
            self.assertEqual(
                [path.name for path in images],
                ["page-0001.png", "page-0002.png"],
            )

    def test_image_zip_rejects_ooxml_even_when_it_contains_images(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            archive_path = root / "not-images.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("[Content_Types].xml", "<Types/>")
                archive.writestr("ppt/media/image1.png", b"image")
            self.assertFalse(MODULE.is_image_zip(archive_path))
            with self.assertRaisesRegex(MODULE.ExportError, "OOXML"):
                MODULE.unzip_images(archive_path, root / "pages")

    def test_unzip_images_rejects_casefold_name_collisions(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            archive_path = root / "images.zip"
            make_images_zip(
                archive_path,
                names=("one/1.jpeg", "two/1.JPEG"),
            )
            with self.assertRaisesRegex(MODULE.ExportError, "colliding filenames"):
                MODULE.unzip_images(archive_path, root / "pages")

    def test_image_zip_enforces_uncompressed_entry_limit(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            archive_path = root / "images.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("1.png", b"12345")
            with patch.object(MODULE, "MAX_IMAGE_ARCHIVE_ENTRY_BYTES", 4):
                self.assertFalse(MODULE.is_image_zip(archive_path))
                with self.assertRaisesRegex(MODULE.ExportError, "exceeds"):
                    MODULE.unzip_images(archive_path, root / "pages")

    def test_image_zip_counts_non_image_members_and_compressed_bytes(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            archive_path = root / "images.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("1.png", b"x")
                archive.writestr("padding.bin", b"12345")

            with patch.object(MODULE, "MAX_IMAGE_ARCHIVE_TOTAL_BYTES", 5):
                self.assertFalse(MODULE.is_image_zip(archive_path))
                with self.assertRaisesRegex(MODULE.ExportError, "expands beyond"):
                    MODULE.unzip_images(archive_path, root / "pages-total")

            with patch.object(
                MODULE,
                "MAX_IMAGE_ARCHIVE_BYTES",
                archive_path.stat().st_size - 1,
            ):
                self.assertFalse(MODULE.is_image_zip(archive_path))
                with self.assertRaisesRegex(MODULE.ExportError, "compressed-size"):
                    MODULE.unzip_images(archive_path, root / "pages-compressed")

    def test_image_zip_member_budget_runs_before_zipfile_construction(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            archive_path = root / "images.zip"
            make_images_zip(archive_path, names=("1.png", "2.png", "3.png"))
            with patch.object(MODULE, "MAX_IMAGE_ARCHIVE_MEMBERS", 2), \
                    patch.object(MODULE.zipfile, "ZipFile", wraps=MODULE.zipfile.ZipFile) as constructor:
                self.assertFalse(MODULE.is_image_zip(archive_path))
            constructor.assert_not_called()

    def test_validate_page_count_requires_exact_manifest_count(self):
        payload = {
            "pages": [
                {"path": "pages/01.page"},
                {"path": "pages/02.page"},
            ]
        }
        with self.assertRaisesRegex(MODULE.ExportError, "expected 2, received 1"):
            MODULE.validate_page_count([Path("1.jpeg")], payload)
        self.assertEqual(
            MODULE.validate_page_count(
                [Path("1.jpeg"), Path("2.jpeg")], payload
            ),
            ["pages/01.page", "pages/02.page"],
        )

    def test_directory_snapshot_streaming_limits_width_and_depth(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            wide = root / "wide"
            wide.mkdir()
            for index in range(3):
                (wide / f"{index}.txt").write_text("x", encoding="utf-8")
            with patch.object(MODULE, "MAX_OUTPUT_SNAPSHOT_ENTRIES", 2):
                with self.assertRaisesRegex(MODULE.ExportError, "snapshot entries"):
                    MODULE.capture_directory_snapshot(wide)

            deep = root / "deep"
            (deep / "one" / "two").mkdir(parents=True)
            with patch.object(MODULE, "MAX_OUTPUT_SNAPSHOT_DEPTH", 1):
                with self.assertRaisesRegex(MODULE.ExportError, "depth limit"):
                    MODULE.capture_directory_snapshot(deep)

    def test_validate_image_destination_rejects_source_and_page_ancestors(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name) / "project"
            pages = project / "pages"
            pages.mkdir(parents=True)
            manifest = project / "deck.pptd"
            manifest.write_text("manifest", encoding="utf-8")
            page = pages / "01.page"
            page.write_text("page", encoding="utf-8")
            payload = {
                "pages": [{"path": "pages/01.page"}],
                "imageMap": {},
            }

            for output in (project, pages):
                with self.subTest(output=output):
                    with self.assertRaises(MODULE.ExportError):
                        MODULE.validate_image_destination(
                            manifest, payload, output, True
                        )
            self.assertTrue(manifest.is_file())
            self.assertTrue(page.is_file())

            safe, existed, snapshot = MODULE.validate_image_destination(
                manifest, payload, project / ".qa-images", False
            )
            self.assertEqual(safe, (project / ".qa-images").resolve())
            self.assertFalse(existed)
            self.assertFalse(snapshot.root.exists)

            media = project / "media"
            media.mkdir()
            hero = media / "hero.png"
            hero.write_bytes(b"source image")
            with self.assertRaisesRegex(MODULE.ExportError, "unowned directory"):
                MODULE.validate_image_destination(manifest, payload, media, True)
            self.assertEqual(hero.read_bytes(), b"source image")

            empty = project / "empty-output"
            empty.mkdir()
            with self.assertRaisesRegex(MODULE.ExportError, "unowned directory"):
                MODULE.validate_image_destination(manifest, payload, empty, False)

            owned = project / "owned-output"
            owned.mkdir()
            (owned / MODULE.OUTPUT_MARKER).write_text(
                MODULE.json.dumps(MODULE.OUTPUT_MARKER_CONTENT), encoding="utf-8"
            )
            safe, existed, snapshot = MODULE.validate_image_destination(
                manifest, payload, owned, True
            )
            self.assertEqual(safe, owned.resolve())
            self.assertTrue(existed)
            self.assertTrue(snapshot.root.exists)

            retained_target = project / "retained-output"
            retained_backup = project / ".retained-output.backup"
            retained_backup.mkdir()
            with self.assertRaisesRegex(
                MODULE.ExportError, "previous image-output backup"
            ):
                MODULE.validate_image_destination(
                    manifest, payload, retained_target, True
                )

            stale_media = project / "stale-media"
            stale_media.mkdir()
            (stale_media / MODULE.OUTPUT_MARKER).write_text(
                MODULE.json.dumps(MODULE.OUTPUT_MARKER_CONTENT), encoding="utf-8"
            )
            (stale_media / "logo.png").write_bytes(b"source image")
            payload_with_image = {
                **payload,
                "imageMap": {"stale-media/logo.png": "data:image/png;base64,eA=="},
            }
            with self.assertRaisesRegex(MODULE.ExportError, "project input"):
                MODULE.validate_image_destination(
                    manifest, payload_with_image, stale_media, True
                )
            self.assertEqual((stale_media / "logo.png").read_bytes(), b"source image")

    def test_validate_image_destination_rejects_case_alias_ancestor(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name) / "project"
            pages = project / "pages"
            media = project / ".qa-images"
            pages.mkdir(parents=True)
            media.mkdir()
            manifest = project / "deck.pptd"
            manifest.write_text("manifest", encoding="utf-8")
            (pages / "01.page").write_text("page", encoding="utf-8")
            (media / "logo.png").write_bytes(b"image")
            payload = {
                "pages": [{"path": "pages/01.page"}],
                "imageMap": {".qa-images/logo.png": "data:image/png;base64,eA=="},
            }

            with self.assertRaisesRegex(MODULE.ExportError, "project input"):
                MODULE.validate_image_destination(
                    manifest, payload, project / ".QA-IMAGES", True
                )

    @unittest.skipIf(os.name == "nt", "Windows Path.home does not use HOME")
    def test_validate_image_destination_wraps_a_home_symlink_loop(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            project = root / "project"
            pages = project / "pages"
            pages.mkdir(parents=True)
            manifest = project / "deck.pptd"
            manifest.write_text("manifest", encoding="utf-8")
            (pages / "01.page").write_text("page", encoding="utf-8")
            first = root / "home-a"
            second = root / "home-b"
            try:
                first.symlink_to(second)
                second.symlink_to(first)
            except OSError as exc:
                self.skipTest(f"symlinks are unavailable: {exc}")

            with patch.dict(MODULE.os.environ, {"HOME": str(first)}):
                with self.assertRaisesRegex(MODULE.ExportError, "invalid home directory path"):
                    MODULE.validate_image_destination(
                        manifest,
                        {"pages": [{"path": "pages/01.page"}], "imageMap": {}},
                        root / "qa-output",
                        False,
                    )

    def test_owned_marker_rejects_symlink_and_oversize_file_before_json_read(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = root / "qa"
            output.mkdir()
            marker = output / MODULE.OUTPUT_MARKER
            target = root / "marker-target.json"
            target.write_text(
                MODULE.json.dumps(MODULE.OUTPUT_MARKER_CONTENT), encoding="utf-8"
            )
            try:
                marker.symlink_to(target)
            except OSError as exc:
                self.skipTest(f"symlinks are unavailable: {exc}")
            self.assertFalse(MODULE.is_owned_output_directory(output))
            marker.unlink()
            marker.write_bytes(b"x" * (MODULE.MAX_OUTPUT_MARKER_BYTES + 1))
            with patch.object(
                MODULE.os,
                "read",
                side_effect=AssertionError("oversize marker must not be read"),
            ):
                self.assertFalse(MODULE.is_owned_output_directory(output))

    def test_missing_websocket_fails_before_browser_or_output_work(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            project = root / "project"
            pages = project / "pages"
            pages.mkdir(parents=True)
            manifest = project / "deck.pptd"
            manifest.write_text("manifest", encoding="utf-8")
            (pages / "01.page").write_text("page", encoding="utf-8")
            output = project / ".qa-images"
            payload = {"pages": [{"path": "pages/01.page"}], "imageMap": {}}

            with patch.object(MODULE, "find_manifest", return_value=manifest), \
                    patch.object(MODULE, "build_payload", return_value=payload), \
                    patch.object(MODULE, "ensure_agent_browser", return_value="agent-browser"), \
                    patch.object(MODULE, "ensure_pillow", return_value=(None, None, None)), \
                    patch.object(
                        MODULE,
                        "ensure_websocket",
                        side_effect=MODULE.ExportError("websocket-client is required"),
                    ), \
                    patch.object(MODULE, "BrowserSession") as browser:
                with self.assertRaisesRegex(MODULE.ExportError, "websocket-client"):
                    MODULE.export_images(project, output)

            browser.assert_not_called()
            self.assertFalse(output.exists())
            self.assertFalse(any(project.glob(".*.staging")))

    def test_marker_write_failure_cleans_private_staging_directory(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name) / "project"
            pages = project / "pages"
            pages.mkdir(parents=True)
            manifest = project / "deck.pptd"
            manifest.write_text("manifest", encoding="utf-8")
            (pages / "01.page").write_text("page", encoding="utf-8")
            output = project / ".qa-images"
            payload = {"pages": [{"path": "pages/01.page"}], "imageMap": {}}
            real_write_text = Path.write_text

            def fail_marker_write(path, *args, **kwargs):
                if path.name == MODULE.OUTPUT_MARKER:
                    raise OSError("simulated marker write failure")
                return real_write_text(path, *args, **kwargs)

            with patch.object(MODULE, "find_manifest", return_value=manifest), \
                    patch.object(MODULE, "build_payload", return_value=payload), \
                    patch.object(MODULE, "ensure_agent_browser", return_value="agent-browser"), \
                    patch.object(MODULE, "ensure_pillow", return_value=(None, None, None)), \
                    patch.object(MODULE, "ensure_websocket", return_value=object()), \
                    patch.object(Path, "write_text", fail_marker_write):
                with self.assertRaisesRegex(OSError, "marker write failure"):
                    MODULE.export_images(project, output)

            self.assertFalse(output.exists())
            self.assertFalse(any(project.glob(".*.staging")))

    def test_private_staging_cleanup_refuses_symlink_swap(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            staged = root / ".qa.random.staging"
            staged.mkdir()
            expected = MODULE.capture_path_snapshot(staged)
            displaced = root / "displaced"
            staged.replace(displaced)
            victim = root / "victim"
            victim.mkdir()
            (victim / "keep.txt").write_text("keep", encoding="utf-8")
            try:
                staged.symlink_to(victim, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlinks are unavailable: {exc}")

            MODULE.cleanup_private_directory(
                staged,
                expected,
                label="image staging directory",
            )

            self.assertTrue(staged.is_symlink())
            self.assertEqual((victim / "keep.txt").read_text(encoding="utf-8"), "keep")
            self.assertTrue(displaced.is_dir())

    def test_commit_staged_directory_restores_existing_output_on_failure(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = root / "qa"
            output.mkdir()
            (output / MODULE.OUTPUT_MARKER).write_text(
                MODULE.json.dumps(MODULE.OUTPUT_MARKER_CONTENT), encoding="utf-8"
            )
            (output / "marker.txt").write_text("old", encoding="utf-8")
            staged = root / ".qa.staging"
            staged.mkdir()
            (staged / MODULE.OUTPUT_MARKER).write_text(
                MODULE.json.dumps(MODULE.OUTPUT_MARKER_CONTENT), encoding="utf-8"
            )
            (staged / "marker.txt").write_text("new", encoding="utf-8")
            expected = MODULE.capture_directory_snapshot(output)
            real_publish = MODULE.rename_directory_noreplace

            def fail_staged_publish(path, target):
                if path == staged and target == output:
                    raise OSError("simulated directory publish failure")
                return real_publish(path, target)

            with patch.object(
                MODULE,
                "rename_directory_noreplace",
                side_effect=fail_staged_publish,
            ):
                with self.assertRaisesRegex(OSError, "simulated directory publish failure"):
                    MODULE.commit_staged_directory(
                        staged,
                        output,
                        replace_existing=True,
                        expected_snapshot=expected,
                    )
            self.assertEqual(
                (output / "marker.txt").read_text(encoding="utf-8"), "old"
            )

    def test_commit_refuses_unowned_directory_that_appeared_during_export(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = root / "qa"
            expected = MODULE.capture_directory_snapshot(output)
            output.mkdir()
            (output / "late-file.txt").write_text("keep me", encoding="utf-8")
            staged = root / ".qa.staging"
            staged.mkdir()
            (staged / MODULE.OUTPUT_MARKER).write_text(
                MODULE.json.dumps(MODULE.OUTPUT_MARKER_CONTENT), encoding="utf-8"
            )

            with self.assertRaisesRegex(MODULE.ExportError, "changed during export"):
                MODULE.commit_staged_directory(
                    staged,
                    output,
                    replace_existing=True,
                    expected_snapshot=expected,
                )

            self.assertEqual(
                (output / "late-file.txt").read_text(encoding="utf-8"), "keep me"
            )

    def test_force_commit_refuses_directory_modified_since_snapshot(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = root / "qa"
            output.mkdir()
            (output / MODULE.OUTPUT_MARKER).write_text(
                MODULE.json.dumps(MODULE.OUTPUT_MARKER_CONTENT), encoding="utf-8"
            )
            old = output / "old.txt"
            old.write_text("old", encoding="utf-8")
            expected = MODULE.capture_directory_snapshot(output)
            old.write_text("concurrent", encoding="utf-8")
            staged = root / ".qa.staging"
            staged.mkdir()
            (staged / MODULE.OUTPUT_MARKER).write_text(
                MODULE.json.dumps(MODULE.OUTPUT_MARKER_CONTENT), encoding="utf-8"
            )

            with self.assertRaisesRegex(MODULE.ExportError, "changed during export"):
                MODULE.commit_staged_directory(
                    staged,
                    output,
                    replace_existing=True,
                    expected_snapshot=expected,
                )

            self.assertEqual(old.read_text(encoding="utf-8"), "concurrent")
            self.assertTrue(staged.is_dir())

    def test_force_commit_replaces_snapshotted_directory_and_retains_one_backup(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = root / "qa"
            output.mkdir()
            (output / MODULE.OUTPUT_MARKER).write_text(
                MODULE.json.dumps(MODULE.OUTPUT_MARKER_CONTENT), encoding="utf-8"
            )
            (output / "old.txt").write_text("old", encoding="utf-8")
            expected = MODULE.capture_directory_snapshot(output)
            staged = root / ".qa.staging"
            staged.mkdir()
            (staged / MODULE.OUTPUT_MARKER).write_text(
                MODULE.json.dumps(MODULE.OUTPUT_MARKER_CONTENT), encoding="utf-8"
            )
            (staged / "new.txt").write_text("new", encoding="utf-8")

            backup = MODULE.commit_staged_directory(
                staged,
                output,
                replace_existing=True,
                expected_snapshot=expected,
            )

            self.assertEqual((output / "new.txt").read_text(encoding="utf-8"), "new")
            self.assertFalse((output / "old.txt").exists())
            self.assertFalse(staged.exists())
            self.assertEqual(backup, (root / ".qa.backup").resolve(strict=False))
            self.assertEqual((backup / "old.txt").read_text(encoding="utf-8"), "old")

            second = root / ".qa.second.staging"
            second.mkdir()
            (second / MODULE.OUTPUT_MARKER).write_text(
                MODULE.json.dumps(MODULE.OUTPUT_MARKER_CONTENT), encoding="utf-8"
            )
            with self.assertRaises(FileExistsError):
                MODULE.commit_staged_directory(
                    second,
                    output,
                    replace_existing=True,
                    expected_snapshot=MODULE.capture_directory_snapshot(output),
                )
            self.assertEqual((output / "new.txt").read_text(encoding="utf-8"), "new")
            self.assertEqual((backup / "old.txt").read_text(encoding="utf-8"), "old")

    def test_force_commit_restores_late_directory_moved_to_backup(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = root / "qa"
            output.mkdir()
            (output / MODULE.OUTPUT_MARKER).write_text(
                MODULE.json.dumps(MODULE.OUTPUT_MARKER_CONTENT), encoding="utf-8"
            )
            (output / "old.txt").write_text("old", encoding="utf-8")
            expected = MODULE.capture_directory_snapshot(output)
            staged = root / ".qa.staging"
            staged.mkdir()
            (staged / MODULE.OUTPUT_MARKER).write_text(
                MODULE.json.dumps(MODULE.OUTPUT_MARKER_CONTENT), encoding="utf-8"
            )
            real_publish = MODULE.rename_directory_noreplace
            injected = {"done": False}

            def inject_before_backup(source, destination):
                if source == output and not injected["done"]:
                    injected["done"] = True
                    superseded = root / "superseded"
                    output.replace(superseded)
                    concurrent = root / "concurrent"
                    concurrent.mkdir()
                    (concurrent / "concurrent.txt").write_text(
                        "concurrent", encoding="utf-8"
                    )
                    concurrent.replace(output)
                return real_publish(source, destination)

            with patch.object(
                MODULE,
                "rename_directory_noreplace",
                side_effect=inject_before_backup,
            ):
                with self.assertRaisesRegex(MODULE.ExportError, "safely restored"):
                    MODULE.commit_staged_directory(
                        staged,
                        output,
                        replace_existing=True,
                        expected_snapshot=expected,
                    )

            self.assertEqual(
                (output / "concurrent.txt").read_text(encoding="utf-8"),
                "concurrent",
            )
            self.assertTrue(staged.is_dir())
            self.assertEqual(list(root.glob(".*.backup")), [])

    def test_force_commit_retains_backup_when_concurrent_directory_occupies_target(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = root / "qa"
            output.mkdir()
            (output / MODULE.OUTPUT_MARKER).write_text(
                MODULE.json.dumps(MODULE.OUTPUT_MARKER_CONTENT), encoding="utf-8"
            )
            (output / "old.txt").write_text("old", encoding="utf-8")
            expected = MODULE.capture_directory_snapshot(output)
            staged = root / ".qa.staging"
            staged.mkdir()
            (staged / MODULE.OUTPUT_MARKER).write_text(
                MODULE.json.dumps(MODULE.OUTPUT_MARKER_CONTENT), encoding="utf-8"
            )
            (staged / "new.txt").write_text("new", encoding="utf-8")
            real_publish = MODULE.rename_directory_noreplace

            def inject_after_backup(source, destination):
                if source == staged and destination == output:
                    output.mkdir()
                    (output / "concurrent.txt").write_text(
                        "concurrent", encoding="utf-8"
                    )
                return real_publish(source, destination)

            with patch.object(
                MODULE,
                "rename_directory_noreplace",
                side_effect=inject_after_backup,
            ):
                with self.assertRaisesRegex(
                    MODULE.ExportError, r"retained at .*\.backup"
                ) as raised:
                    MODULE.commit_staged_directory(
                        staged,
                        output,
                        replace_existing=True,
                        expected_snapshot=expected,
                    )

            self.assertEqual(
                (output / "concurrent.txt").read_text(encoding="utf-8"),
                "concurrent",
            )
            backups = list(root.glob(".*.backup"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(
                (backups[0] / "old.txt").read_text(encoding="utf-8"), "old"
            )
            self.assertIn(str(backups[0].resolve()), str(raised.exception))
            self.assertTrue((staged / "new.txt").is_file())

    def test_commit_without_force_preserves_a_concurrent_owned_directory(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = root / "qa"
            staged = root / ".qa.staging"
            staged.mkdir()
            (staged / MODULE.OUTPUT_MARKER).write_text(
                MODULE.json.dumps(MODULE.OUTPUT_MARKER_CONTENT), encoding="utf-8"
            )
            real_publish = MODULE.rename_directory_noreplace
            injected = {"done": False}

            def inject_concurrent_directory(source, destination):
                if not injected["done"]:
                    injected["done"] = True
                    output.mkdir()
                    (output / MODULE.OUTPUT_MARKER).write_text(
                        MODULE.json.dumps(MODULE.OUTPUT_MARKER_CONTENT),
                        encoding="utf-8",
                    )
                    (output / "late-file.txt").write_text("keep me", encoding="utf-8")
                return real_publish(source, destination)

            with patch.object(
                MODULE,
                "rename_directory_noreplace",
                side_effect=inject_concurrent_directory,
            ):
                with self.assertRaises(FileExistsError):
                    MODULE.commit_staged_directory(
                        staged, output, replace_existing=False
                    )

            self.assertEqual(
                (output / "late-file.txt").read_text(encoding="utf-8"), "keep me"
            )
            self.assertTrue(staged.is_dir())

    def test_commit_without_force_does_not_replace_a_concurrent_empty_directory(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = root / "qa"
            staged = root / ".qa.staging"
            staged.mkdir()
            (staged / MODULE.OUTPUT_MARKER).write_text(
                MODULE.json.dumps(MODULE.OUTPUT_MARKER_CONTENT), encoding="utf-8"
            )
            real_publish = MODULE.rename_directory_noreplace
            injected = {"done": False}

            def inject_empty_directory(source, destination):
                if not injected["done"]:
                    injected["done"] = True
                    output.mkdir()
                return real_publish(source, destination)

            with patch.object(
                MODULE,
                "rename_directory_noreplace",
                side_effect=inject_empty_directory,
            ):
                with self.assertRaises(FileExistsError):
                    MODULE.commit_staged_directory(
                        staged, output, replace_existing=False
                    )

            self.assertTrue(output.is_dir())
            self.assertEqual(list(output.iterdir()), [])
            self.assertTrue(staged.is_dir())

    def test_commit_without_force_publishes_a_complete_owned_directory(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = root / "qa"
            staged = root / ".qa.staging"
            pages = staged / "pages"
            pages.mkdir(parents=True)
            (pages / "1.jpeg").write_bytes(b"image")
            (staged / MODULE.OUTPUT_MARKER).write_text(
                MODULE.json.dumps(MODULE.OUTPUT_MARKER_CONTENT), encoding="utf-8"
            )

            MODULE.commit_staged_directory(staged, output, replace_existing=False)

            self.assertTrue(MODULE.is_owned_output_directory(output))
            self.assertEqual((output / "pages" / "1.jpeg").read_bytes(), b"image")
            self.assertFalse(staged.exists())

    def test_image_directory_publication_stays_on_anchored_parent_after_swap(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            parent = root / "output-parent"
            parent.mkdir()
            redirected = root / "redirected"
            redirected.mkdir()
            displaced = root / "output-parent-displaced"
            source = root / "private-stage"
            (source / "pages").mkdir(parents=True)
            (source / MODULE.OUTPUT_MARKER).write_text(
                MODULE.json.dumps(MODULE.OUTPUT_MARKER_CONTENT),
                encoding="utf-8",
            )
            (source / "pages" / "page-0001.jpeg").write_bytes(b"image")
            (source / "overview.jpg").write_bytes(b"overview")
            output = parent / "qa"
            staged = parent / ".qa.staging"

            with MODULE.AnchoredDirectory.open(parent) as capability:
                staged_root = MODULE.copy_tree_into_capability(
                    source,
                    capability,
                    staged.name,
                )
                try:
                    parent.rename(displaced)
                    parent.symlink_to(redirected, target_is_directory=True)
                except OSError as exc:
                    self.skipTest(f"symlink ancestor exchange is unavailable: {exc}")
                if os.name == "nt":
                    with self.assertRaisesRegex(
                        MODULE.ExportError, "directory path changed"
                    ):
                        MODULE.commit_staged_directory(
                            staged,
                            output,
                            replace_existing=False,
                            expected_snapshot=MODULE.DirectorySnapshot(
                                MODULE.PathSnapshot(False),
                                (),
                            ),
                            directory_capability=capability,
                        )
                else:
                    MODULE.commit_staged_directory(
                        staged,
                        output,
                        replace_existing=False,
                        expected_snapshot=MODULE.DirectorySnapshot(
                            MODULE.PathSnapshot(False),
                            (),
                        ),
                        directory_capability=capability,
                    )
                with self.assertRaisesRegex(MODULE.ExportError, "directory path changed"):
                    capability.assert_path_binding()

            self.assertFalse((redirected / "qa").exists())
            if os.name == "nt":
                self.assertTrue((displaced / staged.name).is_dir())
            else:
                self.assertEqual(
                    (displaced / "qa" / "pages" / "page-0001.jpeg").read_bytes(),
                    b"image",
                )
            self.assertEqual(staged_root.mode & 0o170000, MODULE.stat.S_IFDIR)

    def test_commit_without_force_failure_leaves_no_destination_partial(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            output = root / "qa"
            staged = root / ".qa.staging"
            pages = staged / "pages"
            pages.mkdir(parents=True)
            (pages / "1.jpeg").write_bytes(b"image")
            (staged / MODULE.OUTPUT_MARKER).write_text(
                MODULE.json.dumps(MODULE.OUTPUT_MARKER_CONTENT), encoding="utf-8"
            )

            with patch.object(
                MODULE,
                "rename_directory_noreplace",
                side_effect=OSError("simulated atomic publication failure"),
            ):
                with self.assertRaisesRegex(OSError, "simulated atomic publication failure"):
                    MODULE.commit_staged_directory(
                        staged, output, replace_existing=False
                    )

            self.assertFalse(output.exists())
            self.assertEqual((staged / "pages" / "1.jpeg").read_bytes(), b"image")
            self.assertTrue(MODULE.is_owned_output_directory(staged))

    def test_export_failure_preserves_output_and_uses_only_session_downloads(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            project = root / "project"
            pages = project / "pages"
            pages.mkdir(parents=True)
            manifest = project / "deck.pptd"
            manifest.write_text("manifest", encoding="utf-8")
            (pages / "01.page").write_text("page", encoding="utf-8")
            output = project / ".qa-images"
            output.mkdir()
            (output / MODULE.OUTPUT_MARKER).write_text(
                MODULE.json.dumps(MODULE.OUTPUT_MARKER_CONTENT), encoding="utf-8"
            )
            marker = output / "known-good.txt"
            marker.write_text("known-good", encoding="utf-8")
            payload = {"pages": [{"path": "pages/01.page"}], "imageMap": {}}
            observed_roots = []

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

            def fake_find_download(search_roots, **_kwargs):
                roots = tuple(search_roots)
                observed_roots.extend(roots)
                downloaded = roots[0] / "images.zip"
                make_images_zip(downloaded, names=("1.jpeg",))
                return downloaded

            with patch.object(MODULE, "find_manifest", return_value=manifest), \
                    patch.object(MODULE, "build_payload", return_value=payload), \
                    patch.object(MODULE, "ensure_agent_browser", return_value="agent-browser"), \
                    patch.object(MODULE, "ensure_pillow", return_value=(None, None, None)), \
                    patch.object(MODULE, "ensure_websocket", return_value=object()), \
                    patch.object(
                        MODULE,
                        "serve",
                        return_value=(FakeServer(), FakeThread(), "http://localhost/"),
                    ), \
                    patch.object(MODULE, "BrowserSession", FakeBrowser), \
                    patch.object(MODULE, "writer_control_ref", return_value="e1"), \
                    patch.object(MODULE, "wait_for_export_dialog", return_value={}), \
                    patch.object(MODULE, "select_image_format", return_value=None), \
                    patch.object(MODULE, "find_download", side_effect=fake_find_download), \
                    patch.object(
                        MODULE,
                        "stitch_overview",
                        side_effect=MODULE.ExportError("simulated stitch failure"),
                    ):
                with self.assertRaisesRegex(MODULE.ExportError, "stitch failure"):
                    MODULE.export_images(project, output, force=True)

            self.assertEqual(marker.read_text(encoding="utf-8"), "known-good")
            self.assertEqual(len(observed_roots), 1)
            self.assertEqual(observed_roots[0].name, "downloads")
            self.assertFalse(any(project.glob(".*.staging")))

    def test_keep_browser_raw_returns_the_published_zip_path(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name) / "project"
            pages = project / "pages"
            pages.mkdir(parents=True)
            manifest = project / "deck.pptd"
            manifest.write_text("manifest", encoding="utf-8")
            (pages / "01.page").write_text("page", encoding="utf-8")
            output = project / ".qa-images"
            payload = {"pages": [{"path": "pages/01.page"}], "imageMap": {}}
            browser_arguments = []

            class FakeBrowser:
                def __init__(self, *args, **_kwargs):
                    browser_arguments.append(args)

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

            def fake_find_download(search_roots, **_kwargs):
                downloaded = tuple(search_roots)[0] / "images.zip"
                make_images_zip(downloaded, names=("1.jpeg",))
                return downloaded

            def fake_stitch(_images, destination, *_args):
                destination.write_bytes(b"overview")
                return destination

            with patch.object(MODULE, "find_manifest", return_value=manifest), \
                    patch.object(MODULE, "build_payload", return_value=payload), \
                    patch.object(MODULE, "ensure_agent_browser", return_value="agent-browser"), \
                    patch.object(MODULE, "ensure_pillow", return_value=(None, None, None)), \
                    patch.object(MODULE, "ensure_websocket", return_value=object()), \
                    patch.object(MODULE, "ensure_debug_chrome", return_value=9444), \
                    patch.object(
                        MODULE,
                        "serve",
                        return_value=(FakeServer(), FakeThread(), "http://localhost/"),
                    ), \
                    patch.object(MODULE, "BrowserSession", FakeBrowser), \
                    patch.object(MODULE, "writer_control_ref", return_value="e1"), \
                    patch.object(MODULE, "wait_for_export_dialog", return_value={}), \
                    patch.object(MODULE, "select_image_format", return_value=None), \
                    patch.object(MODULE, "find_download", side_effect=fake_find_download), \
                    patch.object(MODULE, "stitch_overview", side_effect=fake_stitch):
                summary = MODULE.export_images(
                    project,
                    output,
                    keep_download=True,
                )

            raw = output.resolve() / "browser-raw.zip"
            self.assertEqual(summary["browserRawOutput"], str(raw))
            self.assertTrue(raw.is_file())
            self.assertEqual(browser_arguments[0][-1], 9444)

    def test_stitch_overview_grid(self):
        try:
            image_cls, draw_cls, image_font = MODULE.ensure_pillow()
        except MODULE.ExportError:
            self.skipTest("Pillow is not available")
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            images = []
            for index in range(1, 5):
                path = root / f"{index}.jpeg"
                image = image_cls.new("RGB", (320, 180), (index * 40 % 255, 30, 60))
                image.save(path, "JPEG")
                images.append(path)
            overview = MODULE.stitch_overview(
                images, root / "overview.jpg", image_cls, draw_cls, image_font
            )
            self.assertTrue(overview.is_file())
            with image_cls.open(overview) as result:
                self.assertEqual(
                    result.width,
                    3 * MODULE.OVERVIEW_THUMB_WIDTH + 4 * MODULE.OVERVIEW_GAP,
                )
                rows = 2
                cell = MODULE.OVERVIEW_LABEL_HEIGHT + 360
                self.assertEqual(result.height, rows * cell + (rows + 1) * MODULE.OVERVIEW_GAP)

    def test_stitch_overview_wraps_non_oserror_decoder_failures(self):
        class DecoderFailure(Exception):
            pass

        class FakeImage:
            @staticmethod
            def open(_path):
                raise DecoderFailure("simulated decompression bomb")

        with self.assertRaisesRegex(MODULE.ExportError, "safely decode"):
            MODULE.stitch_overview(
                [Path("page.png")], Path("overview.jpg"), FakeImage, None, None
            )

    def test_overview_layout_scales_large_decks_within_resource_limits(self):
        layout = MODULE.overview_layout([(1920, 1080)] * 500)
        self.assertLess(layout["thumbWidth"], MODULE.OVERVIEW_THUMB_WIDTH)
        self.assertLessEqual(layout["width"], MODULE.MAX_OVERVIEW_DIMENSION)
        self.assertLessEqual(layout["height"], MODULE.MAX_OVERVIEW_DIMENSION)
        self.assertLessEqual(
            layout["width"] * layout["height"], MODULE.MAX_OVERVIEW_PIXELS
        )

    def test_overview_layout_rejects_decompression_bomb_dimensions(self):
        with self.assertRaisesRegex(MODULE.ExportError, "pixel safety limit"):
            MODULE.overview_layout([(10_000, 10_000)])

    def test_overview_layout_rejects_aggregate_source_pixel_budget(self):
        with patch.object(MODULE, "MAX_TOTAL_SOURCE_IMAGE_PIXELS", 10):
            with self.assertRaisesRegex(MODULE.ExportError, "aggregate .*pixel"):
                MODULE.overview_layout([(2, 3), (2, 3)])


if __name__ == "__main__":
    unittest.main()
