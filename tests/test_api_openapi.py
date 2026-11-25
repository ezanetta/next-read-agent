import json
import re
from pathlib import Path

from book_agent.api.openapi import main, schema


def test_every_operation_has_its_explicit_id() -> None:
    operations = {op["operationId"] for path in schema()["paths"].values() for op in path.values()}
    assert operations == {
        "health", "searchBooks", "createManualProfile", "createGoodreadsProfile",
        "listPresetProfiles", "getProfile", "listProfileBooks", "deleteProfile",
        "streamRecommendation",
    }  # fmt: skip


def test_the_event_union_is_in_the_components() -> None:
    components = schema()["components"]["schemas"]
    union = components["RecommendationEvent"]
    assert union["discriminator"]["propertyName"] == "type"
    assert {ref["$ref"].rsplit("/", 1)[-1] for ref in union["oneOf"]} == {
        "StartedEvent", "InterpretedEvent", "SearchingEvent", "SubmittedEvent",
        "CheckingEvent", "RejectedEvent", "DoneEvent", "FailedEvent",
    }  # fmt: skip
    assert "Body_createGoodreadsProfile" not in components
    upload = schema()["paths"]["/profiles/goodreads"]["post"]["requestBody"]["content"]
    assert upload["multipart/form-data"]["schema"]["$ref"].endswith("/GoodreadsUpload")
    for name in (
        "RecommendationResult",
        "TraceSummary",
        "CitedLibraryBook",
        "ErrorBody",
        "GoodreadsUpload",
    ):
        assert name in components
    refs = json.dumps(schema())
    for target in {r.rsplit("/", 1)[-1] for r in _refs(refs)}:
        assert target in components, f"dangling $ref to {target}"


def _refs(text: str) -> set[str]:
    return set(re.findall(r'"#/components/schemas/([^"]+)"', text))


def test_the_command_writes_the_schema(tmp_path: Path) -> None:
    out = tmp_path / "openapi.json"
    assert main(["--out", str(out)]) == 0
    assert json.loads(out.read_text())["info"]["title"] == "Book Recommendation Agent"
