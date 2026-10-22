import os
import json
import hashlib
import hmac
import logging
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from dotenv import load_dotenv

# The .env file must be visible to this module no matter which module the
# process imports first. `config.py` also calls load_dotenv(), but this module
# is imported directly by routers (and by the sibling services' test suites),
# and without the .env the KEK would be derived from an empty master key —
# silently producing data nobody can decrypt.
load_dotenv()

logger = logging.getLogger(__name__)

_master_key = os.getenv("MASTER_KEY")
if not _master_key:
    raise RuntimeError("FATAL: MASTER_KEY environment variable is not set. Refusing to start.")
MASTER_KEY_ENV = _master_key

# Derive KEK and HMAC Key
KEK = hashlib.sha256((MASTER_KEY_ENV + "-kek").encode()).digest()
HMAC_KEY = hashlib.sha256((MASTER_KEY_ENV + "-hmac").encode()).digest()

# Envelope format this module understands. Anything else must fail loudly
# instead of being decrypted with the wrong scheme.
ENVELOPE_VERSION = 1
ENVELOPE_ALGORITHM = "AES-256-GCM"
ENVELOPE_KEY_ID = "master-key-v1"

# Fields for which a deterministic search token is stored alongside the
# ciphertext so exact-match lookups stay possible without decrypting.
# Keep in sync with the sibling services (bill_service / user-service /
# worker-service) — they read each other's documents.
SEARCHABLE_FIELDS = ("client_name", "email", "mobile_number")
_SEARCH_TOKEN_KEYS = frozenset(f"{field}_search" for field in SEARCHABLE_FIELDS)


def get_search_token(value: str) -> str:
    """Generate a deterministic, secure search token for exact match queries."""
    if not value:
        return ""
    normalized = value.strip().lower()
    return hmac.new(HMAC_KEY, normalized.encode(), hashlib.sha256).hexdigest()


def encrypt_field(plaintext: str, dek: bytes) -> dict:
    """Encrypt a single string field using AES-256-GCM with a DEK."""
    aesgcm = AESGCM(dek)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return {"ciphertext": ciphertext.hex(), "nonce": nonce.hex()}


def decrypt_field(encrypted_data: dict, dek: bytes) -> str:
    """Decrypt a single string field using AES-256-GCM with a DEK.

    The raised message deliberately contains only the exception *type*: the
    default ``str(exc)`` of the underlying codecs can embed fragments of the
    ciphertext or of the decrypted plaintext, which would leak customer data
    into logs and API responses.
    """
    try:
        aesgcm = AESGCM(dek)
        nonce = bytes.fromhex(encrypted_data["nonce"])
        ciphertext = bytes.fromhex(encrypted_data["ciphertext"])
        decrypted_bytes = aesgcm.decrypt(nonce, ciphertext, None)
        return decrypted_bytes.decode("utf-8")
    except Exception as e:
        raise ValueError(f"Decryption failed ({type(e).__name__})") from e


def encrypt_dict(data: dict, sensitive_fields: list) -> dict:
    """
    Perform envelope encryption on a flat/nested dict.
    Leaves non-sensitive fields intact, encrypts sensitive fields.
    Wraps a generated DEK using KEK and appends encryption metadata.
    """
    # 1. Generate DEK
    dek = AESGCM.generate_key(bit_length=256)

    # 2. Wrap DEK using KEK
    aesgcm_kek = AESGCM(KEK)
    dek_nonce = os.urandom(12)
    wrapped_dek_bytes = aesgcm_kek.encrypt(dek_nonce, dek, None)
    wrapped_dek = {"ciphertext": wrapped_dek_bytes.hex(), "nonce": dek_nonce.hex()}

    # 3. Encrypt sensitive fields
    encrypted_data = {}
    for key, val in data.items():
        if key in sensitive_fields and val is not None:
            # Serialize to json string if it's not a string (e.g. lists, dicts)
            if not isinstance(val, str):
                val_str = json.dumps(val)
                is_json = True
            else:
                val_str = val
                is_json = False

            enc_field = encrypt_field(val_str, dek)
            enc_field["is_json"] = is_json
            encrypted_data[key] = enc_field

            # Handle searchable fields
            if key in SEARCHABLE_FIELDS:
                encrypted_data[f"{key}_search"] = get_search_token(val_str)
        else:
            encrypted_data[key] = val

    # 4. Add metadata
    encrypted_data["encryption_metadata"] = {
        "version": ENVELOPE_VERSION,
        "algorithm": ENVELOPE_ALGORITHM,
        "keyId": ENVELOPE_KEY_ID,
        "wrappedDek": wrapped_dek,
    }
    return encrypted_data


def _validate_metadata(meta: dict) -> None:
    """Reject envelopes this build cannot read, instead of guessing."""
    if not isinstance(meta, dict):
        raise ValueError("Encryption metadata is malformed")

    version = meta.get("version")
    if version is not None and int(version) != ENVELOPE_VERSION:
        raise ValueError(f"Unsupported encryption envelope version: {version!r}")

    algorithm = meta.get("algorithm")
    if algorithm is not None and str(algorithm).strip().upper() != ENVELOPE_ALGORITHM:
        raise ValueError(f"Unsupported encryption algorithm: {algorithm!r}")

    key_id = meta.get("keyId")
    if key_id is not None and key_id != ENVELOPE_KEY_ID:
        # Not fatal — the KEK is derived from MASTER_KEY, not from the key id —
        # but it means the document was written by a different key generation.
        logger.warning("Document was encrypted with key id %r, this build uses %r", key_id, ENVELOPE_KEY_ID)

    if not isinstance(meta.get("wrappedDek"), dict):
        raise ValueError("Encryption metadata is missing the wrapped data key")


def decrypt_dict(encrypted_data: dict, sensitive_fields: list) -> dict:
    """
    Decrypt envelope-encrypted dict.
    """
    if not encrypted_data:
        return encrypted_data

    if "encryption_metadata" not in encrypted_data:
        # If no metadata, fail/reject
        raise ValueError("Encryption metadata missing: document is not secure.")

    meta = encrypted_data["encryption_metadata"]
    _validate_metadata(meta)
    wrapped_dek = meta["wrappedDek"]

    # 1. Unwrap DEK using KEK
    try:
        aesgcm_kek = AESGCM(KEK)
        dek_nonce = bytes.fromhex(wrapped_dek["nonce"])
        wrapped_dek_bytes = bytes.fromhex(wrapped_dek["ciphertext"])
        dek = aesgcm_kek.decrypt(dek_nonce, wrapped_dek_bytes, None)
    except Exception as e:
        raise ValueError(f"Failed to unwrap DEK ({type(e).__name__})") from e

    # 2. Decrypt sensitive fields
    decrypted_data = {}
    for key, val in encrypted_data.items():
        if key == "encryption_metadata":
            continue
        if (
            key in sensitive_fields
            and isinstance(val, dict)
            and "ciphertext" in val
            and "nonce" in val
        ):
            # Checked before the search-token rule below: a sensitive field
            # whose name happens to end in "_search" holds real (encrypted)
            # data and must be decrypted, not silently dropped.
            dec_val_str = decrypt_field(val, dek)
            if val.get("is_json"):
                decrypted_data[key] = json.loads(dec_val_str)
            else:
                decrypted_data[key] = dec_val_str
        elif key in _SEARCH_TOKEN_KEYS or key.endswith("_search"):
            # Derived search tokens never leave the service: they exist so the
            # database can match an exact value without decrypting.
            continue
        else:
            decrypted_data[key] = val

    return decrypted_data
