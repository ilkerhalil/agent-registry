import pytest

from agent_registry.signing import SignatureError, sign, verify

KEY = "test-key"


def test_sign_verify_roundtrip():
    payload = '{"agent":"x"}'
    sig = sign(payload, KEY)
    assert sig.startswith("hmac-sha256:")
    verify(payload, sig, KEY)


def test_tampered_payload_rejected():
    sig = sign('{"agent":"x"}', KEY)
    with pytest.raises(SignatureError):
        verify('{"agent":"y"}', sig, KEY)


def test_wrong_key_rejected():
    sig = sign("payload", KEY)
    with pytest.raises(SignatureError):
        verify("payload", sig, "other-key")


def test_empty_key_rejected():
    with pytest.raises(SignatureError):
        sign("payload", "")


def test_malformed_signature_rejected():
    with pytest.raises(SignatureError):
        verify("payload", "deadbeef", KEY)
