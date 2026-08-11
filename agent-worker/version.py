"""The one place the build number lives.

The worker and the token server ship as the same image but run as separate
containers, so a redeploy that recreates one and not the other leaves them
skewed. That has happened, and it was invisible: only the token server ever
reported a version. Both now read this, so there is one number to bump and
both say the same thing.

Keep in step with the git tag (v0.9.0 -> "0.9.0") when cutting a release.
"""

APP_VERSION = "0.10.52"
