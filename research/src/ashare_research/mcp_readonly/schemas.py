from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FactorId = Literal["momentum_20", "momentum_60", "momentum_120", "ma60_distance", "volatility_20", "liquidity_20"]
Scope = Literal["native", "matched", "legacy_label_first", "all"]


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class ListRequest(StrictModel):
    stage: Literal["M2", "M3A", "M3B"] | None = None
    offset: int = Field(default=0, ge=0, le=10000)
    limit: int = Field(default=20, ge=1, le=50)


class ExperimentRequest(StrictModel):
    experiment_id: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    section: Literal["summary", "coverage", "definitions", "provenance", "artifacts"] = "summary"
    offset: int = Field(default=0, ge=0, le=100000)
    limit: int = Field(default=20, ge=1, le=50)


class FactorRequest(StrictModel):
    experiment_id: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    factor_ids: list[FactorId] = Field(default_factory=lambda: ["momentum_60"], min_length=1, max_length=6)
    scope: Scope = "all"
    view: Literal["summary", "daily"] = "summary"
    start_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    offset: int = Field(default=0, ge=0, le=100000)
    limit: int = Field(default=20, ge=1, le=50)

    @model_validator(mode="after")
    def validate_dates(self):
        from datetime import date
        for value in (self.start_date, self.end_date):
            if value is not None:
                date.fromisoformat(value)
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("Dates must be ordered")
        if len(set(self.factor_ids)) != len(self.factor_ids):
            raise ValueError("Duplicate factors")
        if self.view == "summary" and (self.start_date or self.end_date):
            raise ValueError("Date filters apply to saved daily rows only")
        return self


class ReportRequest(StrictModel):
    experiment_id: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    offset: int = Field(default=0, ge=0, le=1000000)
    max_characters: int = Field(default=8000, ge=1, le=20000)


class ErrorInfo(StrictModel):
    code: str
    message: str


class Response(StrictModel):
    api_version: Literal["1.0"] = "1.0"
    ok: bool
    data: dict[str, Any] | None = None
    source: dict[str, Any] | None = None
    research_context: dict[str, Any] | None = None
    pagination: dict[str, Any] | None = None
    error: ErrorInfo | None = None


REQUESTS = {"list_experiments": ListRequest, "get_experiment": ExperimentRequest, "get_factor_results": FactorRequest, "get_research_report": ReportRequest}
