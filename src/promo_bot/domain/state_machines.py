"""Explicit and independent state transition rules."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from promo_bot.domain.enums import (
    AffiliateCandidateState,
    DealState,
    DeliveryState,
    ReviewState,
    SourceMessageState,
)


class StateTransitionError(ValueError):
    """Raised when a workflow tries to cross responsibility boundaries."""


SOURCE_MESSAGE_TRANSITIONS: Mapping[str, frozenset[str]] = {
    SourceMessageState.RECEIVED.value: frozenset(
        {SourceMessageState.PROCESSING.value, SourceMessageState.FAILED_PERMANENT.value}
    ),
    SourceMessageState.PROCESSING.value: frozenset(
        {
            SourceMessageState.COMPLETED.value,
            SourceMessageState.FAILED_RETRYABLE.value,
            SourceMessageState.FAILED_PERMANENT.value,
        }
    ),
    SourceMessageState.FAILED_RETRYABLE.value: frozenset(
        {SourceMessageState.PROCESSING.value, SourceMessageState.FAILED_PERMANENT.value}
    ),
}

AFFILIATE_CANDIDATE_TRANSITIONS: Mapping[str, frozenset[str]] = {
    AffiliateCandidateState.PENDING_AFFILIATE.value: frozenset(
        {AffiliateCandidateState.VALIDATING.value}
    ),
    AffiliateCandidateState.VALIDATING.value: frozenset(
        {
            AffiliateCandidateState.AWAITING_AFFILIATE_GENERATION.value,
            AffiliateCandidateState.ENRICHED.value,
            AffiliateCandidateState.FAILED_RETRYABLE.value,
            AffiliateCandidateState.FAILED_PERMANENT.value,
            AffiliateCandidateState.MANUAL_REVIEW.value,
        }
    ),
    AffiliateCandidateState.AWAITING_AFFILIATE_GENERATION.value: frozenset(
        {
            AffiliateCandidateState.GENERATING_AFFILIATE.value,
            AffiliateCandidateState.FAILED_PERMANENT.value,
            AffiliateCandidateState.MANUAL_REVIEW.value,
        }
    ),
    AffiliateCandidateState.GENERATING_AFFILIATE.value: frozenset(
        {
            AffiliateCandidateState.ENRICHED.value,
            AffiliateCandidateState.FAILED_RETRYABLE.value,
            AffiliateCandidateState.FAILED_PERMANENT.value,
            AffiliateCandidateState.MANUAL_REVIEW.value,
        }
    ),
    AffiliateCandidateState.FAILED_RETRYABLE.value: frozenset(
        {
            AffiliateCandidateState.VALIDATING.value,
            AffiliateCandidateState.FAILED_PERMANENT.value,
        }
    ),
    AffiliateCandidateState.MANUAL_REVIEW.value: frozenset(
        {AffiliateCandidateState.PENDING_AFFILIATE.value}
    ),
}

REVIEW_TRANSITIONS: Mapping[str, frozenset[str]] = {
    ReviewState.AWAITING_INTERNAL_REVIEW.value: frozenset(
        {ReviewState.MANUALLY_APPROVED.value, ReviewState.REJECTED.value}
    ),
    ReviewState.MANUALLY_APPROVED.value: frozenset(
        {ReviewState.EXTERNAL_DISCLOSURE_PENDING.value, ReviewState.REJECTED.value}
    ),
    ReviewState.EXTERNAL_DISCLOSURE_PENDING.value: frozenset(
        {ReviewState.EXTERNALLY_DISCLOSED.value, ReviewState.REJECTED.value}
    ),
}

DEAL_TRANSITIONS: Mapping[str, frozenset[str]] = {
    DealState.DISCOVERED.value: frozenset(
        {
            DealState.READY.value,
            DealState.DISCARDED.value,
            DealState.ERROR.value,
            DealState.MANUAL_REVIEW.value,
        }
    ),
    DealState.READY.value: frozenset(
        {
            DealState.STALE.value,
            DealState.EXPIRED.value,
            DealState.DISCARDED.value,
            DealState.ERROR.value,
        }
    ),
    DealState.STALE.value: frozenset(
        {
            DealState.READY.value,
            DealState.EXPIRED.value,
            DealState.DISCARDED.value,
            DealState.ERROR.value,
            DealState.MANUAL_REVIEW.value,
        }
    ),
    DealState.ERROR.value: frozenset(
        {
            DealState.DISCOVERED.value,
            DealState.DISCARDED.value,
            DealState.MANUAL_REVIEW.value,
        }
    ),
    DealState.MANUAL_REVIEW.value: frozenset({DealState.READY.value, DealState.DISCARDED.value}),
}

DELIVERY_TRANSITIONS: Mapping[str, frozenset[str]] = {
    DeliveryState.PENDING.value: frozenset(
        {DeliveryState.SENDING.value, DeliveryState.FAILED_PERMANENT.value}
    ),
    DeliveryState.SENDING.value: frozenset(
        {
            DeliveryState.SENT.value,
            DeliveryState.FAILED_RETRYABLE.value,
            DeliveryState.FAILED_PERMANENT.value,
            DeliveryState.DELIVERY_AMBIGUOUS.value,
        }
    ),
    DeliveryState.FAILED_RETRYABLE.value: frozenset(
        {DeliveryState.SENDING.value, DeliveryState.FAILED_PERMANENT.value}
    ),
    DeliveryState.DELIVERY_AMBIGUOUS.value: frozenset({DeliveryState.MANUAL_REVIEW.value}),
    DeliveryState.MANUAL_REVIEW.value: frozenset(
        {DeliveryState.SENT.value, DeliveryState.FAILED_PERMANENT.value}
    ),
}


def _ensure_transition(
    workflow: str,
    current: StrEnum,
    new: StrEnum,
    transitions: Mapping[str, frozenset[str]],
) -> None:
    if new.value not in transitions.get(current.value, frozenset()):
        raise StateTransitionError(f"invalid {workflow} transition: {current.value} -> {new.value}")


def ensure_source_message_transition(current: SourceMessageState, new: SourceMessageState) -> None:
    _ensure_transition("source message", current, new, SOURCE_MESSAGE_TRANSITIONS)


def ensure_affiliate_candidate_transition(
    current: AffiliateCandidateState, new: AffiliateCandidateState
) -> None:
    _ensure_transition("affiliate candidate", current, new, AFFILIATE_CANDIDATE_TRANSITIONS)


def ensure_deal_transition(current: DealState, new: DealState) -> None:
    _ensure_transition("deal", current, new, DEAL_TRANSITIONS)


def ensure_delivery_transition(current: DeliveryState, new: DeliveryState) -> None:
    _ensure_transition("delivery", current, new, DELIVERY_TRANSITIONS)


def ensure_review_transition(current: ReviewState, new: ReviewState) -> None:
    _ensure_transition("review", current, new, REVIEW_TRANSITIONS)
