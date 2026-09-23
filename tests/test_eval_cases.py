"""Offline eval-case validation.

The JSON cases describe "task -> expected tool calls". A live eval would run
them against a real provider; offline we validate that every expected tool
exists in the registry schema snapshot and that the arguments conform to
that schema (required keys, enums, primitive types, array item counts).

The schema snapshot (tests/tool_schemas.json) is generated from Blender 5.2:
    blender --background --python-expr "..."   # see repository docs
"""

import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(path, default):
    try:
        with open(os.path.join(HERE, path), encoding="utf-8") as fh:
            return json.load(fh)
    except OSError:
        return default


CASES = _load("eval_cases.json", [])
SCHEMAS = _load("tool_schemas.json", [])


def validate_against_schema(value, schema, path="args"):
    """Minimal JSON-Schema subset validator (type/enum/required/items)."""
    errors = []
    if "enum" in schema and value not in schema["enum"]:
        errors.append("%s: %r not in enum %s" % (path, value, schema["enum"]))
        return errors
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            return ["%s: expected object" % path]
        for key in schema.get("required", []):
            if key not in value:
                errors.append("%s: missing required %r" % (path, key))
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                errors += validate_against_schema(value[key], sub,
                                                  "%s.%s" % (path, key))
    elif expected == "array":
        if not isinstance(value, list):
            return ["%s: expected array" % path]
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append("%s: fewer than %d items"
                          % (path, schema["minItems"]))
        if "items" in schema:
            for i, item in enumerate(value):
                errors += validate_against_schema(item, schema["items"],
                                                  "%s[%d]" % (path, i))
    elif expected == "string":
        if not isinstance(value, str):
            errors.append("%s: expected string" % path)
    elif expected == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            errors.append("%s: expected number" % path)
    elif expected == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            errors.append("%s: expected integer" % path)
    elif expected == "boolean":
        if not isinstance(value, bool):
            errors.append("%s: expected boolean" % path)
    return errors


@unittest.skipUnless(CASES, "eval_cases.json missing")
@unittest.skipUnless(SCHEMAS, "tool_schemas.json missing (generate from Blender)")
class TestEvalCases(unittest.TestCase):
    def setUp(self):
        registry = {}
        for schema in SCHEMAS:
            function = schema["function"]
            registry[function["name"]] = function
        self.registry = registry

    def test_cases_exist(self):
        self.assertGreaterEqual(len(CASES), 5)

    def test_tools_and_arguments_match_registry(self):
        problems = []
        for case in CASES:
            for call in case.get("expected_tool_calls", []):
                name = call.get("name")
                function = self.registry.get(name)
                if function is None:
                    problems.append("%s: unknown tool %r" % (case["id"], name))
                    continue
                problems += [
                    "%s/%s: %s" % (case["id"], name, err)
                    for err in validate_against_schema(
                        call.get("arguments", {}),
                        function["parameters"],
                    )
                ]
        self.assertEqual(problems, [])

    def test_ambiguity_case_asks_user(self):
        case = next(c for c in CASES if c["id"] == "ambiguity_asks_user")
        self.assertEqual(case["expected_tool_calls"][0]["name"], "ask_user")


if __name__ == "__main__":
    unittest.main()
