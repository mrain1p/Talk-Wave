"""What the environment told us.

The LiveKit credentials and the port are read by more than one route module,
and they must be read after .env is loaded — so the load happens here, once,
and everything else imports the values rather than re-reading os.environ in
its own order.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

PORT = int(os.environ.get("TOKEN_SERVER_PORT", "8100"))

LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "")
# What the *browser* connects to — not the internal docker hostname.
LIVEKIT_PUBLIC_URL = os.environ.get("LIVEKIT_PUBLIC_URL", "ws://localhost:7880")
