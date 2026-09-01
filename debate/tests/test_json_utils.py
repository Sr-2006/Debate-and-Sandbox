from json_utils import safe_parse_json


def test_parses_clean_json():
    assert safe_parse_json('{"a": 1}') == {"a": 1}


def test_strips_code_fences():
    assert safe_parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extracts_object_from_prose():
    assert safe_parse_json('Here is the result: {"a": 1} hope that helps') == {"a": 1}


def test_repairs_truncated_json():
    assert safe_parse_json('{"a": "b"') == {"a": "b"}


def test_empty_returns_fallback():
    fb = {"logic": "x"}
    assert safe_parse_json("", fallback=fb) == fb


def test_garbage_returns_fallback():
    fb = {"logic": "fb"}
    assert safe_parse_json("not json at all", fallback=fb) == fb
