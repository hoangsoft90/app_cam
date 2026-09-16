# working.md — nhật ký đang làm

Task đang làm / đã xong gần đây. Format ngày: `YYYY-MM-DD` (ISO). Dọn mục cũ hơn 1–2 tuần.

## Đang làm

- [2026-09-16] Change `p0-feed-dealer-foundation` — **32/36 task xong** (còn 5.17, 8.5, 10.10,
  10.11 — tất cả đều chờ quyết định/duyệt của user), `openspec validate` → "is valid". App
  `feed_dealer` đã install trên site ERPNext **v16** của user (Mac, `frappe_docker`, site
  `frontend`), 19 DocType module `Feed Dealer` (0 custom).
  Bằng chứng mới nhất (sau khi vá P1A + làm P1B): P1B `TOTAL: 6 PASS: 6 FAIL: 0` (`P1B ACCEPTANCE:
  ALL PASS`), P1A `6 PASS: 6 FAIL: 0` (thêm T6 mới), P0 `9 PASS: 9 FAIL: 0` — chạy trên site thật
  sau `migrate` (`/tmp/mig_p1b.log`, `=== EXIT 0 ===`).
  Chưa commit — repo chưa có commit nào, đợi user duyệt (task 8.5 + 10.11).

## Chờ user quyết

- [2026-09-16] **2 lỗ hổng P1B đã ghi rõ trong `design.md` D14** (không giấu): (1) ERPNext
  *Unreconcile Payment* tạo JE mới và **không** cancel Payment Entry → lớp allocation sẽ vẫn coi
  khoản nợ là đã trả; (2) 2 Payment Entry submit **đồng thời** cho cùng khách có thể cùng đọc
  outstanding cũ và cấp phát vượt (cần row lock `select … for update` + test concurrency). Cả hai
  đều để P1C quyết.

- [2026-09-16] **Cột rác trên `tabBatch` của ERPNext.** Sự cố trùng tên DocType `Batch` (đã sửa bằng
  cách đổi app sang `Feed Batch`) để lại 12 cột không dùng trên bảng `tabBatch`:
  `customer, animal_type, start_date, expected_end_date, quantity, start_weight, current_weight,
  status, split_operation, has_been_split, total_debt, notes`. Frappe khôi phục metadata của ERPNext
  nhưng không xoá cột. Xoá cột là DDL không hoàn tác trên site thật → chờ user xác nhận.
- [2026-09-16] Có commit `apps/feed_dealer` + artifacts vào git không? Repo hiện chưa có commit nào
  (mọi file còn untracked).

## Vướng mắc môi trường

- [2026-09-16] Cầu MCP sang Mac (host Tailscale IPv6-only) đã **hồi phục** sau khi mất lần 2
  (`Cannot assign requested address`). Bài học: lỗi mạng tunnel là tạm thời — thử lại đúng 1 lần sau
  vài phút, và các lệnh dài phải ghi log vào file trong container để đọc sau.

## Đã xong (task lớn, gần nhất)

- [2026-09-16] Xoá cài đặt Docker ERPNext khỏi **máy này** theo yêu cầu user: 0 container, 0 volume,
  0 build cache, xoá image `frappe/erpnext:v15.121.2` + `postgres:15` + `redis:7-alpine` và network
  `app-cam_default` (còn `alpine:3.19` 11.6MB — có trước, không phải của stack này, chưa xoá).
  Xoá luôn `docker-compose.yml`, `docker/`, `.dockerignore` khỏi repo. `.env` (ERPNEXT_URL/API key/
  API secret) giữ nguyên, đã được `.gitignore` chặn.
- [2026-09-16] Chuyển mục tiêu từ ERPNext v15 + stack Docker tự dựng sang site v16 có sẵn của user
  (ngrok + MCP). Cập nhật lại toàn bộ artifacts của change cho khớp thực tế.
- [2026-09-16] Đổi DocType lứa nuôi `Batch` → `Feed Batch` (giữ mã `LOT-{YYYY}-{#####}`), sửa hooks,
  test và README theo; ERPNext `Batch` đã trở lại module `Stock`.

## Ghi chú kỹ thuật đáng nhớ (v16)

- `UOM` bỏ `conversion_factor`; dùng `UOM Conversion Factor` và `category` là **bắt buộc**.
- Không được để hàm SQL dạng chuỗi trong `fields`: dùng `{"SUM": "field", "as": "alias"}`.
- `doc.owner = user` trước `insert()` bị ghi đè; phải `db_set("owner", …)` sau khi insert.
- Xoá doc đã submit phải `cancel()` trước, `force=True` không đủ.
- `frappe.get_hooks("doc_events")` có thể trả **list** khi nhiều app cùng đăng ký một event.
- `bench execute` nuốt lỗi thật và báo nhầm thành `NameError: name 'feed_dealer' is not defined`;
  dùng `p0_acceptance.debug` để thấy traceback thật.
- **`save()` trên doc đã submit không ghi gì khi gọi từ trong `on_submit` của doc khác** — đo được:
  `_recalculate` tính đúng `paid=400.000/outstanding=600.000` trong bộ nhớ nhưng dòng DB vẫn
  `0/1.000.000` sau khi `save()` trả về (`doc_before_save` rỗng → coi như không có field nào đổi).
  Cách đúng cho cột derived của doc đã submit: `frappe.db.set_value(..., update_modified=False)`
  (giống `Feed Batch.total_debt`).
- **ERPNext tự ghi đè `item_tax_template` của dòng hàng** theo Item/Item Group
  (`TaxesAndTotals.validate_item_tax_template`) ⇒ muốn 1 hóa đơn có 2 nhóm thuế trên cùng một lứa
  thì phải khác **item**, không thể khác template trên cùng item.
- Xoá file trên Mac: `rm`/`docker exec rm` bị chặn quyền (file do user host sở hữu) — dùng
  `python3 .agent/mac.py "python3 -c \"__import__('os').remove('<đường dẫn host>')\""`.

- [2026-09-16] **Review lại P1A/P1B (sau commit eb75222) — tìm ra 1 bug High + 1 Medium, đã sửa + có test.**
  High: refund Payment Entry (`payment_type="Pay"` + `party_type="Customer"`) submit được qua API
  (ERPNext chỉ chặn ở JS của Desk) và hook cũ **cấp phát 1.000.000 của tiền hoàn về khách cho một
  khoản nợ cũ** → đã gate `payment_type == "Receive"` (P1B T7). Medium: khoá idempotency dùng `""`
  không match dòng có `NULL` (import/raw SQL) → retry tạo debt thứ hai; đã dùng `["is","not set"]`
  (P1A T7). Low: nhánh "draft còn sót" (P1A-2) trước đó không có test → thêm T8, bỏ `save()` thừa.
  **Mutation check**: cố tình khôi phục 2 hành vi cũ → T7/T8 FAIL (2 debt cho 1 nhóm; draft + debt mới
  song song), khôi phục lại → xanh. Bằng chứng: P1A 8/8, P1B 7/7, P0 9/9 PASS;
  `result_2026-09-16_1405.txt`; bài học #14–#19 trong LESSONS_LEARNED.md + skill
  `erpnext-v16-pitfalls` (mục 1.3–1.5, 2.3–2.4, 6).
- [2026-09-16] **P1A vá + P1B Payment Allocation: xong + verify trên site thật.**
  P1A: khoá idempotency đổi thành đúng bộ ba đang group `(sales_invoice, batch,
  item_tax_template)` (trước đó chỉ `(invoice, batch)` nên **nhóm thuế thứ 2 của một hóa đơn bị
  nuốt mất** — fixture T6 mất hẳn 1.000.000 tiền nợ); thêm field `item_tax_template` trên Batch
  Debt (sinh từ generator); nhóm có draft còn sót thì **submit nốt** thay vì bỏ qua mãi.
  P1B: `Payment Allocation` đổi từ child table → **DocType độc lập, submittable** (`ALLOC-{YYYY}-{#####}`,
  link `payment_entry`) vì `on_submit` chạy SAU khi doc cha đã ghi DB; FIFO theo `due_date` trên
  các nợ `outstanding_amount > 0`; cancel PE → cancel allocation → tính lại nợ + `total_debt`.
  Bằng chứng: P1B 6/6 PASS (T1 một phần, T2 đủ, T3 FIFO, T4 cancel khôi phục, T5 quá hạn + fee
  theo Settings, T6 không cấp phát trùng), P1A 6/6 PASS, P0 9/9 PASS; migrate `EXIT 0`.
- [2026-09-16] P1A Order-to-Batch-Debt: xong + verify. Custom field `custom_batch` trên
  Sales Invoice Item (sync qua after_migrate); events/sales_invoice.py thật (on_submit
  group theo (lứa, tax template) + idempotent; guard huỷ đặt ở **before_cancel** vì
  frappe ghi docstatus=2 TRƯỚC khi on_cancel chạy; on_cancel cascade + refresh
  total_debt); fallback rate = Settings / 0.00022. Fix hộ custom_app: get_severity
  'Low' → 'Medium' (user duyệt). Bằng chứng: p1a_acceptance 5/5 PASS, p0_acceptance
  9/9 PASS (không regress), log --to-file trong container /tmp/p1a_run2.json +
  /tmp/p0_recheck2.json. 2 trap mới (đã ghi vào .agents/skills/erpnext-v16-pitfalls):
  guard huỷ phải nằm ở before_cancel (không phải on_cancel); hooks.py sửa xong phải
  `bench clear-cache` mới ăn (Redis app_hooks cache). Chưa commit (chờ user duyệt).
