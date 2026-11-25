"""Build the demo's sample profiles from Open Library data, by rule (D-043).

    uv run python -m book_agent.sample_profiles [--out data_public/profiles] [--review FILE]
    uv run python -m book_agent.sample_profiles --verify   # Google Books check, once

No title is chosen by hand or by an LLM: for each category, one Open Library search
(subjects, sorted by reading-log count) gives the candidates, and fixed rules filter them:
- excluded subjects (per category), a cover, a primary author name in Latin script
  (alternative names can be translators'), at most 3 books per author;
- the work's own title is shown, and the ISBN-13 comes from an edition in the category's
  language with that title (works titled in another script show their edition's title);
- no publication year: Open Library's first-publication years are often wrong;
- for the Latin American and Spanish category, the work must be originally Spanish: its
  earliest dated edition is in Spanish (some translations keep the English title, so a
  Spanish edition with the work's title isn't enough).
- the owner's exclusion list (data_public/sample_profile_exclusions.json: work keys with
  a one-line reason), so the next-ranked books move up.
The top 50 by popularity are read and the next 5 are on the to-read shelf. Ratings are
implied, like a manual list's: Open Library averages are other readers' opinions, so every
read book counts as liked and prompts show no number (D-044).

Open Library's catalog data is CC0. Requests identify the project (OPEN_LIBRARY_CONTACT)
and are spaced out. The output is reviewed before it's committed.
"""

import argparse
import asyncio
import json
import re
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

from book_agent.adapters.open_library import BASE_URL, user_agent
from book_agent.config import get_settings
from book_agent.demo_profiles import SAMPLES_DIR, PresetFile
from book_agent.domain.matching import same_work, titles_match
from book_agent.domain.models import Book, Profile, ReadingStatus
from book_agent.files import write_atomic
from book_agent.ports import BookSearch, BookSearchError
from book_agent.wiring import build_book_search

READ_BOOKS = 50
TO_READ_BOOKS = 5
PER_AUTHOR = 3
IMPLIED_RATING = 5.0  # liked; never shown (ratings_implied)
EXCLUSIONS_PATH = Path("data_public/sample_profile_exclusions.json")
CANDIDATES = 300  # search results per category: enough after filtering
REQUEST_GAP_S = 0.5  # polite: Open Library is volunteer-run
VERIFY_GAP_S = 0.7  # Google Books: under its 100 requests a minute
COVER = "https://covers.openlibrary.org/b/id/{id}-M.jpg"
SEARCH_FIELDS = "key,title,author_name,first_publish_year,readinglog_count,cover_i,subject"


@dataclass(frozen=True)
class Category:
    id: str
    name: str
    description: str
    query: str
    language: str  # MARC code of the edition shown: "eng" or "spa"
    exclude_subjects: tuple[str, ...] = ()
    original_language_only: bool = False


_ADULT = ("juvenile", "children's", "picture books")
_FICTION = (*_ADULT, "biography", "autobiography", "memoir")  # no memoirs in fiction lists
CATEGORIES = (
    Category(
        id="sample-classics",
        name="A classics reader",
        description="19th and 20th century classic novels",
        query='subject:"classic literature" subject:fiction first_publish_year:[1800 TO 1970]',
        language="eng",
        exclude_subjects=(
            *_FICTION, "drama", "philosophy", "military", "political science", "sex",
        ),
    ),
    Category(
        id="sample-sf-fantasy",
        name="A science fiction and fantasy reader",
        description="Science fiction and fantasy, old and new",
        query='subject:("science fiction" OR "fantasy fiction")',
        language="eng",
        exclude_subjects=(*_FICTION, "drama"),
    ),
    Category(
        id="sample-mystery",
        name="A mystery and thriller reader",
        description="Detective stories and thrillers",
        query='subject:("detective and mystery stories" OR "thrillers (fiction)" OR '
        '"suspense fiction") subject:fiction',
        language="eng",
        exclude_subjects=(*_FICTION, "drama"),
    ),
    Category(
        id="sample-latin-american",
        name="Un lector de literatura latinoamericana y española",
        description="Latin American and Spanish literature, in Spanish",
        query='subject:("latin american fiction" OR "argentine fiction" OR "mexican fiction" '
        'OR "colombian fiction" OR "chilean fiction" OR "peruvian fiction" OR '
        '"spanish fiction") language:spa',
        language="spa",
        exclude_subjects=_FICTION,
        original_language_only=True,
    ),
    Category(
        id="sample-nonfiction",
        name="A popular non-fiction reader",
        description="Popular history and science",
        query='subject:"popular works" subject:(history OR science) -subject:fiction',
        language="eng",
        exclude_subjects=(
            *_ADULT, "textbook", "study guides", "self-help", "religion", "study and teaching",
            "computer",
        ),
    ),
)  # fmt: skip


class Candidate(BaseModel, frozen=True):
    key: str
    title: str
    author: str
    year: int | None
    readinglog: int
    cover_id: int


class Edition(BaseModel, frozen=True):
    title: str
    isbn13: str


@dataclass
class Selection:
    category: Category
    chosen: list[tuple[Candidate, Edition]] = field(default_factory=list[tuple[Candidate, Edition]])
    dropped: Counter[str] = field(default_factory=Counter[str])  # reason -> count


# --- Pure rules -----------------------------------------------------------------------------


def candidates(
    docs: Sequence[dict[str, Any]], category: Category, excluded: Mapping[str, str] | None = None
) -> tuple[list[Candidate], Counter[str]]:
    """Search results that pass the owner's exclusions and the subject, cover, author and
    per-author rules, in order. `excluded` maps work keys to the reason."""
    kept: list[Candidate] = []
    dropped: Counter[str] = Counter()
    per_author: Counter[str] = Counter()
    for doc in docs:
        subjects = " | ".join(s.casefold() for s in doc.get("subject", []))
        primary: list[str] = doc.get("author_name") or []
        authors = [_clean_author(primary[0])] if primary and latin_script(primary[0]) else []
        if excluded is not None and doc["key"] in excluded:
            dropped["excluded by the owner"] += 1
        elif any(word in subjects for word in category.exclude_subjects):
            dropped["excluded subject"] += 1
        elif not doc.get("cover_i"):
            dropped["no cover"] += 1
        elif not authors:
            dropped["no author with a Latin-script name"] += 1
        elif per_author[authors[0]] >= PER_AUTHOR:
            dropped[f"more than {PER_AUTHOR} by the author"] += 1
        else:
            per_author[authors[0]] += 1
            kept.append(
                Candidate(
                    key=doc["key"],
                    title=doc["title"],
                    author=authors[0],
                    year=doc.get("first_publish_year"),
                    readinglog=doc.get("readinglog_count", 0),
                    cover_id=doc["cover_i"],
                )
            )
    return kept, dropped


def pick_edition(
    work_title: str,
    editions: Sequence[dict[str, Any]],
    language: str,
    original_only: bool,
    rejected_isbns: Collection[str] = (),
) -> tuple[Edition | None, str | None]:
    """The ISBN-13 and title to show, or None with the reason the book was dropped.

    The title is the work's own when it's in Latin script, and the ISBN comes from an
    edition in the category's language carrying that title. A work titled in another
    script (a Russian novel) shows the title of its edition in the category's language.
    """
    if original_only and original_language(editions) != language:
        return None, "not originally in the category's language"
    in_language = [e for e in editions if f"/languages/{language}" in _languages(e)]
    with_isbn = [
        (e, isbn)
        for e in in_language
        if (isbn := _first_isbn13(e, rejected_isbns)) and e.get("title")
    ]
    if latin_script(work_title):
        title = _clean_title(work_title)
        same = [(e, isbn) for e, isbn in with_isbn if titles_match(e["title"], title)]
        if same:
            return Edition(title=title, isbn13=same[0][1]), None
        return None, "no edition in the language with the work's title and an ISBN-13"
    if with_isbn:
        edition, isbn = with_isbn[0]
        return Edition(title=_clean_title(edition["title"]), isbn13=isbn), None
    return None, "no edition in the language with an ISBN-13"


def original_language(editions: Sequence[dict[str, Any]]) -> str | None:
    """The language of the earliest dated edition with a language, as a MARC code."""
    dated: list[tuple[int, str]] = []
    for edition in editions:
        year = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", str(edition.get("publish_date", "")))
        languages = sorted(_languages(edition))
        if year and languages:
            dated.append((int(year.group(1)), languages[0].rsplit("/", 1)[-1]))
    return min(dated)[1] if dated else None


def latin_script(text: str) -> bool:
    """True when every letter is Latin (accents included): no Cyrillic, CJK, Greek..."""
    return all(ord(ch) < 0x0250 or not ch.isalpha() for ch in text)


def _clean_title(title: str) -> str:
    """A title without series notes or a bilingual second title:
    "X (Penguin Classics)" -> "X", "Satanás / Satan (Biblioteca Breve)" -> "Satanás"."""
    return re.split(r"\s+[(\[]|\s+:\s+|\s+/\s+", title, maxsplit=1)[0].strip()


def _clean_author(name: str) -> str:
    """An author name without a note: "V. S. Ramachandran (neurology)" -> "V. S. Ramachandran"."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", name).strip() or name


def _first_isbn13(edition: dict[str, Any], rejected: Collection[str] = ()) -> str | None:
    isbns = (i.replace("-", "") for i in edition.get("isbn_13", []) if _isbn13(i))
    return next((i for i in isbns if i not in rejected), None)


def preset(selection: Selection, today: date) -> PresetFile:
    category = selection.category
    books: list[Book] = []
    covers: dict[int, str] = {}
    for index, (candidate, edition) in enumerate(selection.chosen[: READ_BOOKS + TO_READ_BOOKS]):
        read = index < READ_BOOKS
        books.append(
            Book(
                title=edition.title,
                author=candidate.author,
                isbn13=edition.isbn13,
                my_rating=IMPLIED_RATING if read else None,
                status=ReadingStatus.READ if read else ReadingStatus.TO_READ,
            )
        )
        covers[index] = COVER.format(id=candidate.cover_id)
    provenance = (
        f"Open Library search, {today.isoformat()}: {category.query!r}, sorted by "
        "reading-log count; rules in book_agent.sample_profiles. CC0 data."
    )
    return PresetFile(
        description=category.description,
        profile=Profile(name=category.name, books=books, ratings_implied=True),
        covers=covers,
        provenance=provenance,
    )


def _languages(edition: dict[str, Any]) -> set[str]:
    languages: list[dict[str, Any]] = edition.get("languages", [])
    return {str(lang.get("key", "")) for lang in languages}


def _isbn13(value: str) -> bool:
    digits = value.replace("-", "")
    return bool(re.fullmatch(r"97[89]\d{10}", digits))


# --- Fetching -------------------------------------------------------------------------------


REJECTED_ISBNS = "rejected-isbns"


def load_exclusions(path: Path = EXCLUSIONS_PATH) -> dict[str, dict[str, str]]:
    """The owner's exclusions: category id -> {work key: reason}, plus "rejected-isbns"
    -> {ISBN-13: reason} for editions whose ISBN belongs to another book (the next
    matching edition is used). Missing file: none."""
    if not path.exists():
        return {}
    data: dict[str, dict[str, str]] = json.loads(path.read_text(encoding="utf-8"))
    for section, entries in data.items():
        is_isbns = section == REJECTED_ISBNS
        if not is_isbns and section not in {c.id for c in CATEGORIES}:
            raise ValueError(f"{path}: unknown category {section!r}")
        for key, reason in entries.items():
            valid_key = _isbn13(key) if is_isbns else key.startswith("/works/")
            if not valid_key or not reason.strip():
                raise ValueError(f"{path}: {key!r} needs a work key (or ISBN) and a reason")
    return data


async def build(
    client: httpx.AsyncClient,
    category: Category,
    excluded: Mapping[str, str] | None = None,
    rejected_isbns: Collection[str] = (),
) -> Selection:
    response = await client.get(
        f"{BASE_URL}/search.json",
        params={
            "q": category.query,
            "sort": "readinglog",
            "limit": CANDIDATES,
            "fields": SEARCH_FIELDS,
        },
    )
    response.raise_for_status()
    found, dropped = candidates(response.json()["docs"], category, excluded)
    selection = Selection(category, dropped=dropped)
    for candidate in found:
        if len(selection.chosen) >= READ_BOOKS + TO_READ_BOOKS:
            break
        await asyncio.sleep(REQUEST_GAP_S)
        editions = await client.get(
            f"{BASE_URL}{candidate.key}/editions.json", params={"limit": 200}
        )
        if editions.status_code != 200:
            selection.dropped["editions lookup failed"] += 1
            continue
        edition, reason = pick_edition(
            candidate.title,
            editions.json().get("entries", []),
            category.language,
            category.original_language_only,
            rejected_isbns,
        )
        if edition is None:
            selection.dropped[reason or "dropped"] += 1
        else:
            selection.chosen.append((candidate, edition))
    return selection


def review(selections: Sequence[Selection]) -> str:
    """A Markdown listing for the owner's review before anything is committed."""
    lines = ["# Sample profiles: generated lists for review", ""]
    for s in selections:
        lines += [f"## {s.category.id}: {s.category.name}", "", f"Query: `{s.category.query}`", ""]
        lines += [
            "| # | Title | Author | Shelf | Reading log | Work |",
            "| --- | --- | --- | --- | --- | --- |",
        ]  # fmt: skip
        for i, (c, e) in enumerate(s.chosen[: READ_BOOKS + TO_READ_BOOKS], start=1):
            read = i <= READ_BOOKS
            lines.append(
                f"| {i} | {e.title} | {c.author} | "
                f"{'read' if read else 'to-read'} | {c.readinglog} | {c.key} |"
            )
        dropped = ", ".join(f"{reason}: {n}" for reason, n in s.dropped.most_common())
        lines += [
            "",
            f"Dropped before reaching {READ_BOOKS + TO_READ_BOOKS}: {dropped or 'none'}",
            "",
        ]
    return "\n".join(lines)


async def run(out: Path, review_path: Path, today: date) -> list[Selection]:
    contact = get_settings().open_library_contact
    if contact is None:
        raise ValueError("OPEN_LIBRARY_CONTACT is not set: Open Library asks for contact details")
    exclusions = load_exclusions()
    selections: list[Selection] = []
    async with httpx.AsyncClient(headers={"User-Agent": user_agent(contact)}, timeout=30) as client:
        for category in CATEGORIES:
            selection = await build(
                client,
                category,
                exclusions.get(category.id, {}),
                set(exclusions.get(REJECTED_ISBNS, {})),
            )
            selections.append(selection)
            write_atomic(
                out / f"{category.id}.json",
                preset(selection, today).model_dump_json(indent=1) + "\n",
            )
            print(f"{category.id}: {len(selection.chosen)} books", flush=True)
    write_atomic(review_path, review(selections) + "\n")
    return selections


def read_presets(folder: Path) -> dict[str, PresetFile]:
    return {
        path.stem: PresetFile.model_validate_json(path.read_bytes())
        for path in sorted(folder.glob("*.json"))
    }


async def verify(
    presets: Mapping[str, PresetFile],
    search: BookSearch,
    *,
    only: set[str] | None = None,
    gap_s: float = VERIFY_GAP_S,
) -> list[str]:
    """Check every sample book on Google Books by ISBN: a volume with the same title and
    author must come back. Returns one line per book that fails, for the review.
    `only` limits the check to some ISBNs (to finish a check a rate limit interrupted);
    requests are spaced by `gap_s`."""
    problems: list[str] = []
    for name, preset_file in presets.items():
        for book in preset_file.profile.books:
            if only is not None and book.isbn13 not in only:
                continue
            await asyncio.sleep(gap_s)
            where = f"{name}: {book.title} by {book.author} ({book.isbn13})"
            try:
                found = await search.search(f"isbn:{book.isbn13}", max_results=3)
            except BookSearchError as e:
                problems.append(f"{where}: lookup failed: {e}")
                continue
            if not any(same_work(book.title, [book.author], b.title, b.authors) for b in found):
                seen = "; ".join(f"{b.title} by {', '.join(b.authors)}" for b in found)
                problems.append(f"{where}: Google Books has {seen or 'nothing'}")
    return problems


async def run_verify(presets: Mapping[str, PresetFile], only: set[str] | None) -> list[str]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=15) as http:
        return await verify(presets, build_book_search(settings, http), only=only)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m book_agent.sample_profiles")
    parser.add_argument("--out", type=Path, default=SAMPLES_DIR, help="folder for the JSON files")
    parser.add_argument("--review", type=Path, default=Path("data/sample_profiles_review.md"))
    parser.add_argument(
        "--verify", action="store_true", help="check the built files on Google Books (quota)"
    )
    parser.add_argument("--isbns", nargs="*", help="with --verify: check only these ISBNs")
    args = parser.parse_args(argv)
    if args.verify:
        only = set(args.isbns) if args.isbns else None
        problems = asyncio.run(run_verify(read_presets(args.out), only))
        print("\n".join(problems) or "Every book was found on Google Books by its ISBN.")
        print(f"{len(problems)} problems")
        return 1 if problems else 0
    selections = asyncio.run(run(args.out, args.review, date.today()))
    short = [s.category.id for s in selections if len(s.chosen) < READ_BOOKS + TO_READ_BOOKS]
    print(f"Review: {args.review}" + (f" (short: {', '.join(short)})" if short else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
