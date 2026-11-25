from datetime import date
from pathlib import Path

import pytest
from pydantic import HttpUrl

from book_agent.cli import (
    EXIT_ERROR,
    EXIT_OK,
    agent_command,
    format_library_plan,
    library_command,
    main,
)
from book_agent.domain.library import RelatedBook
from book_agent.domain.models import Book, CatalogBook, Profile, ReadingStatus
from book_agent.domain.recommendations import Filters
from book_agent.domain.works import WorkInfo
from book_agent.embedding_index import VectorStore
from book_agent.library_enrichment import EnrichmentCache
from book_agent.llm.base import ToolCallPart
from book_agent.prompts import agent_v4
from book_agent.work_keys import CacheEntry, WorkKeyCache
from tests.fakes import (
    FakeBookSearch,
    FakeEmbedder,
    FakeLibraryRetriever,
    FakeLLM,
    FakeWorkDescriptions,
    text_response,
    tool_call_response,
)
from tests.library_data import CLEAN_THREE, catalog
from tests.library_data import PROFILE as LIBRARY

TODAY = date(2026, 11, 24)
DUNE = Book(title="Dune", author="Frank Herbert", isbn13="9780441013593", my_rating=5.0,
            status=ReadingStatus.READ)  # fmt: skip
SEA = Book(title="Moby Dick", author="Herman Melville", my_rating=None, status=ReadingStatus.READ)
LOW = Book(title="Twilight", author="Stephenie Meyer", my_rating=2.0, status=ReadingStatus.READ)
PROFILE = Profile(name="Reader", books=[DUNE, SEA, LOW])
WORK_KEYS = WorkKeyCache(entries={"9780441013593": CacheEntry(work="/works/OL1W", checked=TODAY)})
MOBY = CatalogBook(source_id="moby", title="Moby Dick", authors=("Herman Melville",),
                   description="A whale.", link=HttpUrl("https://example.com"))  # fmt: skip


def stores(embedder: FakeEmbedder) -> VectorStore:
    return VectorStore(model=embedder.model, dimensions=embedder.dimensions)


def test_the_plan_counts_the_pool_and_the_work_to_do() -> None:
    plan = format_library_plan(PROFILE, EnrichmentCache(), stores(FakeEmbedder()), refresh=False)
    assert "Retrieval pool: 2 read books (rated 3+ or unrated)" in plan
    assert "To describe: 2 (Open Library first, then at most 2 Google Books searches)" in plan
    assert "To embed: 2 now" in plan


async def test_library_command_describes_embeds_and_reports_coverage() -> None:
    embedder = FakeEmbedder()
    enrichment, documents = EnrichmentCache(), stores(embedder)
    saved: list[str] = []
    output, code = await library_command(
        PROFILE, enrichment, documents, WORK_KEYS,
        works=FakeWorkDescriptions({"/works/OL1W": WorkInfo(description="Spice.")}),
        search=FakeBookSearch([MOBY]), embedder=embedder, today=TODAY,
        save_enrichment=lambda c: saved.append("enrichment"),
        save_documents=lambda s: saved.append("documents"),
    )  # fmt: skip
    assert code == EXIT_OK
    assert "Described: 1 from Open Library, 1 from Google Books, 0 with no description" in output
    assert "Embedded: 2 books" in output
    assert "With a description: 2 of 2 pool books, 1 of 1 liked books" in output
    assert saved == ["enrichment", "enrichment", "documents"]
    rerun, _ = await library_command(
        PROFILE, enrichment, documents, WORK_KEYS, works=None, search=FakeBookSearch(),
        embedder=embedder, today=TODAY,
    )  # fmt: skip
    assert "Described: 0 from Open Library, 0 from Google Books" in rerun
    assert "Embedded: 0 books" in rerun


async def test_an_embedding_failure_keeps_the_descriptions_and_exits_with_an_error() -> None:
    enrichment = EnrichmentCache()
    output, code = await library_command(
        PROFILE, enrichment, stores(FakeEmbedder()), WORK_KEYS, works=None,
        search=FakeBookSearch([MOBY]), embedder=FakeEmbedder(fail=True), today=TODAY,
    )  # fmt: skip
    assert code == EXIT_ERROR and "error: embedding failed" in output
    assert len(enrichment.entries) == 2  # both described: Dune as "none", Moby Dick found


async def test_agent_report_lists_the_related_books_shown() -> None:
    llm = FakeLLM([
        text_response('{"preference": "whales", "language": null, "max_pages": null}'),
        tool_call_response(ToolCallPart(
            id="s-1", name="submit_recommendations",
            arguments={"recommendations": [r.model_dump() for r in CLEAN_THREE]},
        )),
    ])  # fmt: skip
    retriever = FakeLibraryRetriever([RelatedBook(title="Ficciones", author="Borges", rating=None,
                                                  description=None)])  # fmt: skip
    output, _ = await agent_command(
        LIBRARY, Filters(), "a whale story", llm=llm, books=catalog(),
        prompt=agent_v4.PROMPT, retriever=retriever,
    )  # fmt: skip
    assert "Prompt agent_v4" in output and "Related books shown: Ficciones" in output


def test_agent_v4_without_an_index_fails_before_any_call(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv = tmp_path / "library.csv"
    csv.write_text(Path("tests/fixtures/goodreads_sample.csv").read_text(encoding="utf-8"))
    monkeypatch.chdir(tmp_path)  # no data/ here: no index
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    code = main(["agent", "x", "--prompt", "agent_v4", "--csv", str(csv), "--cache", "c.json"])
    assert code == EXIT_ERROR
    assert "the library index is out of date" in capsys.readouterr().out


def test_library_without_yes_only_plans(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv = tmp_path / "library.csv"
    csv.write_text(Path("tests/fixtures/goodreads_sample.csv").read_text(encoding="utf-8"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert main(["library", "--csv", str(csv), "--cache", "c.json"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Retrieval pool:" in out and out.rstrip().endswith("Add --yes to start.")
    assert not (tmp_path / "data").exists()


def test_export_profile_writes_only_the_agreed_fields(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    out = tmp_path / "owner.json"
    code = main(["export-profile", "--out", str(out), "--name", "Demo owner",
                 "--csv", "tests/fixtures/goodreads_sample.csv"])  # fmt: skip
    assert code == EXIT_OK and "Keep it out of the repo" in capsys.readouterr().out
    text = out.read_text()
    assert '"Demo owner"' in text and "goodreads_id" not in text and '"pages"' not in text
