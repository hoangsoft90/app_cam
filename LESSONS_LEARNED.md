# LESSONS_LEARNED.md — bài học lỗi đã xác định chắc chắn nguyên nhân (không tái phạm)

Mỗi mục dưới đây đều đã **xảy ra thật trong phiên 2026-09-16**, đã fix và đã verify bằng test/output.
Định dạng: ❌ hiện tượng → 🔍 nguyên nhân gốc (đã xác nhận, không suy đoán) → ✅ cách đúng → 📍 bằng chứng.

---

## 1. Đặt tên DocType trùng với DocType có sẵn của ERPNext/Frappe

- ❌ App khai báo DocType `Batch` → ERPNext's stock `Batch` bị đè: module `Stock` → `Feed Dealer`,
  field set thay đổi, stock code vỡ `'Batch' object has no attribute 'reference_doctype'`.
- 🔍 **Tên DocType là global trên site**, không theo app. App sau cài đè metadata app trước.
- ✅ Trước khi thêm DocType mới: quét tên trong source `frappe` + `erpnext` (thư mục `doctype/` trên
  bench), không chỉ tra DB. Trùng → đổi tên có ngữ cảnh (`Feed Batch`), giữ mã nghiệp vụ người dùng
  thấy qua `autoname` (`LOT-{YYYY}-{#####}`).
- 📍 Sau sửa: `Batch` module `Stock` với field gốc; quét lại toàn bộ tên → 0 collision còn lại.

## 2. Migrate fail vì child table thiếu file controller `.py`

- ❌ `bench migrate` lỗi import khi sync DocType.
- 🔍 Frappe import `<scrub(doctype)>.<scrub(doctype)>` cho **mọi** DocType, child table kể cả —
  thiếu file là vỡ luôn parent.
- ✅ Generator luôn xuất `.py` cho 19/19 DocType; class name theo đúng rule
  `doctype.replace(" ","").replace("-","")` (`AI Workflow Config` → `AIWorkflowConfig`, không phải
  snake_case, không phải capitalize từng chữ).
- 📍 19/19 controller khớp rule; migrate EXIT 0.

## 3. ERPNext v16: `UOM.conversion_factor` không còn tồn tại

- ❌ Ghi conversion_factor vào `UOM` → `Unknown column 'conversion_factor'`.
- 🔍 v16 tách conversion thành DocType `UOM Conversion Factor` (`category` **bắt buộc**, `from_uom`,
  `to_uom`, `value`).
- ✅ Seeder tạo UOM (kèm `category`) + conversion riêng. Bỏ qua category → `MandatoryError ...:
  category` (bị `bench execute` che thành `NameError` — xem mục 8).
- 📍 `1 Bao = 25 Kg`, `1 Tấn = 1000 Kg` (category Mass) tạo thành công; A9 PASS.

## 4. v16 chặn hàm SQL dạng chuỗi trong `fields`

- ❌ `fields=["sum(paid_amount) as total"]` → `frappe.exceptions.ValidationError: SQL functions are
  not allowed as strings in SELECT`.
- 🔍 v16 validate field string, chỉ cho phép format đơn giản; aggregate phải đi qua dict.
- ✅ `fields=[{"SUM": "paid_amount", "as": "total"}]` (mapping trong `frappe/database/query.py`,
  hằng `FUNCTION_MAPPING`).
- 📍 A3 tính `outstanding_amount` đúng 10,000,000 sau khi submit.

## 5. `doc.owner = user` trước `insert()` không có tác dụng

- ❌ Fixture Feed Batch cho farmer vẫn owner = Administrator → nếu kiểm "farmer thấy lứa của mình"
  sẽ PASS ảo (cùng owner).
- 🔍 Frappe ghi đè `owner` bằng session user khi insert; assignment trước insert bị bỏ qua.
- ✅ Sau insert: `doc.db_set("owner", owner_user, update_modified=False)`. Với fixture tái dùng:
  kiểm tra lại và sửa owner mỗi lần chạy.
- 📍 A5 PASS thật: farmer thấy đúng 1 lứa (own=LOT-2026-00638), lứa khách khác bị ẩn,
  `has_permission(other)=False`.

## 6. Xoá document đã submit cần cancel trước — `force=True` không còn đủ

- ❌ `frappe.delete_doc("Batch Debt", name, force=True, ignore_permissions=True)` trên doc docstatus=1
  → `Submitted Record cannot be deleted`.
- 🔍 v16 chặn xoá submitted ở `check_permission_and_not_submitted` bất kể force.
- ✅ `if old.docstatus == 1: old.cancel()` rồi mới delete.
- 📍 Acceptance chạy lại liên tiếp 2 lần không lỗi (fixture tái sử dụng).

## 7. `frappe.get_hooks("doc_events")` trả LIST khi nhiều app đăng ký cùng event

- ❌ `path.rpartition(".")` trên list → `AttributeError: 'list' object has no attribute 'rpartition'`;
  và khi gọi thẳng handler của app khác (`custom_app`) với doc giả → side-effect không mong muốn.
- 🔍 Khi ≥2 app cùng hook 1 event, giá trị là list path; site này có `custom_app` đăng ký cùng
  Sales Invoice/Payment Entry.
- ✅ Normalize: nếu list/tuple thì duyệt từng phần tử; **lọc chỉ gọi handler `feed_dealer.*`** —
  handler app khác không thuộc contract của mình và gọi vào là nguy hiểm.
- 📍 A7 PASS: 5 handler của app import được và gọi không lỗi.

## 8. `bench execute` che exception thật bằng `NameError`

- ❌ Báo lỗi `NameError: name 'feed_dealer' is not defined` cho MỌI lỗi thật bên trong hàm.
- 🔍 Đọc source `frappe/commands/utils.py::execute`: catch exception của `frappe.get_attr(method)`,
  fallback `eval(method)` → NameError giả. Lỗi thật nằm ở đoạn traceback TRƯỚC đó.
- ✅ Có `p0_acceptance.debug()`: chạy từng check, in traceback thật, KHÔNG raise → đọc được toàn bộ
  lỗi trong 1 lần chạy.
- 📍 debug() lần đầu hiện đúng 3 lỗi thật (mục 4, 5, 7) thay vì NameError chung chung.

## 9. `format:` autoname vứt bỏ `name` do caller truyền

- ❌ Fixture truyền `name="P0-ACCEPT Batch ..."` vào DocType có `autoname: format:` → doc được đặt
  tên `LOT-2026-006xx`, các bước tra cứu theo tên cũ không tìm thấy.
- 🔍 Đọc source `frappe/model/naming.py::set_new_name`: với autoname không phải prompt/uuid,
  `doc.name = None` trước khi đặt tên.
- ✅ Fixture định vị qua **filter dữ liệu** (`customer`, prefix `P0-ACCEPT%` trên customer), không
  qua tên tự chọn; cleanup cũng đi theo customer.
- 📍 cleanup/A3/A5 chạy lặp ổn định.

## 10. Lệnh dài qua MCP bridge phải ghi log vào file trong container

- ❌ `bench migrate` chạy ~2-4 phút, MCP call timeout → "(no output)", không biết thành bại;
  có lần đoán sai rằng migrate đã xong.
- 🔍 Kết nối cầu có thời gian sống ngắn hơn lệnh migrate; giữ call chờ là mất output.
- ✅ `bench.py --to-file <path-trong-container>`: subprocess ghi stdout/stderr/exit-code vào file,
  rồi đọc file bằng call sau (`print(open(P).read()[-N:])`). Chạy tuần tự, kiểm tra file tồn tại
  trước khi kết luận.
- 📍 mig1.log + mig2.log đều đọc được, `=== EXIT 0 ===` rõ ràng.

## 11. `bench migrate` có lock — không chạy song song

- ❌ Lần 2 báo `LockTimeoutError: Failed to aquire lock: bench_migrate` (lần đầu vẫn đang chạy).
- 🔍 Frappe giữ file lock `sites/<site>/locks/bench_migrate.lock` trong suốt migrate.
- ✅ Chạy tuần tự; gặp lock error thì kiểm tra tiến trình cũ trước khi conclusion "lỗi migrate".
- 📍 2 migrate tuần tự đều EXIT 0.

## 12. Kết nối cầu (tunnel/Tailscale) là đường yếu — thiết kế cho việc nó đứt

- ❌ Giữa phiên bridge đứt (`Errno 99 Cannot assign requested address`, host IPv6-only trong khi
  workspace mất IPv6 route) — công việc bench đứng lại.
- ✅ 2 hướng: (a) chờ vài phút rồi thử lại ĐÚNG 1 lần; (b) trong lúc chờ, mọi việc đọc/ghi dữ liệu
  vẫn làm qua REST ngrok (IPv4, đường độc lập). Lệnh dài ghi file trong container (mục 10) nên
  kết quả không phụ thuộc bridge sống đủ lâu.
- 📍 Bridge hồi phục sau ~10 phút; acceptance + 2 migrate hoàn tất sau đó; ngrok sống sót xuyên suốt.

## 13. Dữ liệu thật trên site đích → mọi seed phải create-if-absent

- ❌ (nguy cơ được chặn sớm) Site đã có 3 company, `Bao`, `Kg`, warehouse thật của user.
- 🔍 Seeder nào insert vô điều kiện sẽ nhân bản/nhẫm dữ liệu thật.
- ✅ `frappe.db.exists` trước khi tạo; không sửa/xoá gì đã có; company không tạo mới (chỉ verify VND
  + ghi vào Settings); UOM cũ giữ nguyên `must_be_whole_number` do người đặt.
- 📍 Seeder chạy 2 lần: lần 2 = 0 thay đổi; companies nguyên vẹn.

## 14. Hook `on_submit` chạy SAU khi doc cha đã ghi DB — child row không tự persist

- ❌ Không thể tạo child row (child table) của chính document đang submit trong `on_submit`:
  `doc.append("field", {...})` rồi để đó thì row **không** được ghi.
- 🔍 Đọc source `frappe/model/document.py`: `Document._submit()` = `self.docstatus = 1; return self.save()`, và
  `run_post_save_methods()` (nơi phát `doc_events.on_submit`) chạy **cuối** `_save()` — sau `db_update()`.
- ✅ Chọn 1 trong 2: (a) làm việc đó ở `before_save`/`validate`; hoặc (b) cho bản ghi đó một DocType
  **độc lập, submittable** (link về doc cha) để có docstatus riêng — P1B chọn (b) cho
  `Payment Allocation`, nhờ vậy cancel Payment Entry là cancel được từng lát cắt.
- 📍 P1B T1/T4 PASS: allocation submit được ngay trong hook; cancel PE → allocation docstatus=2.

## 15. `save()` trên document đã submit có thể KHÔNG ghi gì (im lặng) — dùng `db.set_value`

- ❌ `debt.save()` (Batch Debt, docstatus=1) gọi từ trong `on_submit` của Payment Entry: tính đúng
  `paid=400,000 / outstanding=600,000` trong bộ nhớ, nhưng dòng DB sau `save()` vẫn `0 / 1,000,000`
  — không exception, không log, dễ tưởng đã cập nhật.
- 🔍 `Document._save()` → `db_update()` chỉ ghi các field "changed", và changed suy ra từ baseline
  `_doc_before_save`; đo được `get_doc_before_save()` trả rỗng trong ngữ cảnh này ⇒ không field nào
  được coi là đổi ⇒ không UPDATE nào chạy.
- ✅ Với cột **derived, do server sở hữu** trên doc đã submit: `frappe.db.set_value(doctype, name,
  {field: value}, update_modified=False)` (đúng cách P1A ghi `Feed Batch.total_debt`). Công thức vẫn
  nằm ở controller (`calculate_derived_fields()`) để không lệch khỏi giá trị hiển thị trên form.
- 📍 P1B T1–T6 PASS sau khi chuyển sang `set_value`.

## 16. Khoá idempotency phải trùng đúng BỘ KHOÁ ĐANG GROUP

- ❌ Group theo `(batch, item_tax_template)` nhưng kiểm trùng theo `(sales_invoice, batch)`: nhóm thứ 2
  khớp nhầm với debt của nhóm thứ 1 → **bỏ luôn 1,000,000 tiền nợ**, không log gì.
- 🔍 Check tồn tại "gần đúng" (thiếu 1 chiều) luôn fail theo hướng âm thầm mất dữ liệu.
- ✅ Lưu đủ thành phần khoá xuống doc (`item_tax_template` trên Batch Debt) và check `docstatus=1`
  theo đúng bộ ba; nhóm còn draft thì submit nốt thay vì skip mãi.
- 📍 T6: 1 hoá đơn/1 lứa/2 nhóm thuế → 2 debt (trước khi sửa: 1 debt).
- ⚠️ Kèm theo: ERPNext **ghi đè** `item_tax_template` của dòng hàng theo Item/Item Group
  (`TaxesAndTotals.validate_item_tax_template`) ⇒ muốn 2 nhóm thuế trên cùng lứa phải khác **item**.

## 17. Chiều của payment quyết định có được cấp phát hay không (refund ≠ thu tiền)

- ❌ `payment_type="Pay"` + `party_type="Customer"` (hoàn tiền lại cho khách) submit được qua API/import
  và hook `Payment Entry.on_submit` đã **cấp phát 1.000.000 của khoản hoàn tiền cho khoản nợ cũ nhất của
  khách** — tức là ghi nhận "khách đã trả" bằng một giao dịch tiền đi RA.
- 🔍 ERPNext chỉ lọc dropdown `party_type` theo `payment_type` ở **JS của Desk**; server không chặn,
  nên API/import/integration (P2 mobile) vẫn tạo được.
- ✅ Gate rõ: chỉ cấp phát khi `party_type == "Customer"` **và** `payment_type == "Receive"`.
- 📍 P1B T7: refund PE submit thành công → 0 allocation, khoản nợ giữ nguyên 1.000.000.

## 18. `""` và `NULL` là hai thứ khác nhau trong filter — và `None` thì không match gì cả

- ❌ Kiểm trùng bằng `{"item_tax_template": ""}` bỏ sót dòng có `NULL`: retry tạo **debt thứ hai**
  cho cùng một nhóm (`DEBT-…232` với `None` + `DEBT-…233` với `''`). Nợ bị nhân đôi = mất tiền.
- 🔍 Frappe ghi `''` cho field Link rỗng (đo được `item_tax_template is null` → 0), nhưng import /
  data migration / `set_value(field, None)` để lại `NULL`; `{"field": ""}` không match `NULL`, còn
  `{"field": None}` sinh `= NULL` nên **không match gì** (đo được: trả `None`).
- ✅ Khi giá trị rỗng thì filter bằng `["is", "not set"]` (phủ cả `NULL` lẫn `''`); không bao giờ
  truyền `None` làm giá trị filter.
- 📍 P1A T7: re-fire 2 lần (một lần với `''`, một lần với `NULL`) → vẫn đúng 1 debt.

## 19. Test PASS chỉ đáng tin khi nó FAIL lúc cố tình làm hỏng code (mutation check)

- ❌ Hai test mới (T7/T8 của P1A) đều xanh — nhưng "xanh" chưa chứng minh test có tác dụng.
- ✅ Cách kiểm: sửa tạm code về đúng hành vi cũ (`_tax_filter` trả `""`; nhánh draft thành "skip mọi
  doc đã tồn tại") rồi chạy lại → **T7 FAIL** (2 debt cho 1 nhóm) và **T8 FAIL** (draft + debt mới
  song song); sau đó khôi phục đúng nguyên trạng và chạy lại → 8/8 PASS.
- 📍 Bằng chứng nằm trong `result_2026-09-16_1405.txt`.

## 20. `flags.ignore_validate` bỏ qua **cả `before_submit`** — và làm ERPNext không kịp điền field

- ❌ Tôi định dùng `doc.flags.ignore_validate = True` để "lách" 1 đơn hàng vào DB không qua gate,
  mô phỏng đường import/offline-sync. Kết quả: `MandatoryError: [Sales Order, SAL-ORD-…]:
  conversion_rate, price_list_currency, plc_conversion_rate, item_name, uom, conversion_factor`.
- ✅ Nguyên nhân đọc từ source: `run_before_save_methods()` mở đầu bằng
  `if self.flags.ignore_validate: return` → **bỏ luôn** `validate` của controller (nên các field
  price-list/UOM không được điền) và **bỏ luôn `before_submit`**. Nghĩa là cờ này không chỉ tắt gate
  của mình mà tắt cả nghiệp vụ của ERPNext, và với test thì còn nguy hiểm hơn: nếu để nguyên cờ khi
  submit thì gate submit của mình cũng không chạy ⇒ test **xanh giả**.
- 📍 Cách đúng: đừng lách cờ; tạo ra trạng thái cần test bằng nghiệp vụ thật (ví dụ hạ hạn mức giữa
  lúc draft đang chờ) — P1C T3.

## 21. `save()` bị từ chối vẫn bump `modified` trong bộ nhớ → lần save sau chết vì TimestampMismatch

- ❌ Trong 1 test: gọi `save()` để test nhánh bị chặn (thiếu lý do) → nhận `ValidationError` đúng như
  mong đợi; nhưng lần `save()` thứ hai trên **cùng instance** báo
  `TimestampMismatchError: … has been modified after you have opened it` dù không ai sửa row.
- ✅ Đo được (probe in ra 4 mốc thời gian): DB **không** đổi (`after_save1_db_modified` giữ nguyên),
  nhưng `doc.modified` trong bộ nhớ đã bị frappe đặt thành thời điểm hiện tại **trước khi** validate.
  Lần save sau so `self.modified` với DB → tự thấy mình "cũ hơn" DB.
- 📍 Cách đúng: sau một save bị từ chối mà vẫn muốn dùng lại instance → `doc.reload()` (đúng như
  thông báo lỗi khuyên, và đúng cái Desk làm).
- 📍 Phụ: điều này cũng chứng minh **save bị từ chối không ghi DB** — chi tiết quan trọng khi code
  thuộc vùng tiền.

## 22. Test lock phải nhìn vào **đường production**, không phải gọi lại helper

- ❌ Test "FIFO dùng row lock" của tôi gọi thẳng `_open_debts(customer, for_update=True)` rồi kiểm
  connection thứ hai bị chặn → xanh. Nhưng khi mutation-check (đổi chỗ gọi thật trong `on_submit`
  thành `for_update=False`), test **vẫn xanh** — vì nó chưa bao giờ kiểm chỗ gọi thật.
- ✅ Viết lại: (1) spy lên `_open_debts` để bắt tham số khi **một Payment Entry thật** được submit
  (`AssertionError: the real submit path did not request the row lock (for_update=False)` khi mutation),
  và (2) vẫn giữ phép thử lock thật bằng connection thứ hai (`1205, 'Lock wait timeout exceeded'`).
- 📍 Quy tắc: test chỉ chứng minh được thứ nó **quan sát qua đường mà hệ thống thật sự đi**.

## 23. `@frappe.whitelist()` mở cho **mọi** user đã đăng nhập — phải tự kiểm quyền

- ❌ Tự review phát hiện: `get_credit_position(customer)` / `check_order_credit(...)` trả về hạn mức
  của **bất kỳ** khách nào cho bất kỳ user đăng nhập (kể cả một Farmer thuộc trại khác).
- ✅ Thêm `_require_credit_read(customer)` → `frappe.has_permission("Credit Score", "read", doc=name)`
  (Farmer có `if_owner` nên chỉ thấy hồ sơ của mình), fail closed, kèm test âm (P1C T10).
- 📍 Whitelist chỉ là "cho gọi qua HTTP", **không** phải kiểm quyền.

---

## Quy tắc mang đi (tóm tắt 1 dòng mỗi bài)

1. Check trùng tên DocType với source frappe/erpnext TRƯỚC khi thêm.
2. Mọi DocType (kể cả child table) phải có `.py` controller đúng rule tên class.
3. v16: conversion nằm ở `UOM Conversion Factor`, `category` bắt buộc.
4. Aggregate = dict `{"SUM": ...}`, không dùng string SQL.
5. Muốn owner khác → `db_set("owner", ...)` sau insert.
6. Submitted doc: cancel trước khi delete.
7. `get_hooks()` có thể trả list; chỉ gọi handler của app mình.
8. `bench execute` che lỗi thật → có entry `debug()` in traceback từng check.
9. `format:` autoname vứt name truyền vào → định vị fixture bằng filter dữ liệu.
10. Lệnh dài ghi log file trong container, đọc sau; không giữ call chờ.
11. Migrate có lock → tuần tự.
12. Bridge có thể đứt: thử lại 1 lần, trong lúc chờ dùng REST.
13. Site thật → seed create-if-absent, không đè dữ liệu người dùng.
14. `on_submit` chạy SAU khi doc cha ghi DB → child row trong hook không persist; dùng doc độc lập
    submittable hoặc `before_save`.
15. Cột derived của doc đã submit: `frappe.db.set_value(..., update_modified=False)`, không `save()`.
16. Khoá idempotency phải đúng bằng bộ khoá đang group — thiếu 1 chiều là mất dữ liệu âm thầm.
17. Chỉ cấp phát cho **thu tiền** (`Receive`); refund (`Pay` + Customer) chỉ bị chặn ở JS của Desk.
18. Filter rỗng: dùng `["is", "not set"]` (phủ `''` + `NULL`); `None` không match gì.
19. Test mới phải được mutation-check: làm hỏng code → test phải đỏ → khôi phục → xanh lại.
20. Không dùng `flags.ignore_validate` để lách nghiệp vụ — nó tắt cả `before_submit` của mình và
    khiến ERPNext không điền field ⇒ test xanh giả.
21. `save()` bị từ chối không ghi DB nhưng vẫn bump `modified` trong bộ nhớ → `reload()` trước khi
    dùng lại instance.
22. Test lock/bảo vệ phải quan sát **đường production** (spy/hook thật), không chỉ gọi lại helper.
23. `@frappe.whitelist()` không kiểm quyền — hàm API dữ liệu riêng của khách phải tự
    `frappe.has_permission()`, fail closed.
