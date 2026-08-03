import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase

import jwt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import auth  # noqa: E402


class WebUiJwtAuthenticationTest(TestCase):
    secret = "a" * 32
    claims = {
        "workid": "W1001",
        "cnname": "Test User",
        "depart": "IT",
        "username": "test.user",
        "role": "user",
    }

    def setUp(self) -> None:
        self.original = (auth.JWT_SECRET, auth.JWT_ISSUER, auth.JWT_AUDIENCE)
        auth.JWT_SECRET = self.secret
        auth.JWT_ISSUER = "non-gmp-lims"
        auth.JWT_AUDIENCE = "web-ui"

    def tearDown(self) -> None:
        auth.JWT_SECRET, auth.JWT_ISSUER, auth.JWT_AUDIENCE = self.original

    def make_token(self, **overrides: object) -> str:
        payload = {
            **self.claims,
            "iss": auth.JWT_ISSUER,
            "aud": auth.JWT_AUDIENCE,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            **overrides,
        }
        return jwt.encode(payload, self.secret, algorithm="HS256")

    def test_accepts_valid_webui_token(self) -> None:
        user = auth.authenticate_authorization_header(f"Bearer {self.make_token()}")
        self.assertEqual(user.workid, "W1001")
        self.assertEqual(user.username, "test.user")

    def test_rejects_missing_or_tampered_token(self) -> None:
        with self.assertRaises(auth.JwtAuthenticationError):
            auth.authenticate_authorization_header(None)
        token = jwt.encode({**self.claims, "iss": auth.JWT_ISSUER, "aud": auth.JWT_AUDIENCE}, "b" * 32, algorithm="HS256")
        with self.assertRaises(auth.JwtAuthenticationError):
            auth.authenticate_authorization_header(f"Bearer {token}")

    def test_rejects_expired_wrong_audience_and_missing_claim(self) -> None:
        for token in (
            self.make_token(exp=datetime.now(timezone.utc) - timedelta(seconds=1)),
            self.make_token(aud="other-service"),
            self.make_token(role=None),
        ):
            with self.assertRaises(auth.JwtAuthenticationError):
                auth.authenticate_authorization_header(f"Bearer {token}")
