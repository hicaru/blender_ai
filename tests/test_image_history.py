"""History: image_ref storage, placeholder trimming, fresh-turn expansion."""
# mypy: ignore-errors

import unittest

import _common

history = _common.load_module("history")


class TestImageRefs(unittest.TestCase):
    def test_with_image_refs_wraps_parts(self):
        stored = history.with_image_refs("look", [
            {"type": "image_ref", "sha256": "abc", "label": "a.png", "w": 64, "h": 32}])
        self.assertIsInstance(stored, list)
        self.assertEqual(stored[0], {"type": "text", "text": "look"})
        self.assertEqual(stored[1]["type"], "image_ref")

    def test_with_image_refs_no_refs_returns_text(self):
        self.assertEqual(history.with_image_refs("text only", []), "text only")

    def test_expand_images_latest_only(self):
        ref_old = {"type": "image_ref", "sha256": "old", "label": "old.png",
                   "w": 1, "h": 1, "path": "/nope"}
        ref_new = {"type": "image_ref", "sha256": "new", "label": "new.png",
                   "w": 2, "h": 2, "path": "/nope"}
        messages = [
            history.message("user", content=history.with_image_refs("first", [ref_old])),
            history.message("assistant", content="ok"),
            history.message("user", content=history.with_image_refs("second", [ref_new])),
        ]
        out = history.expand_images(messages, lambda ref: {"type": "image_url"})
        first = out[0]["content"]
        second = out[2]["content"]
        # old turn -> placeholder text
        self.assertTrue(any(p.get("text", "").startswith("[image: old.png")
                            for p in first if isinstance(p, dict)))
        # latest turn -> resolver output
        self.assertIn({"type": "image_url"}, second)

    def test_expand_images_missing_resolver(self):
        ref = {"type": "image_ref", "sha256": "x", "label": "gone.png",
               "w": 3, "h": 4, "path": "/missing.png"}
        messages = [history.message("user", content=[ref])]
        out = history.expand_images(messages, lambda r: {"type": "image_url"})
        self.assertEqual(out[0]["content"][0]["type"], "image_url")

    def test_plain_messages_untouched(self):
        messages = [history.message("user", content="plain"),
                    history.message("assistant", content="reply")]
        out = history.expand_images(messages, lambda r: {})
        self.assertEqual(out, messages)


class TestPlaceholderShape(unittest.TestCase):
    def test_placeholder_has_label_and_size(self):
        ref = {"type": "image_ref", "sha256": "x", "label": "concept.png",
               "w": 1024, "h": 768, "path": "/x.png"}
        messages = [
            history.message("user", content=[ref]),
            history.message("assistant", content="ok"),
            history.message("user", content="next ask"),
        ]
        out = history.expand_images(messages, lambda r: {"type": "image_url"})
        text = out[0]["content"][0]["text"]
        self.assertIn("concept.png", text)
        self.assertIn("1024x768", text)


if __name__ == "__main__":
    unittest.main()
