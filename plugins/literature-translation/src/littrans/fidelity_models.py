"""Original-evidence contracts; representations deliberately live elsewhere."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from littrans.models import StrictModel
from littrans.storage import read_jsonl

ASSET_REFERENCE = re.compile(r"\{\{asset:([A-Za-z0-9][A-Za-z0-9._-]*)\}\}")


def asset_reference_ids(text: str) -> list[str]:
    return ASSET_REFERENCE.findall(text)


class FidelityFragment(StrictModel):
    page: int = Field(ge=1)
    bbox: tuple[float, float, float, float]
    png_path: str
    svg_path: str
    # Fragments written before 0.6.1 also carried a per-region PDF; kept only for those records.
    pdf_path: str | None = Field(default=None, exclude_if=lambda value: value is None)
    glyph_ids: list[str] = Field(default_factory=list)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    baseline: float | None = None
    dpi: int = 300
    export_method: Literal["raw-region", "explicit-glyph-paths-v1", "explicit-glyph-paths-v2"] = "raw-region"
    file_sha256: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_geometry(self) -> FidelityFragment:
        x0, y0, x1, y1 = self.bbox
        if x1 <= x0 or y1 <= y0:
            raise ValueError("asset fragment must have positive area")
        present = {value for value in (self.png_path, self.svg_path, self.pdf_path) if value}
        for value in present:
            if Path(value).is_absolute() or ".." in Path(value).parts:
                raise ValueError("asset evidence paths must stay inside the project")
        if set(self.file_sha256) != present:
            raise ValueError("all original fragment files require hashes")
        if any(not re.fullmatch(r"[a-f0-9]{64}", v) for v in self.file_sha256.values()):
            raise ValueError("invalid fragment SHA256")
        return self


class FormulaCondition(StrictModel):
    """Source-native language belonging inside one displayed formula line."""
    glyph_ids: list[str] = Field(min_length=1)
    source_text: str = Field(min_length=1)


class FidelityAsset(StrictModel):
    schema_version: Literal[6] = 6
    id: str
    kind: Literal["math", "table", "code", "figure", "mixed-region"]
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_identity_version: Literal[1, 2] = Field(default=1, exclude_if=lambda value: value == 1)
    fragments: list[FidelityFragment] = Field(min_length=1)
    grouping_pending: bool = False
    display: bool = False
    provenance: list[str] = Field(default_factory=list)
    formula_conditions: list[FormulaCondition] = Field(default_factory=list, exclude_if=lambda value: not value)

    @model_validator(mode="after")
    def valid_formula_conditions(self) -> FidelityAsset:
        if self.formula_conditions and self.kind != "math":
            raise ValueError("formula_conditions require a math asset")
        owned = {gid for fragment in self.fragments for gid in fragment.glyph_ids}
        declared = [gid for condition in self.formula_conditions for gid in condition.glyph_ids]
        if len(declared) != len(set(declared)) or not set(declared) <= owned:
            raise ValueError("formula condition glyphs must be unique and owned by this asset")
        return self


def load_assets(root: Path) -> dict[str, FidelityAsset]:
    records = read_jsonl(Path(root) / "derived/fidelity-assets.jsonl", FidelityAsset)
    if len({item.id for item in records}) != len(records):
        raise ValueError("duplicate fidelity asset IDs")
    return {item.id: item for item in records}
