from __future__ import annotations

import pytest

from promo_bot.domain import (
    AffiliateCandidateState,
    DealState,
    DeliveryPurpose,
    DeliveryState,
    ReviewState,
    SourceMessageState,
    StateTransitionError,
    ensure_affiliate_candidate_transition,
    ensure_deal_transition,
    ensure_delivery_transition,
    ensure_review_transition,
    ensure_source_message_transition,
)


def test_source_message_completion_is_independent_and_terminal() -> None:
    ensure_source_message_transition(SourceMessageState.RECEIVED, SourceMessageState.PROCESSING)
    ensure_source_message_transition(SourceMessageState.PROCESSING, SourceMessageState.COMPLETED)

    with pytest.raises(StateTransitionError):
        ensure_source_message_transition(
            SourceMessageState.COMPLETED, SourceMessageState.FAILED_RETRYABLE
        )


def test_affiliate_candidate_controls_provider_recovery() -> None:
    ensure_affiliate_candidate_transition(
        AffiliateCandidateState.PENDING_AFFILIATE,
        AffiliateCandidateState.VALIDATING,
    )
    ensure_affiliate_candidate_transition(
        AffiliateCandidateState.VALIDATING,
        AffiliateCandidateState.AWAITING_AFFILIATE_GENERATION,
    )
    ensure_affiliate_candidate_transition(
        AffiliateCandidateState.AWAITING_AFFILIATE_GENERATION,
        AffiliateCandidateState.GENERATING_AFFILIATE,
    )
    ensure_affiliate_candidate_transition(
        AffiliateCandidateState.GENERATING_AFFILIATE,
        AffiliateCandidateState.FAILED_RETRYABLE,
    )
    ensure_affiliate_candidate_transition(
        AffiliateCandidateState.FAILED_RETRYABLE,
        AffiliateCandidateState.VALIDATING,
    )


def test_deal_state_does_not_contain_delivery_or_affiliate_states() -> None:
    assert "SENT" not in DealState
    assert "PENDING_AFFILIATE" not in DealState
    ensure_deal_transition(DealState.DISCOVERED, DealState.READY)


def test_delivery_ambiguous_requires_manual_review() -> None:
    ensure_delivery_transition(DeliveryState.SENDING, DeliveryState.DELIVERY_AMBIGUOUS)
    ensure_delivery_transition(
        DeliveryState.DELIVERY_AMBIGUOUS,
        DeliveryState.MANUAL_REVIEW,
    )

    with pytest.raises(StateTransitionError):
        ensure_delivery_transition(
            DeliveryState.DELIVERY_AMBIGUOUS,
            DeliveryState.SENDING,
        )


def test_delivery_sent_is_terminal_for_automation() -> None:
    with pytest.raises(StateTransitionError):
        ensure_delivery_transition(DeliveryState.SENT, DeliveryState.SENDING)


def test_internal_delivery_and_public_disclosure_are_independent() -> None:
    assert DeliveryPurpose.INTERNAL_REVIEW != DeliveryPurpose.EXTERNAL_DISCLOSURE
    ensure_review_transition(
        ReviewState.AWAITING_INTERNAL_REVIEW,
        ReviewState.MANUALLY_APPROVED,
    )
    ensure_review_transition(
        ReviewState.MANUALLY_APPROVED,
        ReviewState.EXTERNAL_DISCLOSURE_PENDING,
    )
    with pytest.raises(StateTransitionError):
        ensure_review_transition(
            ReviewState.AWAITING_INTERNAL_REVIEW,
            ReviewState.EXTERNALLY_DISCLOSED,
        )
