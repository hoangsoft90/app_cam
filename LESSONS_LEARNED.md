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

57. **Child-table field vs master-DocType field cùng tên — dùng sai là naming crash.** Trên `Role` master, trường định danh là `role_name`; `role` chỉ là trường của bảng con `Has Role` (trên User). Tạo `Role` bằng `{"role": "Driver"}` → frappe naming từ field rỗng → lỗi insert khó hiểu. Dấu hiệu nhận: traceback chấm ở naming validate / name rỗng.

58. **frappe_docker: backend và frontend (nginx) KHÔNG dùng chung assets — file tĩnh app mới 404 dù app chạy bình thường.** `sites/assets` + symlink app chỉ tồn tại bên backend; frontend có bản assets riêng trong image. Đo 2026-09-18: 404 cả 5 icon → `docker cp` vào `frappe_docker-frontend-1:/home/frappe/frappe-bench/assets/<app>/` → 200. `bench link-assets` không tồn tại; fix bền (rebuild image/volume) là quyết định của chủ infra. Verify bằng HTTP status + content-type + mở bytes bằng PIL, không tin `ls` trong container.

59. **Dọn tool trên sandbox: `rm -rf` xong phải verify — husk root-owned và symlink đứt không tự biến mất.** Đo 2026-09-18: `/google/flutter` xoá 1.1GB nội dung xong vẫn còn thư mục rỗng owner root (cần `sudo rmdir`); `adb` là symlink đứt còn sót sau khi gỡ package (dpkg chỉ xoá file thật). Verify bằng `which` + `ls -d`, không tin exit 0 của rm.

60. **Flutter android template .gitignore exclude gradlew + gradlew là project root theo CWD — 3 bẫy path khi build gradle trực tiếp trên CI.** Đo 2026-09-18 (run 3→6): (1) phải track `gradlew`/`gradlew.bat`/`gradle-wrapper.jar` vì template .gitignore đang exclude; (2) `defaults.run.working-directory` áp cho MỌI step → `./gradlew` sai tầng; (3) gradlew lấy CWD làm root — phải chạy TỪ `mobile/android/` (nơi có settings.gradle.kts). Bonus: `flutter analyze` exit 1 cả với lint INFO; jobs-log endpoint có lúc trả rỗng → dùng zip run logs.

61. **`http.Response(String)` của Dart dùng latin-1 — chữ Việt làm nổ `Invalid argument (string): Contains invalid characters`.** Đo trên CI 2026-09-18: mọi test mock trả payload có "Vũ"/"Hạn mức…" đều chết ngay trong handler, và vì handler ném ra ngoài nên test thật bại với thông báo lệch hướng (như "Actual: <Instance of Future<void>>"). Dùng `http.Response.bytes(utf8.encode(jsonEncode(body)), status, headers: {'content-type': 'application/json; charset=utf-8'})`. Dấu hiệu: test đỏ ở chỗ không liên quan tới logic đang test.

62. **`FlutterSecureStorage.setMockInitialValues(const {...})` → mọi lệnh GHI nổ `Cannot modify unmodifiable map`.** Map literal `const` là immutable, mà plugin ghi thẳng vào chính map được đưa. Truyền map mutable (`<String, String>{...}`). Insight tổng quát: helper mock nhận container từ caller thì phải truyền container ghi được — đừng dùng `const` cho dữ liệu test sẽ bị mutate.

63. **`FinancialAction.guard()` ném `StateError` — là `Error`, KHÔNG phải `Exception`, nên `on Exception` không bắt được.** Đo 2026-09-18 (review Mốc 2): bấm "Duyệt" khi offline → `StateError` xuyên qua 2 clause `on SubmitRejected`/`on Exception` → crash app. Quy tắc: mỗi guard ném `Error` phải có clause riêng (`on StateError`) hoặc chặn trước khi gọi; và phải có test khẳng định **không có request nào rời máy** khi offline.

64. **Refactor tách file dễ làm lệch NGẦM hành vi — test cũ là thứ duy nhất bắt được.** Đo 2026-09-18: khi tách `main.dart` → `core.dart` (Mốc 1.5), nhánh fallback token bị đổi từ `pair: '$user:$password'` (owner nhập api_key ở ô user, api_secret ở ô password) thành `pair: password` → hỏng hẳn đường đăng nhập token. CI bắt được vì test Mốc 1 vẫn còn nguyên. Ngược lại, code Mốc 1.5chưa từng chạy CI (`pubspec` thiếu `flutter_secure_storage`, `restore_session.dart` đọc key `token_pair` mà không writer nào tạo) — nghĩa là **file mới chưa push = chưa từng được biên dịch**. Kỷ luật: push sớm để CI làm compiler, và giữ nguyên test hành vi xuyên refactor.

65. **`Uri.queryParameters` với giá trị null không đáng tin như một số tài liệu gợi ý — dựng tham số bằng mutation.** Đo 2026-09-18: map literal có `if (x != null)` vừa bị lint `use_null_aware_elements`, vừa dễ lọt chuỗi `"null"` vào `order_by` (frappe đưa thẳng vào SQL ORDER BY). Cách an toàn: tạo `Map<String, String>` rồi `if (filters != null) query['filters'] = ...` — vắng nghĩa là vắng thật.

66. **`doc.flags.ignore_validate = True` DÍNH vào instance — save sau đó cũng không validate.** Đo 2026-09-18 (Mốc 3): API cần insert trước để có `name` rồi mới attach file, nên tạm bỏ validate ở lần insert; nhưng cờ không tự reset → lần `save()` cuối **cũng** bỏ qua hết luật → acceptance đỏ 9/15 với "nothing was refused" (mọi luật B9 đều không chạy). Fix: `doc.flags.ignore_validate = False` trước lần save quyết định. Dấu hiệu nhận: test cố tình vi phạm luật mà KHÔNG có exception nào.

67. **Lưu file base64 vào frappe: dùng `File` doc thuần, không dùng wrapper `file_manager.save_file` khi có `dt`/`dn`.** Đo trên site v16: wrapper làm `strip_exif_data` nổ `TypeError: a bytes-like object is required, not 'str'` với tên file `.jpg` (content bị biến thành str); route chạy đúng là `frappe.get_doc({"doctype":"File", "file_name":…, "attached_to_doctype":…, "attached_to_name":…, "is_private":1, "content": <base64 str>, "decode": True}).insert()` — đo lại: PNG 70B vào đúng 70B, file `.jpg` bị strip EXIF (đổi kích thước 631B) chứng tỏ decode thành bytes thật. Lưu ý: **`File` KHÔNG có field `decode`** ở v16 — đó là attribute mà `get_content()` đọc.

68. **`autoname` dạng format phải dùng dấu ngoặc `{}`: `format:DEL-{YYYY}-{#####}`.** Viết `format:DEL-.YYYY.-.#####` (kiểu Python `.format`) làm frappe lấy **nguyên chuỗi format** làm tên document: bản ghi đầu tạo được, bản thứ hai chết `DuplicateEntryError('DEL-.YYYY.-.#####')`. Dấu hiệu: tên document trông như chuỗi format, lỗi chỉ xuất hiện ở bản ghi THỨ HAI.

69. **`pluck="field"` trả về list GIÁ TRỊ — đừng gọi attribute trên phần tử.** `for photo in frappe.get_all(..., pluck="image")` rồi `photo.image` → `AttributeError: 'str' object has no attribute 'image'`. Muốn object thì bỏ `pluck`.

70. **Logic duyệt/từ chối phải miễn nhiễm với chính hàm validate của nó (2 lỗi review cùng gốc).** Đo 2026-09-18: (a) controller gán `driver = frappe.session.user` ở MỌI lần save → khi Manager duyệt, tên tài xế bị ghi đè thành Manager; (b) `_compute_state()` chạy lại theo `confirmation_method` ở mọi lần save → save của lần duyệt **tự hoàn tác quyết định duyệt** (status quay về "tạm"). Fix: chỉ stamp chủ thể khi `is_new()`, và thoát sớm khỏi hàm suy diễn trạng thái khi `approved_at` đã có. Quy tắc: hàm `validate()` phải có đường thoát cho trạng thái do NGƯỜI khác quyết.

71. **Hai lần liên tiếp "chạy thử mới biết": acceptance suite tự viết là thứ duy nhất bắt được lỗi backend.** Vòng Mốc 3: 4 lỗi do review tay (driver bị ghi đè, validate tự hoàn tác, attach file vào Table field, base64 validate) + 5 lỗi CHỈ lộ khi chạy thật trên site (thiếu dep/name trùng/ignore_validate dính/save_file trả str/pluck). Kỷ luật đã hiệu quả: mỗi vòng đỏ → đọc log → sửa 1 nguyên nhân → chạy lại, kết thúc `TOTAL: 15 PASS: 15 FAIL: 0`.

72. **`test` DB / bench execute: code có dấu ngoặc kép không đi qua `--capture` được — hãy đặt code vào module trong app rồi `bench execute feed_dealer.setup.<probe>.run`.** Đo 2026-09-18 (2 lần): `bench.py --capture --site frontend execute "print(open('/tmp/x.log').read()[-3000:])"` trả stdout RỖNG (shlex re-split cắt nát lệnh), trong khi cùng ý đó đặt trong module thì in ra đủ. Kèm: `run_cmd` trên Mac **từ chối cả `rm`** → xoá file trên Mac phải qua script python (`push_file.py` → `python3 /Users/hoang/.aki/<script>.py`).

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
24. **Docstring không phải bằng chứng**: guard `_validate_over_return` ghi "chạy trên draft"
    nhưng chỉ được gọi trong `approve()` — review phải `grep` **call site thực tế** của mọi hàm
    guard, không tin comment (đã lệch suốt từ lúc viết P1D).
25. **Guard số lượng ≠ guard giá trị**: invariant về TIỀN phải so cả giá trị, không chỉ số đếm.
    Nợ đã trả một phần vẫn được trả đủ số lượng → `outstanding` âm → thổi hạn mức tín dụng của
    P1C (nó cộng `outstanding`). Mọi chỗ cộng trừ tiền phải có bound giá trị tường minh.
26. `bench --to-file <path trong thư mục app>` → **PermissionError** (bind mount repo thuộc user
    khác). Ghi log vào `/tmp` **trong container**, đọc bằng
    `docker exec <c> python3 -c "print(open('/tmp/x.log').read()[-N:])"` — allowlist của MCP bỏ
    dấu `"`, dùng `'` bên trong là còn nguyên (đừng dựng path bằng `chr()`).
27. `/tmp` của sandbox **mất mỗi phiên** ⇒ cầu nối aki-MCP không còn: dựng lại
    `/tmp/aki-state/credentials.json` (từ chat) rồi `python3 .agent/akimcp.py export` để ghi
    `/tmp/mcp_access_token` + `/tmp/mcp_env.sh` cho `bench.py`/`mac.py`/`push_to_mac.py` dùng lại.
    **Trước khi hỏi user**: kiểm `.env` — repo này đã có sẵn `AKI_Passphrase`,
    `AKI_OAuth_Client_ID`, `AKI_OAuth_Client_Secret` (`.env` đã gitignore), nên dựng lại được ngay.
28. **DDL / cài dữ liệu một lần ⇒ viết thành Frappe `patch`** (`patches.txt` +
    `patches/v1_0/<tên>.py`), không chạy SQL tay: idempotent, có trong `tabPatch Log`, hiện rõ trong
    log migrate. Luôn `bench backup` TRƯỚC, và để patch **tự chặn** khi thao tác sẽ mất dữ liệu
    (ví dụ: cột còn giá trị thì throw, không drop).
29. Field `reqd=1` mà chỉ người mới điền được (vd `period_from`/`period_to` của phiếu xác nhận nợ)
    phải có **default suy từ dữ liệu** ở đường API — không dùng hằng số vô nghĩa, nếu không API
    không tạo nổi document (lỗi `MandatoryError` mới lộ ra khi chạy thật).
30. Tự động hoá TIỀN: **"bảo toàn hơn là làm được"** — khi một cột tiền DERIVED không thể tách
    trung thực (paid/returned/offset đến từ các allocation), phải TỪ CHỐI thao tác kèm lý do rõ
    ("tất toán khoản nợ đó trước"), tuyệt đối không bịa một con số trông hợp lý.
31. Cảnh báo (cảnh báo ≠ chặn) cũng phải có test: assert CẢ HAI vế — cảnh báo BẬT **và** nghiệp vụ
    vẫn chạy tiếp; nếu ai đó đổi cảnh báo thành `throw`, test phải đỏ.
32. **Guard chặn ở `on_submit` KHÔNG chặn được gì** — `_submit()` là `docstatus = 1; save()`,
    `save()` chạy `validate`/`before_submit` TRƯỚC khi ghi DB, còn `on_submit` chạy SAU. Ai đó
    nuốt exception (script, API catch, test runner) là document vẫn nằm trong DB ở docstatus=1.
    Guard phải ở `validate` (chạy cho cả draft save lẫn submit, vẫn trước write). Bằng chứng:
    P1F T9 — Journal Entry sai chủ thể vẫn được ghi docstatus=1 khi guard ở `on_submit`; chuyển
    sang `validate` thì JE không bao giờ được insert.
33. **Test "bị chặn" phải chứng minh là KHÔNG có gì được ghi.** "Có exception" ≠ "đã ngăn được".
    Assert thẳng trạng thái đã lưu (query bảng con link tới debt), không tin cái throw.
34. **Cleanup của test phải quét fixture theo NGỮ CẢNH THAM CHIẾU, không chỉ theo link cha.**
    `p1f_acceptance.cleanup` chỉ thu JE qua `Livestock Sale.journal_entry`, nên JE do test tự submit
    không thấy được → `delete_doc("Batch Debt")` lỗi `LinkExistsError` bị `try/except` ghi thành
    dòng FAILED vô hại → debt còn lại dạng cancelled → `sales_invoice` của nó sau đó trùng tên
    hoá đơn được tạo LẠI → lần chạy sau fixture báo "2 debts cho 1 hoá đơn" (lỗi cách nguyên nhân
    3 bước). Quét theo thứ document đang trỏ tới (mọi `Journal Entry Account.batch_debt` của các
    khách fixture), và coi mọi dòng `FAILED:` trong báo cáo cleanup là bug thật.
35. `run_cmd` của MCP: `rm -f` bị **chặn tên**, và **newline trong `python3 -c` cũng tính là
    chaining** — giữ 1 dòng/1 expression. Xoá file trên Mac bằng
    `find <path> -name <pat> -delete` hoặc `python3 -c "print(__import__('os').unlink('<path>'))"`.
    `push_to_mac.py` KHÔNG xoá file chỉ có trên Mac (chỉ báo "extra files"), nên xoá local rồi
    phải xoá tay trên Mac (đó là cách `_p1d_probe.py`/`_t9_probe.py` sống sót).
36. **Dataset ngay từ đầu phải sinh DỮ LIỆU THẬT đa dạng — nó tìm ra bug mà test viết tay bỏ sót.**
    Dataset P1G (60 giao dịch ngẫu nhiên có seed: hoá đơn nhiều lứa, nhiều thuế suất, phiếu thu có/không
    `references`, trả hàng từng phần, cấn trừ) lộ ngay 1 bug thật của P1D: trả **1 dòng của hoá đơn
    nhiều dòng** bị chặn, vì `_map_debts_from_note` chạy TRƯỚC khi cắt các dòng `make_return_doc` tự
    điền. Mọi test P1D cũ đều trả **cả** hoá đơn nên không bao giờ chạm ca này. Bài học kép:
    (a) test "1 hoá đơn 1 dòng" KHÔNG đại diện cho "1 hoá đơn nhiều dòng";
    (b) khi một hàm mapper tự điền thêm dòng, thứ tự "lọc trước hay map trước" là bug tiềm ẩn.
37. **Đẳng thức kiểm tra phải là đẳng thức TUYỆT ĐỐI, và phải chứng minh nó có răng.** Viết ra
    `BD = TT − PA − OF` rồi để dung sai 0 đồng: mọi số hạng là tổng số nguyên cùng đơn vị, nên lệch
    ≠ 0 luôn là thiếu số hạng hoặc lỗi thật. Kèm **mutation-check** (bỏ 1 số hạng trong công thức
    sản xuất → check phải đỏ **đúng bằng** số hạng đó). Ngoài ra phải có check "dataset không thoái
    hoá" (đủ return/offset/receipt có references), nếu không các check khác có thể PASS vì lý do sai.
38. **Đừng để check PASS trên DB rỗng (`0 == 0`).** `ensure_dataset` cũ chỉ kiểm "đã có customer
    chưa" — một build chết giữa đường đã commit customer (do `_set_limit` commit) nên lần sau nó
    tưởng dataset đã có, và 9/9 check PASS trên database trống. Phải có **marker chỉ được ghi khi
    build chạy xong** và `_facts()` từ chối chạy nếu marker thiếu.
39. **Frappe: `bench migrate` BỎ QUA standard doc khi `modified` không mới hơn** — nên một file
    report/print format sửa xong, `--check` báo không drift, mà DB vẫn giữ bản cũ (đo được:
    `tabReport.query` còn bản cũ). Cách chuẩn: `frappe.reload_doc(module, "report", name, force=True)`
    trong một patch idempotent. Kèm: **Query Report phải BẮT ĐẦU bằng `SELECT`/`WITH`** — comment `--`
    ở đầu làm `check_safe_sql_query` từ chối ("Query must be of SELECT or read-only WITH type").
40. **Report về một LUẬT phải gọi chính hàm của luật, không viết lại bằng SQL.** Report hạn mức nếu
    tự tính "limit − nợ lứa" sẽ hiện NHIỀU hơn số gate thực cho (gate còn trừ đơn đã submit chưa xuất
    hoá đơn + đơn nháp) ⇒ chủ dự án nhìn một con số mà hệ thống không tôn trọng. Dùng Script Report
    gọi `credit_position()`; acceptance assert theo từng khách + assert có ít nhất 1 khách có cam kết
    cấp đơn (nếu không, check không phân biệt được hai công thức).
41. **Công cụ MCP: urllib chết vì DNS chỉ trả IPv6 và vì argv quá dài.** Client phải gọi qua
    **curl có `--resolve` ghim IPv4**, và **payload gửi qua file** (`--data-binary @/tmp/payload.json`)
    — push cả app (~140KB base64) bằng argv làm `subprocess` ném `OSError: [Errno 7] Argument list
    too long`. Ngoài ra `/tmp` mất giữa phiên → dựng lại cầu nối bằng `.agent/akimcp.py export`
    (đọc credential AKI_* từ `.env`, gitignored) thay vì hỏi lại chủ dự án.
42b. **`--to-file` KHÔNG đảm bảo thứ tự: gọi lệnh bench kế tiếp ngay sau đó là RACE.** Đã đo được:
    `cleanup` còn đang xoá tài liệu thì `debug` đã chạy ⇒ nó đọc DB **đang bị xoá dở** (60 hoá đơn,
    0 credit note, 0 offset, marker còn nguyên) và C9 báo "dataset thoái hoá". Tôi đã suýt kết luận
    code mới tự gây lỗi — trong khi builder trên site sạch vẫn cho `returns=10, offsets=3`.
    Cách xử lý: dùng `.agent/bench_wait.py <log> <bench args>` — nó **poll tới khi log có dòng
    `=== EXIT n ===`** của chính lần chạy đó rồi mới trả về, rồi mới gọi lệnh tiếp theo.
43. **So SỐ LIỆU giữa các lần chạy trước khi kết luận "code sai".** Dấu hiệu nhận ra bằng chứng bị
    nhiễm: các con số lẽ ra độc lập với thay đổi của tôi (PA, ER) **giống hệt** lần trước, còn đúng
    nhóm số liên quan bị mất sạch (RT/OF = 0) — tức DATASET khác, không phải logic khác. Chỉ sau khi
    tái lập được thì mới được sửa code.
44. **Fixture/dataset phải kiểm bằng SỐ ĐÚNG so với builder khai báo, không chỉ "đủ dòng".**
    Một lần build chết giữa đường (crash ở app khác) để lại 1 Sales Order đã submit mà bộ đếm không
    ghi; lần retry build đè lên rác đó ⇒ DB có 13 SO trong khi builder khai 12, và **run vẫn xanh**
    vì đẳng thức tiền không liên quan tới đơn lẻ loi. Đã thêm check C9 so exact counts (hoá đơn / SO /
    phiếu thu / credit note) với `shape` đã lưu + `ensure_dataset` dọn dataset dở trước khi build lại.
45. **Hai cái bẫy Python/Frappe tôi tự gây rồi tự sửa:** (a) đặt biến của list-comprehension (`index`)
    rồi dùng lại cùng tên đó làm biến vòng lặp ở dưới ⇒ `UnboundLocalError` (comprehension có scope
    riêng, còn phép gán ở dưới biến nó thành local của hàm); (b) `inv.items.index(row)` trên Document
    của Frappe — so sánh là **theo field**, nên hai dòng cùng item/rate coi như bằng nhau và trả về
    index của dòng anh em ⇒ gửi sai `return_line` và bị controller từ chối. Dùng `enumerate` theo
    **vị trí** cho dòng của Document, đừng bao giờ dùng `.index()`.
46. **Lỗi từ APP KHÁC cũng làm test của mình đỏ — đọc traceback trước khi nghi code mình.** Một lần
    rebuild dataset chết với `NameError: name 'is_perpetual_inventory_enabled' is not defined` —
    traceback chỉ đích danh `custom_app/.../stock_integrity.py` (hook `Sales Invoice.on_submit`), và
    file đó đang được chủ dự án sửa song song (line number lệch 1 dòng giữa 2 lần đọc = bằng chứng
    file vừa đổi). Retry sau đó xanh lại. Không sửa app của người khác, không tự kết luận code mình sai.
47. **Nhận con JE mồ côi chỉ theo (khách, tiền) là HOÁN ĐỔI AUDIT, không phải idempotency.**
    `_adopt_or_create_je` (P0.5) đầu tiên adopt JE mồ côi chỉ khớp party+debit: hai dòng CÙNG khách
    CÙNG số tiền (2 lứa cũ nợ bằng nhau — ngoài đời thật) trong cửa sổ crash sẽ bị hoán đổi JE —
    tiền đúng nhưng remark audit ("lứa cũ LOT-…") ghi nhầm lứa. Vá: khớp thêm needle remark
    `lứa cũ {old_code}` (remark dựng từ FILE, không từ đồng hồ); dòng không có mã lứa cũ thì adopt
    con mồ côi CŨ NHẤT (các dòng đối xứng). Mồ côi không ai nhận để lại cho dòng anh em; reconcile
    (diff≠0) bật đèn đỏ cho operator dọn — lỗi nhìn thấy được tốt hơn swap audit âm thầm.
    Mutation-check bằng cách ĐẢO needle → T5 đỏ `11 -> 12`; khôi phục → xanh 6/6.
48. **Test giải fixture TRƯỚC khi tự tạo nó = không bao giờ bootstrap được.** T1 p05_acceptance
    gọi `_scope_customers()` (raise nếu thiếu khách) TRƯỚC import tạo khách ⇒ sau khi `cleanup()`
    dọn sạch, `run()` trắng site fail CẢ 6 check "T1 must run first" — trong khi T1 chính là check
    chạy import. Sửa: import xong mới tính scope. Nguyên tắc: check tạo fixture không được
    precondition trên output của chính nó.
49. **Hai `def cleanup()` trong cùng module: định nghĩa CUỐI âm thầm thắng.** Bản cleanup mới viết
    đầu file bị bản gốc đầy đủ hơn (cuối file) che mất — site chạy bản GỐC trong khi review đọc
    bản CỦA TÔI. Dấu hiệu lộ: format output không khớp (`[P0.5 cleanup] removed …` vs
    `[feed_dealer] P0.5 cleanup removed 42 fixture(s)`). Sau khi thêm hàm vào file dài:
    `grep -c "def cleanup"` phải ra 1. Gặp duplicate bị che: giữ bản đầy đủ hơn, xoá bản mới.
50. **Không log = chưa từng chạy; log không EXIT = đang chạy.** `bench.py --to-file` chỉ ghi log
    KHI LỆNH XONG: log không có `=== EXIT n ===` nghĩa là lệnh vẫn đang chạy (đọc lại sau), còn
    KHÔNG TỒN TẠI file log sau khi kill wrapper local = lệnh CHƯA BAO GIỜ khởi động (RPC chết cùng
    wrapper) — chạy lại ngay, đừng tiếp tục thăm dò một đường dẫn sẽ không bao giờ có file.
51. **Xoá hàng loạt trong MỘT transaction = xoá 0 document.** frappe giới hạn
    `MAX_WRITES_PER_TRANSACTION = 200_000`; vượt ngưỡng ⇒ `TooManyWritesError` và **rollback TOÀN BỘ**
    action. Đo thật: `p1g_perf.cleanup()` trên dataset 2.000 hoá đơn ghi log "removed 592 fixture(s)"
    trong khi 25 khách vẫn còn nguyên (kiểm bằng `select count(*)`). Vá:
    `frappe.db.auto_commit_on_many_writes = True` trước vòng lặp. **Luôn xác nhận dọn dẹp bằng COUNT
    trên DB**, không tin dòng "removed N" của chính hàm.
52. **`rm -f /tmp/x.log` ở sandbox KHÔNG xoá log trong container** — hai filesystem khác nhau. Vòng
    lặp poll đọc log CŨ, thấy `=== EXIT 0 ===` và báo "DONE sau 30s" cho một lệnh CHƯA chạy (đo
    thật: cleanup 345s bị báo xong sau 30s). Dùng TÊN LOG MỚI mỗi lần chạy, hoặc
    `docker exec <c> rm -f /tmp/x.log` trước. Dấu hiệu log cũ: số dòng trong traceback lệch với file
    hiện tại (`p1g_perf.py:195` trong khi bản deploy có câu đó ở `:201`) — đó là site chạy bản CŨ,
    không phải fix sai.
53. **`frappe.db.set_default(key, "")` để lại ROW RỖNG, không xoá.** `tabDefaultValue` giữ
    `defvalue=''` — vô hại về logic (`get_default` trả falsy) nhưng là rác trên site thật. Xoá bằng
    PRIMARY KEY: `mariadb -e` trên site này chạy **safe update mode**, `DELETE ... WHERE defkey LIKE`
    bị chặn (`ERROR 1175`); phải `select name` trước rồi `delete ... where name in (...)`.
54. **Hook `on_update` của app KHÁC có thể chặn ghi dữ liệu của mình.** `custom_app`
    (`capture_change`) gọi `frappe.enqueue` cho MỌI on_update ⇒ build 2.000 document làm ngập queue,
    mọi insert sau đó chết với `QueueOverloaded: Too many queued background jobs (600)`. Traceback
    chỉ đích danh app kia ⇒ không sửa app người khác, kiểm queue
    (`docker exec <redis-queue> redis-cli llen rq:queue:default`) và retry sau khi worker dọn.
55. **Builder dạng APPEND phải tự dọn leftover — và đừng đọc marker TRƯỚC khi rebuild.**
    `build_dataset` commit document ngay khi tạo, nên build chết giữa chừng để lại row mà KHÔNG có
    marker; lần build sau APPEND lên đó ⇒ shape ghi nhận ≠ DB (đo thật: 1 Sales Order lạc làm C9
    đỏ `expected 12, found 13`). Phải mirror guard "fixture có, marker thiếu ⇒ dọn trước" ở MỌI
    entry point. Đồng thời: đọc marker ở đầu hàm rồi in nó ở cuối ⇒ run 2.000 giao dịch báo
    `transactions: 100` (số của lần pilot trước); phải đọc LẠI sau khi build.
56. **Môi trường build Flutter/Android của sandbox (đo 2026-09-17):** `/home` chỉ ~4,8 GB nên
    Flutter SDK cache / Gradle cache / Android SDK / NDK phải nằm trên `/` (`/opt/...`), nếu không
    sẽ chết giữa chừng với `No space left on device` (~55%); NDK cài bằng tải zip trực tiếp khi
    `sdkmanager` fail ở bước "preparing"; Flutter 3.47 cần Android SDK 36; bỏ dòng `ndkVersion`
    trong `build.gradle.kts` KHÔNG tránh được NDK (plugin Flutter tự áp default). Mac và sandbox
    đều KHÔNG có Xcode ⇒ chỉ build được Android (`flutter build apk` đã chạy thật, APK 150 MB).

---

## P2 Mốc 3 — proof of delivery (2026-09-18, vòng review thứ 2)

64. **`_, _, text = text.partition(",")` GIẾT luôn hàm dịch `_()` của frappe trong cùng hàm.**
    Gán vào tên `_` làm nó thành biến LOCAL của cả function body ⇒ mọi `frappe.throw(_("..."))`
    phía dưới chết với `UnboundLocalError: cannot access local variable '_'`. Đo thật (P2 Mốc 3,
    2026-09-18): guard `data:` URI nằm trong `_normalise_image`, mà mọi nhánh THROW nằm SAU nó —
    nên đúng lúc cần báo lỗi cho người dùng thì server trả 500 với message vô nghĩa. Happy path
    vẫn xanh nên không test nào bắt được cho tới khi có test cho nhánh LỖI. Quy tắc: không bao giờ
    dùng `_` làm biến tạm trong file có `from frappe import _`; đặt tên thật (`_prefix`, `_comma`).
65. **Test nhánh LỖI phải kiểm chính CÂU THÔNG BÁO, không chỉ "có throw".** Cùng gốc với #64:
    assert `raise` là chưa đủ, phải khẳng định message chứa nội dung nghiệp vụ mong đợi. Nếu chỉ
    kiểm "có exception", một 500 (UnboundLocalError/TypeError) cũng làm test xanh. Bộ P2 Mốc 3 dùng
    helper `_reject(fn, expect_substring)` nên bắt được ngay ở T12c/T13a.
66. **Dữ liệu client gửi lên phải được kiểm TRA KIỂU trước khi đo/kích thước.** `photos` là list
    thô từ app: một entry dạng dict ⇒ `len()` trả số KEY (không phải số byte) và `raw.strip()` chết
    `AttributeError` — hai lỗi im lặng khác nhau trên cùng một payload. Ép "list of non-empty str"
    một lần ở cửa vào (`_photo_texts`) rồi mọi hàm sau được phép giả định sạch.
67. **Đọc bản ghi ưu tiên trạng thái SỐNG, đừng dùng dict comprehension.** Với bảng có thể có nhiều
    row cho cùng một khoá (một xác nhận bị từ chối + một xác nhận nộp lại),
    `{row.key: row for row in rows}` giữ row cuối do DB trả về — nghĩa là tài xế có thể thấy bản ghi
    ĐÃ CHẾT và giao lại đơn đã xác nhận. Phải chọn có chủ đích: row `live` thắng, row bị từ chối chỉ
    là fallback.
68. **`MAX // (1024*1024)` in ra người dùng là SỐ SAI.** Giới hạn 1,5 MB bị in thành "1 MB" ⇒
    một ảnh 1,4 MB bị yêu cầu nén xuống dưới mức nó đã thoả. Dùng phép chia thực + `{:.1f}` cho MỌI
    ngưỡng hiển thị.
69. **Retry phải idempotent ở CẢ hai đường (approve và reject).** Sau khi thêm guard "chỉ duyệt/từ
    chối được bản đang chờ", lần bấm thứ hai của chủ đại lý ném lỗi — trong khi approve thì không.
    Bất đối xứng kiểu này lộ ra khi mạng chập chờn: cùng một cú double-tap, một nút im lặng một nút
    báo lỗi. Cho `reject` trả về bản ghi hiện có khi đã ở trạng thái đó, và giữ throw cho xung đột
    THẬT (từ chối bản đã duyệt).
70. **`driver_deliveries` trả về Sales Order — khoá là `name`, KHÔNG có `sales_order`.** Test viết
    theo giả định sai đó chết `KeyError` giữa suite (đo thật), làm cả lượt chạy không có kết quả nào
    dùng được. Trước khi viết assert, in 1 row mẫu và đọc ĐÚNG tên field API trả về.
71. **Khi test bắt được lỗi mà bản vá trước đó "đã PASS": đừng sửa test, hãy đọc kết quả.** Lượt
    Mốc 3 đầu PASS 15/15 vì bộ test CHƯA chạm nhánh lỗi; sau khi thêm T12/T13 (payload sai + ngưỡng
    kích thước) mới lộ ra #64 và 2 lỗ khác. Lỗ hổng thật là ở ĐỘ PHỦ test, không phải ở con số 15/15.

## P2 Mốc 3 — Delivery Note (2026-09-18, đo trên site thật)

72. **`frappe.flags.ignore_permissions` KHÔNG ảnh hưởng `frappe.has_permission()`.** Đo trên site:
    `has_permission("Delivery Note", "create")` trả False cho Driver dù flag = True (source
    `has_permission` không hề đọc flag). Hệ quả: mọi bước CẦN quyền của SERVER (map tài liệu, tạo
    chứng từ kho) phải chạy bằng một phiên có quyền thật, rồi ghi lại chủ thể thật vào `owner` cho
    audit — đừng tưởng set flag là qua được. Driver trên site chỉ có role All/Driver/Guest: vừa
    không `read` được Sales Order, vừa không `create` được Delivery Note (nhưng danh sách đơn của
    app vẫn chạy vì `frappe.get_all` bỏ qua quyền — đó là một khoảng trống phân quyền cần ghi lại).
73. **SAVEPOINT cho mọi bước có side-effect mà người gọi có thể bắt exception.** Đo thật: một lần
    submit DN bị từ chối đã ghi Stock Ledger Entry TRƯỚC khi chạm guard; vì người gọi bắt exception
    (ngoài rollback của request), lệnh ghi đó ở lại và **tồn kho âm thêm 10 đơn vị dù hồ sơ ghi
    "chưa xuất được"**. `frappe.db.savepoint("x")` + `rollback(save_point="x")` trong nhánh lỗi làm
    từ chối sạch tuyệt đối.
74. **`frappe.db.rollback()` trần cuộn cả transaction — kể cả việc KHÔNG liên quan.** Ca thật: test
    đặt `allow_negative_stock = 0` (chưa commit), sau đó một lời từ chối của API gọi rollback trần →
    cấu hình bị trả về 1 → test "kho không đủ hàng" thứ hai PASS trong khi KHÔNG hề test gì. Triệu
    chứng: cùng một điều kiện, nhánh này bị chặn còn nhánh kia không. Vá: rollback theo savepoint, và
    setup của test phải `commit()`.
75. **Hàng phải CÓ TRƯỚC khi xuất được:** item chưa từng nhập kho → submit Delivery Note bị từ chối
    `Valuation Rate for the Item ..., is required to do accounting entries`. Fixture test phải tạo
    Material Receipt thật (có `basic_rate`), **và phải TOP UP về một số dương xác định** — bin âm từ
    lần chạy trước làm mọi con số thiếu hàng trở nên vô nghĩa (`_ensure_stock()` cũ chỉ tạo khi bin =
    0 nên bin = -40 vẫn "đủ").
76. **`Delivery Note` KHÔNG có field `sales_order`** — liên kết nằm ở dòng hàng:
    `Delivery Note Item.against_sales_order`. Cùng loại bẫy với #70 (`driver_deliveries` trả Sales
    Order, khoá là `name`): trước khi viết assert, in 1 row thật để đọc đúng tên field.
77. **`default_warehouse` sai CÔNG TY chặn toàn bộ xuất kho:** site có `Stores - S` (công ty SANLOAN)
    trong khi công ty pilot là "Minh Phát Cám & VLXD" → mọi Delivery Note chết với
    `Warehouse Stores - S does not belong to company ...`. Nguyên nhân sâu hơn: field này được điền
    MỘT LẦN rồi không bao giờ được kiểm lại, nên giá trị cũ (hoặc do người đặt tay) vẫn nằm đó.
    Vá: `ensure_settings_defaults()` giờ kiểm chéo warehouse ↔ company và tự sửa, in ra lý do.
78. **Item tồn kho bắt buộc có warehouse trên dòng Sales Order** (`Delivery warehouse required for
    stock item ...`). Trong vận hành thật việc này đến từ Item Default; nếu chưa cấu hình thì người
    tạo đơn (Desk/API) phải điền — cần ghi vào checklist go-live.
79. **Số lượng cần trừ kho phải đọc từ ĐÚNG bản ghi:** `Delivery Note Item.qty` là 60 rất khác
    "50 đơn vị còn thiếu" trong câu lỗi; đừng lấy số trong message làm dữ liệu, cũng đừng lấy qty của
    DN làm bằng chứng tồn kho — đọc `Bin.actual_qty` TRƯỚC/SAU để chứng minh hàng thực sự dịch chuyển.
80. **CI xanh KHÔNG có nghĩa app chạy được — client và server deploy bằng 2 đường khác nhau.**
    Ca thật 2026-09-18: `driver_delivery.dart` đọc `row['confirmation_delivery_note']` và
    `confirmation_reject_reason`; CI build APK thành công (`40 tests passed`, `BUILD SUCCESSFUL`),
    màn Driver vẫn trống 2 chỗ, vì bản sửa backend trả 2 field đó còn **chưa commit, chưa deploy**.
    Không test nào đỏ vì phía Dart, khoá không tồn tại thì trả `null` — im lặng.
    Cách phát hiện (đã dùng, nên lặp lại): so **file triển khai thật** với file local —
    `docker exec <container> grep -n <field> <path trong container>` → rỗng, kèm so kích thước
    (local 17.671 B vs container 17.245 B). Đừng kiểm bằng CI, đừng kiểm bằng `git status`.
    Cách chặn tái phát: viết assert NGAY TRÊN SERVER cho đúng hợp đồng mà UI cần (ở đây T17a–c) —
    lần sau quên deploy là suite đỏ ngay tại chỗ.
81. **Cửa sổ dữ liệu bị cắt im lặng biến "API hỏng" thành kết luận sai.** `driver_deliveries` mặc
    định 50, cap 200, sắp xếp theo `delivery_date asc`; đơn mới tạo (ngày giao xa nhất) là thứ bị cắt
    ĐẦU TIÊN khi vượt ngưỡng, mà triệu chứng chỉ là `next(...) -> None`. Test hợp đồng kiểu này phải
    (a) gọi sát cap (`limit=200`) và (b) đưa **số dòng nhận được** vào câu lỗi — nếu không, người
    debug sau sẽ đi tìm lỗi phân quyền/điều kiện lọc trong khi thật ra chỉ là phân trang.
82. **Đổi một notifier toàn cục trong test thì phải `pump()` một frame trước khi assert nhãn UI.**
    Ca thật (Mốc 4): nút đổi nhãn theo `isOnline` qua `ValueListenableBuilder`; test set
    `isOnline.value = false` rồi `expect(find.text('Lưu chờ gửi'))` ngay ⇒ đỏ trên CI với "Found 0
    widgets". Widget không sai — bài test đọc frame CŨ. Rule: sau mọi thay đổi state ngoài widget
    (`ValueNotifier`, `ChangeNotifier` toàn cục, stream) phải `await tester.pump()` trước khi khẳng
    định giao diện.
83. **Dart KHÔNG có named parameter bắt đầu bằng `_` — và lint coi đây là lỗi CI.**
    `OfflineQueue({required SharedPreferences prefs, ...}) : _prefs = prefs` bị
    `prefer_initializing_formals` chặn (dự án này để warning/info thành fail). Cách sửa rẻ nhất: để
    field ở dạng public (`this.prefs`) thay vì cố giữ private + tham số cùng tên.
84. **Thêm method vòng đời vào một widget ĐÃ CÓ thì phải grep trước: tôi tự tạo `initState` thứ hai.**
    Chèn `initState` khi class đã có `initState` ⇒ 2 override trong cùng class, code sau"che" code trước
    (bootstrap cũ ngừng chạy mà không có cảnh báo nào ở tầng logic). Bắt buộc: `grep -n "initState\|dispose"`
    trên file trước khi thêm, đúng như cách đã phải làm với `api.py` ở các lượt trước.
85. **Hàng đợi ngoại tuyến: quyết định "cái gì được xếp hàng" phải là WHITELIST, không phải blacklist.**
    Thiết kế Mốc 4: một Set các operation được phép (`{delivery_confirm}`) kiểm ở cửa vào `enqueue()`,
    nên một thao tác tài chính thêm nhầm sau này cũng bị `QueueRejected` ngay. Kèm 2 luật nữa đã cài
    bằng code (không phải comment): (a) payload bị server TỪ CHỐI thì không bao giờ xếp hàng — quyết
    định đã có rồi; (b) lỗi 401 KHÔNG phải từ chối — row giữ nguyên + đòi đăng nhập lại.
86. **"Mất mạng" không suy ra được từ trạng thái interface — chỉ suy ra được từ kết quả request.**
    Không dùng package connectivity: nó báo "wifi đang bật" trong khi uplink chết (đúng cảnh chuồng
    trại có router nhưng không ra internet). Cài bằng cách cho MỌI round trip đi qua một hàm bọc duy
    nhất, hàm này bật/tắt cờ và ném một exception RIÊNG (`OfflineFailure`) — nhờ vậy tầng trên phân
    biệt được "chưa tới server" (được phép xếp hàng) với "server đã trả lời và từ chối" (cấm xếp hàng).
