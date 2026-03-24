from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DeliveryResult:
    ok: bool
    status_code: int | None
    error: str | None


@dataclass(frozen=True)
class DeliveryJob:
    id: str
    artifact_id: str
    destination_id: str
    destination_type: str
    destination_enabled: bool
    config: dict[str, Any]
    event_payload: dict[str, Any]
    event_type: str
    occurred_at: datetime | str | None
    camera_id: str
    camera_name: str
    kind: str
    local_path: str
    checksum_sha256: str
    attempt_no: int

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "DeliveryJob":
        config = row.get("config_json")
        return cls(
            id=str(row["id"]),
            artifact_id=str(row["artifact_id"]),
            destination_id=str(row["destination_id"]),
            destination_type=str(row["type"]),
            destination_enabled=bool(row["enabled"]),
            config=config if isinstance(config, dict) else {},
            event_payload=row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {},
            event_type=str(row.get("event_type") or ""),
            occurred_at=row.get("occurred_at"),
            camera_id=str(row.get("camera_id") or ""),
            camera_name=str(row.get("camera_name") or ""),
            kind=str(row.get("kind") or ""),
            local_path=str(row.get("local_path") or ""),
            checksum_sha256=str(row.get("checksum_sha256") or ""),
            attempt_no=int(row.get("attempt_no") or 1),
        )

    @property
    def local_file(self) -> Path:
        return Path(self.local_path)
