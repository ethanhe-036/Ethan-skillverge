import re
import sys
import unittest
import zipfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT = REPOSITORY_ROOT / "skills" / "open-kimi-ppt" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

import export_pptx  # noqa: E402


PRESENTATION_NAMESPACE = b"http://schemas.openxmlformats.org/presentationml/2006/main"


class ProductExampleContractTests(unittest.TestCase):
    def test_real_examples_keep_the_documented_pptx_contract(self):
        examples = (
            (
                REPOSITORY_ROOT
                / "example"
                / "dji-pocket4"
                / "DJI Osmo Pocket 4 产品深度解读.pptx",
                18,
            ),
            (
                REPOSITORY_ROOT
                / "example"
                / "xiaomi-yu7-ppt-animation"
                / "xiaomi-yu7.pptx",
                8,
            ),
            (REPOSITORY_ROOT / "example" / "yu7-ppt" / "yu7.pptx", 8),
        )

        for pptx, expected_slides in examples:
            with self.subTest(pptx=pptx.name):
                summary = export_pptx.verify_output(
                    pptx,
                    "fade",
                    expect_fonts=True,
                    expected_slides=expected_slides,
                )
                self.assertEqual(summary["slides"], expected_slides)
                self.assertEqual(summary["fadeTransitions"], expected_slides)
                self.assertGreater(summary["fontParts"], 0)

                with zipfile.ZipFile(pptx) as archive:
                    slides = [
                        archive.read(name)
                        for name in archive.namelist()
                        if re.fullmatch(r"ppt/slides/slide[1-9]\d*\.xml", name)
                    ]
                self.assertTrue(all(PRESENTATION_NAMESPACE in slide for slide in slides))
                self.assertGreater(
                    sum(slide.count(b"<p:sp>") for slide in slides),
                    0,
                    "the example must contain editable DrawingML shapes, not only bitmaps",
                )

    def test_animation_example_exercises_every_page_and_exported_slide(self):
        example = REPOSITORY_ROOT / "example" / "xiaomi-yu7-ppt-animation"
        pages = sorted((example / "pages").glob("*.page"))
        self.assertEqual(len(pages), 8)
        self.assertTrue(
            all(re.search(r"^animations:", page.read_text(encoding="utf-8"), re.MULTILINE) for page in pages)
        )

        with zipfile.ZipFile(example / "xiaomi-yu7.pptx") as archive:
            slides = [
                archive.read(name)
                for name in archive.namelist()
                if re.fullmatch(r"ppt/slides/slide[1-9]\d*\.xml", name)
            ]
        self.assertEqual(len(slides), 8)
        self.assertTrue(all(b"<p:timing" in slide for slide in slides))


if __name__ == "__main__":
    unittest.main()
