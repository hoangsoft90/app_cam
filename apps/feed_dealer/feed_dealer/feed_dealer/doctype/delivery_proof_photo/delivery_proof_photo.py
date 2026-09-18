"""Delivery Proof Photo (child table) - one row per proof photo.

No logic: the parent `Delivery Confirmation` owns the validation and the files
are attached by `feed_dealer.api.confirm_delivery`. Deliberately dependency-free
(no app-internal imports) - see the warning in `batch_debt.py`: an ImportError
inside a DocType controller makes frappe treat the DocType as orphaned code and
delete its metadata.
"""

from frappe.model.document import Document


class DeliveryProofPhoto(Document):
	pass
