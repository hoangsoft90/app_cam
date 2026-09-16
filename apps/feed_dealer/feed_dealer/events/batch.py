"""Feed Batch -> derived debt refresh.

Registered on `Feed Batch` (the lứa nuôi), never on ERPNext's own `Batch`
stock DocType.

P0: stub only. `FeedBatch.recalculate_debt()` already runs in the controller on
validate; this hook exists so Phase 1 can refresh the batch's debt when the
allocation layer changes outside the batch's own save (for example a Payment
Allocation landing on one of its debts).
"""

import frappe


def on_update(doc, method=None):
	"""Placeholder: refresh derived debt after an allocation-layer change."""
	frappe.logger("feed_dealer").info("feed_dealer.events.batch.on_update stub for %s", doc.name)
	return {"stub": True, "hook": "feed_batch.on_update", "feed_batch": doc.name}
