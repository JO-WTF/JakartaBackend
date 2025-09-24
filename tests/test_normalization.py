from app.utils.normalization import (
    normalize_du,
    normalize_dn,
    normalize_batch_dn_numbers,
    collect_query_values,
    strip_optional_string,
)


def test_normalize_du_handles_full_width_and_whitespace():
    assert normalize_du("　du１２３ ") == "DU123"


def test_normalize_dn_aliases_du_logic():
    assert normalize_dn("dn００１") == "DN001"


def test_normalize_batch_dn_numbers_deduplicates_and_validates():
    numbers = normalize_batch_dn_numbers([" dn1 ", "DN1", "dn2,dn3", ""])
    assert numbers == ["DN1", "DN2", "DN3"]


def test_collect_query_values_handles_iterables_and_commas():
    values = collect_query_values(["a", "b"], "a, c", None)
    assert values == ["a", "b", "c"]


def test_strip_optional_string_trims_and_handles_empty():
    assert strip_optional_string(" value ") == "value"
    assert strip_optional_string("   ") is None
    assert strip_optional_string(None) is None
