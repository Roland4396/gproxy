#!/usr/bin/env python3
"""Memory-only secret parity for closed, operator-owned migration snapshots.

Never writes/prints plaintext, token hashes or keys and performs no HTTP calls.
The master key is selected by private env-file path, never a CLI argument.
"""
import base64
import json
import re

from audit_v4_snapshot import closed, equal, row_by_id
from migrate_history_v3 import v3_id


def audit_secret_payloads(source_path, target_path, key_text):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    text = key_text.strip()
    key = bytes.fromhex(text) if re.fullmatch(r"[0-9a-fA-F]{64}", text) else base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if len(key) != 32:
        raise ValueError("master key must decode to 32 bytes")
    master = AESGCM(key)
    source, target = closed(source_path), closed(target_path)
    checked = {}
    try:
        for table, native, domain in [("credentials", "credentials", "credential"), ("user_keys", "api_keys", "user-key")]:
            count = 0
            for old in source.execute(f"SELECT * FROM {table}"):
                new_id = v3_id(table, old["id"])
                new = row_by_id(target, native, new_id)
                if old["ciphertext"] is None:
                    equal(new["secret"], None, f"secret_presence/{new_id}")
                    continue
                try:
                    if not old["wrapped_key"]:
                        before = json.loads(old["ciphertext"])
                    else:
                        old_dek = master.decrypt(old["key_nonce"], old["wrapped_key"], f"gproxy:v3:{domain}-envelope:v1:wrapped-dek".encode())
                        before = json.loads(AESGCM(old_dek).decrypt(old["payload_nonce"], old["ciphertext"], f"gproxy:v3:{domain}-envelope:v1:payload".encode()))
                    sealed = new["secret"]
                    if not sealed or sealed[0] != 1 or len(sealed) < 27:
                        raise ValueError("invalid native envelope")
                    payload_nonce, key_nonce = sealed[1:13], sealed[13:25]
                    if payload_nonce == key_nonce:
                        raise ValueError("nonce reuse")
                    length = int.from_bytes(sealed[25:27], "big")
                    wrapped, ciphertext = sealed[27:27+length], sealed[27+length:]
                    new_dek = master.decrypt(key_nonce, wrapped, ("gproxy:v4:credential:v1:wrapped-dek:" + new_id).encode())
                    after = json.loads(AESGCM(new_dek).decrypt(payload_nonce, ciphertext, ("gproxy:v4:credential:v1:payload:" + new_id).encode()))
                    equal(after, before, "secret_payload/" + new_id)
                except Exception:
                    raise AssertionError("sealed payload parity failed for " + new_id) from None
                count += 1
            checked[table + "_payloads"] = count
        return {"passed": True, **checked, "plaintext_output": False, "network_calls": 0}
    finally:
        source.close()
        target.close()
