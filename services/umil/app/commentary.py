"""Governed explanation: language is rendered only from approved evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Mapping

from .dna import MarketDNASnapshot
from .versions import VERSIONS


@dataclass(frozen=True, slots=True)
class EvidenceClaim:
    claim_id: str
    text: str
    evidence_keys: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        row = asdict(self)
        row["evidence_keys"] = list(self.evidence_keys)
        row["evidence_ids"] = list(self.evidence_ids)
        return row


@dataclass(frozen=True, slots=True)
class Commentary:
    summary: str
    claims: tuple[EvidenceClaim, ...]
    schema_version: str = VERSIONS.commentary_schema_version

    def as_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary, "claims": [claim.as_dict() for claim in self.claims],
            "commentary_schema_version": self.schema_version,
            "generated_from_market_data_only": True,
        }


def deterministic_commentary(snapshot: MarketDNASnapshot) -> Commentary:
    dna = snapshot.components
    claims: list[EvidenceClaim] = []
    trend = dna["TRN"]
    claims.append(EvidenceClaim(
        "trend", f"Trend conditions are {trend.state.lower()} ({trend.score:.0f}/100).",
        trend.inputs, trend.evidence_ids or snapshot.evidence_ids,
    ))
    momentum = dna["MOM"]
    claims.append(EvidenceClaim(
        "momentum", f"Momentum is {momentum.state.lower()} ({momentum.score:.0f}/100).",
        momentum.inputs, momentum.evidence_ids or snapshot.evidence_ids,
    ))
    pressure = dna["PRS"]
    if pressure.confidence > 0:
        claims.append(EvidenceClaim(
            "pressure", f"Observed market pressure is {pressure.state.lower()} ({pressure.score:.0f}/100).",
            pressure.inputs, pressure.evidence_ids or snapshot.evidence_ids,
        ))
    risk = dna["RSK"]
    claims.append(EvidenceClaim(
        "risk", f"Measured market and data risk is {risk.state.lower()} ({risk.score:.0f}/100; higher is riskier).",
        risk.inputs, risk.evidence_ids or snapshot.evidence_ids,
    ))
    lead = " ".join(claim.text for claim in claims)
    suffix = (
        f" Clarity is {snapshot.clarity:.0f}/100 and evidence confidence is "
        f"{snapshot.confidence:.0f}/100. This is descriptive intelligence, not a trade instruction."
    )
    return Commentary(lead + suffix, tuple(claims))


def validate_commentary(
    commentary: Commentary,
    *,
    allowed_evidence_ids: Iterable[str],
    allowed_evidence_keys: Iterable[str],
) -> tuple[bool, tuple[str, ...]]:
    ids = set(allowed_evidence_ids)
    keys = set(allowed_evidence_keys)
    errors: list[str] = []
    if not commentary.claims:
        errors.append("NO_CLAIMS")
    for claim in commentary.claims:
        if not claim.evidence_ids or not set(claim.evidence_ids).issubset(ids):
            errors.append(f"UNSUPPORTED_EVIDENCE:{claim.claim_id}")
        if not claim.evidence_keys or not set(claim.evidence_keys).issubset(keys):
            errors.append(f"UNSUPPORTED_KEY:{claim.claim_id}")
    forbidden = ("guarantee", "risk-free", "will profit", "buy now", "sell now")
    if any(term in commentary.summary.lower() for term in forbidden):
        errors.append("PROHIBITED_CLAIM")
    return not errors, tuple(errors)
