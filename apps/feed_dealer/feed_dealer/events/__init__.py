"""Document event handlers wired from hooks.py.

One module per source DocType so the Phase 1 diff for each hook lands in an
obvious file. P0 registers every hook with a stub body: the point of P0 is that
the *event surface* is final, not that the logic exists.

Each stub returns a small result dict instead of bare `None` so the acceptance
test can prove the handler actually ran (a silent no-op would be
indistinguishable from a missing registration).
"""
