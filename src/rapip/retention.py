"""Validated lifecycle policy. Budget and recovery state are deliberately retained."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RetentionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success_log_hours: int = Field(default=24, ge=1, le=2160)
    error_log_days: int = Field(default=7, ge=1, le=365)
    probe_log_days: int = Field(default=7, ge=1, le=365)
    metrics_days: int = Field(default=30, ge=7, le=365)
    operation_days: int = Field(default=30, ge=1, le=365)
    event_days: int = Field(default=30, ge=1, le=365)
    max_records: int = Field(default=100000, ge=1000, le=1000000)
    max_database_mb: int = Field(default=256, ge=32, le=4096)
    trace_days: int = Field(default=30, ge=1, le=365)
    max_trace_records: int = Field(default=20000, ge=1000, le=200000)
    metrics_interval_seconds: Literal[10, 30, 60] = 60
