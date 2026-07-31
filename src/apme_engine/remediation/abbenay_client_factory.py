"""Factory for Abbenay gRPC clients with optional TLS (issue #400).

Older ``abbenay-client`` wheels lack ``tls`` / ``ca_cert`` constructor kwargs.
When TLS is required, a thin subclass overrides ``connect()`` to use
``grpc.aio.secure_channel`` until the dependency ships native TLS support.
"""

from __future__ import annotations

import inspect
import logging
import os
from dataclasses import dataclass

import grpc

logger = logging.getLogger(__name__)

_DEFAULT_CA_CERT = "/tmp/abbenay-run/abbenay/tls/ca.crt"
_DEFAULT_SSL_TARGET_NAME = "abbenay-grpc"


@dataclass(frozen=True)
class AbbenayTlsConfig:
    """TLS settings for TCP connections to Abbenay.

    Attributes:
        enabled: Whether to use TLS for TCP connections.
        ca_cert: Path to CA PEM, if any.
        ssl_target_name: TLS server name override for auto-generated certs.
    """

    enabled: bool
    ca_cert: str | None
    ssl_target_name: str


def resolve_abbenay_tls_config(addr: str) -> AbbenayTlsConfig:
    """Resolve TLS settings from environment and address type.

    Args:
        addr: Abbenay daemon address (``host:port`` or ``unix://``).

    Returns:
        TLS configuration for ``build_abbenay_client``.
    """
    explicit_tls = os.environ.get("APME_ABBENAY_TLS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    ca_cert = os.environ.get("APME_ABBENAY_CA_CERT", "").strip() or None
    ssl_target_name = os.environ.get("APME_ABBENAY_SSL_TARGET_NAME", _DEFAULT_SSL_TARGET_NAME).strip()

    if not explicit_tls and ca_cert is None and _is_tcp_addr(addr) and os.path.isfile(_DEFAULT_CA_CERT):
        ca_cert = _DEFAULT_CA_CERT
        explicit_tls = True

    enabled = explicit_tls or ca_cert is not None
    return AbbenayTlsConfig(enabled=enabled, ca_cert=ca_cert, ssl_target_name=ssl_target_name)


def build_abbenay_client(addr: str) -> object:
    """Create an AbbenayClient for the given daemon address.

    Args:
        addr: Daemon address from ``APME_ABBENAY_ADDR`` or auto-discovery.

    Returns:
        ``AbbenayClient`` instance (not yet connected).
    """
    from abbenay_grpc import AbbenayClient  # noqa: PLC0415

    kwargs = _parse_addr_kwargs(addr)
    tls = resolve_abbenay_tls_config(addr)
    use_tls = tls.enabled and not addr.startswith("unix://")

    if not use_tls:
        return AbbenayClient(**kwargs)

    if "tls" in inspect.signature(AbbenayClient.__init__).parameters:
        return AbbenayClient(
            **kwargs,
            tls=True,
            ca_cert=tls.ca_cert,
            ssl_target_name=tls.ssl_target_name,
        )

    if "socket_path" in kwargs:
        return _TlsAbbenayClient(
            socket_path=str(kwargs["socket_path"]),
            ca_cert=tls.ca_cert,
            ssl_target_name=tls.ssl_target_name,
        )
    return _TlsAbbenayClient(
        host=str(kwargs.get("host", "localhost")),
        port=int(kwargs.get("port", 50051)),
        ca_cert=tls.ca_cert,
        ssl_target_name=tls.ssl_target_name,
    )


def _is_tcp_addr(addr: str) -> bool:
    return not addr.startswith("unix://") and bool(addr)


def _parse_addr_kwargs(addr: str) -> dict[str, str | int]:
    if addr.startswith("unix://"):
        return {"socket_path": addr.removeprefix("unix://")}
    if ":" in addr:
        host, _, port_str = addr.rpartition(":")
        return {"host": host or "localhost", "port": int(port_str)}
    return {"host": addr}


class _TlsAbbenayClient:
    """AbbenayClient shim that connects over TLS for older client wheels."""

    def __init__(
        self,
        *,
        socket_path: str | None = None,
        host: str | None = None,
        port: int = 50051,
        ca_cert: str | None = None,
        ssl_target_name: str = _DEFAULT_SSL_TARGET_NAME,
    ) -> None:
        """Initialize the TLS shim around a delegate AbbenayClient.

        Args:
            socket_path: Unix socket path, if used.
            host: TCP host, if used.
            port: TCP port.
            ca_cert: Path to CA PEM matching the daemon certificate.
            ssl_target_name: TLS server name override.
        """
        from abbenay_grpc import AbbenayClient  # noqa: PLC0415

        self._delegate = AbbenayClient(
            socket_path=socket_path,
            host=host,
            port=port,
        )
        self._target = self._delegate._target  # noqa: SLF001
        self._ca_cert = ca_cert
        self._ssl_target_name = ssl_target_name
        self._channel: grpc.aio.Channel | None = None
        self._stub: object | None = None
        self._client_id: str | None = None

    def __getattr__(self, name: str) -> object:
        return getattr(self._delegate, name)

    async def connect(self) -> None:
        from abbenay_grpc.client import (  # noqa: PLC0415  # noqa: PLC0415
            AbbenayError,
            grpc_service,
            proto,
        )
        from abbenay_grpc.client import (
            ConnectionError as AbbenayConnectionError,
        )

        if grpc_service is None or proto is None:
            raise AbbenayError("gRPC stubs failed to import")

        if self._channel is not None:
            usable = False
            try:
                state = self._channel.get_state(try_to_connect=False)
                usable = state in (
                    grpc.ChannelConnectivity.IDLE,
                    grpc.ChannelConnectivity.READY,
                    grpc.ChannelConnectivity.CONNECTING,
                )
            except Exception:
                logger.debug("Abbenay TLS channel state check failed", exc_info=True)
            if usable and self._stub is not None and self._client_id is not None:
                return
            try:
                await self._channel.close(grace=None)
            except Exception:
                logger.debug("Abbenay TLS channel close failed", exc_info=True)
            self._channel = None
            self._stub = None
            self._client_id = None

        try:
            root_certs = None
            if self._ca_cert:
                with open(self._ca_cert, "rb") as cert_file:
                    root_certs = cert_file.read()
            credentials = grpc.ssl_channel_credentials(root_certificates=root_certs)
            options = [
                ("grpc.ssl_target_name_override", self._ssl_target_name),
                ("grpc.default_authority", self._ssl_target_name),
            ]
            self._channel = grpc.aio.secure_channel(self._target, credentials, options=options)
            stub = grpc_service.AbbenayStub(self._channel)
            response = await stub.Register(
                proto.RegisterRequest(
                    client=proto.ClientInfo(client_type=proto.CLIENT_TYPE_PYTHON),
                    is_spawner=False,
                )
            )
            self._stub = stub
            self._client_id = response.client_id
            self._delegate._channel = self._channel  # noqa: SLF001
            self._delegate._stub = stub  # noqa: SLF001
            self._delegate._client_id = self._client_id  # noqa: SLF001
        except grpc.aio.AioRpcError as exc:
            self._channel = None
            self._stub = None
            self._client_id = None
            raise AbbenayConnectionError(f"Failed to connect to daemon: {exc}") from exc

    async def disconnect(self) -> None:
        await self._delegate.disconnect()

    async def reconnect(self) -> None:
        await self._delegate.reconnect()
