from .config import DeliverySettings
from .repository import DeliveryRepository
from .service import DeliveryTransportRegistry, DeliveryWorkerService, LocalArtifactCleanup
from .transports import HttpsDeliveryTransport, SftpDeliveryTransport, TransferNaming


def build_delivery_worker() -> DeliveryWorkerService:
    settings = DeliverySettings()
    naming = TransferNaming(settings)
    repository = DeliveryRepository(settings)
    transport_registry = DeliveryTransportRegistry(
        {
            "https_post": HttpsDeliveryTransport(settings, naming),
            "sftp": SftpDeliveryTransport(naming),
        }
    )
    cleanup = LocalArtifactCleanup(settings)
    return DeliveryWorkerService(settings, repository, transport_registry, cleanup)
