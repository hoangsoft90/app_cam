"""Wire the Feed Dealer icon into the site chrome (one-shot, idempotent).

Run:  bench --site <site> execute feed_dealer.setup.branding.run

Sets Website Settings (Single) so the icon shows outside the apps screen:
  favicon   -> /assets/feed_dealer/images/feed_dealer_favicon.ico  (Desk + website tab icon)
  app_logo  -> /assets/feed_dealer/images/feed_dealer_logo_256.png (navbar / login surfaces)

Idempotent: setting the same value again is a no-op (compares current db value
first), so re-running after every migrate is safe. The Desk apps-screen logo is
hooked separately in hooks.py (add_to_apps_screen) and does NOT live here.

A custom Website Theme that overrides favicon/app_logo still wins — this only
fills the site defaults.
"""

import frappe

ASSETS = "/assets/feed_dealer/images"
FIELDS = {
	"favicon": f"{ASSETS}/feed_dealer_favicon.ico",
	"app_logo": f"{ASSETS}/feed_dealer_logo_256.png",
}


def run():
	changed, unchanged = [], []
	for field, value in FIELDS.items():
		if frappe.db.get_single_value("Website Settings", field) != value:
			frappe.db.set_single_value("Website Settings", field, value)
			changed.append(f"{field} -> {value}")
		else:
			unchanged.append(field)
	frappe.db.commit()

	# clear the cached website settings so the browser sees the new chrome now
	frappe.cache().delete_value("website_settings")

	result = {"changed": changed, "unchanged": unchanged}
	print(f"[feed_dealer branding] changed={changed} unchanged={unchanged}")
	return result
