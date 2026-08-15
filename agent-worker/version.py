"""The one place the build number lives.

The worker and the token server ship as the same image but run as separate
containers, so a redeploy that recreates one and not the other leaves them
skewed. That has happened, and it was invisible: only the token server ever
reported a version. Both now read this, so there is one number to bump and
both say the same thing.

Keep in step with the git tag (v0.97.0 -> "0.97.0") when cutting a release.

THE SERIES RESTARTED AT 0.97.0, from 0.10.159 (2026-08-15). The old line had
run to a three-digit patch number and said nothing about how close the thing
was to being finished; the operator's read was "close to a 1.0 but not quite
there". 0.97 says that in the number, and leaves 0.98 and 0.99 in hand before
1.0 is claimed.

It had to go UP, which is why this is not 0.9.7. Everything that compares two
of these — the panel's newer-version flag, the container-skew check — reads
them part by part, so 0.9.7 would have sorted BELOW 0.10.159 and every box
would have reported an update that was actually a downgrade, for ever. 97 > 10
sorts correctly everywhere.
"""

APP_VERSION = "0.97.4"
