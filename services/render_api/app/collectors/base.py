from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass
class Record:
    source_record_id: str
    identity: str
    text: str
    url: str
    published_at: datetime


@dataclass
class Batch:
    records: list[Record] = field(default_factory=list)
    cursor: dict = field(default_factory=dict)
    scanned: int = 0
    exhausted: bool = False
    stop_reason: str = "batch_limit"
    retry_seconds: int = 0


class Adapter(Protocol):
    def collect(self, cursor: dict, limit: int) -> Batch: ...


class SourceError(Exception):
    def __init__(self, code, retry_seconds=60):
        self.code, self.retry_seconds = code, retry_seconds
        super().__init__(code)
