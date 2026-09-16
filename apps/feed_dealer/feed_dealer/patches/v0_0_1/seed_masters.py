"""Seed baseline masters/roles on migrate.

A patch (not fixtures) because fixtures overwrite operator edits on every
migrate, while this seeder only ever creates what is missing.
"""

from feed_dealer.setup.masters import seed_all


def execute():
	report = seed_all()
	created = sum(len(items) for items in report.values())
	print(f"[feed_dealer] seed_masters patch: {created} record(s) created")
