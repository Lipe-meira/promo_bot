"""Dependency-free boundary implemented by every store provider."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, TypeVar

ReferenceT = TypeVar("ReferenceT", contravariant=True)
OfferT = TypeVar("OfferT", covariant=True)


class ProviderError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        manual_review: bool = False,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.manual_review = manual_review


class StoreProvider(Protocol[ReferenceT, OfferT]):
    async def enrich(
        self,
        reference: ReferenceT,
        *,
        sub_ids: Sequence[str] = (),
    ) -> OfferT: ...
