import pytest

from book_agent.domain.isbn import to_isbn13


@pytest.mark.parametrize(
    ("raw", "isbn13"),
    [
        ("9871165072", "9789871165070"),  # La metamorfosis in the real Goodreads export
        ("9789871165070", "9789871165070"),
        ("978-0-553-21369-0", "9780553213690"),
        (" 0553213695 ", "9780553213690"),
        ("080442957X", "9780804429573"),  # check digit X
        ("080442957x", "9780804429573"),
        ("9798569694662", "9798569694662"),  # 979 prefix, no ISBN-10 form
    ],
)
def test_valid_isbns_become_isbn13(raw: str, isbn13: str) -> None:
    assert to_isbn13(raw) == isbn13


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "9871165073",  # wrong ISBN-10 check digit
        "9789871165071",  # wrong ISBN-13 check digit
        "X804429570",  # X only allowed as the check digit
        "1234567890123",  # 13 digits without a 978/979 prefix
        "B00ABC1234",  # an ASIN, as on Kindle rows
        "12345",
    ],
)
def test_invalid_values_give_none(raw: str | None) -> None:
    assert to_isbn13(raw) is None
