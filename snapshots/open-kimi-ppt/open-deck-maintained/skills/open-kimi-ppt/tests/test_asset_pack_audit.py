#!/usr/bin/env python3
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "asset_pack_audit.py"
SPEC = importlib.util.spec_from_file_location("asset_pack_audit", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class AssetPackAuditTests(unittest.TestCase):
    def make_pack(self, root: Path, *, license_id="CC0-1.0", trademarks=False, kind="sounds"):
        asset = root / "sounds" / "click.wav"
        asset.parent.mkdir(parents=True)
        asset.write_bytes(b"RIFF-test-audio")
        notice = root / "THIRD_PARTY_NOTICES.txt"
        notice.write_text("source notice\n", encoding="utf-8")
        source = {
            "license": license_id,
            "license_url": "https://example.com/license",
            "source_url": "https://example.com/source",
            "contains_trademarks": trademarks,
        }
        if license_id != "CC0-1.0":
            source["notice"] = notice.name
        if license_id == "CC-BY-4.0":
            source["attribution"] = "Example Author, CC BY 4.0"
        if trademarks:
            source["usage_policy_url"] = "https://example.com/trademarks"
        manifest = {
            "pack_id": "sample-sounds",
            "version": "1.0.0",
            "snapshot_date": "2026-08-12",
            "kind": kind,
            "distribution": "optional-pack",
            "default_enabled": False,
            "sources": {"example": source},
            "assets": [
                {
                    "path": "sounds/click.wav",
                    "source": "example",
                    "sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
                }
            ],
        }
        manifest_path = root / "asset-pack.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest_path, manifest

    def test_cc0_pack_passes_and_is_fingerprinted(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest_path, _manifest = self.make_pack(Path(directory))
            report = MODULE.audit_asset_pack(manifest_path)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["receipt"]["assetsVerified"], 1)
        self.assertEqual(report["receipt"]["licenses"], ["CC0-1.0"])
        self.assertRegex(report["pack"]["treeSha256"], r"^[0-9a-f]{64}$")

    def test_cc_by_requires_attribution_and_notice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path, manifest = self.make_pack(root, license_id="CC-BY-4.0")
            manifest["sources"]["example"].pop("attribution")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            report = MODULE.audit_asset_pack(manifest_path)
        self.assertEqual(report["status"], "fail")
        self.assertIn("source.attribution", {item["code"] for item in report["findings"]})

    def test_digest_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path, manifest = self.make_pack(root)
            manifest["assets"][0]["sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            report = MODULE.audit_asset_pack(manifest_path)
        self.assertEqual(report["status"], "fail")
        self.assertIn("asset.digest", {item["code"] for item in report["findings"]})

    def test_snapshot_date_and_version_match_schema_constraints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path, manifest = self.make_pack(root)
            manifest["snapshot_date"] = "2026-02-30"
            manifest["version"] = "v" * 129
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            report = MODULE.audit_asset_pack(manifest_path)
        codes = {item["code"] for item in report["findings"]}
        self.assertEqual(report["status"], "fail")
        self.assertIn("manifest.snapshot_date", codes)
        self.assertIn("manifest.version", codes)

    def test_brand_assets_require_redistribution_permission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path, manifest = self.make_pack(root, trademarks=True, kind="brand-assets")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            report = MODULE.audit_asset_pack(manifest_path)
        codes = {item["code"] for item in report["findings"]}
        self.assertEqual(report["status"], "fail")
        self.assertIn("source.brand.permission", codes)
        self.assertIn("source.brand.redistribution", codes)
        self.assertIn("source.brand.evidence", codes)

    def test_mixed_pack_requires_classification_and_brand_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path, manifest = self.make_pack(
                root, trademarks=True, kind="mixed"
            )
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            unclassified = MODULE.audit_asset_pack(manifest_path)
            self.assertIn(
                "source.content_kind",
                {item["code"] for item in unclassified["findings"]},
            )

            source = manifest["sources"]["example"]
            source.update(
                {
                    "content_kind": "brand-assets",
                    "permission_basis": "official-redistribution-license",
                    "permission_evidence": {
                        "type": "url",
                        "url": "https://example.com/redistribution-permission",
                    },
                    "redistributable": True,
                }
            )
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            admitted = MODULE.audit_asset_pack(manifest_path)
            self.assertEqual(admitted["status"], "pass", admitted["findings"])

    def test_required_notice_bytes_are_bound_into_tree_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path, _manifest = self.make_pack(root, license_id="MIT")
            first = MODULE.audit_asset_pack(manifest_path)
            notice = root / "THIRD_PARTY_NOTICES.txt"
            notice.write_text("changed source notice\n", encoding="utf-8")
            second = MODULE.audit_asset_pack(manifest_path)
            self.assertEqual(first["status"], "pass")
            self.assertEqual(second["status"], "pass")
            self.assertNotEqual(first["pack"]["treeSha256"], second["pack"]["treeSha256"])
            self.assertNotEqual(first["pack"]["noticeSha256"], second["pack"]["noticeSha256"])

            notice.write_bytes(b"")
            empty = MODULE.audit_asset_pack(manifest_path)
            self.assertIn(
                "source.notice.empty", {item["code"] for item in empty["findings"]}
            )

    def test_derived_assets_require_declared_parent_modifications_and_no_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path, manifest = self.make_pack(root, license_id="CC-BY-4.0")
            derived = root / "sounds" / "derived.wav"
            derived.write_bytes(b"derived sound")
            manifest["assets"].append(
                {
                    "path": "sounds/derived.wav",
                    "source": "example",
                    "sha256": hashlib.sha256(derived.read_bytes()).hexdigest(),
                    "derived_from": "sounds/click.wav",
                }
            )
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            missing_record = MODULE.audit_asset_pack(manifest_path)
            self.assertIn(
                "asset.derived_from.modifications",
                {item["code"] for item in missing_record["findings"]},
            )

            manifest["assets"][1]["modifications"] = ["trimmed and normalized"]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            valid = MODULE.audit_asset_pack(manifest_path)
            self.assertEqual(valid["status"], "pass", valid["findings"])

            manifest["assets"][0]["derived_from"] = "sounds/derived.wav"
            manifest["assets"][0]["modifications"] = ["source edit"]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            cycle = MODULE.audit_asset_pack(manifest_path)
            self.assertIn(
                "asset.derived_from.cycle",
                {item["code"] for item in cycle["findings"]},
            )

    def test_manifest_symlink_is_rejected_before_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path, _manifest = self.make_pack(root)
            link = root / "linked-pack.json"
            try:
                link.symlink_to(manifest_path.name)
            except (NotImplementedError, OSError):
                self.skipTest("symbolic links are unavailable")
            with self.assertRaisesRegex(MODULE.AssetAuditError, "non-symlink"):
                MODULE.audit_asset_pack(link)

    def test_duplicate_manifest_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path, manifest = self.make_pack(root)
            encoded = json.dumps(manifest)
            manifest_path.write_text(
                encoded[:-1] + ', "kind": "mixed"}', encoding="utf-8"
            )
            with self.assertRaisesRegex(MODULE.AssetAuditError, "duplicate JSON key"):
                MODULE.audit_asset_pack(manifest_path)

    def test_parent_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path, manifest = self.make_pack(root)
            manifest["assets"][0]["path"] = "../outside.wav"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            report = MODULE.audit_asset_pack(manifest_path)
        self.assertEqual(report["status"], "fail")
        self.assertIn("asset.path", {item["code"] for item in report["findings"]})

    def test_report_publish_is_no_clobber_and_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path, _manifest = self.make_pack(root)
            report = MODULE.audit_asset_pack(manifest_path)
            destination = root / "audit.json"
            MODULE._write_report(destination, report, force=False)
            with self.assertRaisesRegex(MODULE.AssetAuditError, "already exists"):
                MODULE._write_report(destination, report, force=False)

            target = root / "target.json"
            target.write_text("keep", encoding="utf-8")
            destination.unlink()
            try:
                destination.symlink_to(target)
            except (NotImplementedError, OSError):
                self.skipTest("symbolic links are unavailable")
            with self.assertRaisesRegex(MODULE.AssetAuditError, "symbolic link"):
                MODULE._write_report(destination, report, force=True)
            self.assertEqual(target.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
