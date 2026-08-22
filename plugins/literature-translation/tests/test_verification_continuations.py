from __future__ import annotations

from littrans.models import SemanticStatus, SourceUnit, UnitKind
from littrans.storage import sha256_text
from littrans.verification import _has_unresolved_paragraph_continuation


def _paragraph(
    unit_id: str,
    source_text: str,
    *,
    bbox: tuple[float, float, float, float] = (72.0, 300.0, 500.0, 360.0),
    source_markdown: str | None = None,
    verified: bool = False,
    continues_from_previous: bool = False,
) -> SourceUnit:
    return SourceUnit(
        unit_id=unit_id,
        kind=UnitKind.PARAGRAPH,
        page=53,
        bbox=bbox,
        source_text=source_text,
        source_hash=sha256_text(source_text),
        source_markdown=source_markdown,
        math_status=SemanticStatus.VERIFIED if verified else None,
        verification_status=(SemanticStatus.VERIFIED if verified else SemanticStatus.UNVERIFIED),
        continues_from_previous=continues_from_previous,
        confidence=1.0 if verified else 0.7,
    )


def _equation(
    unit_id: str,
    source_text: str,
    *,
    bbox: tuple[float, float, float, float],
) -> SourceUnit:
    return SourceUnit(
        unit_id=unit_id,
        kind=UnitKind.EQUATION,
        page=53,
        bbox=bbox,
        source_text=source_text,
        source_hash=sha256_text(source_text),
        latex=r"D(p,q)=\max_S(p(S)-q(S))",
        math_status=SemanticStatus.VERIFIED,
        verification_status=SemanticStatus.VERIFIED,
        translatable=False,
        confidence=1.0,
    )


def test_equation_appended_after_paragraphs_visually_interposes() -> None:
    previous = _paragraph(
        "p0077-u013-cb8feba1",
        "Proposition 3.28. For any two classical probability distributions p, q in R^N,",
        bbox=(73.44, 318.61, 408.46, 330.76),
        source_markdown=(
            "Proposition 3.28. For any two classical probability distributions "
            "$p,q\\in\\mathbb{R}^{N}$,"
        ),
        verified=True,
    )
    current = _paragraph(
        "p0077-u018-08cd000f",
        "where the maximization is over all subsets S.",
        bbox=(73.44, 371.90, 270.31, 381.87),
        source_markdown="where the maximization is over all subsets $S$.",
        verified=True,
    )
    equation = _equation(
        "p0077-u037-7ad82912",
        "(3.73) D(p, q) = max",
        bbox=(73.44, 345.88, 231.10, 355.86),
    )

    # Match the persisted order: the reviewed equation was appended after both
    # prose units even though its PDF bbox lies between them.
    page_units = [previous, current, equation]

    assert not _has_unresolved_paragraph_continuation(previous, current, page_units)


def test_direct_lowercase_paragraph_continuation_without_interposition_is_detected() -> None:
    previous = _paragraph(
        "p0001-u001",
        "The construction is obtained by",
        bbox=(72.0, 300.0, 500.0, 320.0),
    )
    current = _paragraph(
        "p0001-u002",
        "taking the tensor product with the identity.",
        bbox=(72.0, 332.0, 500.0, 352.0),
    )
    other_column_equation = _equation(
        "p0001-u003",
        "x = y",
        bbox=(540.0, 324.0, 680.0, 344.0),
    )

    assert _has_unresolved_paragraph_continuation(
        previous, current, [previous, current, other_column_equation]
    )


def test_reviewed_mixed_display_paragraph_does_not_reuse_raw_overlap_tail() -> None:
    previous = _paragraph(
        "p0053-u011",
        "The state space is F = tensor n",
        source_markdown=(
            "The state space is "
            "$\\mathcal{F}=\\bigotimes_{i=1}^{n}\\mathbb{C}^{d}$ "
            "and is finite dimensional."
        ),
        verified=True,
    )
    current = _paragraph(
        "p0053-u012",
        (
            "i=1 Cd is finite dimensional. In a single-mode truncated bosonic "
            "system, b and b dagger are finite dimensional matrices"
        ),
        source_markdown=(
            "In a single-mode truncated bosonic system, $b,b^\\dagger$ are "
            "finite dimensional matrices:\n\n$$b=(\\cdots),\\quad "
            "b^\\dagger=(\\cdots).$$ (2.129)"
        ),
        verified=True,
    )

    assert not _has_unresolved_paragraph_continuation(previous, current)


def test_reviewed_true_paragraph_continuation_is_still_detected() -> None:
    previous = _paragraph(
        "p0001-u001",
        "The construction is obtained by",
        source_markdown="The construction is obtained by",
        verified=True,
    )
    current = _paragraph(
        "p0001-u002",
        "taking the tensor product with the identity.",
        source_markdown="taking the tensor product with $I$.",
        verified=True,
    )

    assert _has_unresolved_paragraph_continuation(previous, current)
    assert not _has_unresolved_paragraph_continuation(
        previous,
        current.model_copy(update={"continues_from_previous": True}),
    )


def test_unreviewed_markdown_cannot_hide_a_raw_continuation() -> None:
    previous = _paragraph(
        "p0001-u001",
        "The construction is obtained by",
        source_markdown="A complete but unreviewed sentence.",
    )
    current = _paragraph(
        "p0001-u002",
        "taking the tensor product with the identity.",
        source_markdown="Taking a separate path.",
    )

    assert _has_unresolved_paragraph_continuation(previous, current)
