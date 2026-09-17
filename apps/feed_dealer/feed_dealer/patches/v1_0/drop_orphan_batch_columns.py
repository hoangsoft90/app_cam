"""Drop the 12 orphan columns the P0-era `Batch` collision left on ERPNext's `tabBatch`.

Before P0's name fix, this app owned a DocType called `Batch` (module Feed Dealer) with its own
columns. Frappe then restored ERPNext's Stock `Batch` metadata (tasks.md 5.16) but does NOT drop
columns, so `tabBatch` still carries 12 columns that no DocType describes. P1A renamed the concept
to `Feed Batch`, which lives in its own table — nothing reads or writes those columns any more.

This is irreversible DDL, so before it was enabled the site was backed up with
`bench --site frontend backup` (path + size recorded in working.md and the result file) and every
orphan column was counted: all 12 were empty on every row.

Idempotent: a column already gone is skipped (`frappe.db.has_column`), and the whole patch refuses
to run if ANY of the columns still holds data — dropping that would be silent data loss, which is a
decision for a human, not a migration.
"""

import frappe

# The columns P0's `Batch` DocType created on tabBatch (tasks.md 5.17).
ORPHAN_COLUMNS = (
	"customer",
	"animal_type",
	"start_date",
	"expected_end_date",
	"quantity",
	"start_weight",
	"current_weight",
	"status",
	"split_operation",
	"has_been_split",
	"total_debt",
	"notes",
)


def _non_empty(column):
	"""Rows where this column actually holds something ('', NULL and 0 do not count)."""
	return frappe.db.sql(
		f"SELECT COUNT(*) FROM `tabBatch` WHERE `{column}` IS NOT NULL AND `{column}` != ''"
	)[0][0]


def execute():
	existing = [column for column in ORPHAN_COLUMNS if frappe.db.has_column("Batch", column)]
	if not existing:
		print("[drop_orphan_batch_columns] no orphan column left - nothing to do")
		return

	rows = frappe.db.sql("SELECT COUNT(*) FROM `tabBatch`")[0][0]
	carrying = {column: _non_empty(column) for column in existing}
	carrying = {column: count for column, count in carrying.items() if count}
	print(
		f"[drop_orphan_batch_columns] tabBatch rows={rows}, orphan columns present={len(existing)}, "
		f"columns holding data={carrying or 'none'}"
	)
	if carrying:
		# Never drop a column that still has content: that is real data loss and must be a human
		# decision (export it first), not a side effect of a migrate.
		frappe.throw(
			"Không thể xoá cột rác trên tabBatch: các cột sau vẫn còn dữ liệu "
			f"{carrying}. Xuất dữ liệu các cột này ra file trước rồi quyết định thủ công.",
			title="Cột rác còn dữ liệu",
		)

	for column in sorted(existing):
		frappe.db.sql(f"ALTER TABLE `tabBatch` DROP COLUMN `{column}`")
	frappe.db.commit()
	print(f"[drop_orphan_batch_columns] dropped {len(existing)} column(s): {sorted(existing)}")
