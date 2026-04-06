"""Model value-index scoring for Abacus/RouteLLM catalog entries.

This module enriches models from the upstream `/v1/models` response with:
- Use-case specific "value index" (0-100, higher is better bang-for-buck)
- Minimum capability gates per use-case (e.g. condensation requires large context)
- Family "quality priors" multipliers (heuristic, configurable)
- Penalties for missing `thinking` and low `max_completion_tokens`

The upstream catalog is expected to contain items like:
{
  "id": "...",
  "model_type": "text_generation",
  "context_length": 128000,
  "max_completion_tokens": 16384,
  "thinking": true | false | missing,
  "input_token_rate": "0.00000015",
  "output_token_rate": "0.0000006",
  ...
}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

UseCaseName = Literal["coding", "architecting", "condensation"]


def _ffloat(x: object) -> float | None:
    try:
        if x is None:
            return None
        return float(x)  # type: ignore[arg-type]
    except Exception:
        return None


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _safe_int(x: object, default: int = 0) -> int:
    try:
        return int(x)  # type: ignore[arg-type]
    except Exception:
        return default


def _context_score(context_length: int) -> float:
    """Log-scaled context score: 128k -> 0.0, 2M -> 1.0 (clamped)."""
    ctx_min = 128_000
    ctx_max = 2_097_152

    if context_length <= 0:
        return 0.0

    c = max(ctx_min, min(ctx_max, float(context_length)))

    # log base-2 normalization
    import math

    return math.log(c / ctx_min, 2) / math.log(ctx_max / ctx_min, 2)


def _is_coding_id(model_id: str) -> bool:
    m = model_id.lower()
    keys = (
        "codex",
        "coder",
        "code-fast",
        "grok-code",
        "qwen-2.5-coder",
        "qwen3-coder",
    )
    return any(k in m for k in keys)


def _provider_family(model_id: str) -> str:
    if "/" in model_id:
        return model_id.split("/", 1)[0]

    if model_id.startswith("claude"):
        return "anthropic"
    if model_id.startswith(("gpt", "o3", "o4", "openai/")):
        return "openai"
    if model_id.startswith("gemini"):
        return "google"
    if model_id.startswith(("meta-llama", "llama")):
        return "meta"
    if model_id.startswith(("kimi", "moonshotai")):
        return "moonshot"
    if model_id.startswith("deepseek"):
        return "deepseek"
    if model_id.lower().startswith(("qwen", "qwen/")):
        return "qwen"
    if model_id.startswith("grok"):
        return "xai"

    return "other"


def _capability_scores(model: dict[str, Any], *, missing_thinking_penalty: float) -> dict[str, float]:
    """Compute base capability components in [0,1] (heuristic, not benchmark-derived)."""
    model_id = str(model.get("id") or "")
    ctx_s = _context_score(_safe_int(model.get("context_length"), 0))

    thinking = model.get("thinking", None)
    if thinking is True:
        reasoning = 1.0
        thinking_missing = False
    elif thinking is False:
        # Some providers don't expose "thinking" but set false; treat as lower reasoning.
        reasoning = 0.6
        thinking_missing = False
    else:
        # Missing/unspecified; treat as neutral then apply a penalty (configurable).
        reasoning = 0.75
        thinking_missing = True

    if thinking_missing:
        reasoning *= (1.0 - _clamp(missing_thinking_penalty, 0.0, 0.8))

    coding = 1.0 if _is_coding_id(model_id) else 0.6

    desc = str(model.get("description") or "").lower()
    if any(k in desc for k in ("coding", "code generation", "software", "developer", "tool", "refactor", "debug")):
        coding = min(1.0, coding + 0.15)
    if any(k in desc for k in ("reasoning", "reason", "analytical", "deep analysis")):
        reasoning = min(1.0, reasoning + 0.1)

    return {"ctx": _clamp(ctx_s), "reason": _clamp(reasoning), "coding": _clamp(coding)}


def _max_out_multiplier(*, max_out: int, ideal_max_out: int, floor: float = 0.35) -> float:
    """Penalty multiplier for low max output tokens relative to an ideal.

    - If max_out >= ideal_max_out, multiplier = 1.0
    - Else multiplier scales linearly and is clamped to [floor, 1.0]
    """
    if ideal_max_out <= 0:
        return 1.0
    if max_out <= 0:
        return floor
    return _clamp(max_out / float(ideal_max_out), floor, 1.0)


@dataclass(frozen=True)
class UseCaseGates:
    min_context_length: int
    min_max_completion_tokens: int


@dataclass(frozen=True)
class UseCaseSpec:
    name: UseCaseName
    # Token budget used for expected job cost
    in_tokens: int
    out_tokens: int
    # Capability weights (sum ~= 1)
    w_ctx: float
    w_reason: float
    w_coding: float
    # Gates
    gates: UseCaseGates
    # Smooth penalty reference
    ideal_max_completion_tokens: int


@dataclass(frozen=True)
class ValueIndexConfig:
    """Configuration for enrichment/scoring."""

    # Multiplier by provider family (heuristic "quality prior")
    family_quality_priors: dict[str, float] = field(default_factory=dict)

    # Penalty applied when `thinking` is missing/unspecified (reduces reason component)
    missing_thinking_penalty: float = 0.10

    # Use-case specs (defaults defined via `default_use_cases()`)
    use_cases: tuple[UseCaseSpec, ...] = field(default_factory=tuple)


def default_family_quality_priors() -> dict[str, float]:
    """Conservative defaults (near-neutral). Tune via env JSON."""
    return {
        "openai": 1.00,
        "anthropic": 1.00,
        "google": 1.00,
        "xai": 1.00,
        "qwen": 1.00,
        "meta": 1.00,
        "deepseek": 1.00,
        "moonshot": 1.00,
        "zai-org": 1.00,
        "minimax": 1.00,
        "other": 1.00,
    }


def default_use_cases() -> tuple[UseCaseSpec, ...]:
    # Minimum gates are intentionally stricter than "works at all" for better realism.
    return (
        UseCaseSpec(
            name="coding",
            in_tokens=8000,
            out_tokens=2000,
            w_ctx=0.20,
            w_reason=0.35,
            w_coding=0.45,
            gates=UseCaseGates(min_context_length=128_000, min_max_completion_tokens=8_192),
            ideal_max_completion_tokens=16_384,
        ),
        UseCaseSpec(
            name="architecting",
            in_tokens=6000,
            out_tokens=1500,
            w_ctx=0.30,
            w_reason=0.50,
            w_coding=0.20,
            gates=UseCaseGates(min_context_length=128_000, min_max_completion_tokens=4_096),
            ideal_max_completion_tokens=8_192,
        ),
        UseCaseSpec(
            name="condensation",
            in_tokens=40_000,
            out_tokens=800,
            w_ctx=0.65,
            w_reason=0.25,
            w_coding=0.10,
            gates=UseCaseGates(min_context_length=256_000, min_max_completion_tokens=512),
            ideal_max_completion_tokens=4_096,
        ),
    )


def _passes_gates(model: dict[str, Any], gates: UseCaseGates) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    ctx = _safe_int(model.get("context_length"), 0)
    max_out = _safe_int(model.get("max_completion_tokens"), 0)

    if ctx < gates.min_context_length:
        reasons.append(f"context_length<{gates.min_context_length}")
    if max_out < gates.min_max_completion_tokens:
        reasons.append(f"max_completion_tokens<{gates.min_max_completion_tokens}")

    return (len(reasons) == 0), reasons


def enrich_abacus_models(
    upstream_data: dict[str, Any],
    *,
    cfg: ValueIndexConfig,
    exclude_router_models: bool = True,
) -> dict[str, Any]:
    """Return a copy of `/v1/models` payload enriched with value indexes and metadata.

    Adds per-model:
      model["abacus_proxy"] = {
        "family": "...",
        "quality_prior": 1.0,
        "use_cases": {
           "coding": {"eligible": true, "index": 12.3, "job_cost": 0.0021, "notes": [...]},
           ...
        }
      }

    Also adds top-level:
      payload["abacus_proxy_meta"] = {"version": 1, ...}
    """
    if not isinstance(upstream_data, dict):
        return upstream_data

    models = upstream_data.get("data")
    if not isinstance(models, list):
        return upstream_data

    # Prepare normalized specs
    use_cases = cfg.use_cases or default_use_cases()
    family_priors = cfg.family_quality_priors or default_family_quality_priors()

    # First pass: compute raw value metrics for eligible models
    scored: list[dict[str, Any]] = []
    for m in models:
        if not isinstance(m, dict):
            scored.append(m)
            continue

        model_type = m.get("model_type")
        model_id = str(m.get("id") or "")
        if model_type != "text_generation":
            # Only score text_generation for these use cases.
            scored.append(m)
            continue

        if exclude_router_models and model_id == "route-llm":
            scored.append(m)
            continue

        in_rate = _ffloat(m.get("input_token_rate"))
        out_rate = _ffloat(m.get("output_token_rate"))
        if in_rate is None or out_rate is None:
            scored.append(m)
            continue

        family = _provider_family(model_id)
        prior = float(family_priors.get(family, family_priors.get("other", 1.0)))

        caps = _capability_scores(m, missing_thinking_penalty=cfg.missing_thinking_penalty)
        max_out = _safe_int(m.get("max_completion_tokens"), 0)

        use_case_results: dict[str, Any] = {}

        for uc in use_cases:
            eligible, gate_notes = _passes_gates(m, uc.gates)

            # Capability uses smooth max_out penalty even when eligible.
            out_mult = _max_out_multiplier(
                max_out=max_out,
                ideal_max_out=uc.ideal_max_completion_tokens,
                floor=0.35,
            )
            capability = (
                uc.w_ctx * caps["ctx"] + uc.w_reason * caps["reason"] + uc.w_coding * caps["coding"]
            ) * out_mult

            # Apply quality prior after internal heuristics.
            capability *= prior

            job_cost = uc.in_tokens * in_rate + uc.out_tokens * out_rate
            value = (capability / job_cost) if (eligible and job_cost > 0) else 0.0

            use_case_results[uc.name] = {
                "eligible": bool(eligible),
                "value_raw": float(value),
                "index": 0.0,  # filled in later after normalization
                "job_cost": float(job_cost),
                "capability": float(capability),
                "notes": gate_notes,
                "penalties": {
                    "max_out_multiplier": float(out_mult),
                    "missing_thinking_penalty": float(_clamp(cfg.missing_thinking_penalty, 0.0, 0.8)),
                },
            }

        m2 = dict(m)
        m2["abacus_proxy"] = {
            "family": family,
            "quality_prior": prior,
            "use_cases": use_case_results,
        }
        scored.append(m2)

    # Second pass: normalize indexes to 0-100 per use case based on max value_raw
    maxima: dict[str, float] = {uc.name: 0.0 for uc in use_cases}
    for m in scored:
        if not isinstance(m, dict):
            continue
        ap = m.get("abacus_proxy")
        if not isinstance(ap, dict):
            continue
        ucs = ap.get("use_cases")
        if not isinstance(ucs, dict):
            continue
        for uc in use_cases:
            d = ucs.get(uc.name)
            if isinstance(d, dict):
                maxima[uc.name] = max(maxima[uc.name], float(d.get("value_raw", 0.0) or 0.0))

    for m in scored:
        if not isinstance(m, dict):
            continue
        ap = m.get("abacus_proxy")
        if not isinstance(ap, dict):
            continue
        ucs = ap.get("use_cases")
        if not isinstance(ucs, dict):
            continue
        for uc in use_cases:
            d = ucs.get(uc.name)
            if not isinstance(d, dict):
                continue
            mx = maxima.get(uc.name, 0.0)
            if mx > 0:
                d["index"] = 100.0 * float(d.get("value_raw", 0.0) or 0.0) / mx
            else:
                d["index"] = 0.0

    out = dict(upstream_data)
    out["data"] = scored
    out["abacus_proxy_meta"] = {
        "version": 1,
        "excluded_router_models": bool(exclude_router_models),
        "use_case_gates": {
            uc.name: {
                "min_context_length": uc.gates.min_context_length,
                "min_max_completion_tokens": uc.gates.min_max_completion_tokens,
                "ideal_max_completion_tokens": uc.ideal_max_completion_tokens,
                "token_budget": {"in": uc.in_tokens, "out": uc.out_tokens},
                "weights": {"ctx": uc.w_ctx, "reason": uc.w_reason, "coding": uc.w_coding},
            }
            for uc in use_cases
        },
        "family_quality_priors": dict(family_priors),
        "missing_thinking_penalty": float(_clamp(cfg.missing_thinking_penalty, 0.0, 0.8)),
    }
    return out