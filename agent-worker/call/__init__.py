"""Everything that happens during one call.

    tools/      what the DJ can do, and the one table describing that surface
    actions     the per-call ledger: what the caller actually made happen
    air         keeping the call DJ and the on-air DJ off each other's toes
    hangup      ending a call, the same way from all three places that do
    background  fire-and-forget tasks that survive long enough to finish

`main.py` wires these together and owns the provider choices; nothing here
imports it.
"""
