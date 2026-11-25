#!/usr/bin/env python3
"""Mailbox OTP adapters for Outlook/Hotmail (Graph / IMAP XOAUTH2) and URL APIs.

Exposes latest_otp_for(address, after_ts, timeout) -> str | None via outlook_mail.py.
"""


class OutlookGraphOtp:
    """OTP polling via outlook_mail (Microsoft Graph, IMAP XOAUTH2, or URL OTP)."""

    def latest_otp_for(self, address, after_ts, timeout=180):
        import os
        import sys
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        import outlook_mail as om
        return om.latest_otp(address, after_ts, timeout)
