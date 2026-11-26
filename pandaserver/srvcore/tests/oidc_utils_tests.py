# Description: Unit tests for how TokenDecoder.deserialize_token finds the client ID
import base64
import unittest
from typing import Any
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from pandaserver.srvcore.oidc_utils import TokenDecoder

CLIENT_ID = "panda-client"
ISSUER = "https://idp.example.org/"
DISCOVERY_URL = "https://idp.example.org/.well-known/openid-configuration"
JWKS_URL = "https://idp.example.org/jwks"
KID = "test-key"
SERVER_AUDIENCE = "https://pandaserver.cern.ch"

AUTH_CONFIG = {CLIENT_ID: {"oidc_config_url": DISCOVERY_URL, "vo": "atlas"}}

PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64_int(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


_PUBLIC_NUMBERS = PRIVATE_KEY.public_key().public_numbers()
JWKS = {"keys": [{"kty": "RSA", "kid": KID, "n": _b64_int(_PUBLIC_NUMBERS.n), "e": _b64_int(_PUBLIC_NUMBERS.e)}]}


def _fake_get_data(self: TokenDecoder, url: str, log_stream: Any) -> Any:
    if url == DISCOVERY_URL:
        return {"issuer": ISSUER, "jwks_uri": JWKS_URL}
    if url == JWKS_URL:
        return JWKS
    raise AssertionError(f"unexpected URL {url}")


def _make_token(**claims: Any) -> str:
    payload = {"iss": ISSUER, "sub": "some-user"}
    payload.update(claims)
    token: str = jwt.encode(payload, PRIVATE_KEY, algorithm="RS256", headers={"kid": KID})
    return token


class TestDeserializeTokenClientId(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(TokenDecoder, "get_data", _fake_get_data)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.decoder = TokenDecoder()
        self.log = mock.Mock()

    def _decode(self, token: str) -> dict[str, Any]:
        return self.decoder.deserialize_token(token, AUTH_CONFIG, "atlas", self.log, None)

    def test_aud_string(self) -> None:
        decoded = self._decode(_make_token(aud=CLIENT_ID))
        self.assertEqual(decoded["extracted_client_id"], CLIENT_ID)

    def test_aud_list_containing_client_id(self) -> None:
        decoded = self._decode(_make_token(aud=[SERVER_AUDIENCE, CLIENT_ID]))
        self.assertEqual(decoded["extracted_client_id"], CLIENT_ID)

    def test_aud_list_falls_back_to_client_id_claim(self) -> None:
        decoded = self._decode(_make_token(aud=[SERVER_AUDIENCE], client_id=CLIENT_ID))
        self.assertEqual(decoded["extracted_client_id"], CLIENT_ID)

    def test_aud_list_falls_back_to_sub(self) -> None:
        decoded = self._decode(_make_token(aud=[SERVER_AUDIENCE], sub=CLIENT_ID))
        self.assertEqual(decoded["extracted_client_id"], CLIENT_ID)

    def test_aud_list_with_non_string_element_is_invalid_token(self) -> None:
        with self.assertRaises(jwt.exceptions.InvalidTokenError):
            self._decode(_make_token(aud=[{"x": 1}, CLIENT_ID]))

    def test_no_aud_uses_client_id_claim(self) -> None:
        decoded = self._decode(_make_token(client_id=CLIENT_ID))
        self.assertEqual(decoded["extracted_client_id"], CLIENT_ID)


if __name__ == "__main__":
    unittest.main()
