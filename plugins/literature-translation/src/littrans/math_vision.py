"""Generate non-authoritative visual candidates for unverified mathematics.

This module deliberately has a narrow write surface.  It reads the extracted
units and the project PDF, and writes only candidate evidence plus the
temporary crop/provider files under ``.littrans/work/math-vision``.  In
particular, it never applies an extraction override and never changes
``derived/units.jsonl`` or ``derived/verification.json``.

The provider adapter is intentionally small.  DeepSeek's OpenAI-compatible
``chat/completions`` endpoint is used through ``httpx`` when it is installed;
the standard-library HTTP client is retained as a fallback so the plugin does
not make ``httpx`` a mandatory project dependency.  Tests and embedding code
can set :data:`HTTP_TRANSPORT` to an ``httpx.MockTransport`` (or a compatible
callable) without making a real network request.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz

from littrans.extractor import parse_page_spec
from littrans.models import (
    MathCandidate,
    MathCandidateClassification,
    ReviewUsage,
    SemanticStatus,
    SourceUnit,
    UnitKind,
)
from littrans.storage import (
    atomic_write_bytes,
    load_project,
    project_write_lock,
    read_jsonl,
    sha256_file,
)

try:  # ``httpx`` is used by tests and by installations that already have it.
    import httpx
except ImportError:  # pragma: no cover - exercised only in minimal installs
    httpx = None  # type: ignore[assignment]


DEFAULT_PROVIDER = "deepseek"
DEFAULT_MODEL = "deepseek-v4-flash-vision-exp"
DEFAULT_API_BASE = "https://api.deepseek.com"
API_BASE_URL = DEFAULT_API_BASE
PROMPT_VERSION = "math-vision-v2"
MAX_TOKENS = 2048
MAX_BATCH_SIZE = 6
MAX_CONCURRENCY = 4
MAX_PILOT_UNITS = 60
MAX_RETRIES = 3  # retries after the initial request
RETRY_BACKOFF_SECONDS = 0.05
INPUT_PRICE_PER_MILLION = 0.44
OUTPUT_PRICE_PER_MILLION = 1.32
CROP_PADDING_POINTS = 8.0
WIDE_CROP_PADDING_POINTS = 32.0
DISPLAY_CROP_PADDING_X_POINTS = 56.0
DISPLAY_CROP_PADDING_Y_POINTS = 40.0
FRAGMENT_CROP_PADDING_X_POINTS = 72.0
FRAGMENT_CROP_PADDING_Y_POINTS = 48.0
RENDER_SCALE = 3.0
MAX_REMOTE_CROP_AREA_RATIO = 0.5
MAX_REMOTE_CROP_DIMENSION_RATIO = 0.85
CROP_STRATEGY_VERSION = "target-box-v3-privacy-bounded-context"

# Public on purpose: tests can install an httpx.MockTransport without changing
# the public generate_math_candidates signature.  ``_HTTP_TRANSPORT`` is kept
# as a compatibility alias for callers that used the early prototype.
HTTP_TRANSPORT: Any = None
_HTTP_TRANSPORT: Any = None

_MATH_MARKER_RE = re.compile(r"(?<!\\)(?:\$|\\\(|\\\[)")
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.IGNORECASE | re.DOTALL)
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_DATA_URL_RE = re.compile(
    r"data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=\r\n]+",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?bearer\s+)[^\s\"']+")

SYSTEM_PROMPT = """You are a conservative mathematical notation transcription verifier.
Inspect each supplied local PDF crop and return one JSON object with a
`candidates` array. The metadata OCR is an unreliable locator hint, never a
source to copy. The image is authoritative. Transcribe only the target region
identified in metadata; pixels outside it are local context for deciding
whether the target is structurally complete.

For every target, first decide whether it is an independent unit. A lone
delimiter, summation stroke, matrix entry, equation tail, continuation, or
other fragment of a neighboring expression is `needs-split`, not a formula to
reconstruct. Use `not-math` when the target itself contains no independent
mathematics. For `needs-split` and `not-math`, set `latex`, `source_markdown`,
and `equation_number` to null and explain the visible structural evidence in
`uncertainties`.

For a complete display, use `display` and put the entire visible formula in
`latex`. Preserve every row, column, delimiter, operator, limit, punctuation
mark, and prose tail in the target; do not add a row or term from context and
do not omit a visible prefix or suffix. For prose containing inline math, use
`mixed` and return the complete `source_markdown`: copy every visible prose
word, space, punctuation mark, and inline expression in order, using `$...$`
only for math. Do not rewrite, summarize, silently correct, decorate, or stop
at the OCR hint's boundary. Use `inline` only when the whole independent target
is inline math rather than prose plus math.

An equation number must be independent of `latex` and contain no outer parentheses
(for example, visible `(2.11)` becomes `2.11`); use null when no number is visible
for this target. Never put the number in `latex`. Do not
solve, simplify, translate, normalize away source distinctions, or infer
anything that is not visible. Record genuine ambiguity in `uncertainties` and
set `needs_second_pass` only when another visual observation would materially
help. Return JSON only, with no Markdown fences.
"""

USER_PROMPT_RULES = """Images follow in exactly the same order as metadata entries.
Treat each target independently and never merge targets. The pass-2 image has
more local context, but contains no pass-1 answer or reasoning. Use
`target_region_px` as the transcription boundary and `crop_size_px` as its
coordinate space. Inspect context only to decide whether the target is a
fragment. Return exactly one candidate for every unit_id and no others.
"""


@dataclass(frozen=True)
class _Crop:
    unit: SourceUnit
    path: Path
    relative_path: str
    sha256: str
    data_url: str
    source_pdf_sha256: str
    pass_index: int
    crop_size_pixels: tuple[int, int]
    target_region_pixels: tuple[int, int, int, int]
    context_profile: str


@dataclass
class _Budget:
    limit: float
    reserved: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def reserve(self, amount: float) -> bool:
        if amount < 0 or not math.isfinite(amount):
            return False
        with self.lock:
            if self.reserved + amount > self.limit + 1e-12:
                return False
            self.reserved += amount
            return True

    def spent(self) -> float:
        with self.lock:
            return self.reserved


@dataclass
class _BatchResult:
    index: int
    unit_ids: list[str]
    candidates: list[MathCandidate] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    usage: ReviewUsage = field(default_factory=ReviewUsage)
    attempts: int = 0
    reserved_cost: float = 0.0
    budget_exhausted: bool = False


class _FormatError(ValueError):
    """The provider answered, but not with the requested JSON contract."""


class _BudgetExhausted(RuntimeError):
    """A retry would exceed the caller's conservative cost ceiling."""


def set_http_transport(transport: Any | None) -> None:
    """Install a test/embedding transport for subsequent calls.

    The function is a convenience around :data:`HTTP_TRANSPORT`; the
    production API remains :func:`generate_math_candidates`.
    """

    global HTTP_TRANSPORT
    HTTP_TRANSPORT = transport


def _transport() -> Any:
    return HTTP_TRANSPORT if HTTP_TRANSPORT is not None else _HTTP_TRANSPORT


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw).strip().lower()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _safe_project_file(root: Path, value: str) -> Path | None:
    """Resolve a recorded path without permitting evidence outside the project."""

    candidate = Path(value)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return None
    return resolved


def _safe_name(value: str) -> str:
    normalized = _SAFE_NAME_RE.sub("-", value).strip("-")
    return normalized[:80] or "unit"


def _is_math_unit(unit: SourceUnit) -> bool:
    """Return whether a source unit needs visual math verification."""

    kind = _enum_value(unit.kind)
    status = _enum_value(unit.math_status) if unit.math_status is not None else ""
    if status == _enum_value(SemanticStatus.VERIFIED):
        return False
    if kind == _enum_value(UnitKind.EQUATION):
        return True
    if status:
        return status == _enum_value(SemanticStatus.UNVERIFIED)
    markdown = unit.source_markdown or ""
    return bool(_MATH_MARKER_RE.search(markdown))


def _looks_like_structural_fragment(unit: SourceUnit) -> bool:
    """Identify tiny extracted regions that need extra context, not auto-correction."""

    x0, y0, x1, y1 = (float(value) for value in unit.bbox)
    compact_text = re.sub(r"\s+", "", unit.source_text or "")
    return (
        _enum_value(unit.kind) != _enum_value(UnitKind.EQUATION)
        and x1 - x0 <= 48.0
        and y1 - y0 <= 36.0
        and len(compact_text) <= 16
    )


def _crop_padding(unit: SourceUnit, pass_index: int) -> tuple[float, float, str]:
    """Return local context padding tailored to the independent visual pass."""

    if pass_index == 1:
        return CROP_PADDING_POINTS, CROP_PADDING_POINTS, "tight-target"
    if _looks_like_structural_fragment(unit):
        return (
            FRAGMENT_CROP_PADDING_X_POINTS,
            FRAGMENT_CROP_PADDING_Y_POINTS,
            "possible-structural-fragment",
        )
    if _enum_value(unit.kind) == _enum_value(UnitKind.EQUATION):
        return (
            DISPLAY_CROP_PADDING_X_POINTS,
            DISPLAY_CROP_PADDING_Y_POINTS,
            "display-structure",
        )
    return WIDE_CROP_PADDING_POINTS, WIDE_CROP_PADDING_POINTS, "prose-context"


def _selected_units(
    units: Iterable[SourceUnit],
    pages: set[int],
    sampling: str,
    source_pdf_sha256: str,
    limit: int | None,
) -> list[SourceUnit]:
    selected = [unit for unit in units if unit.page in pages and _is_math_unit(unit)]
    mode = sampling.strip().lower()
    if mode == "sequential":
        selected.sort(key=lambda unit: (unit.page, unit.bbox[1], unit.bbox[0], unit.unit_id))
    elif mode in {"uncertainty", "uncertainty-first"}:
        selected.sort(
            key=lambda unit: (
                float(unit.confidence),
                unit.page,
                unit.bbox[1],
                unit.bbox[0],
                unit.unit_id,
            )
        )
    elif mode in {"random", "stable-random"}:
        # Never use process-randomized ``hash()``: a resumed run must choose the
        # same sequence on every interpreter.
        selected.sort(
            key=lambda unit: _sha256_bytes(f"{source_pdf_sha256}\0{unit.unit_id}".encode())
        )
    elif mode == "stratified":
        selected.sort(
            key=lambda unit: (
                unit.page,
                _enum_value(unit.kind),
                float(unit.confidence),
                unit.bbox[1],
                unit.bbox[0],
                unit.unit_id,
            )
        )
    else:
        raise ValueError(
            "sampling must be one of sequential, uncertainty-first, random, or stratified"
        )
    if limit is not None:
        return selected[:limit]
    return selected


def _record_matches(
    root: Path,
    record: MathCandidate,
    unit: SourceUnit,
    source_pdf_sha256: str,
    provider: str,
    model: str,
    pass_index: int,
) -> bool:
    if record.unit_id != unit.unit_id:
        return False
    if record.page != unit.page or record.source_hash != unit.source_hash:
        return False
    if record.source_pdf_sha256 != source_pdf_sha256:
        return False
    if record.provider != provider or record.model != model:
        return False
    if record.pass_index != pass_index:
        return False
    if record.prompt_version != _prompt_contract_version(pass_index):
        return False
    crop = _safe_project_file(root, record.crop_path)
    response = _safe_project_file(root, record.raw_response_path)
    try:
        return bool(
            crop is not None
            and response is not None
            and crop.is_file()
            and response.is_file()
            and sha256_file(crop) == record.crop_sha256
            and sha256_file(response) == record.response_sha256
        )
    except OSError:
        return False


def _load_existing_candidates(path: Path) -> list[MathCandidate]:
    if not path.exists():
        return []
    return read_jsonl(path, MathCandidate)


def _render_crop(
    root: Path,
    document: fitz.Document,
    unit: SourceUnit,
    source_pdf_sha256: str,
    pass_index: int,
) -> _Crop:
    if unit.page < 1 or unit.page > document.page_count:
        raise ValueError(f"unit {unit.unit_id} references unavailable PDF page {unit.page}")
    page = document[unit.page - 1]
    x0, y0, x1, y1 = (float(value) for value in unit.bbox)
    if pass_index not in {1, 2}:
        raise ValueError(f"unsupported math vision pass: {pass_index}")
    padding_x, padding_y, context_profile = _crop_padding(unit, pass_index)
    page_rect = page.rect
    target = fitz.Rect(x0, y0, x1, y1) & page_rect
    if target.is_empty or target.width <= 0 or target.height <= 0:
        raise ValueError(f"unit {unit.unit_id} has an empty crop rectangle")

    def padded_clip(scale: float) -> fitz.Rect:
        return fitz.Rect(
            max(page_rect.x0, x0 - padding_x * scale),
            max(page_rect.y0, y0 - padding_y * scale),
            min(page_rect.x1, x1 + padding_x * scale),
            min(page_rect.y1, y1 + padding_y * scale),
        )

    def remotely_safe(rect: fitz.Rect) -> bool:
        if rect.is_empty or rect.width <= 0 or rect.height <= 0:
            return False
        width_ratio = rect.width / page_rect.width
        height_ratio = rect.height / page_rect.height
        area_ratio = rect.get_area() / page_rect.get_area()
        is_full_page = all(
            abs(left - right) <= 1e-6
            for left, right in zip(tuple(rect), tuple(page_rect), strict=True)
        )
        return (
            not is_full_page
            and width_ratio <= MAX_REMOTE_CROP_DIMENSION_RATIO
            and height_ratio <= MAX_REMOTE_CROP_DIMENSION_RATIO
            and area_ratio <= MAX_REMOTE_CROP_AREA_RATIO
        )

    if not remotely_safe(target):
        raise ValueError(f"unit {unit.unit_id} cannot be rendered as a privacy-bounded local crop")
    clip = padded_clip(1.0)
    if not remotely_safe(clip):
        lower = 0.0
        upper = 1.0
        for _ in range(32):
            midpoint = (lower + upper) / 2.0
            if remotely_safe(padded_clip(midpoint)):
                lower = midpoint
            else:
                upper = midpoint
        clip = padded_clip(lower)
    if not remotely_safe(clip):
        raise ValueError(f"unit {unit.unit_id} cannot be rendered as a privacy-bounded local crop")
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(RENDER_SCALE, RENDER_SCALE),
        clip=clip,
        alpha=False,
    )
    png = pixmap.tobytes("png")
    target_region_pixels = (
        max(0, min(pixmap.width, round((x0 - clip.x0) * RENDER_SCALE))),
        max(0, min(pixmap.height, round((y0 - clip.y0) * RENDER_SCALE))),
        max(0, min(pixmap.width, round((x1 - clip.x0) * RENDER_SCALE))),
        max(0, min(pixmap.height, round((y1 - clip.y0) * RENDER_SCALE))),
    )
    crop_identity = _sha256_json(
        {
            "source_pdf_sha256": source_pdf_sha256,
            "unit_id": unit.unit_id,
            "source_hash": unit.source_hash,
            "page": unit.page,
            "bbox": [x0, y0, x1, y1],
            "padding_points": [padding_x, padding_y],
            "context_profile": context_profile,
            "crop_strategy_version": CROP_STRATEGY_VERSION,
            "scale": RENDER_SCALE,
            "pass_index": pass_index,
        }
    )
    work = root / ".littrans" / "work" / "math-vision"
    work.mkdir(parents=True, exist_ok=True)
    crop_path = work / f"crop-p{unit.page:04d}-{_safe_name(unit.unit_id)}-{crop_identity[:16]}.png"
    crop_sha256 = _sha256_bytes(png)
    if not crop_path.is_file() or sha256_file(crop_path) != crop_sha256:
        atomic_write_bytes(crop_path, png)
    data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    return _Crop(
        unit=unit,
        path=crop_path,
        relative_path=_relative(root, crop_path),
        sha256=crop_sha256,
        data_url=data_url,
        source_pdf_sha256=source_pdf_sha256,
        pass_index=pass_index,
        crop_size_pixels=(pixmap.width, pixmap.height),
        target_region_pixels=target_region_pixels,
        context_profile=context_profile,
    )


def _prompt_and_request(crops: Sequence[_Crop], model: str) -> tuple[dict[str, Any], str]:
    pass_indices = {crop.pass_index for crop in crops}
    if len(pass_indices) != 1:
        raise ValueError("a provider request cannot mix independent vision passes")
    pass_index = next(iter(pass_indices))
    metadata: list[dict[str, Any]] = []
    for crop in crops:
        unit = crop.unit
        metadata.append(
            {
                "unit_id": unit.unit_id,
                "page": unit.page,
                "bbox": [float(value) for value in unit.bbox],
                "kind_hint": _enum_value(unit.kind),
                "ocr_locator_hint_non_authoritative": unit.source_text,
                "crop_size_px": list(crop.crop_size_pixels),
                "target_region_px": list(crop.target_region_pixels),
                "context_profile": crop.context_profile,
            }
        )
    user_text = (
        f"This is independent visual transcription pass {pass_index}; no answer or "
        "reasoning from any other pass is available.\n"
        + USER_PROMPT_RULES
        + "Input metadata:\n"
        + json.dumps({"units": metadata}, ensure_ascii=False, sort_keys=True)
        + "\nRequired JSON shape: "
        + '{"candidates":[{"unit_id":"...","classification":"display|inline|mixed|not-math|needs-split",'
        + '"latex":null,"source_markdown":null,"equation_number":null,'
        + '"uncertainties":[],"needs_second_pass":false}]}.'
    )
    content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    for crop in crops:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": crop.data_url, "detail": "original"},
            }
        )
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": MAX_TOKENS,
        "thinking": {"type": "disabled"},
    }
    return body, _prompt_contract_version(pass_index)


def _prompt_contract_version(pass_index: int) -> str:
    """Hash the prompt contract, not batch-specific metadata, for resumability."""

    contract = (
        SYSTEM_PROMPT
        + "\nuser-rules:"
        + USER_PROMPT_RULES
        + "\nindependent-pass:"
        + str(pass_index)
        + "\ncrop-strategy:"
        + CROP_STRATEGY_VERSION
        + "\nrequest-shape:target-region-metadata-then-images-no-cross-pass-answer"
    )
    return f"{PROMPT_VERSION}:{_sha256_bytes(contract.encode('utf-8'))[:16]}"


def _estimate_cost(body: Mapping[str, Any]) -> tuple[int, float]:
    # Image token accounting is provider-specific.  DeepSeek's vision API
    # documents a maximum of 384 tokens per image; count that peak rather than
    # treating the base64 transport bytes as text tokens (which would wildly
    # overestimate a crop and make a $10 run stop prematurely).
    sanitized = json.loads(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    image_count = 0
    for message in sanitized.get("messages", []):
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, Mapping) or part.get("type") != "image_url":
                continue
            image = part.get("image_url")
            if isinstance(image, dict) and isinstance(image.get("url"), str):
                image["url"] = "<image-data-url>"
                image_count += 1
    encoded = json.dumps(
        sanitized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    input_tokens = max(1, math.ceil(len(encoded) / 4) + image_count * 384)
    cost = (
        input_tokens * INPUT_PRICE_PER_MILLION / 1_000_000
        + MAX_TOKENS * OUTPUT_PRICE_PER_MILLION / 1_000_000
    )
    return input_tokens, cost


def _http_request(
    body: Mapping[str, Any],
    api_key: str,
    transport: Any,
) -> tuple[int, bytes, Mapping[str, str]]:
    url = API_BASE_URL.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if transport is not None and callable(transport) and not hasattr(transport, "handle_request"):
        result = transport(url=url, headers=headers, json=dict(body))
        if isinstance(result, tuple):
            status = int(result[0])
            raw = result[1]
            response_headers = result[2] if len(result) > 2 else {}
            if isinstance(raw, Mapping):
                raw = json.dumps(raw, ensure_ascii=False).encode("utf-8")
            elif isinstance(raw, str):
                raw = raw.encode("utf-8")
            return status, bytes(raw), response_headers
        if isinstance(result, Mapping):
            return 200, json.dumps(result, ensure_ascii=False).encode("utf-8"), {}
        if hasattr(result, "status_code"):
            raw = getattr(result, "content", b"")
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            return int(result.status_code), bytes(raw), getattr(result, "headers", {})
        raise TypeError("injected HTTP transport returned an unsupported value")
    if httpx is not None:
        with httpx.Client(transport=transport, timeout=60.0) as client:
            response = client.post(url, headers=headers, json=dict(body))
        return int(response.status_code), bytes(response.content), response.headers

    # Minimal fallback for installations without httpx.  No credentials are
    # logged if urllib raises an HTTPError.
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60.0) as response:  # noqa: S310
            return int(response.status), response.read(), dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read(), dict(exc.headers.items())


def _redact_sensitive_text(value: str, api_key: str) -> str:
    if api_key:
        value = value.replace(api_key, "[REDACTED_API_KEY]")
    value = _BEARER_RE.sub(r"\1[REDACTED_API_KEY]", value)
    return _DATA_URL_RE.sub("[REDACTED_IMAGE_DATA]", value)


def _sanitize_response_body(content: bytes, api_key: str) -> bytes:
    """Keep provider evidence while ensuring credentials/request images are never persisted."""

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return json.dumps(
            {
                "redacted_non_utf8_response": True,
                "original_sha256": _sha256_bytes(content),
            },
            sort_keys=True,
        ).encode("utf-8")
    return _redact_sensitive_text(text, api_key).encode("utf-8")


def _write_raw_response(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, content)


def _response_content(payload: Any) -> Any:
    if isinstance(payload, Mapping) and "choices" in payload:
        choices = payload.get("choices")
        if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)) or not choices:
            raise _FormatError("provider response has no choices")
        first = choices[0]
        if not isinstance(first, Mapping):
            raise _FormatError("provider choice is not an object")
        message = first.get("message", first)
        if not isinstance(message, Mapping):
            raise _FormatError("provider message is not an object")
        content = message.get("content")
        if content is None:
            raise _FormatError("provider returned empty content")
        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if isinstance(item, str):
                    chunks.append(item)
                elif isinstance(item, Mapping):
                    value = item.get("text", item.get("content"))
                    if isinstance(value, str):
                        chunks.append(value)
            content = "".join(chunks)
        return content
    return payload


def _decode_provider_json(body: bytes) -> Any:
    if not body:
        raise _FormatError("provider returned an empty response")
    try:
        outer = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _FormatError(f"provider response is not valid JSON: {exc}") from exc
    content = _response_content(outer)
    if isinstance(content, (Mapping, list)):
        if (
            isinstance(content, Mapping)
            and isinstance(outer, Mapping)
            and isinstance(outer.get("usage"), Mapping)
        ):
            content = dict(content)
            content["_provider_usage"] = dict(outer["usage"])
        return content
    if not isinstance(content, str) or not content.strip():
        raise _FormatError("provider returned empty content")
    text = content.strip()
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        decoded = json.loads(text)
        if (
            isinstance(outer, Mapping)
            and isinstance(decoded, Mapping)
            and isinstance(outer.get("usage"), Mapping)
        ):
            # DeepSeek reports usage beside ``choices`` while the requested
            # JSON object lives in ``message.content``.  Carry the usage into
            # the decoded object without changing the provider's candidate
            # fields.
            decoded = dict(decoded)
            decoded["_provider_usage"] = dict(outer["usage"])
        return decoded
    except json.JSONDecodeError as exc:
        raise _FormatError(f"provider content is not valid JSON: {exc}") from exc


def _provider_items(payload: Any, unit_ids: Sequence[str]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        raw_items: Any = payload
    elif isinstance(payload, Mapping):
        raw_items = None
        for key in ("candidates", "results", "items", "outputs"):
            if key in payload:
                raw_items = payload[key]
                break
        if raw_items is None and ("unit_id" in payload or "unitId" in payload):
            raw_items = [payload]
        if raw_items is None:
            keyed: list[dict[str, Any]] = []
            for key, value in payload.items():
                if key in unit_ids and isinstance(value, Mapping):
                    keyed.append({"unit_id": key, **dict(value)})
            raw_items = keyed
    else:
        raise _FormatError("provider JSON must be an object or array")
    if isinstance(raw_items, Mapping):
        raw_items = [
            {"unit_id": key, **dict(value)}
            for key, value in raw_items.items()
            if isinstance(value, Mapping)
        ]
    if not isinstance(raw_items, list):
        raise _FormatError("provider candidates must be an array")
    items: list[dict[str, Any]] = []
    for value in raw_items:
        if isinstance(value, Mapping):
            items.append(dict(value))
    if not items:
        raise _FormatError("provider returned no candidates")
    if not any(item.get("unit_id", item.get("unitId")) for item in items):
        if len(items) != len(unit_ids):
            raise _FormatError("provider candidates lack unit_id values")
        for unit_id, item in zip(unit_ids, items, strict=True):
            item["unit_id"] = unit_id
    return items


def _classification(value: Any, item: Mapping[str, Any]) -> MathCandidateClassification:
    raw = _enum_value(value) if value is not None else ""
    aliases = {
        "display-math": "display",
        "display_math": "display",
        "equation": "display",
        "inline-math": "inline",
        "inline_math": "inline",
        "not_math": "not-math",
        "needs_split": "needs-split",
    }
    raw = aliases.get(raw, raw)
    if not raw:
        if item.get("latex"):
            raw = "display"
        elif item.get("source_markdown") or item.get("markdown"):
            raw = "inline"
    try:
        return MathCandidateClassification(raw)
    except ValueError as exc:
        raise _FormatError(f"unsupported math classification: {raw!r}") from exc


def _usage(payload: Any, attempts: int) -> ReviewUsage:
    usage = (
        payload.get("_provider_usage", payload.get("usage", {}))
        if isinstance(payload, Mapping)
        else {}
    )
    if not isinstance(usage, Mapping):
        usage = {}

    def integer(*keys: str) -> int:
        for key in keys:
            value = usage.get(key)
            if isinstance(value, (int, float)) and value >= 0:
                return int(value)
        return 0

    return ReviewUsage(
        input_tokens=integer("prompt_tokens", "input_tokens"),
        cache_creation_input_tokens=integer("cache_creation_input_tokens"),
        cache_read_input_tokens=integer("cache_read_input_tokens"),
        output_tokens=integer("completion_tokens", "output_tokens"),
        provider_turns=max(1, attempts),
    )


def _add_usage(left: ReviewUsage, right: ReviewUsage) -> ReviewUsage:
    return ReviewUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        cache_creation_input_tokens=(
            left.cache_creation_input_tokens + right.cache_creation_input_tokens
        ),
        cache_read_input_tokens=left.cache_read_input_tokens + right.cache_read_input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        provider_turns=left.provider_turns + right.provider_turns,
    )


def _retry_delay(attempt: int, headers: Mapping[str, str] | None = None) -> float:
    if headers:
        retry_after = headers.get("retry-after") or headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, min(float(retry_after), 2.0))
            except ValueError:
                pass
    return min(RETRY_BACKOFF_SECONDS * (2.0 ** max(0, attempt - 1)), 1.0)


def _normalize_equation_number(value: Any) -> str | None:
    """Store a visible equation label independently and without outer parentheses."""

    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.startswith("(") and normalized.endswith(")"):
        inner = normalized[1:-1].strip()
        if inner and "(" not in inner and ")" not in inner:
            normalized = inner
    return normalized or None


def _candidate_from_item(
    item: Mapping[str, Any],
    unit: SourceUnit,
    crop: _Crop,
    provider: str,
    model: str,
    prompt_version: str,
    source_pdf_sha256: str,
    request_sha256: str,
    response_sha256: str,
    raw_response_path: str,
    usage: ReviewUsage,
    estimated_cost_usd: float,
) -> MathCandidate:
    unit_id = item.get("unit_id", item.get("unitId"))
    if unit_id != unit.unit_id:
        raise _FormatError(f"provider returned unexpected unit_id {unit_id!r}")
    classification = _classification(
        item.get("classification", item.get("math_type", item.get("type"))), item
    )
    latex_value = item.get("latex")
    source_markdown_value = item.get("source_markdown", item.get("markdown"))
    equation_number_value = item.get("equation_number", item.get("equationNumber"))
    latex = latex_value.strip() if isinstance(latex_value, str) and latex_value.strip() else None
    source_markdown = (
        source_markdown_value.strip()
        if isinstance(source_markdown_value, str) and source_markdown_value.strip()
        else None
    )
    if classification is MathCandidateClassification.DISPLAY and latex is None:
        raise _FormatError(f"display candidate {unit.unit_id} has no latex")
    if classification is MathCandidateClassification.INLINE and source_markdown is None:
        raise _FormatError(f"inline candidate {unit.unit_id} has no source_markdown")
    uncertainties_value = item.get("uncertainties", [])
    if isinstance(uncertainties_value, str):
        uncertainties = [uncertainties_value] if uncertainties_value.strip() else []
    elif isinstance(uncertainties_value, Sequence):
        uncertainties = [str(value) for value in uncertainties_value if str(value).strip()]
    else:
        uncertainties = []
    needs_second_pass = item.get("needs_second_pass", item.get("needsSecondPass", False))
    if isinstance(needs_second_pass, str):
        needs_second_pass = needs_second_pass.strip().lower() in {"1", "true", "yes"}
    if not isinstance(needs_second_pass, bool):
        needs_second_pass = bool(needs_second_pass)
    equation_number = _normalize_equation_number(equation_number_value)
    candidate_id = _sha256_bytes(
        "\0".join(
            (
                crop.unit.unit_id,
                crop.sha256,
                provider,
                model,
                prompt_version,
                request_sha256,
                response_sha256,
                str(crop.pass_index),
            )
        ).encode("utf-8")
    )[:32]
    return MathCandidate(
        candidate_id=candidate_id,
        unit_id=unit.unit_id,
        page=unit.page,
        source_pdf_sha256=source_pdf_sha256,
        source_hash=unit.source_hash,
        crop_path=crop.relative_path,
        crop_sha256=crop.sha256,
        provider=provider,
        model=model,
        prompt_version=prompt_version,
        pass_index=crop.pass_index,
        classification=classification,
        latex=latex,
        source_markdown=source_markdown,
        equation_number=equation_number,
        uncertainties=uncertainties,
        needs_second_pass=needs_second_pass,
        request_sha256=request_sha256,
        response_sha256=response_sha256,
        raw_response_path=raw_response_path,
        usage=usage,
        estimated_cost_usd=max(0.0, estimated_cost_usd),
    )


def _request_batch(
    index: int,
    crops: Sequence[_Crop],
    provider: str,
    model: str,
    prompt_version: str,
    api_key: str,
    root: Path,
    budget: _Budget,
    attempt_cost: float,
    initial_reserved: bool,
) -> _BatchResult:
    unit_ids = [crop.unit.unit_id for crop in crops]
    result = _BatchResult(index=index, unit_ids=unit_ids)
    body, body_prompt_version = _prompt_and_request(crops, model)
    # A caller may monkeypatch the prompt implementation in tests; preserve
    # the actual request's prompt hash when that happens.
    if body_prompt_version != prompt_version:
        prompt_version = body_prompt_version
    request_sha256 = _sha256_json(body)
    work = root / ".littrans" / "work" / "math-vision"
    transport = _transport()
    last_error = ""
    last_failure_type = "provider"
    response_usage = ReviewUsage()
    reserved = attempt_cost if initial_reserved else 0.0

    for attempt in range(1, MAX_RETRIES + 2):
        if attempt > 1:
            if not budget.reserve(attempt_cost):
                result.budget_exhausted = True
                result.errors.append(
                    {
                        "unit_ids": unit_ids,
                        "failure_type": "budget",
                        "message": "cost ceiling reached before a retry",
                    }
                )
                result.attempts = attempt - 1
                result.reserved_cost = reserved
                return result
            reserved += attempt_cost
        result.attempts = attempt
        raw_path = work / f"response-{request_sha256}-attempt-{attempt:02d}.json"
        try:
            status, raw, headers = _http_request(body, api_key, transport)
            stored_raw = _sanitize_response_body(raw, api_key)
            _write_raw_response(raw_path, stored_raw)
        except Exception as exc:  # network clients expose several exception types
            safe_error = _redact_sensitive_text(str(exc), api_key)
            last_error = f"network request failed: {type(exc).__name__}: {safe_error}"
            last_failure_type = (
                "timeout" if "timeout" in type(exc).__name__.casefold() else "network"
            )
            _write_raw_response(
                raw_path,
                json.dumps(
                    {"error_type": type(exc).__name__, "error": safe_error},
                    ensure_ascii=False,
                ).encode("utf-8"),
            )
            if attempt <= MAX_RETRIES:
                time.sleep(_retry_delay(attempt))
                continue
            break
        if status < 200 or status >= 300:
            last_error = f"provider returned HTTP {status}"
            last_failure_type = (
                "authentication"
                if status in {401, 403}
                else "timeout"
                if status == 408
                else "quota"
                if status == 429
                else "provider"
            )
            if status in {408, 429} or status >= 500:
                if attempt <= MAX_RETRIES:
                    time.sleep(_retry_delay(attempt, headers))
                    continue
            break
        try:
            payload = _decode_provider_json(stored_raw)
            items = _provider_items(payload, unit_ids)
            by_id: dict[str, Mapping[str, Any]] = {}
            for item in items:
                item_id = item.get("unit_id", item.get("unitId"))
                if item_id in by_id:
                    raise _FormatError(f"provider returned duplicate unit_id {item_id!r}")
                if item_id in unit_ids:
                    by_id[str(item_id)] = item
            if set(by_id) != set(unit_ids):
                missing = sorted(set(unit_ids) - set(by_id))
                raise _FormatError(f"provider omitted unit_ids: {', '.join(missing)}")
            response_usage = _usage(payload, attempt)
            per_candidate_cost = reserved / max(len(crops), 1)
            parsed_candidates: list[MathCandidate] = []
            for crop in crops:
                candidate = _candidate_from_item(
                    by_id[crop.unit.unit_id],
                    crop.unit,
                    crop,
                    provider,
                    model,
                    prompt_version,
                    crop.source_pdf_sha256,
                    request_sha256,
                    _sha256_bytes(stored_raw),
                    _relative(root, raw_path),
                    response_usage,
                    per_candidate_cost,
                )
                parsed_candidates.append(candidate)
            result.candidates = parsed_candidates
            result.usage = response_usage
            result.reserved_cost = reserved
            return result
        except _FormatError as exc:
            last_error = str(exc)
            last_failure_type = "format"
            if attempt <= MAX_RETRIES:
                time.sleep(_retry_delay(attempt))
                continue
            break
        except (TypeError, ValueError) as exc:
            last_error = f"provider candidate validation failed: {exc}"
            last_failure_type = "format"
            if attempt <= MAX_RETRIES:
                time.sleep(_retry_delay(attempt))
                continue
            break
    result.usage = response_usage
    result.reserved_cost = reserved
    result.errors.append(
        {
            "unit_ids": unit_ids,
            "failure_type": last_failure_type,
            "message": last_error or "provider request failed",
            "attempts": result.attempts,
        }
    )
    return result


def _prepare_crops(
    root: Path,
    document: fitz.Document,
    work_items: Sequence[tuple[SourceUnit, int]],
    source_pdf_sha256: str,
) -> list[_Crop]:
    crops: list[_Crop] = []
    for unit, pass_index in work_items:
        crop = _render_crop(root, document, unit, source_pdf_sha256, pass_index)
        crops.append(crop)
    return crops


def _persist_candidates(
    root: Path,
    candidates: Sequence[MathCandidate],
    force: bool,
) -> tuple[list[MathCandidate], int]:
    path = root / "evidence" / "math" / "candidates.jsonl"
    if not candidates:
        return [], 0
    with project_write_lock(root):
        existing = _load_existing_candidates(path)
        existing_ids = {record.candidate_id for record in existing}
        appended: list[MathCandidate] = []
        for candidate in candidates:
            if not force and candidate.candidate_id in existing_ids:
                continue
            if force and candidate.candidate_id in existing_ids:
                # ``force`` is an explicit request for a new evidence item.
                # Keep the deterministic request identity in the fields below,
                # but avoid duplicate IDs because the local review importer
                # treats duplicate candidate IDs as an integrity error.
                candidate = candidate.model_copy(
                    update={
                        "candidate_id": _sha256_bytes(
                            f"{candidate.candidate_id}\0force\0{time.time_ns()}".encode()
                        )[:32]
                    }
                )
            appended.append(candidate)
            existing_ids.add(candidate.candidate_id)
        if appended:
            path.parent.mkdir(parents=True, exist_ok=True)
            previous = path.read_text(encoding="utf-8") if path.exists() else ""
            addition = "".join(
                candidate.model_dump_json(exclude_none=True) + "\n" for candidate in appended
            )
            atomic_write_bytes(path, (previous + addition).encode("utf-8"))
        return appended, len(existing) + len(appended)


def _empty_result(
    *,
    provider: str,
    model: str,
    page_spec: str,
    candidate_path: str,
    selected: int = 0,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "model": model,
        "page_spec": page_spec,
        "candidate_path": candidate_path,
        "selected_units": selected,
        "pending_units": 0,
        "pending_candidates": 0,
        "generated": 0,
        "written": 0,
        "reused": 0,
        "failed": 0,
        "budget_exhausted": False,
        "estimated_cost_usd": 0.0,
        "total_cost_usd": 0.0,
        "cost_usd": 0.0,
        "usage": ReviewUsage().model_dump(mode="json"),
        "errors": [],
        "candidates": [],
    }


def generate_math_candidates(
    root: Path,
    page_spec: str = "all",
    provider: str = DEFAULT_PROVIDER,
    model: str = DEFAULT_MODEL,
    limit: int | None = None,
    sampling: str = "sequential",
    max_cost_usd: float = 10.0,
    allow_remote: bool = False,
    concurrency: int = MAX_CONCURRENCY,
    batch_size: int = MAX_BATCH_SIZE,
    force: bool = False,
    unit_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Generate visual math candidates and persist durable evidence.

    ``allow_remote`` is an explicit authorization gate.  When work is
    pending, a disabled gate or a missing ``DEEPSEEK_API_KEY`` raises
    :class:`PermissionError` before rendering crops or making a request.
    """

    root = Path(root).resolve()
    provider = provider.strip().lower()
    if provider != DEFAULT_PROVIDER:
        raise ValueError(f"unsupported math vision provider: {provider}")
    if limit is not None and (isinstance(limit, bool) or limit < 0):
        raise ValueError("limit must be a non-negative integer or None")
    if not math.isfinite(float(max_cost_usd)) or max_cost_usd < 0:
        raise ValueError("max_cost_usd must be a finite non-negative number")
    if isinstance(concurrency, bool) or concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    if isinstance(batch_size, bool) or batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    # The service contract is a hard ceiling even when a caller supplies a
    # larger tuning value.
    concurrency = min(int(concurrency), MAX_CONCURRENCY)
    batch_size = min(int(batch_size), MAX_BATCH_SIZE)
    if not isinstance(sampling, str):
        raise ValueError("sampling must be a string")
    mode = sampling.strip().lower()
    if mode not in {
        "sequential",
        "uncertainty",
        "uncertainty-first",
        "random",
        "stable-random",
        "stratified",
    }:
        raise ValueError(
            "sampling must be one of sequential, uncertainty-first, random, or stratified"
        )
    if unit_ids is None or isinstance(unit_ids, (str, bytes)):
        raise ValueError("remote math vision requires a non-empty sequence of exact unit_ids")
    if any(not isinstance(value, str) for value in unit_ids):
        raise ValueError("unit_ids must contain only strings")
    normalized_ids = [value.strip() for value in unit_ids]
    if not normalized_ids or any(not value for value in normalized_ids):
        raise ValueError("unit_ids must contain at least one non-empty exact unit ID")
    if len(set(normalized_ids)) != len(normalized_ids):
        raise ValueError("unit_ids cannot contain duplicates")
    if len(normalized_ids) > MAX_PILOT_UNITS:
        raise ValueError(f"remote math vision pilot is limited to {MAX_PILOT_UNITS} exact unit IDs")
    requested_unit_ids = set(normalized_ids)

    config = load_project(root)
    source = config.source(root)
    if not source.is_file():
        raise FileNotFoundError(f"source PDF does not exist: {source}")
    source_pdf_sha256 = sha256_file(source)
    if source_pdf_sha256 != config.source_sha256:
        raise ValueError("Source PDF hash changed after project initialization")
    units = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    if not units:
        raise ValueError("unknown unit_ids: " + ", ".join(sorted(requested_unit_ids)))
    with fitz.open(source) as document:
        pages = set(parse_page_spec(page_spec, document.page_count))
    known_ids = {unit.unit_id for unit in units}
    unknown = sorted(requested_unit_ids - known_ids)
    if unknown:
        raise ValueError(f"unknown unit_ids: {', '.join(unknown)}")
    ineligible = sorted(
        unit.unit_id
        for unit in units
        if unit.unit_id in requested_unit_ids
        and (unit.page not in pages or not _is_math_unit(unit))
    )
    if ineligible:
        raise ValueError(
            "requested unit_ids are outside the selected pages, not math, or already verified: "
            + ", ".join(ineligible)
        )
    selected = _selected_units(
        units,
        pages,
        mode,
        source_pdf_sha256,
        None,
    )
    selected = [unit for unit in selected if unit.unit_id in requested_unit_ids]
    if limit is not None:
        selected = selected[:limit]
    invalid_source_hashes = [
        unit.unit_id
        for unit in selected
        if _sha256_bytes(unit.source_text.encode("utf-8")) != unit.source_hash
    ]
    if invalid_source_hashes:
        raise ValueError(
            "current unit source_hash does not match source_text: "
            + ", ".join(invalid_source_hashes)
        )
    candidate_path = root / "evidence" / "math" / "candidates.jsonl"
    existing = _load_existing_candidates(candidate_path)
    pending: list[tuple[SourceUnit, int]] = []
    reused = 0
    reused_candidates = 0
    for unit in selected:
        missing_passes: list[int] = []
        for pass_index in (1, 2):
            matched = any(
                _record_matches(
                    root,
                    record,
                    unit,
                    source_pdf_sha256,
                    provider,
                    model,
                    pass_index,
                )
                for record in existing
            )
            if matched:
                reused_candidates += 1
            else:
                missing_passes.append(pass_index)
        if not missing_passes:
            reused += 1
        else:
            pending.extend((unit, pass_index) for pass_index in missing_passes)
    result = _empty_result(
        provider=provider,
        model=model,
        page_spec=page_spec,
        candidate_path="evidence/math/candidates.jsonl",
        selected=len(selected),
    )
    result["reused"] = reused
    result["reused_candidates"] = reused_candidates
    result["pending_units"] = len({unit.unit_id for unit, _ in pending})
    result["pending_candidates"] = len(pending)
    if not pending:
        return result
    if not allow_remote:
        raise PermissionError(
            "remote math vision is disabled; pass allow_remote=True to authorize DeepSeek"
        )
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise PermissionError("DEEPSEEK_API_KEY is required when allow_remote=True")
    if max_cost_usd <= 0:
        result["budget_exhausted"] = True
        result["failed"] = len(pending)
        result["errors"] = [
            {
                "unit_ids": [unit.unit_id for unit, _ in pending],
                "failure_type": "budget",
                "message": "max_cost_usd is zero",
            }
        ]
        return result

    with fitz.open(source) as document:
        crops = _prepare_crops(root, document, pending, source_pdf_sha256)
    # Keep the two visual passes in separate requests.  Pass 2 receives a wider
    # crop and no pass-1 answer, so it is an actual independent observation.
    batches: list[list[_Crop]] = []
    for pass_index in (1, 2):
        pass_crops = [crop for crop in crops if crop.pass_index == pass_index]
        batches.extend(
            pass_crops[start : start + batch_size]
            for start in range(0, len(pass_crops), batch_size)
        )
    budget = _Budget(float(max_cost_usd))
    prompt_versions: list[str] = []
    reserved_batches: list[tuple[int, float, bool]] = []
    blocked_from_index: int | None = None
    for index, batch in enumerate(batches):
        body, prompt_version = _prompt_and_request(batch, model)
        prompt_versions.append(prompt_version)
        _, attempt_cost = _estimate_cost(body)
        if not budget.reserve(attempt_cost):
            blocked_from_index = index
            break
        reserved_batches.append((index, attempt_cost, True))
    if blocked_from_index is not None:
        result["budget_exhausted"] = True
        result["failed"] = sum(len(batch) for batch in batches[blocked_from_index:])
        result["errors"].append(
            {
                "unit_ids": [
                    crop.unit.unit_id for batch in batches[blocked_from_index:] for crop in batch
                ],
                "failure_type": "budget",
                "message": "cost ceiling reached before request",
            }
        )
    if not reserved_batches:
        result["estimated_cost_usd"] = budget.spent()
        result["total_cost_usd"] = budget.spent()
        result["cost_usd"] = budget.spent()
        return result

    batch_results: list[_BatchResult] = []
    persisted_candidates: list[MathCandidate] = []
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="littrans-math") as pool:
        futures: dict[Future[_BatchResult], int] = {}
        for index, attempt_cost, initial_reserved in reserved_batches:
            future = pool.submit(
                _request_batch,
                index,
                batches[index],
                provider,
                model,
                prompt_versions[index],
                api_key,
                root,
                budget,
                attempt_cost,
                initial_reserved,
            )
            futures[future] = index
        for future in as_completed(futures):
            batch_result = future.result()
            batch_results.append(batch_result)
            # Persist each completed request immediately.  A later provider
            # failure must not discard already completed non-authoritative
            # evidence, and a resumed run can then skip those units.
            appended, _ = _persist_candidates(root, batch_result.candidates, force=force)
            persisted_candidates.extend(appended)
    batch_results.sort(key=lambda item: item.index)
    all_candidates = [candidate for batch in batch_results for candidate in batch.candidates]
    errors = list(result["errors"])
    usage = ReviewUsage()
    for completed_batch in batch_results:
        errors.extend(completed_batch.errors)
        usage = _add_usage(usage, completed_batch.usage)
    result["generated"] = len(all_candidates)
    result["written"] = len(persisted_candidates)
    result["failed"] = max(0, len(pending) - len(all_candidates))
    result["errors"] = errors
    result["budget_exhausted"] = bool(
        result["budget_exhausted"] or any(batch.budget_exhausted for batch in batch_results)
    )
    result["estimated_cost_usd"] = budget.spent()
    result["total_cost_usd"] = budget.spent()
    result["cost_usd"] = budget.spent()
    result["usage"] = usage.model_dump(mode="json")
    result["candidates"] = [candidate.model_dump(mode="json") for candidate in persisted_candidates]
    return result


__all__ = ["generate_math_candidates", "set_http_transport", "HTTP_TRANSPORT"]
