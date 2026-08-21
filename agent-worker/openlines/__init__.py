"""Open Lines — the DJ puts a subject to the audience and knows what it asked
when somebody turns up.

The seam, in one place:

    director  decides WHEN anything happens (web container only)
    premise   decides WHAT the subject is — operator's list, or invented
    air       puts it on the broadcast, and keeps the words that aired
    state     the one record, on disk, shared by both containers
    prompt    the additive block both doors read; "" when no line is open

Nothing in here reconfigures the station. The invitation goes out through
`/dj/say`, which is an action, and the premise lives on our side of the wire —
so pointing Talk Wave at another SUB/WAVE re-homes this with everything else.
"""

from openlines.prompt import block, voicemail_clause
from openlines.state import current, read_raw

__all__ = ["block", "voicemail_clause", "current", "read_raw"]
