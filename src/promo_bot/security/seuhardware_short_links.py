"""One observed 302 hop, opt-in only; never expand the AliExpress short."""

from __future__ import annotations

import asyncio
import ipaddress
import re
from collections.abc import Callable

import httpcore
import httpx

from promo_bot.observability.shadow import mute_shadow_payload_logs
from promo_bot.providers.aliexpress.coin_shadow import validate_coin_short
from promo_bot.security.aliexpress_short_links import PinnedAliExpressHttpTransport
from promo_bot.security.urls import (
    DnsResolver,
    HopRequester,
    HopResponse,
    SafeUrlError,
    SafeUrlExpander,
    SystemDnsResolver,
    TransientUrlError,
    _response_peer_ip,
)

_WRAPPER = re.compile(r"https://seuhardware\.com/s/[A-Za-z0-9]{7}\Z", re.ASCII)
MAX_HEADER_BYTES = 16_384


class SeuHardwareRejected(ValueError):
    """Only a fixed diagnostic code, never an external exception or URL."""


def validate_seuhardware_short(value: str) -> str:
    if not isinstance(value, str) or _WRAPPER.fullmatch(value) is None:
        raise SeuHardwareRejected("SEUHARDWARE_SHORT_INVALID")
    return value


class SeuHardwareHeaderRequester:
    async def fetch(self, url: str, *, method: str, allowed_ips: frozenset[str]) -> HopResponse:
        # Pin one IP only: the existing backend cannot try alternate addresses.
        transport = PinnedAliExpressHttpTransport("seuhardware.com", allowed_ips)
        with mute_shadow_payload_logs():
            async with httpx.AsyncClient(
                transport=transport,
                follow_redirects=False,
                trust_env=False,
                timeout=httpx.Timeout(5.0),
            ) as client:
                async with client.stream(method, url) as response:
                    if sum(len(k) + len(v) + 4 for k, v in response.headers.raw) > MAX_HEADER_BYTES:
                        raise SeuHardwareRejected("SEUHARDWARE_HEADERS_TOO_LARGE")
                    # Deliberately do not read any body, HTML or JavaScript.
                    return HopResponse(
                        response.status_code, dict(response.headers), _response_peer_ip(response)
                    )


class SeuHardwareShortResolver:
    def __init__(
        self,
        *,
        resolver: DnsResolver | None = None,
        requester: HopRequester | None = None,
        before_request: Callable[[], None] | None = None,
    ) -> None:
        self.resolver = resolver or SystemDnsResolver()
        self.requester = requester or SeuHardwareHeaderRequester()
        self.before_request = before_request

    async def resolve(self, original: str) -> str:
        validate_seuhardware_short(original)
        try:
            # Total DNS + connect + TLS + headers deadline. No retry or second hop.
            async with asyncio.timeout(8.0):
                addresses = await self.resolver.resolve("seuhardware.com", 443)
                if not addresses:
                    raise SeuHardwareRejected("SEUHARDWARE_DNS_EMPTY")
                public = set()
                for address in addresses:
                    parsed = ipaddress.ip_address(address)
                    if not parsed.is_global:
                        raise SeuHardwareRejected("SEUHARDWARE_DNS_NON_GLOBAL")
                    public.add(str(parsed))
                if self.before_request is not None:
                    self.before_request()
                selected = frozenset({sorted(public)[0]})
                response = await self.requester.fetch(original, method="GET", allowed_ips=selected)
                SafeUrlExpander._validate_peer(response.peer_ip, selected)
                if sum(len(k) + len(v) + 4 for k, v in response.headers.items()) > MAX_HEADER_BYTES:
                    raise SeuHardwareRejected("SEUHARDWARE_HEADERS_TOO_LARGE")
                if response.status_code != 302:
                    raise SeuHardwareRejected("SEUHARDWARE_STATUS_UNSUPPORTED")
                location = response.headers.get("location")
                if not location:
                    raise SeuHardwareRejected("SEUHARDWARE_LOCATION_MISSING")
                try:
                    return validate_coin_short(location)
                except ValueError:
                    raise SeuHardwareRejected("SEUHARDWARE_LOCATION_INVALID") from None
        except SeuHardwareRejected:
            raise
        except (TimeoutError, httpx.TimeoutException, httpcore.TimeoutException):
            raise SeuHardwareRejected("SEUHARDWARE_TIMEOUT") from None
        except SafeUrlError as exc:
            raise SeuHardwareRejected("SEUHARDWARE_" + exc.code) from None
        except (
            ValueError,
            TransientUrlError,
            httpx.HTTPError,
            httpcore.NetworkError,
            httpcore.ProtocolError,
            OSError,
        ):
            raise SeuHardwareRejected("SEUHARDWARE_RESOLUTION_FAILED") from None
