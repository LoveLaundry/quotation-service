"""Envelope encryption behaviour.

Pins the rules the quotation service relies on and shares with the sibling
services (bill_service / user-service / worker-service), which read each
other's documents:

  * derived search tokens never leave the service,
  * an encrypted field whose name ends in ``_search`` is still decrypted,
  * an envelope this build cannot read fails loudly instead of being guessed,
  * decryption errors never leak ciphertext or plaintext.
"""
import os

os.environ.setdefault("MASTER_KEY", "test-only-master-key-0123456789abcdef")
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-0123456789abcdef")
os.environ.setdefault("DATABASE_URL", "mongodb://127.0.0.1:27017")

import copy

import pytest

from quotation_service.crypto_helper import (
    ENVELOPE_ALGORITHM,
    ENVELOPE_KEY_ID,
    ENVELOPE_VERSION,
    decrypt_dict,
    encrypt_dict,
)

SENSITIVE = ["client_name", "line_items"]


def _doc(**extra):
    base = {"client_name": "Ada Lovelace", "line_items": [{"item_name": "Coat"}]}
    base.update(extra)
    return base


def test_round_trip_decrypts_sensitive_fields():
    encrypted = encrypt_dict(_doc(), SENSITIVE)
    decrypted = decrypt_dict(encrypted, SENSITIVE)
    assert decrypted["client_name"] == "Ada Lovelace"
    assert decrypted["line_items"] == [{"item_name": "Coat"}]
    assert "encryption_metadata" not in decrypted


def test_sensitive_values_are_not_stored_in_plaintext():
    encrypted = encrypt_dict(_doc(), SENSITIVE)
    assert "Ada Lovelace" not in str(encrypted)
    assert isinstance(encrypted["client_name"], dict)
    assert "ciphertext" in encrypted["client_name"]


def test_search_token_is_stored_but_never_returned():
    encrypted = encrypt_dict(_doc(), SENSITIVE)
    assert "client_name_search" in encrypted
    assert isinstance(encrypted["client_name_search"], str)

    decrypted = decrypt_dict(encrypted, SENSITIVE)
    assert "client_name_search" not in decrypted


def test_encrypted_field_named_like_a_search_token_is_decrypted():
    """A sensitive field called e.g. `code_search` holds real data."""
    sensitive = ["client_name", "code_search"]
    encrypted = encrypt_dict(_doc(code_search="ABC-123"), sensitive)
    assert isinstance(encrypted["code_search"], dict)

    decrypted = decrypt_dict(encrypted, sensitive)
    assert decrypted["code_search"] == "ABC-123"


def test_plain_field_named_like_a_search_token_is_stripped():
    """A derived value must not be handed back to a caller."""
    encrypted = encrypt_dict(_doc(worker_name_search="0123456789abcdef" * 4), SENSITIVE)
    decrypted = decrypt_dict(encrypted, SENSITIVE)
    assert "worker_name_search" not in decrypted


def test_metadata_is_written_with_the_expected_scheme():
    encrypted = encrypt_dict(_doc(), SENSITIVE)
    meta = encrypted["encryption_metadata"]
    assert meta["version"] == ENVELOPE_VERSION
    assert meta["algorithm"] == ENVELOPE_ALGORITHM
    assert meta["keyId"] == ENVELOPE_KEY_ID
    assert "wrappedDek" in meta


def test_unsupported_envelope_version_is_rejected():
    encrypted = encrypt_dict(_doc(), SENSITIVE)
    tampered = copy.deepcopy(encrypted)
    tampered["encryption_metadata"]["version"] = 99
    with pytest.raises(ValueError, match="version"):
        decrypt_dict(tampered, SENSITIVE)


def test_unsupported_algorithm_is_rejected():
    encrypted = encrypt_dict(_doc(), SENSITIVE)
    tampered = copy.deepcopy(encrypted)
    tampered["encryption_metadata"]["algorithm"] = "AES-128-CBC"
    with pytest.raises(ValueError, match="algorithm"):
        decrypt_dict(tampered, SENSITIVE)


def test_missing_wrapped_key_is_rejected():
    encrypted = encrypt_dict(_doc(), SENSITIVE)
    tampered = copy.deepcopy(encrypted)
    tampered["encryption_metadata"].pop("wrappedDek")
    with pytest.raises(ValueError, match="wrapped data key"):
        decrypt_dict(tampered, SENSITIVE)


def test_document_without_metadata_is_rejected():
    with pytest.raises(ValueError, match="Encryption metadata missing"):
        decrypt_dict({"client_name": "plaintext"}, SENSITIVE)


def test_decryption_error_does_not_leak_the_ciphertext():
    encrypted = encrypt_dict(_doc(), SENSITIVE)
    tampered = copy.deepcopy(encrypted)
    tampered["client_name"]["ciphertext"] = "00" * 64

    with pytest.raises(ValueError) as excinfo:
        decrypt_dict(tampered, SENSITIVE)
    message = str(excinfo.value)
    assert "Ada Lovelace" not in message
    assert "00" * 64 not in message
    # The exception *type* is kept for diagnosis, the payload is not.
    assert "InvalidTag" in message
