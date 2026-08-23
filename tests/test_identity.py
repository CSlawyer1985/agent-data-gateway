"""身份模型 + 锚点确定性（spec 01/07）。"""

import unittest

from gateway import anchor
from gateway.hashing import canonical_json, package_id_of, rendition_id_of, source_id_of_bytes


class IdentityTest(unittest.TestCase):
    def test_source_id_stable_across_parsers(self):
        data = b"hello"
        sid = source_id_of_bytes(data)
        # source_id 与任何 parser 无关
        self.assertEqual(sid, "sha256:" + "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824")

    def test_rendition_id_deterministic(self):
        content = {"blocks": []}
        ann = {"comments": []}
        cs = {"name": "page_bbox", "units": "0-1000", "origin": "top-left"}
        r1 = rendition_id_of(content, ann, cs)
        r2 = rendition_id_of(content, ann, cs)
        self.assertEqual(r1, r2)
        # 内容变 → rendition 变
        r3 = rendition_id_of({"blocks": [1]}, ann, cs)
        self.assertNotEqual(r1, r3)

    def test_package_id_order_independent(self):
        ids = ["sha256:aaa", "sha256:bbb", "sha256:ccc"]
        self.assertEqual(package_id_of(ids), package_id_of(list(reversed(ids))))

    def test_canonical_json_stable(self):

        a = canonical_json({"b": 1, "a": 2})
        b = canonical_json({"a": 2, "b": 1})
        self.assertEqual(a, b)
        # 无空白
        self.assertNotIn(" ", a)


class AnchorIdTest(unittest.TestCase):
    def test_element_id_deterministic_same_config(self):
        loc = {"system": "paragraph", "index": 3}
        e1 = anchor.derive_element_id("docx_lite", "0.1.0", "abc123", loc, 0)
        e2 = anchor.derive_element_id("docx_lite", "0.1.0", "abc123", loc, 0)
        self.assertEqual(e1, e2)
        self.assertTrue(e1.startswith("el_"))
        self.assertEqual(len(e1), 3 + 12)

    def test_element_id_changes_with_parser(self):
        loc = {"system": "paragraph", "index": 0}
        e1 = anchor.derive_element_id("pypdf", "0.1.0", "c", loc, 0)
        e2 = anchor.derive_element_id("docling", "0.1.0", "c", loc, 0)
        self.assertNotEqual(e1, e2)

    def test_element_id_changes_with_ordinal(self):
        loc = {"system": "paragraph", "index": 0}
        e1 = anchor.derive_element_id("p", "v", "c", loc, 0)
        e2 = anchor.derive_element_id("p", "v", "c", loc, 1)
        self.assertNotEqual(e1, e2)

    def test_validate_document_catches_bad_locator(self):
        doc = {
            "content": {"blocks": [{"block_type": "page", "index": 0, "elements": [
                {"id": "el_abc", "type": "paragraph", "locator": {"system": "timecode", "start": 5, "end": 3}},
            ]}]},
            "annotations": {"comments": [], "revisions": []},
        }
        problems = anchor.validate_document(doc)
        self.assertTrue(any("timecode" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
