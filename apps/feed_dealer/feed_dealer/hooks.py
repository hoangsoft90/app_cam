app_name = "feed_dealer"
app_title = "Cám Việt"
app_publisher = "app_cam"
app_description = "Feed dealer ERP: quản lý lứa nuôi & công nợ theo lứa (Cám Việt)"
app_email = "dev@app-cam.local"
app_license = "mit"

# Apps
# ------------------

required_apps = ["erpnext"]

# Each item in the list will be shown as an app in the apps page
add_to_apps_screen = [
	{
		"name": "feed_dealer",
		"logo": "/assets/feed_dealer/images/feed_dealer_logo_256.png",
		"title": "Cám Việt",
		"route": "/app/feed-batch",
	}
]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/feed_dealer/css/feed_dealer.css"
# app_include_js = "/assets/feed_dealer/js/feed_dealer.js"

# include js, css files in header of web template
# web_include_css = "/assets/feed_dealer/css/feed_dealer.css"
# web_include_js = "/assets/feed_dealer/js/feed_dealer.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "feed_dealer/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "feed_dealer/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "feed_dealer.utils.jinja_methods",
# 	"filters": "feed_dealer.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "feed_dealer.install.before_install"
# after_install = "feed_dealer.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "feed_dealer.uninstall.before_uninstall"
# after_uninstall = "feed_dealer.uninstall.after_uninstall"

# Sync this app's Custom Fields (custom_batch on Sales Invoice Item) on every
# migrate — declarative file, idempotent, never touches fields owned by others.
after_migrate = "feed_dealer.setup.custom_fields.sync"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "feed_dealer.utils.before_app_install"
# after_app_install = "feed_dealer.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "feed_dealer.utils.before_app_uninstall"
# after_app_uninstall = "feed_dealer.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "feed_dealer.notifications.get_notification_config"

# Awesome Bar
# -----------
# Extra search results: list of dicts with label, description, route, index.
# route: ["List", "ToDo"], "/desk/docs/some/page", or "https://example.com"
# awesomebar_search = ["feed_dealer.search.awesomebar_results"]

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events
#
# P0 registered the event surface with no-op stubs; P1A implements the Sales
# Invoice bodies (on_submit creates Batch Debt allocations; before_cancel
# blocks cancelling an invoice whose debts hold payments — this guard MUST run
# on before_cancel because frappe writes docstatus=2 BEFORE on_cancel fires).
# P1B implements the Payment Entry bodies (on_submit slices the received amount
# FIFO across the customer's open debts as submitted Payment Allocations;
# on_cancel cancels those allocations and reopens the debts).
# P1C implements the Sales Order credit gate: `validate` counts the other open
# drafts as committed credit (closes the "N draft orders" bypass) and
# `before_submit` re-checks atomically with a row lock on the Credit Score
# document. Both go through one shared function (feed_dealer.credit_limit) so a
# Farmer/API caller cannot get a weaker rule.
# `Unreconcile Payment` is ERPNext's own DocType: it de-reconciles an invoice
# while the Payment Entry stays submitted, so P1B's reversal never fires - the
# hook below reverses our allocations for it. Feed Batch on_update stays a stub.
#
# `Feed Batch` (ours), NOT `Batch` (ERPNext's stock batch): hooking `Batch`
# would run our debt refresh on stock batches too.

doc_events = {
	"Sales Invoice": {
		"on_submit": "feed_dealer.events.sales_invoice.on_submit",
		"before_cancel": "feed_dealer.events.sales_invoice.before_cancel",
		"on_cancel": "feed_dealer.events.sales_invoice.on_cancel",
	},
	"Payment Entry": {
		"on_submit": "feed_dealer.events.payment_entry.on_submit",
		"on_cancel": "feed_dealer.events.payment_entry.on_cancel",
	},
	"Sales Order": {
		"validate": "feed_dealer.events.sales_order.validate",
		"before_submit": "feed_dealer.events.sales_order.before_submit",
	},
	"Unreconcile Payment": {
		"on_submit": "feed_dealer.events.unreconcile_payment.on_submit",
	},
	"Feed Batch": {
		"on_update": "feed_dealer.events.batch.on_update",
	},
	# P1F: a Journal Entry is how a livestock offset nets a farmer's purchase against
	# their batch debt, so the JE hook is what makes Batch Debt follow that document
	# (recalculate only — never cascade money). `Customer.validate` is the consent
	# gate: a warning, not a block (policy documented in the consent controller).
	"Journal Entry": {
		# `validate` (pre-write) holds the attribution gate; `on_submit` only recalculates.
		"validate": "feed_dealer.events.journal_entry.validate",
		"on_submit": "feed_dealer.events.journal_entry.on_submit",
		"on_cancel": "feed_dealer.events.journal_entry.on_cancel",
	},
	"Customer": {
		"validate": "feed_dealer.events.consent.on_customer_validate",
	},
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"feed_dealer.tasks.all"
# 	],
# 	"daily": [
# 		"feed_dealer.tasks.daily"
# 	],
# 	"hourly": [
# 		"feed_dealer.tasks.hourly"
# 	],
# 	"weekly": [
# 		"feed_dealer.tasks.weekly"
# 	],
# 	"monthly": [
# 		"feed_dealer.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "feed_dealer.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "feed_dealer.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "feed_dealer.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["feed_dealer.utils.before_request"]
# after_request = ["feed_dealer.utils.after_request"]

# Job Events
# ----------
# before_job = ["feed_dealer.utils.before_job"]
# after_job = ["feed_dealer.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"feed_dealer.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

