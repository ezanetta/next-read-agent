import pytest
from pydantic import BaseModel, ValidationError

from book_agent.pydantic_errors import describe


class _Book(BaseModel):
    title: str
    pages: int


def test_one_line_per_field_without_links() -> None:
    with pytest.raises(ValidationError) as info:
        _Book.model_validate({"pages": "many"})
    text = describe(info.value)
    assert text == (
        "title: Field required; "
        "pages: Input should be a valid integer, unable to parse string as an integer"
    )
    assert "\n" not in text and "errors.pydantic.dev" not in text
