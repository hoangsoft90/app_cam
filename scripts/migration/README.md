# P0.5 — Legacy Data Migration (Opening Balance)

## Files

| File | Nội dung |
|---|---|
| `validate_csv.py` | Kiểm tra **offline** (không cần site): header, SĐT, amount > 0, ngày, dòng trùng, SĐT/batch phải có trong file khách. Chạy trước khi đẩy qua bench. |
| `customers.csv` | Template A — khách cũ (`customer_name, phone, customer_group, address, tax_id, notes, old_code`) |
| `opening_batches.csv` | Template B (tuỳ chọn) — lứa cũ theo `old_batch_code` |
| `opening_balances.csv` | Template C — nợ đầu kỳ (`customer_phone, batch_old_code, outstanding_amount, as_of_date, notes`) |
| `sample/` | Bản mẫu dùng cho `p05_acceptance` (10 khách / 11 dòng nợ / 34.975.000đ) |

## Quy trình (bắt buộc theo thứ tự)

```bash
# 0) Sửa file CSV thật của mình trong 1 thư mục riêng (KHÔNG sửa sample/)
#    Ngày luôn YYYY-MM-DD. SĐT unique, dạng 0xxxxxxxxx.
python3 scripts/migration/validate_csv.py <thư_mục_dữ_liệu_thật>

# 1) Đẩy thư mục đó lên máy bench, rồi import (một lệnh cho cả 3 file):
bench --site frontend execute feed_dealer.setup.migration.import_from_files \
    kwargs='{"dirpath": "/đường/dẫn/dữ_liệu_thật", "equity_account": "<tùy chọn>"}'

#    hoặc từng bước:
bench --site frontend execute feed_dealer.setup.migration.import_customers       kwargs='{"rows": ...}'   # khuyến nghị dùng import_from_files
bench --site frontend execute feed_dealer.setup.migration.signoff_report         kwargs='{"prefix": "…"}'
```

2) In bảng đối chiếu cho chủ dự án ký: `feed_dealer.setup.migration.signoff_report`
   → chép output vào `OWNER_SIGNOFF.md`, điền ngày/nguồn/người import.

## Cơ chế (tại sao là JE, không phải SI)

- Nợ cũ từ sổ giấy **không có hoá đơn**; tạo SI lùi ngày vi phạm NĐ 123/2020 → **cấm**.
- Mỗi dòng nợ = 1 Journal Entry **Dr Phải thu (party = Customer) / Cr tài khoản đầu kỳ**
  + 1 `Batch Debt` `is_opening_balance=1`, `sales_invoice=None`, `opening_journal_entry=<JE>`.
- Tài khoản đầu kỳ tự resolve: tham số `equity_account` → account_type `Temporary` → 1 tài khoản
  Equity lá duy nhất; không xác định được → **từ chối** (liệt kê ứng viên, không tự chọn hộ).
- Dòng JE **không** mang `batch_debt` (trường đó là attribution cấn trừ P1F — dòng nợ mang nó
  sẽ thành offset âm, làm nợ tăng ảo).
- `due_date` = `as_of_date` → trạng thái "Quá hạn" tự suy ra từ controller, không gõ tay.

## Idempotency & clear + re-import

- Chạy lại cùng file → **không thêm gì** (khóa `(customer, batch, allocated_amount)`,
  mọi docstatus). p05 T4 là bằng chứng.
- Crash giữa chừng được **resume**: debt draft tạo trước JE; JE mồ côi từ lần chạy chết
  được **nhận nuôi** (adopt) theo remark + party + số tiền, không tạo JE thứ hai (p05 T5).
- **Xoá import THẬT là huỷ tiền, không bao giờ tự động**: chỉ làm khi chủ dự án duyệt,
  theo thứ tự phụ thuộc —
  1. `Batch Debt` (cancel rồi delete),
  2. `Journal Entry` của nợ đầu kỳ (cancel rồi delete),
  3. `Feed Batch` mở đầu, 4. `Customer` — và luôn backup DB trước (`bench backup --with-files`).

## Owner sign-off (deliverable 8 của plan)

Tạo `OWNER_SIGNOFF.md` theo mẫu:

```
# OWNER SIGNOFF — Opening balances
Ngày: ....    Người import: ....    File nguồn: .... (sha256: ....)
Ngân sách đối chiếu: AR_open == BD_open == <số tiền> (diff 0)
Bảng theo khách: (dán output của signoff_report)
Ký tên chủ đại lý: ____________
```

Không coi P0.5 là DONE khi thiếu file này.
