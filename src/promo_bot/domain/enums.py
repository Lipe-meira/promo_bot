"""Explicit states used across the foundation."""

from enum import StrEnum


class Store(StrEnum):
    MERCADOLIVRE = "mercadolivre"
    AMAZON = "amazon"
    SHOPEE = "shopee"
    ALIEXPRESS = "aliexpress"
    KABUM = "kabum"


class DealState(StrEnum):
    DISCOVERED = "DISCOVERED"
    READY = "READY"
    STALE = "STALE"
    DISCARDED = "DISCARDED"
    EXPIRED = "EXPIRED"
    ERROR = "ERROR"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class AffiliateCandidateState(StrEnum):
    PENDING_AFFILIATE = "PENDING_AFFILIATE"
    VALIDATING = "VALIDATING"
    AWAITING_AFFILIATE_GENERATION = "AWAITING_AFFILIATE_GENERATION"
    GENERATING_AFFILIATE = "GENERATING_AFFILIATE"
    AFFILIATE_GENERATED = "AFFILIATE_GENERATED"
    ENRICHED = "ENRICHED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_PERMANENT = "FAILED_PERMANENT"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class DeliveryState(StrEnum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_PERMANENT = "FAILED_PERMANENT"
    DELIVERY_AMBIGUOUS = "DELIVERY_AMBIGUOUS"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class DeliveryPurpose(StrEnum):
    """Why a Telegram delivery exists; transport is not public disclosure."""

    INTERNAL_REVIEW = "INTERNAL_REVIEW"
    EXTERNAL_DISCLOSURE = "EXTERNAL_DISCLOSURE"


class ReviewState(StrEnum):
    """Human review lifecycle, kept independent from Telegram transport state."""

    AWAITING_INTERNAL_REVIEW = "AWAITING_INTERNAL_REVIEW"
    MANUALLY_APPROVED = "MANUALLY_APPROVED"
    REJECTED = "REJECTED"
    EXTERNAL_DISCLOSURE_PENDING = "EXTERNAL_DISCLOSURE_PENDING"
    EXTERNALLY_DISCLOSED = "EXTERNALLY_DISCLOSED"


class CouponStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    DECLARED = "DECLARED"
    VERIFIED = "VERIFIED"
    PERSONALIZED = "PERSONALIZED"
    APP_ONLY = "APP_ONLY"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class DiscoveryOrigin(StrEnum):
    RELAY = "relay"
    INDEPENDENTLY_DISCOVERED = "independently_discovered"
    MANUAL = "manual"


class ConfidenceLevel(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class PaymentMethod(StrEnum):
    PIX = "PIX"
    BOLETO = "BOLETO"
    CARD = "CARD"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class CapabilityStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    DISABLED = "DISABLED"
    PENDING = "PENDING"
    ERROR = "ERROR"


class SourceMessageState(StrEnum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_PERMANENT = "FAILED_PERMANENT"


class LinkSource(StrEnum):
    TEXT = "TEXT"
    ENTITY_URL = "ENTITY_URL"
    ENTITY_TEXT_URL = "ENTITY_TEXT_URL"
    BUTTON = "BUTTON"


class RelayLinkState(StrEnum):
    RECEIVED = "RECEIVED"
    PENDING_AFFILIATE = "PENDING_AFFILIATE"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    IGNORED = "IGNORED"
    REJECTED = "REJECTED"
