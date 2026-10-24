"""Transient 4xx SMTP AUTH replies must stay retryable (#144106).

``smtplib`` raises ``SMTPAuthenticationError`` for every AUTH reply other than
235/503 (RFC 4954 §6), including transient 4xx such as Postfix's
"450 4.7.1 too many AUTH commands". Classifying those as permanent dropped the
adapter out of the reconnect queue until restart; only 5xx replies are
permanent auth failures.
"""

import os
import smtplib
import unittest
from unittest.mock import MagicMock, patch


def _make_adapter(address="hermes@test.com"):
    from gateway.config import PlatformConfig

    with patch.dict(os.environ, {
        "EMAIL_ADDRESS": address,
        "EMAIL_PASSWORD": "secret",
        "EMAIL_IMAP_HOST": "imap.test.com",
        "EMAIL_SMTP_HOST": "smtp.test.com",
    }):
        from plugins.platforms.email.adapter import EmailAdapter

        adapter = EmailAdapter(PlatformConfig(enabled=True))
    return adapter


class TestSmtpAuthReplyClassification(unittest.TestCase):
    """_probe_smtp maps the AUTH reply code onto retryable (4xx transient, 5xx permanent)."""

    def _probe_with_auth_error(self, smtp_code, message):
        adapter = _make_adapter()
        smtp = MagicMock()
        smtp.login.side_effect = smtplib.SMTPAuthenticationError(smtp_code, message)
        with patch.object(adapter, "_connect_smtp", return_value=smtp):
            ok = adapter._probe_smtp()
        return ok, adapter

    def test_permanent_5xx_auth_failure_is_not_retryable(self):
        ok, adapter = self._probe_with_auth_error(535, b"5.7.8 Bad credentials")
        self.assertFalse(ok)
        self.assertFalse(adapter.fatal_error_retryable)
        self.assertEqual(adapter.fatal_error_code, "email_auth_error")

    def test_transient_450_auth_rate_limit_stays_retryable(self):
        ok, adapter = self._probe_with_auth_error(450, b"4.7.1 too many AUTH commands")
        self.assertFalse(ok)
        self.assertTrue(adapter.fatal_error_retryable)
        self.assertEqual(adapter.fatal_error_code, "email_auth_error")

    def test_transient_454_temporary_auth_failure_stays_retryable(self):
        ok, adapter = self._probe_with_auth_error(454, b"4.7.0 Temporary authentication failure")
        self.assertFalse(ok)
        self.assertTrue(adapter.fatal_error_retryable)

    def test_transient_421_service_unavailable_stays_retryable(self):
        ok, adapter = self._probe_with_auth_error(421, b"4.7.0 Service not available, closing channel")
        self.assertFalse(ok)
        self.assertTrue(adapter.fatal_error_retryable)


if __name__ == "__main__":
    unittest.main()
