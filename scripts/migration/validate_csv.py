"""Offline validate for the P0.5 CSV templates — NO site connection needed.

Run from the repo:  python3 scripts/migration/validate_csv.py <dir>
It checks exactly what `feed_dealer.setup.migration.validate_*` checks BEFORE
any site is touched (headers, phone format/duplication, amounts > 0, dates,
duplicate rows) plus the cross-file rule the site-side script cannot see early:
a balance or batch row whose phone is not in customers.csv.

Catching the file problems on the laptop — before the bench/MCP round-trip —
is the point: an import that dies at row 7 has already booked rows 1-6.
"""

import csv
import os
import re
import sys
from datetime import date, datetime

MARK = "P0.5"
PHONE = re.compile(r"^0\d{9,10}$")
CUSTOMER_COLS = ["customer_name", "phone", "customer_group", "address", "tax_id", "notes", "old_code"]
BATCH_COLS = ["customer_phone", "old_batch_code", "animal_type", "quantity", "start_date", "status", "notes"]
BALANCE_COLS = ["customer_phone", "batch_old_code", "outstanding_amount", "as_of_date", "notes"]
ANIMALS = ("Lợn", "Gà", "Cá", "Khác")
STATUSES = ("Đang nuôi", "Đã xuất bán", "Kết thúc", "Tạm dừng")


def load(path):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        rows = [
            {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            for row in csv.DictReader(handle)
        ]
    return [row for row in rows if any(row.values())]


def parse_date(raw):
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def validate(dirpath):
    problems = []

    def add(message):
        problems.append(message)

    # ---- customers.csv
    path = os.path.join(dirpath, "customers.csv")
    # Defaults keep the later sections working when the file is missing: the
    # customer errors are already reported, so batch/balance checks must not
    # crash on an unbound variable (measured: UnboundLocalError on the first run).
    customers, phones = [], {}
    if not os.path.exists(path):
        add(f"thiếu {path}")
    else:
        customers = load(path)
        extra = set(customers[0]) - set(CUSTOMER_COLS) - {"_row"} if customers else set()
        if extra:
            add(f"customers.csv có cột lạ {sorted(extra)} — template chỉ nhận {CUSTOMER_COLS}")
        for index, row in enumerate(customers, start=2):
            if not row.get("customer_name"):
                add(f"customers.csv dòng {index}: thiếu customer_name")
            phone = row.get("phone", "")
            if not PHONE.match(phone):
                add(f"customers.csv dòng {index}: phone '{phone}' không đúng dạng 0xxxxxxxxx")
            elif phone in phones:
                add(f"customers.csv dòng {index}: phone {phone} trùng dòng {phones[phone]}")
            else:
                phones[phone] = index

    # ---- opening_batches.csv (optional)
    batches = []
    path = os.path.join(dirpath, "opening_batches.csv")
    if os.path.exists(path):
        batches = load(path)
        for index, row in enumerate(batches, start=2):
            phone = row.get("customer_phone", "")
            if not PHONE.match(phone):
                add(f"opening_batches.csv dòng {index}: customer_phone '{phone}' sai dạng")
            elif phone not in phones:
                add(f"opening_batches.csv dòng {index}: SĐT {phone} không có trong customers.csv")
            if row.get("animal_type") and row["animal_type"] not in ANIMALS:
                add(f"opening_batches.csv dòng {index}: animal_type '{row['animal_type']}' phải thuộc {ANIMALS}")
            if row.get("status") and row["status"] not in STATUSES:
                add(f"opening_batches.csv dòng {index}: status '{row['status']}' phải thuộc {STATUSES}")
            if row.get("start_date") and parse_date(row["start_date"]) is None:
                add(f"opening_batches.csv dòng {index}: start_date '{row['start_date']}' sai (YYYY-MM-DD)")

    # ---- opening_balances.csv
    path = os.path.join(dirpath, "opening_balances.csv")
    balances = []
    if not os.path.exists(path):
        add(f"thiếu {path}")
    else:
        balances = load(path)
        seen = set()
        for index, row in enumerate(balances, start=2):
            phone = row.get("customer_phone", "")
            if not PHONE.match(phone):
                add(f"opening_balances.csv dòng {index}: customer_phone '{phone}' sai dạng")
            elif phone not in phones:
                add(f"opening_balances.csv dòng {index}: SĐT {phone} không có trong customers.csv")
            try:
                amount = float(row.get("outstanding_amount") or 0)
            except ValueError:
                amount = 0.0
                add(f"opening_balances.csv dòng {index}: outstanding_amount '{row.get('outstanding_amount')}' không phải số")
            if amount <= 0:
                add(f"opening_balances.csv dòng {index}: outstanding_amount phải > 0")
            as_of = parse_date(row.get("as_of_date"))
            if row.get("as_of_date") and as_of is None:
                add(f"opening_balances.csv dòng {index}: as_of_date '{row['as_of_date']}' sai (YYYY-MM-DD)")
            elif as_of and as_of > date.today():
                add(f"opening_balances.csv dòng {index}: as_of_date {as_of} ở tương lai")
            key = (phone, row.get("batch_old_code", ""), row.get("outstanding_amount", ""))
            if key in seen:
                add(f"opening_balances.csv dòng {index}: trùng đúng dòng khác ({phone}, "
                    f"{row.get('batch_old_code') or '-'}, {row.get('outstanding_amount')})")
            seen.add(key)
            if row.get("batch_old_code"):
                known = {b.get("old_batch_code") for b in batches}
                if batches and row["batch_old_code"] not in known:
                    add(f"opening_balances.csv dòng {index}: batch_old_code "
                        f"'{row['batch_old_code']}' không có trong opening_batches.csv")

    total = sum(float(row.get("outstanding_amount") or 0) for row in balances if row.get("outstanding_amount"))
    print(f"customers={len(customers)}  batches={len(batches)}  balances={len(balances)}  total={total:,.0f} VND")
    if problems:
        print(f"\n{len(problems)} VẤN ĐỀ:")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("OK — file hợp lệ, có thể import qua bench.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python3 scripts/migration/validate_csv.py <dir>")
        sys.exit(2)
    sys.exit(validate(sys.argv[1]))
