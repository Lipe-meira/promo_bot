import logging

from promo_bot.observability.shadow import mute_shadow_payload_logs


def test_overlapping_scopes_keep_payloads_muted_until_last_exit(caplog):
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger("httpx")
    original = logger.level
    first, second = mute_shadow_payload_logs(), mute_shadow_payload_logs()
    first.__enter__()
    second.__enter__()
    first.__exit__(None, None, None)
    try:
        logger.warning("private payload")
        assert "private payload" not in caplog.text
    finally:
        second.__exit__(None, None, None)
    assert logger.level == original
