import pytest

from tarso_pipeline.naming import sanitize_name


@pytest.mark.parametrize(
    "raw, expected",
    [
        (
            "83034-2 HB017PB01 HH0114+34_BUKR_GMW_PB1_F-264.csv",
            "83034-2_HB017PB01_HH0114_34_BUKR_GMW_PB1_F-264",
        ),
        ("83034-2_HB017PB01_HH0114_34_BUKR_GMW_PB1_F-264", "83034-2_HB017PB01_HH0114_34_BUKR_GMW_PB1_F-264"),
        ("a  b++c.csv", "a_b_c"),
        ("__lead_and_trail__.csv", "lead_and_trail"),
        ("brackets(1)[2].csv", "brackets_1_2"),
        ("dots.in.name.csv", "dots_in_name"),
        ("keep-dashes_and_underscores", "keep-dashes_and_underscores"),
    ],
)
def test_sanitize_name(raw, expected):
    assert sanitize_name(raw) == expected


def test_sanitize_is_idempotent():
    once = sanitize_name("x y+z.csv")
    assert sanitize_name(once) == once
