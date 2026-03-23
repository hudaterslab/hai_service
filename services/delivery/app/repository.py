from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

from .config import DeliverySettings
from .models import DeliveryJob


class DeliveryRepository:
    def __init__(self, settings: DeliverySettings):
        self.settings = settings

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        conn = psycopg.connect(self.settings.database_url, row_factory=dict_row)
        try:
            yield conn
        finally:
            conn.close()

    def fetch_next_job(self) -> DeliveryJob | None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  da.*,
                  a.event_id, a.camera_id, a.kind, a.local_path, a.checksum_sha256,
                  e.event_type, e.occurred_at, e.payload_json,
                  c.name AS camera_name,
                  d.type, d.config_json, d.enabled
                FROM delivery_attempts da
                JOIN artifacts a ON a.id = da.artifact_id
                JOIN events e ON e.id = a.event_id
                JOIN cameras c ON c.id = a.camera_id
                JOIN destinations d ON d.id = da.destination_id
                WHERE da.status IN ('queued', 'failed')
                  AND (da.next_retry_at IS NULL OR da.next_retry_at <= NOW())
                ORDER BY da.created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """
            )
            row = cur.fetchone()
            if not row:
                conn.commit()
                return None
            cur.execute(
                "UPDATE delivery_attempts SET status = 'in_progress', updated_at = NOW() WHERE id = %s",
                (row["id"],),
            )
            conn.commit()
            return DeliveryJob.from_row(row)

    def mark_success(self, job: DeliveryJob, status_code: int | None) -> None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE delivery_attempts
                SET status = 'success', http_status = %s, error_message = NULL, updated_at = NOW()
                WHERE id = %s
                """,
                (status_code, job.id),
            )
            cur.execute(
                """
                UPDATE artifacts
                SET uri = COALESCE(uri, 'delivered:' || checksum_sha256)
                WHERE id = %s
                """,
                (job.artifact_id,),
            )
            conn.commit()

    def mark_failure(self, job: DeliveryJob, status_code: int | None, error: str | None) -> None:
        next_attempt = job.attempt_no + 1
        delay_sec = self.next_backoff(next_attempt)
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE delivery_attempts
                SET status = 'failed',
                    attempt_no = %s,
                    http_status = %s,
                    error_message = %s,
                    next_retry_at = NOW() + (%s || ' seconds')::interval,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (next_attempt, status_code, error, delay_sec, job.id),
            )
            conn.commit()

    def next_backoff(self, attempt_no: int) -> int:
        idx = min(max(attempt_no - 1, 0), len(self.settings.backoff) - 1)
        return self.settings.backoff[idx]

