"""Pythia 공통 스키마. Pydantic v2."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class MarketType(str, Enum):
    DAO_GOVERNANCE = "dao_governance"
    POLYMARKET = "polymarket"
    KALSHI = "kalshi"
    OTHER = "other"


class PredictionStatus(str, Enum):
    PENDING = "pending"
    RESOLVED = "resolved"
    INVALID = "invalid"


class Prediction(BaseModel):
    id: str
    ts_created: datetime
    market_type: MarketType
    market_ref: str = Field(description="proposal id, polymarket condition_id, etc.")
    prob: float = Field(ge=0.0, le=1.0, description="P(YES)")
    rationale_hash: str = Field(description="sha256 of full LLM reasoning (stored in logs/agent/)")
    ipfs_cid: str | None = None
    x_tweet_id: str | None = None
    resolve_at: datetime
    status: PredictionStatus = PredictionStatus.PENDING
    outcome: Literal["yes", "no", "invalid"] | None = None
    brier: float | None = Field(default=None, ge=0.0, le=1.0)


class ManifestEntry(BaseModel):
    ts: datetime
    source: str
    path: str = Field(description="relative to data/raw/")
    sha256: str
    rows: int
    bytes: int
    ingester_version: str
