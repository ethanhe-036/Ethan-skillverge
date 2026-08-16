#!/usr/bin/env python3
import json
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
REGISTRIES = SKILL / "reference" / "registries"
SCHEMAS = SKILL / "reference" / "schemas"


class RegistryTests(unittest.TestCase):
    def load(self, name):
        return json.loads((REGISTRIES / name).read_text(encoding="utf-8"))

    def test_design_registry_resolves_every_path_and_id_is_unambiguous(self):
        registry = self.load("design-systems.json")
        canonical = registry["canonical"]
        supplemental = registry["supplemental"]
        self.assertEqual(len(canonical), 30)
        self.assertEqual(len(supplemental), 14)
        selectable = canonical + supplemental
        ids = [entry["id"] for entry in selectable]
        self.assertEqual(len(ids), len(set(ids)))
        for entry in selectable + registry["legacyVariants"]:
            self.assertTrue((SKILL / entry["path"]).is_file(), entry["path"])
        canonical_ids = {entry["id"] for entry in canonical}
        self.assertTrue(all(entry["id"] in canonical_ids for entry in registry["legacyVariants"]))

    def test_layouts_have_unique_ids_and_bounded_regions(self):
        layouts = self.load("layouts.json")["layouts"]
        self.assertGreaterEqual(len(layouts), 10)
        self.assertEqual(len(layouts), len({entry["id"] for entry in layouts}))
        for layout in layouts:
            self.assertTrue(layout["pickFor"])
            self.assertTrue(layout["skipIf"])
            for bounds in layout["regions"].values():
                self.assertEqual(len(bounds), 4)
                x, y, width, height = bounds
                self.assertGreaterEqual(min(bounds), 0)
                self.assertLessEqual(x + width, 1.000001)
                self.assertLessEqual(y + height, 1.000001)

    def test_chart_registry_covers_every_documented_pptd_type(self):
        charts = self.load("charts.json")["charts"]
        expected = {
            "bar", "line", "area", "scatter", "bubble", "candlestick", "pie",
            "radar", "waterfall", "heatmap", "treemap", "sunburst", "sankey",
        }
        self.assertEqual({entry["id"] for entry in charts}, expected)
        self.assertTrue(all(entry["nativePptd"] for entry in charts))
        self.assertTrue(all(entry["pickFor"] and entry["skipIf"] for entry in charts))

    def test_machine_readable_schemas_are_valid_json_schema_documents(self):
        names = {
            "asset-pack.schema.json",
            "deck-metadata.schema.json",
            "media-sources.schema.json",
            "pptd-quality-receipt.schema.json",
            "pptd-quality-report.schema.json",
        }
        self.assertEqual({path.name for path in SCHEMAS.glob("*.json")}, names)
        for name in sorted(names):
            schema = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
            self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
            self.assertEqual(schema["type"], "object")


if __name__ == "__main__":
    unittest.main()
