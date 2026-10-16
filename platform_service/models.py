from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Parameters(BaseModel):
    model_config=ConfigDict(extra='forbid')
    account: float=Field(default=1000000,gt=0,le=1e12)
    topk: int=Field(default=4,ge=1,le=100)
    n_drop: int=Field(default=1,ge=0,le=100)
    buy_cost: float=Field(default=.001,ge=0,le=.1)
    sell_cost: float=Field(default=.002,ge=0,le=.1)
    min_cost: float=Field(default=5,ge=0,le=10000)
    seed: int=Field(default=42,ge=0,le=2147483647)
    @model_validator(mode='after')
    def valid(self):
        if self.n_drop>self.topk:raise ValueError('n_drop must not exceed topk')
        return self


class RunRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    dataset_id: UUID
    title: str=Field(default='Daily baseline research',min_length=1,max_length=160)
    external_ref: str|None=Field(default=None,max_length=200)
    idempotency_key: str=Field(min_length=8,max_length=160,pattern=r'^[A-Za-z0-9_.:-]+$')
    parameters: Parameters=Field(default_factory=Parameters)


class ScheduleRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name: str=Field(min_length=1,max_length=160)
    research: RunRequest
    interval_seconds: int=Field(ge=60,le=31536000)
    first_run_at: datetime
    enabled: bool=False
    @model_validator(mode='after')
    def timezone(self):
        if self.first_run_at.tzinfo is None:raise ValueError('first_run_at must include timezone')
        return self
