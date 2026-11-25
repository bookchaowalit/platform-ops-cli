#!/usr/bin/env python3
"""The ArchiMate reader refuses any DTD, in every encoding. Synthetic models only, no documents."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "scripts"))
from architecture_provider_readiness import validate_archimate_model  # noqa: E402

MODEL = (
    '<?xml version="1.0" encoding="{enc}"?>\n{dtd}'
    '<archimate:model xmlns:archimate="http://www.archimatetool.com/archimate" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" version="5.10.0">'
    '<folder><element xsi:type="archimate:ApplicationComponent" id="a"/>'
    '<element xsi:type="archimate:ApplicationComponent" id="b"/>'
    '<element xsi:type="archimate:FlowRelationship" id="r" source="a" target="b"/>'
    '<element xsi:type="archimate:ArchimateDiagramModel" id="v">'
    '<child archimateElement="a"/><sourceConnection archimateRelationship="r"/></element>'
    "</folder></archimate:model>"
)
DTD = '<!DOCTYPE m [<!ENTITY x "y">]>'


def _validate(text: str, encoding: str) -> dict:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "model.archimate"
        path.write_bytes(text.encode(encoding))
        return validate_archimate_model(path)


class ArchimateDtdTest(unittest.TestCase):
    def test_the_same_model_is_valid_without_a_dtd_and_refused_with_one(self) -> None:
        for encoding in ("utf-8", "utf-16"):
            with self.subTest(encoding=encoding):
                plain = _validate(MODEL.format(enc=encoding, dtd=""), encoding)
                self.assertEqual(plain["state"], "valid", plain["errors"])
                self.assertEqual(_validate(MODEL.format(enc=encoding, dtd=DTD), encoding)["state"], "invalid")

    def test_unknown_encoding_fails_closed(self) -> None:
        self.assertEqual(_validate(MODEL.format(enc="utf-16-be", dtd=DTD), "utf-16-be")["state"], "invalid")


if __name__ == "__main__":
    unittest.main()
