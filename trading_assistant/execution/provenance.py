"""Ownership/provenance for every externally meaningful trade."""

from enum import Enum


class TradeProvenance(str, Enum):
    MANUAL = "MANUAL"
    ATS_ASSISTED_MANUAL = "ATS_ASSISTED_MANUAL"
    ATS_AUTONOMOUS = "ATS_AUTONOMOUS"
    EXTERNAL = "EXTERNAL"
    UNKNOWN = "UNKNOWN"


HUMAN_PROVENANCE = {
    TradeProvenance.MANUAL,
    TradeProvenance.ATS_ASSISTED_MANUAL,
}
