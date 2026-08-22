from __future__ import annotations

from littrans.models import SemanticStatus, SourceUnit, UnitKind
from littrans.storage import sha256_text
from littrans.verification import _has_unresolved_paragraph_continuation


def _paragraph(
    unit_id: str,
    source_text: str,
    *,
    source_markdown: str | None = None,
    verified: bool = False,
    continues_from_previous: bool = False,
) -> SourceUnit:
    return SourceUnit(
        unit_id=unit_id,
        kind=UnitKind.PARAGRAPH,
        page=53,
        bbox=(72.0, 300.0, 500.0, 360.0),
        source_text=source_text,
        source_hash=sha256_text(source_text),
        source_markdown=source_markdown,
        math_status=SemanticStatus.VERIFIED if verified else None,
        verification_status=(SemanticStatus.VERIFIED if verified else SemanticStatus.UNVERIFIED),
        continues_from_previous=continues_from_previous,
        confidence=1.0 if verified else 0.7,
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
