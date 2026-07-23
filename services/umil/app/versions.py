"""Independently evolving UMIL contract versions."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class ContractVersions:
    service_version: str = "1.6.2"
    api_schema_version: str = "umil-api-v2"
    snapshot_schema_version: str = "umil-snapshot-v2"
    feature_schema_version: str = "umil-features-v2"
    dna_schema_version: str = "umil-dna-v2"
    commentary_schema_version: str = "umil-commentary-v2"
    calculation_version: str = "umil-calc-v1.6.2"
    normalization_version: str = "umil-normalization-v2"
    quality_schema_version: str = "umil-quality-v2"
    evidence_schema_version: str = "umil-evidence-v2"

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


VERSIONS = ContractVersions()
