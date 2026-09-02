"""Which edition the packaged build is.

``FREE_EDITION = True`` means Your Pit Box is free: no activation code, no
licence-server call, no device binding. The first launch shows a welcome
screen that asks for an AI API key (optional) and then starts the app.

The licensing machinery stays in the tree untouched, so the paid edition can
be rebuilt by flipping this one switch: the launcher, the first-run window and
the installer text all read it. Existing paid installs keep their cached
licence and are unaffected either way.

The integrity self-check (``gate.integrity_ok``) runs in both editions. It
protects the user from a damaged install; it was never about copy protection.
"""

FREE_EDITION = True

__all__ = ["FREE_EDITION"]
