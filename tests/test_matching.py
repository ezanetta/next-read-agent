import pytest

from book_agent.domain.matching import (
    author_surname,
    authors_match,
    mentions_title,
    normalize_title,
    same_work,
    title_variants,
    titles_match,
)

# --- Normalization rules -------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("Cien años de soledad", "cien anos de soledad"),  # accents removed
        ("DUNE", "dune"),  # casefolded
        ("Dune (Dune Chronicles, #1)", "dune"),  # series marker removed
        ("Dune [Illustrated]", "dune"),  # square brackets too
        ("Ender's Game", "ender game"),  # possessive 's dropped
        ("Rock 'n' Roll", "rock n roll"),  # other apostrophes dropped
        ("Ender\u2019s Game", "ender game"),  # typographic apostrophe too
        ("Pride & Prejudice", "pride and prejudice"),  # & as "and"
        ("The Hobbit", "hobbit"),  # leading English article
        ("La sombra del viento", "sombra del viento"),  # leading Spanish article
        ("Of Mice and Men", "of mice and men"),  # "of" is not an article
        ("  Neuromancer!  ", "neuromancer"),  # punctuation and whitespace
        ("The", "the"),  # an article alone is kept, never emptied
        ("1984", "1984"),
    ],
)
def test_normalize_title(raw: str, normalized: str) -> None:
    assert normalize_title(raw) == normalized


def test_title_variants_split_at_the_first_colon() -> None:
    variants = title_variants("Mistborn: The Final Empire (Mistborn, #1)")
    assert variants.full == "mistborn the final empire"
    assert variants.main == "mistborn"
    assert variants.subtitle == "final empire"


def test_title_without_colon_has_no_subtitle() -> None:
    variants = title_variants("The Final Empire (Mistborn, #1)")
    assert (variants.full, variants.main, variants.subtitle) == (
        "final empire",
        "final empire",
        None,
    )


# --- Title matching ------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Dune", "Dune (Dune Chronicles, #1)"),  # full titles equal after normalization
        ("Sapiens", "Sapiens: A Brief History of Humankind"),  # main title vs full title
        ("Mistborn: The Final Empire", "The Final Empire"),  # subtitle vs full title
        (
            "Mistborn: The Well of Ascension",
            "The Well of Ascension (Mistborn, #2)",
        ),  # Goodreads form
        ("Cien años de soledad", "CIEN ANOS DE SOLEDAD"),
    ],
)
def test_titles_that_match(a: str, b: str) -> None:
    assert titles_match(a, b)
    assert titles_match(b, a)  # symmetric


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # Same main title, different subtitles: different books in a series.
        ("Mistborn: The Final Empire", "Mistborn: The Well of Ascension"),
        ("Dune", "Dune Messiah"),
        ("Foundation", "Foundation and Empire"),
        # Known limitation: regional titles are different strings.
        ("Harry Potter and the Philosopher's Stone", "Harry Potter and the Sorcerer's Stone"),
        ("", ""),  # empty titles never match
    ],
)
def test_titles_that_do_not_match(a: str, b: str) -> None:
    assert not titles_match(a, b)
    assert not titles_match(b, a)


# --- Authors -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "surname"),
    [
        ("Frank Herbert", "herbert"),
        ("J.R.R. Tolkien", "tolkien"),
        ("Ursula K. Le Guin", "guin"),
        ("Gabriel García Márquez", "marquez"),
        ("", ""),
    ],
)
def test_author_surname(name: str, surname: str) -> None:
    assert author_surname(name) == surname


def test_authors_match_by_surname_ignoring_initials() -> None:
    assert authors_match(["J.R.R. Tolkien"], ["J. R. R. Tolkien"])
    assert authors_match(["Terry Pratchett", "Neil Gaiman"], ["Neil Gaiman"])
    # Known limitation: a shared surname counts as the same author.
    assert authors_match(["Frank Herbert"], ["Brian Herbert", "Kevin J. Anderson"])
    assert not authors_match(["Isaac Asimov"], ["Arthur C. Clarke"])
    assert not authors_match([], ["Isaac Asimov"])


def test_same_work_needs_title_and_author() -> None:
    assert same_work(
        "Mistborn: The Final Empire",
        ["Brandon Sanderson"],
        "The Final Empire (Mistborn, #1)",
        ["Brandon Sanderson"],
    )
    assert not same_work("Dune", ["Frank Herbert"], "Dune", ["Someone Else"])


# --- Citations -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "title", "cited"),
    [
        ("If you loved Dune, you'll like this.", "Dune (Dune Chronicles, #1)", True),
        ("Echoes of 'The Final Empire' heist plot.", "The Final Empire (Mistborn, #1)", True),
        ("Like Mistborn's magic system.", "Mistborn: The Final Empire", True),  # main title
        ("A sequel to the final empire story.", "Mistborn: The Final Empire", True),  # subtitle
        ("Dunes and deserts everywhere.", "Dune", False),  # whole words only
        ("It is a great read.", "It", False),  # too short to count as a citation
        ("A great classic.", "Dune", False),
    ],
)
def test_mentions_title(reason: str, title: str, cited: bool) -> None:
    assert mentions_title(reason, title) is cited
