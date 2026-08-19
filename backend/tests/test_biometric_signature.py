"""Prove our signature check matches the Biometric Verify server's `_sign`.

We reconstruct a signed payload exactly as face_service/v1.py::_sign does, then
assert verify_signature accepts it and rejects any tamper.
"""
import hashlib
import hmac
import json

from app import biometric

SECRET = "test-signing-secret"
KEYS = ("success", "match", "user_id", "score", "best_score")


def _sign(payload: dict, ts: str = "1700000000", nonce: str = "abcd1234") -> dict:
    body = json.dumps({k: payload.get(k) for k in KEYS}, sort_keys=True, separators=(",", ":"))
    digest = hmac.new(SECRET.encode(), f"{ts}.{nonce}.{body}".encode(), hashlib.sha256).hexdigest()
    return {**payload, "signature": {"alg": "HMAC-SHA256", "ts": ts, "nonce": nonce, "hmac": digest}}


def test_valid_signature_accepted():
    payload = _sign({"success": True, "user_id": "20512345", "score": 0.71})
    assert biometric.verify_signature(payload, SECRET) is True


def test_tampered_user_id_rejected():
    payload = _sign({"success": True, "user_id": "20512345", "score": 0.71})
    payload["user_id"] = "99999999"  # attacker swaps identity after signing
    assert biometric.verify_signature(payload, SECRET) is False


def test_tampered_success_rejected():
    payload = _sign({"success": False, "user_id": "20512345", "score": 0.10})
    payload["success"] = True  # attacker flips deny -> grant
    assert biometric.verify_signature(payload, SECRET) is False


def test_missing_signature_rejected():
    assert biometric.verify_signature({"success": True, "user_id": "x"}, SECRET) is False


def test_wrong_secret_rejected():
    payload = _sign({"success": True, "user_id": "20512345", "score": 0.71})
    assert biometric.verify_signature(payload, "wrong-secret") is False
