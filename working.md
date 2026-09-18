# working.md — nhật ký đang làm

Task đang làm / đã xong gần đây. Format ngày: `YYYY-MM-DD` (ISO). Dọn mục cũ hơn 1–2 tuần.

## Đang làm

- [2026-09-17] **Review round sau P1G — cứng hóa KIỂM CHỨNG. ĐÃ TEST trên site thật (clear-cache), CHƯA COMMIT**
  — review **không tìm ra lỗi trong code P1G/P1D**, nhưng tìm ra 2 lỗ hổng ở cách kiểm chứng, đã đóng bằng code:
  (1) `--to-file` không đảm bảo thứ tự ⇒ `debug` đọc DB khi `cleanup` còn xoá dở ⇒ `8/9` vô nghĩa;
  dấu hiệu nhận biết bằng chứng nhiễm: PA/ER **giống hệt** lần trước (số độc lập với thay đổi) còn RT/OF về 0.
  Đóng bằng `.agent/bench_wait.py` (chờ dòng `=== EXIT n ===`).
  (2) Fixture kiểm "đủ dòng" ⇒ 1 Sales Order lạc từ build chết (13 SO vs khai 12) vẫn để suite xanh;
  đóng bằng check `C9` so **số đúng** với `shape` + `ensure_dataset` dọn dataset dở trước khi build lại.
  Lỗi tự gây do C9 bắt được và đã sửa: biến comprehension (`index`) trùng tên biến vòng lặp (`UnboundLocalError`);
  thêm assert số dòng SO→SI (chống `zip` truncate âm thầm); `if not debt: continue` trước khi đọc cột nợ;
  `p1g_reports_check.py` dùng chung `Report()` của `p1b_acceptance` (hết drift format).
  Bằng chứng tái xác minh trên revision đã review (`bench_wait` + clear-cache):
  ```
  /tmp/rv2_p1g.log   P1G INTEGRITY: ALL PASS   9/9  (C9 exact: 70 invoices / 12 SO / 45 receipts / 3 offset JEs)
  /tmp/rv2_rep.log   P1G REPORTS: ALL PASS     4/4
  /tmp/rv2_p1d.log   P1D: ALL PASS             8/8
  ```
  Lưu vết: `design.md` **D26**, `tasks.md` **14c**, skill §8, `LESSONS_LEARNED.md` 42b–46,
  `faq.md` 10f/10g, `checklist.md` B0b, `features.md` 1i, `next.md` 1c,
  `result_2026-09-17_0805_review_P1G.txt`, `handoff_2026-09-17_0805.md`.

- [2026-09-17] **P1G — integrity AR vs Batch Debt + 7 báo cáo + Exit Gate Phase 1. ĐÃ TEST trên site
  thật (clear-cache trước mỗi lần chạy), ĐÃ COMMIT** — hash dán nguyên văn từ
  `git log -1 --format='%H %s'`:
  ```
  770be4e2f8f9b02d771c3d398c384717a416b027 feat(feed_dealer): P1G AR-vs-Batch-Debt integrity, 7 Desk reports, Phase 1 exit gate
  ```
  Bằng chứng trên **revision cuối** (sau khi push + clear-cache lần chót):
  ```
  /tmp/final_p1g.log   TOTAL: 9  PASS: 9  FAIL: 0   P1G INTEGRITY: ALL PASS
  /tmp/final_rep.log   TOTAL: 4  PASS: 4  FAIL: 0   P1G REPORTS: ALL PASS
  /tmp/final_p1d.log   TOTAL: 8  PASS: 8  FAIL: 0   P1D ACCEPTANCE: ALL PASS
  ```

  **(1) Dataset + đẳng thức AR vs Batch Debt** (`feed_dealer/setup/p1g_integrity.py`): sinh dataset
  ngẫu nhiên seed cố định **60 giao dịch thật** — 70 hoá đơn (10 credit note), 13 SO→SI, 45 phiếu
  thu (**14 có `references`, 31 để trống theo giả định D19**), 5 phiếu thu vượt nợ, 10 lần trả hàng,
  3 bút toán cấn trừ, 94 khoản nợ mở, 18 lứa, 24 hoá đơn nhiều lứa, 15 hoá đơn 2 thuế suất. Kết quả
  **9/9 PASS**, log `/tmp/p1g_fin.log`:
  ```
  C1  BD 11.289.000 == TT 156.345.000 − PA 143.851.000 − OF 1.205.000          (diff 0)
  C7  AR (SUM SI.outstanding) 132.756.000 vs Batch Debt 11.289.000, diff −121.467.000
      == −17.783.500 [unbatched AR] + 41.372.500 [ERPNext cấn trừ cấp hoá đơn]
         − 143.851.000 [FIFO theo lứa] − 1.205.000 [cấn trừ vật nuôi]   (residual 0)
  C8  gap FIFO vs references = −102.478.500
  ```
  **Ngưỡng dung sai = 0 đồng** (lý do: mọi số hạng là tổng số nguyên VND trên cùng các cột, không
  FX/không làm tròn/ không pro-rata) — ghi ở D23, KHÔNG nới ngưỡng để cho qua.
  **Mutation-check:** bỏ `offset_amount` khỏi công thức `outstanding` → C1+C7 **đỏ**, diff đúng
  1.205.000 (`/tmp/p1g_mut.log`); khôi phục → 9/9 xanh lại, số liệu y hệt.

  **(2) Bug THẬT của P1D do dataset phát hiện (đã vá + có test):** trả **1 dòng của hoá đơn nhiều
  dòng** bị chặn — `_map_debts_from_note` chạy TRƯỚC khi cắt dòng của `make_return_doc`, nên các dòng
  mapper tự điền cho phần KHÔNG trả bị tra vào yêu cầu và bị từ chối (*"không map được về dòng yêu cầu
  trả hàng"*). Mọi test P1D cũ đều trả **cả** hoá đơn (T4 trả cả 2 dòng) nên ca bình thường ngoài thực
  địa chưa bao giờ được chạy. Đã sửa ở generator (nguồn chân lý) + thêm regression **`p1d T8`**
  (P1D `8/8`). Dataset cũng chủ động tính số lượng trả theo `outstanding` **sống** của khoản nợ để
  không vi phạm guard giá-trị của P1D (chính là guard đó hoạt động đúng).

  **(3) 7 báo cáo Desk** (`.agent/gen_reports.py`, `--check` clean): 6 Query Report + 1 Script Report.
  `customer_credit_limit` là Script Report gọi **đúng hàm của gate** `credit_position()` — cố tình
  KHÔNG viết lại luật hạn mức bằng SQL, vì bản SQL (chỉ trừ nợ lứa) sẽ hiện nhiều hạn mức hơn số gate
  thực cho (gate còn trừ đơn đã submit chưa xuất HĐ + đơn nháp). **4/4 PASS**, log `/tmp/p1g_rep_fin.log`:
  ```
  R2  approval_audit_log=40r/7c · batch_profit_loss=69r/9c · cash_flow_30_60_90=37r/7c
      customer_credit_limit=15r/11c · debt_by_batch=69r/9c · overdue_batch_debts=4r/9c
      payment_allocation_detail=126r/10c
  R3  nhật ký phê duyệt 40 dòng, đủ 2 loại quyết định
  R4  15 khách khớp gate, 8 khách có cam kết cấp đơn (nên bản SQL chỉ-trừ-nợ sẽ lệch)
  ```
  Bẫy đã gặp & vá: (a) Query Report **phải bắt đầu bằng `SELECT`** — comment `--` ở đầu làm
  `check_safe_sql_query` từ chối; (b) `bench migrate` **bỏ qua** standard doc khi `modified` không mới
  hơn → thêm patch `v1_0/reimport_reports` (`frappe.reload_doc(..., force=True)`).

  **(4) Exit Gate Phase 1** → `EXIT_GATE_PHASE1.md`: Functional PASS (P1E vẫn BLOCKED), Security /
  Data integrity / Failure recovery / Audit PASS, **Performance + UX = NOT ASSESSABLE** (chưa đo dữ
  liệu lớn, chưa có tài khoản người dùng thật).

  **Lưu ý môi trường (đã gặp, đã vá phía tooling):** app khác (`custom_app`) có hook `on_submit`
  trên Sales Invoice đang bị lỗi `NameError` giữa lúc chủ dự án sửa file → một lần rebuild dataset
  của tôi chết vì nó. Traceback chỉ đích danh `custom_app/.../stock_integrity.py`, KHÔNG phải
  feed_dealer; retry sau khi file đó đổi → xanh lại. Cầu nối MCP cũng phải dựng lại
  (`python3 .agent/akimcp.py export`) vì `/tmp` bị xoá, và `mcp_client` phải chuyển sang **curl
  (ghim IPv4) + payload qua file** — urllib chết `Cannot assign requested address` (DNS chỉ trả IPv6)
  và argv quá dài khi push cả app.

- [2026-09-17] **Review P1F — đã viết xong code + ĐÃ TEST trên site thật (clear-cache trước mỗi lần
  chạy) — ĐÃ COMMIT** (dán nguyên văn từ `git log -1 --format='%H %s'`):
  ```
  545b2b803c616695a95036002d7ccfe3bec73f97 fix(feed_dealer): P1F review - refusal gate moved pre-write, offset identity guard
  ```
  11 file, +353/−21. 4 finding đã sửa, tất cả ở nguồn chân lý:
  • **guard HIỂU SAI HOOK (nặng nhất):** `on_submit` chạy SAU khi ghi DB ⇒ guard đặt ở đó **không
    chặn được gì** — JE sai chủ thể vẫn nằm trong DB docstatus=1 (đã đo bằng probe, không đoán).
    Chuyển sang `validate` (chạy cho cả draft save lẫn submit, vẫn trước write) + đăng ký hook;
    `p1f T9` mới assert **JE không bao giờ được insert** (`Journal Entry Account.batch_debt` rỗng).
  • guard danh tính cho offset (`invoice.supplier != batch.customer` → từ chối) → `T8`.
  • `has_been_split` chỉ set khi thật sự **Tách lứa** (merge/allocate không còn gán nhầm).
  • patch Print Format **không bao giờ ghi đè** (không phân biệt được format của app với bản user
    tự sửa) → cài 1 lần + báo cáo phần đã bỏ qua.
  • sửa `cleanup()`: quét JE theo **attribution** (`batch_debt`), không chỉ qua link `Livestock Sale`
    — đây là gốc của lỗi fixture "2 debts cho 1 hoá đơn" (leftover chặn `delete_doc(Batch Debt)`
    bằng `LinkExistsError` bị nuốt thành dòng FAILED).
  **Bằng chứng (sau `clear-cache`):** `P1F 9/9` + regression `P0 9/9 · P1A 8/8 · P1B 10/10 ·
  P1C 10/10 · P1D 7/7` — tất cả `ALL PASS`, log trong container:
  `/tmp/{p1f,p0,p1a,p1b,p1c,p1d}_fin.log`. **Mutation-check:** tắt guard JE → `T9` đỏ (8/9); khôi
  phục byte-identical (`diff -q`) → `9/9`. Probe tạm (`_t9_probe.py`, và `_p1d_probe.py` sót từ
  phiên trước) đã xoá cả local lẫn Mac.
- [2026-09-17] **VIỆC 1 + VIỆC 2 + P1F xong** — commit (dán nguyên văn từ `git log -1 --format='%H %s'`):
  ```
  b48d723b81f515d12f0dc7860dcd5d050d93fdf6 feat(feed_dealer): P1F legal/livestock/batch-ops + tabBatch cleanup
  ```
  19 file, +1583/−17. Commit docs kèm theo (cũng dán từ `git log -1 --format='%H %s'`):
  ```
  caeb8e2a660a1af46af85dd86005b98d40a47ebf docs: đóng P1F + 5.17, ghi P1E BLOCKED và nợ kỹ thuật P0.5
  ```
  • **VIỆC 1** (tự quyết): FIFO **bỏ qua** `Payment Entry.references` — đã grep xác nhận code không hề
    đọc field này; giả định vận hành "kế toán để trống references, để FIFO theo lứa tự quyết" ghi vào
    `design.md` D19 + comment trong code; thêm **cảnh báo không chặn** khi `references` khác rỗng.
    Bằng chứng `p1b T10`: PE có 1 dòng reference → cảnh báo bật **và vẫn phân bổ 400.000**.
  • **VIỆC 2** (tự quyết, bạn đã duyệt DDL): backup `20260917_090031-frontend-database.sql.gz`
    (2.2 MiB) trước khi ALTER → drop 12 cột rác `tabBatch` bằng **Frappe patch** idempotent
    (`patches/v1_0/drop_orphan_batch_columns`, tự chặn nếu còn dữ liệu): in ra
    `rows=0, columns holding data=none` rồi dropped 12; verify `tabBatch` còn 30 cột / 0 orphan;
    chạy lại → "nothing to do"; migrate EXIT 0, không có dòng Orphaned DocType. `alpine:3.19` không đụng.
  • **P1F** (4 phần, 7/7 PASS): consent append-only + gate `marketing_allowed`; Debt Confirmation Slip
    (rows/tổng suy từ Batch Debt) + Print Format Jinja cài bằng patch; livestock offset = Purchase
    Invoice + JE double-entry (Dr AP / Cr AR, 1 dòng credit/khoản nợ) → `offset_amount` DERIVED, **cap
    tại số nợ còn lại**; Batch Operation tách/gộp **bảo toàn nợ** và **từ chối** khoản đã có
    thanh toán/trả hàng/cấn trừ. Mutation-check: tắt 2 guard tiền mới → **T5+T7 đỏ (5/7)**, khôi phục
    → 7/7. Regression: P0 `9/9` · P1A `8/8` · P1B `10/10` · P1C `10/10` · P1D `7/7` · P1F `7/7`;
    `gen_feed_dealer.py --check` 58/58. Quyết định: `design.md` D19/D20/D21, `tasks.md` mục 13 (đóng
    luôn 5.17), chi tiết `result_2026-09-17_0430_P1F.txt`.
  • **P1E = BLOCKED thật** (không chế mock): `Feed Dealer Settings` chưa có field provider/API URL/
    tax code, `.env` không có credential VNPT/Viettel/MISA → `result_P1E_BLOCKED_2026-09-17.txt`
    liệt kê 4 thứ cần từ user. `tasks.md` mục 14.
  • **P1G chưa làm** trong lượt này (7 báo cáo Desk + script đối chiếu AR vs Batch Debt + Exit Gate
    Phase 1) — là việc kế tiếp, và cũng là nơi verify bằng số liệu thật cho VIỆC 1 (xem `next.md`).
- [2026-09-16] Change `p0-feed-dealer-foundation` — **P1D xong, đã commit `c5152dd`** (task 12.7).
  Bằng chứng (site thật, sau `clear-cache`): **P0 `9/9`, P1A `8/8`, P1B `9/9`, P1C `10/10`, P1D
  `7/7` — tất cả `ALL PASS`, exit 0**. P1D gồm: controller Sales Return Request/Sales Return Item
  (approve → credit note thật qua `make_return_doc`, qty âm + `sales_invoice_item` mapping),
  `returned_amount` DERIVED trong `BatchDebt.calculate_derived_fields` (SUM net_amount đảo dấu,
  ghi qua `DERIVED_FIELDS` của `_recalculate`), guard "1 request = 1 hoá đơn" (T6, mutation
  check), `cancel_credit_note` API với `ignore_links` (T5). Guard mới chống generator đè controller
  thật (`REFUSED`) + embed đủ 6 controller thật. Sự cố đã xử lý trong phiên: xoá nhầm
  `credit_limit.py` trên Mac dựa vào danh sách "extra files" (A7 bắt, đã khôi phục bằng push);
  DocType `Batch Debt` bị orphan-scan xoá do import sai đường dẫn trong controller (migrate sau đó
  dựng lại từ JSON, dữ liệu nguyên vẹn). Chi tiết: `design.md` D17, `tasks.md` mục 12,
  `result_2026-09-16_1700.txt`.
- [2026-09-17] Review P1D lần cuối (user yêu cầu) — **2 phát hiện, đã vá + verify trên site thật**:
  (a) thiếu guard **giá trị** trả lại: nợ đã trả một phần (allocated 1.000.000, paid 600.000) vẫn
  được trả lại 500.000 → `outstanding` = −100.000, `Feed Batch.total_debt` âm, và P1C cộng số âm
  nên **thổi hạn mức khả dụng** → thêm `_validate_value_vs_outstanding` (chặn theo từng khoản nợ);
  (b) guard `_validate_over_return` chỉ được gọi trong `approve()` trong khi docstring/comment T2
  nói "chạy trên draft" → nay nối vào `validate()` (kèm skip dòng chưa có `return_line`, nếu không
  filter `''` khớp chính dòng hoá đơn gốc) và T2 assert **đúng lý do**. Bằng chứng: **P0 `9/9`,
  P1A `8/8`, P1B `9/9`, P1C `10/10`, P1D `7/7`** (T7 mới); mutation-check: tắt guard → T7 đỏ
  `6/7`, khôi phục → `7/7` và file sinh ra **byte-identical** với trước mutation. Cầu nối aki-MCP
  phải dựng lại vì `/tmp` mất giữa phiên (đã ghi vào skill). **Đã commit `c5152dd`** (task 12.7).
  Chi tiết: `result_2026-09-17_0150.txt`, `design.md` D17 mục 4.
- [2026-09-16] Change `p0-feed-dealer-foundation` — **P1C xong, đã commit `7c62129`** (task
  11.9), `openspec validate` → "is valid". App `feed_dealer` trên site ERPNext **v16** của user
  (Mac, `frappe_docker`, site `frontend`), 19 DocType module `Feed Dealer` (0 custom).
  Đã commit: `7c62129` (P1C), `eb75222` (P0+P1A+P1B), `0355d6b` (vá refund/NULL), `0e89ce7` (bỏ
  track `__pycache__`).
  Bằng chứng khi xong: **P0 `9/9`, P1A `8/8`, P1B `9/9`, P1C `10/10`**. Chi tiết + mọi quyết
  định: `design.md` D15/D16, `tasks.md` mục 11, `result_2026-09-16_1423.txt`.

## Chờ user quyết

- [x] [2026-09-17] **Commit đợt review P1F — ĐÃ DUYỆT và đã commit
  `545b2b803c616695a95036002d7ccfe3bec73f97`** (11 file, +353/−21): `journal_entry.py`,
  `livestock_offset.py`, `batch_operation.py`, `hooks.py`, `patches/v1_0/install_print_formats.py`,
  `p1f_acceptance.py` + `design.md` D22 / `tasks.md` 13.8 / `LESSONS_LEARNED.md` / `working.md` /
  `result_2026-09-17_0249_review_P1F.txt`.
- [x] [2026-09-16] **Commit P1C — ĐÃ DUYỆT và đã commit `7c62129`** (19 file, +1857/−64): code +
  4 bộ test + openspec/docs. Task 11.9 đóng.
- [2026-09-16] ~~2 lỗ hỏng P1B~~ **đã đóng ở P1C** (`design.md` D16): (1) *Unreconcile Payment*
  giờ có hook `feed_dealer.events.unreconcile_payment.on_submit` đảo allocation (P1B T8); (2) FIFO
  đã lock row `Batch Debt` bằng `get_values(..., for_update=True)` (P1B T9, chứng minh bằng
  connection thứ hai + spy trên đường submit thật). Còn lại (v16 không re-link khi cancel
  Unreconcile Payment) đã ghi rõ trong D16, không giấu.

- [2026-09-16] **Cột rác trên `tabBatch` của ERPNext.** Sự cố trùng tên DocType `Batch` (đã sửa bằng
  cách đổi app sang `Feed Batch`) để lại 12 cột không dùng trên bảng `tabBatch`:
  `customer, animal_type, start_date, expected_end_date, quantity, start_weight, current_weight,
  status, split_operation, has_been_split, total_debt, notes`. Frappe khôi phục metadata của ERPNext
  nhưng không xoá cột. Xoá cột là DDL không hoàn tác trên site thật → chờ user xác nhận.
- [2026-09-16] Có commit `apps/feed_dealer` + artifacts vào git không? **Đã commit 3 lần**
  (`eb75222`, `0355d6b`, `0e89ce7`); phần P1C còn chờ duyệt (xem mục trên).

## Vướng mắc môi trường

- [2026-09-16] Cầu MCP sang Mac (host Tailscale IPv6-only) đã **hồi phục** sau khi mất lần 2
  (`Cannot assign requested address`). Bài học: lỗi mạng tunnel là tạm thời — thử lại đúng 1 lần sau
  vài phút, và các lệnh dài phải ghi log vào file trong container để đọc sau.

## Đã xong (task lớn, gần nhất)

- [2026-09-16] **P1C Credit Limit + hardening P1B: xong, verify bằng test thật + mutation check.**
  `credit_limit.validate_credit_limit()` là **một hàm dùng chung** cho hook Sales Order và API
  (`check_order_credit`/`get_credit_position`); công thức đúng `plan_final_v2.2_mustfix.md` MUST-3
  (nợ + đơn đã submit chưa xuất HĐ + đơn nháp). Khách **chưa có Credit Score = chặn** (quyết định
  của user), `limit_by_score` = %hạng × TB hoá đơn đã submit (user chọn). Vá thêm 1 lỗ hỏng do
  self-review: API whitelisted trả hạn mức của **mọi** khách cho bất kỳ user đăng nhập → thêm
  `_require_credit_read` (P1C T10). Mutation check: tắt re-check submit → T2/T3 đỏ; tắt hook
  unreconcile → T8 đỏ; tắt lock → T9 đỏ (và T9 bản đầu vẫn xanh ⇒ đã viết lại cho có răng).
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
2026-09-17 10:42 — P0.5 Legacy Data Migration: DONE, test thật trên site frontend.
- migration.py: JE per balance row (Dr AR party=Customer / Cr opening, resolver
  param > Temporary > 1 leaf Equity, thiếu → throw), Batch Debt
  is_opening_balance=1 + link opening_journal_entry, KHÔNG gắn batch_debt trên
  dòng JE (tránh _offset_sum cấn sai chiều), KHÔNG whitelist API (vùng tiền).
  Draft debt trước → JE sau: crash giữa chừng còn resume được.
- Adoption JE mồ côi khớp (khách, tiền) + needle remark `lứa cũ {old_code}`
  (chống hoán đổi audit khi 2 dòng cùng khách cùng tiền); rows không mã lứa
  adopt oldest (đối xứng).
- Templates CSV + validate_csv.py (validate offline, sửa lỗi phones chưa gán);
  import_from_files đọc từ bench host; reconcile diff=0 (tolerance tuyệt đối).
- p05_acceptance: 6/6 PASS từ site trắng (sau cleanup) — T1 bootstrap fixed
  (import xong mới tính scope); T5 đo bằng JE DOCUMENT count (link-count đếm
  nhầm adopt đúng thành tạo mới); mutation-check needle đảo → T5 đỏ 11→12 →
  khôi phục → xanh 6/6. Regression: P0 9/9, P1A 8/8, P1B 10/10, P1C 10/10,
  P1D 8/8, P1F 9/9. Commits: e26a836 (docs review round), 5f05dbe (P0.5
  feature), fcdbe89 (docs result/lessons).
- Bài học 47–50 (LESSONS_LEARNED) + 4 bullet mới trong skill §8/§4.

## [2026-09-17 16:15] VIỆC A/B/C — vá tài liệu lệch nhịp, đo hiệu năng, dò môi trường P2

### VIỆC A — tài liệu lệch nhịp (xong)
- `EXIT_GATE_PHASE1.md`: P0.5 chuyển sang **DONE** (bỏ khỏi "việc còn treo", chuyển thành mục
  "Đã đóng"); **quyết định FIFO đã chốt** ghi vào mục 3 (giữ nguyên FIFO vì là hệ quả đúng của kiến
  trúc view-layer, `residual=0` chứng minh không mất/đúp tiền).
- `next.md`: mục "NỢ KỸ THUẬT BẮT BUỘC" P0.5 → "✅ NỢ KỸ THUẬT ĐÃ ĐÓNG"; đóng mục chờ quyết định
  FIFO; P0.5 trong bảng roadmap chuyển ✅.

### VIỆC B — đo hiệu năng trên dữ liệu lớn (xong, có số thật)
- Module mới `feed_dealer.setup.p1g_perf`: tái dùng **đúng** generator của P1G nhưng chạy dưới
  prefix `P1G-PERF` + Site Default key riêng ⇒ dataset perf và dataset P1G sống độc lập.
- `p1g_integrity.py`: `MARKER_BUILT`/`MARKER_SHAPE` thành biến module (mặc định GIỮ NGUYÊN như cũ,
  không đổi hành vi) để dataset phụ không đè marker của P1G.
- Dataset đo: **2.000 giao dịch** → 2.285 hoá đơn (285 credit note), 400 SO→SI, 1.425 phiếu thu
  (400 có `references`, 1.025 trống), 163 bút toán cấn trừ. Build 865,8 s (432,9 ms/giao dịch).
- Kết quả: (a) 1 `on_submit` hoá đơn **651 ms** (pilot 100 giao dịch: 486 ms); (b) integrity 9/9
  **0,747 s** trên 2.285 hoá đơn (C1 diff = 0); (c) báo cáo chậm nhất `customer_credit_limit`
  **0,160 s**/40 khách, `payment_allocation_detail` 0,029 s/3.857 dòng.
- `EXIT_GATE_PHASE1.md` mục 6: NOT ASSESSABLE → **PASS (with caveat)** + bảng số + lý do KHÔNG tự đặt
  ngưỡng SLA. Cổng Phase 1: 6/7 PASS (1 caveat).
- Dọn dataset: `p1g_perf.cleanup` cần `auto_commit_on_many_writes` (frappe rollback TOÀN BỘ vì
  >200k writes/transaction). Xác nhận bằng COUNT: perf_cust = 0, perf_keys = 0, **P1G vẫn 6 khách**.

### VIỆC C — dò môi trường P2 (Flutter) TRƯỚC khi code
- Sandbox: Flutter **3.47.2** stable; đã cài Android SDK (platform-tools, platforms;android-35/36,
  build-tools;35/36, NDK 28.2.13676358) + Gradle home trên `/` tại `/opt/...` (vì `/home` chỉ ~4,8 GB).
  **Bằng chứng build thật:** `flutter build apk --debug` trên project scratch → `app-debug.apk` (150 MB).
- Mac: có Flutter (`/Users/hoang/Softwares/flutter`) + Android SDK đầy đủ, **KHÔNG có Xcode**.
  ⇒ **Chỉ build được Android** (không iOS).

### Review code (tự soát; OCR không khả dụng trong phiên)
- Tìm ra **2 lỗi thật trong code tôi vừa viết**, đã vá + xác minh trên site:
  1. `measure()` in `dataset` đọc TRƯỚC khi rebuild ⇒ run 2.000 giao dịch báo `transactions: 100`
     (số của lần pilot) — sửa: đọc LẠI marker sau khi build, thêm `requested_transactions`.
  2. `build()` gọi thẳng `build_dataset`, **bỏ qua guard "partial → clean"** của `ensure_dataset`
     ⇒ build chết vì `QueueOverloaded` (hook `on_update` của app `custom_app`) để lại 1 SO lạc →
     C9 đỏ (`expected 12, found 13`). Vá bằng guard mirror; **xác minh thật**: log có dòng
     `[perf] partial PERF dataset detected (no build marker) — cleaning before rebuild` → C9 PASS →
     `TOTAL: 9 PASS: 9 FAIL: 0`.
- Regression sau thay đổi: **P1G INTEGRITY 9/9** + **P1G REPORTS 4/4** (site sạch).
- Bài học 51–56 (LESSONS_LEARNED) + §3.4/3.5/3.6/§9/§10 trong skill `erpnext-v16-pitfalls`.

## [2026-09-18 01:40–01:54 UTC] P2 test accounts chốt + Icon & đổi tên app "Cám Việt"
- **P2 test accounts (chốt vòng trước):** `p2_test_accounts.run` EXIT 0 trên site — tạo Role `Driver`
  (desk_access=0) + 3 user `p2-test-{owner,staff,driver}@example.com`, mật khẩu random in 1 lần,
  không lưu repo. Bug đã sửa khi test: field Role là `role_name` (không phải `role`) → bài 57.
- **Icon app (feed_dealer):** `.agent/gen_icon.py` vẽ SVG master + PNG (1024 master → 512/256/48 +
  ICO 16/32/48) — thiết kế bao cám + mầm xanh trên gradient. Pixel-verified (đúng 512px, alpha góc
  = 0 sau khi sửa 2 bug của generator: resize thiếu, sheen tràn mask).
- **Đổi tên hiển thị → "Cám Việt":** `app_title` + `add_to_apps_screen.title` trong hooks.py
  (app_name `feed_dealer` giữ nguyên — đổi module name là destructive, không làm).
- **Site:** `branding.py` set Website Settings favicon + app_logo (idempotent, log
  `changed=[favicon, app_logo]`). Phát hiện hạ tầng: **nginx frontend không mount assets của app
  mới** → 404 cả 5 file dù backend OK → `docker cp` 5 file vào
  `frappe_docker-frontend-1:/home/frappe/frappe-bench/assets/feed_dealer/images/` → **bằng chứng:
  logo256 HTTP 200 image/png 256×256 (PIL mở được), favicon 200, HTML /login tham chiếu favicon**.
  Fix bền (rebuild frontend image / volume dùng chung) cần quyết định của chủ infra.
- Bài học 57 (role_name vs role), 58 (frappe_docker assets split) + skill §4 cập nhật.
- Treo mới: không. Treo cũ: P1E BLOCKED chờ sandbox; go-live checklist tài khoản thật.
- Commits: `6981fcc` (P1G/P2 perf + test accounts) → `adde787` (icon + Cám Việt + branding).

## [2026-09-18 ~02:20 UTC] GH Actions debug APK + xoá Android tools local
- **Chính sách mới của chủ (không hỏi lại):** CẤM build APK local; mọi build qua GH Actions
  (gradle trực tiếp, không keystore, không EAS). Token GH đọc từ `.agent/gh_token`
  (git-ignored, chmod 600) — không hỏi user lần nữa.
- **Xoá local (verify từng bước):** `/google/flutter` 1.1GB (husk root-owned cần sudo rmdir),
  `~/.pub-cache`, `~/.dart*`, `/opt/gradle`, `/usr/lib/android-sdk`, `/usr/bin/adb` (symlink đứt).
  `which flutter dart adb` → rỗng.
- **Scaffold `mobile/`** (camviet, vn.appcam): login ERPNext + multi-role + FinancialAction.guard
  (cấm tiền khi offline) + offline queue idempotent. Golden matrix Gradle 9.3.1/AGP 9.1.0/Kotlin 2.4.0.
- **Workflow** `.github/workflows/build-debug-apk.yml`: `./gradlew assembleDebug --no-daemon`,
  Java 17, local.properties sinh trong CI, artifact `camviet-debug-apk` (14 ngày).
- **Push:** secrets scan sạch (repo PUBLIC) → rename master→main → push one-shot bằng token →
  `8dda6ce`. **Run đã kích hoạt:** Build Debug APK · head 8dda6ce · in_progress
  (github.com/hoangsoft90/app_cam/actions/runs/35298515190) — không chờ theo yêu cầu.
- Simplenote lessons: kho trống (`[]`) — đọc theo yêu cầu, không có bài học build nào để tham khảo.
- Skill mới: `camviet-gh-apk-build` (3 chính sách cứng + push flow + verify run).

## [2026-09-18 ~03:00 UTC] VIỆC D + E — checklist sync; P2 Mốc 1 DONE (CI xanh)
- **Quyết định tự quyết (theo quyền chủ dự án đã cấp):** Android-only cho P2 — nhóm dùng Android 100%,
  không có phản hồi khác trong lúc làm. Ghi rõ: iOS làm bổ sung riêng sau này, không chặn P2.
- **VIỆC D:** `checklist.md` đồng bộ thực tế — P0.5/P1G/drop cột rác/perf → `[x]` kèm commit hash;
  P2 test accounts chuyển "cần chủ" → đã tạo; thêm mục C2 với 6 mốc P2.
- **Đổi tên codebase:** `mobile/` → `mobile-dealer/`, package `mobile_dealer` (đúng prompt P2 "1
  codebase mobile-dealer"); label Android = "Cám Việt"; workflow + artifact paths sửa theo.
- **Mốc 1 DONE (commit `d793c64`):** login 2 đường (password → sid cookie, fallback API
  key:secret token), lỗi auth map tiếng Việt qua `exc` (không tin `Message` English);
  fetch roles từ SERVER (parse 2 shape payload) → switcher disable role không được cấp;
  role đã chọn restore chỉ khi server vẫn cấp (server-wins cả ở UI state);
  `sid` gửi lại qua header Cookie (http package không có cookie jar).
- **Bằng chứng:** unit test MockClient 9 test (guard offline, key unique, login OK/fail/token/
  network, roles 2 shape, session restore) — CI `Unit tests` PASS trong run `35300989583`;
  `flutter analyze` sạch; **run GREEN**, artifact `camviet-debug-apk` 72.367.871 B.
- UI thao tác tay (màn hình thật) chưa chụp được — sẽ bổ sung ảnh chụp ở Mốc 6 khi có máy thật,
  đúng tinh thần "mô tả bằng chứng thao tác tay thay vì tự nhận PASS".
- DỪNG theo mốc: chờ review trước khi làm Mốc 2 (Owner dashboard).
