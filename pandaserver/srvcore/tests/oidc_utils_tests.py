# Description: Unit tests for how TokenDecoder.deserialize_token finds the client ID and checks aud
import base64
import unittest
from typing import Any
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from pandaserver.srvcore.oidc_utils import TokenDecoder, get_expected_audiences

CLIENT_ID = "panda-client"
# a client whose auth config has no audience
BARE_CLIENT_ID = "bare-client"
ISSUER = "https://idp.example.org/"
DISCOVERY_URL = "https://idp.example.org/.well-known/openid-configuration"
JWKS_URL = "https://idp.example.org/jwks"
KID = "test-key"
SERVER_AUDIENCE = "https://pandaserver.cern.ch"
OTHER_AUDIENCE = "https://other-service.example.org"
DEFAULT_AUDIENCE = "https://default.example.org"

AUTH_CONFIG = {
    CLIENT_ID: {"oidc_config_url": DISCOVERY_URL, "vo": "atlas", "audience": SERVER_AUDIENCE},
    BARE_CLIENT_ID: {"oidc_config_url": DISCOVERY_URL, "vo": "atlas"},
}

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


class _DecoderTestCase(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(TokenDecoder, "get_data", _fake_get_data)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.decoder = TokenDecoder()
        self.log = mock.Mock()

    def _decode(self, token: str, default_audience: str | None = None) -> dict[str, Any]:
        return self.decoder.deserialize_token(token, AUTH_CONFIG, "atlas", self.log, None, default_audience=default_audience)

    def _warnings(self) -> list[str]:
        return [str(c.args[0]) for c in self.log.warning.call_args_list]


class TestDeserializeTokenClientId(_DecoderTestCase):
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


class TestDeserializeTokenAudienceWarning(_DecoderTestCase):
    def test_id_token_is_not_checked(self) -> None:
        self._decode(_make_token(aud=CLIENT_ID), default_audience=DEFAULT_AUDIENCE)
        self.assertEqual(self._warnings(), [])

    def test_expected_aud_does_not_warn(self) -> None:
        self._decode(_make_token(aud=[OTHER_AUDIENCE, SERVER_AUDIENCE], client_id=CLIENT_ID))
        self.assertEqual(self._warnings(), [])

    def test_other_aud_warns_but_is_accepted(self) -> None:
        decoded = self._decode(_make_token(aud=OTHER_AUDIENCE, client_id=CLIENT_ID))
        self.assertEqual(decoded["extracted_client_id"], CLIENT_ID)
        warnings = self._warnings()
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith("aud mismatch:"), warnings[0])
        self.assertIn(f"client_id={CLIENT_ID} token=user", warnings[0])

    def test_missing_aud_warns_but_is_accepted(self) -> None:
        decoded = self._decode(_make_token(sub=CLIENT_ID))
        self.assertEqual(decoded["extracted_client_id"], CLIENT_ID)
        warnings = self._warnings()
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith("aud missing:"), warnings[0])
        self.assertIn(f"client_id={CLIENT_ID} token=client_credentials", warnings[0])

    def test_iam_client_credentials_token(self) -> None:
        # as INDIGO IAM issues it: client_id claim, sub is the client, aud defaults to the WLCG "any" value
        self._decode(_make_token(aud="https://wlcg.cern.ch/jwt/v1/any", client_id=CLIENT_ID, sub=CLIENT_ID))
        warnings = self._warnings()
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith("aud mismatch:"), warnings[0])
        self.assertIn(f"client_id={CLIENT_ID} token=client_credentials", warnings[0])

    def test_default_audience_when_config_has_none(self) -> None:
        self._decode(_make_token(aud=DEFAULT_AUDIENCE, client_id=BARE_CLIENT_ID), default_audience=DEFAULT_AUDIENCE)
        self.assertEqual(self._warnings(), [])
        self._decode(_make_token(aud=SERVER_AUDIENCE, client_id=BARE_CLIENT_ID), default_audience=DEFAULT_AUDIENCE)
        self.assertEqual(len(self._warnings()), 1)

    def test_nothing_expected_does_not_warn(self) -> None:
        self._decode(_make_token(aud=OTHER_AUDIENCE, client_id=BARE_CLIENT_ID))
        self.assertEqual(self._warnings(), [])


class TestGetExpectedAudiences(unittest.TestCase):
    def test_config_audience_wins(self) -> None:
        self.assertEqual(get_expected_audiences({"audience": SERVER_AUDIENCE}, DEFAULT_AUDIENCE), [SERVER_AUDIENCE])

    def test_space_separated(self) -> None:
        self.assertEqual(get_expected_audiences({"audience": f"{SERVER_AUDIENCE} {OTHER_AUDIENCE}"}, None), [SERVER_AUDIENCE, OTHER_AUDIENCE])

    def test_list(self) -> None:
        self.assertEqual(get_expected_audiences({"audience": [SERVER_AUDIENCE, 1]}, None), [SERVER_AUDIENCE])

    def test_default(self) -> None:
        self.assertEqual(get_expected_audiences({}, DEFAULT_AUDIENCE), [DEFAULT_AUDIENCE])
        self.assertEqual(get_expected_audiences({"audience": ""}, DEFAULT_AUDIENCE), [DEFAULT_AUDIENCE])

    def test_nothing(self) -> None:
        self.assertEqual(get_expected_audiences({}, None), [])


if __name__ == "__main__":
    unittest.main()
