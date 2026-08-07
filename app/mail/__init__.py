"""Outbound email: transport, templates, and the four message kinds.

`send` is the only thing the rest of the app should import.
"""
from app.mail.sender import SendResult, send

__all__ = ["send", "SendResult"]
