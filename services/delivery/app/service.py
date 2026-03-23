import time

from .config import DeliverySettings
from .models import DeliveryJob
from .repository import DeliveryRepository
from .transports import DeliveryTransport


class LocalArtifactCleanup:
    def __init__(self, settings: DeliverySettings):
        self.settings = settings

    def cleanup(self, job: DeliveryJob) -> None:
        allow_delete = self.settings.delete_local_artifact_on_success or (
            self.settings.delete_local_snapshot_on_success and job.kind == "snapshot"
        )
        if not allow_delete:
            return
        try:
            if job.local_file.exists():
                job.local_file.unlink()
        except Exception:
            pass


class DeliveryTransportRegistry:
    def __init__(self, transports: dict[str, DeliveryTransport]):
        self.transports = transports

    def for_job(self, job: DeliveryJob) -> DeliveryTransport:
        transport = self.transports.get(job.destination_type)
        if not transport:
            raise RuntimeError(f"unsupported destination type: {job.destination_type}")
        return transport


class DeliveryWorkerService:
    def __init__(
        self,
        settings: DeliverySettings,
        repository: DeliveryRepository,
        transport_registry: DeliveryTransportRegistry,
        cleanup: LocalArtifactCleanup,
    ):
        self.settings = settings
        self.repository = repository
        self.transport_registry = transport_registry
        self.cleanup = cleanup

    def process_one(self) -> bool:
        job = self.repository.fetch_next_job()
        if not job:
            return False
        try:
            if not job.destination_enabled:
                raise RuntimeError("destination disabled")
            result = self.transport_registry.for_job(job).send(job)
        except Exception as ex:
            self.repository.mark_failure(job, None, str(ex))
            return True
        if result.ok:
            self.cleanup.cleanup(job)
            self.repository.mark_success(job, result.status_code)
        else:
            self.repository.mark_failure(job, result.status_code, result.error)
        return True

    def run_forever(self) -> None:
        while True:
            worked = self.process_one()
            if not worked:
                time.sleep(self.settings.poll_sec)

