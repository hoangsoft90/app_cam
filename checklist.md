# checklist.md — trạng thái công việc P0 → P2 (cập nhật 2026-09-18)

Legend: `[x]` đã làm CÓ BẰNG CHỨNG · `[~]` làm một phần · `[ ]` chưa làm · `[?]` cần hỏi lại user

## A. Yêu cầu trực tiếp của user (phiên này)

- [x] Đọc `.env` mới (ERPNEXT_URL, ERPNEXT_API_KEY, ERPNEXT_API_SECRET — không có biến Docker nào)
- [x] Xoá cài đặt Docker ERPNext khỏi **máy này**: 0 container, 0 volume, 0 build cache,
      xoá network `app-cam_default`, xoá image `frappe/erpnext:v15.121.2` + `postgres:15` +
      `redis:7-alpine`. File repo `docker-compose.yml`, `docker/`, `.dockerignore` đã xoá từ phiên trước.
      Còn `alpine:3.19` (11.6MB) — có TRƯỚC khi làm, chưa đụng.
- [x] Verify endpoint ngrok sau khi xoá: `ping` HTTP 200, `get_logged_user` → Administrator,
      versions → frappe 16.17.2 / erpnext 16.16.0 + 3 app custom (camvlxd, custom_app, feed_dealer)

## B0. P1G — integrity + reports + Exit Gate (2026-09-17)

- [x] Script integrity AR vs Batch Debt (`feed_dealer.setup.p1g_integrity`) — dataset 60 giao dịch
      ngẫu nhiên (SO→SI nhiều lứa/nhiều thuế → PE (14 có references / 31 trống) → return → offset)
- [x] Bằng chứng `P1G INTEGRITY: ALL PASS` 9/9: C1 `BD 11.289.000 == TT 156.345.000 − PA 143.851.000
      − OF 1.205.000` (diff 0); C7 diff −121.467.000 **phân rã đúng 4 nhóm, residual 0**
- [x] Ngưỡng dung sai **0 đồng** + lý do đã ghi (số nguyên VND cùng cột, không FX/không làm tròn)
- [x] Mutation-check: bỏ `offset_amount` → C1+C7 đỏ, diff đúng 1.205.000 → khôi phục xanh lại
- [x] Số liệu cho VIỆC 1 (FIFO bỏ qua `references`): `ER 41.372.500` vs `PA 143.851.000` ⇒ gap
      **−102.478.500** — CHỜ chủ dự án quyết, agent KHÔNG tự sửa cơ chế FIFO
- [x] Bug thật P1D do dataset tìm ra (trả 1 dòng của hoá đơn nhiều dòng bị chặn) — đã vá generator +
      thêm regression `p1d T8`; P1D `8/8`
- [x] 7 báo cáo Desk: 6 Query Report + 1 Script Report (`customer_credit_limit` gọi đúng hàm gate)
- [x] Bằng chứng `P1G REPORTS: ALL PASS` 4/4 (mọi report chạy qua chính runner của Desk, có số dòng)
- [x] Exit Gate Phase 1 → `EXIT_GATE_PHASE1.md`: 5/7 tiêu chí PASS, **Performance + UX NOT ASSESSABLE**
- [x] Regression đầy đủ sau khi sửa: P0 9/9 · P1A 8/8 · P1B 10/10 · P1C 10/10 · P1D 8/8 · P1F 9/9
- [x] Tài khoản TEST cho P2 — **đã tạo 2026-09-18** (`p2_test_accounts.run` EXIT 0): role `Driver`
      (desk_access=0) + `p2-test-{owner,staff,driver}@example.com`, mật khẩu random in 1 lần, không
      lưu repo. Tài khoản THẬT + chính sách mật khẩu vẫn để dành go-live checklist.
- [x] Ngưỡng hiệu năng + đo trên dữ liệu lớn — **đã đo 2026-09-17** (perf smoke 2.000 SI: build 33.3s
      → on_submit đơn lẻ, integrity, reports — số thật trong `EXIT_GATE_PHASE1.md`; Performance
      chuyển NOT ASSESSABLE → PASS with caveat), commit `1a74647`
- [x] **P0.5 import nợ đầu kỳ — ĐÃ DONE 2026-09-17**: JE (Dr AR/Cr Opening Equity) + Batch Debt
      `is_opening_balance=1`, reconcile diff = 0 trên dataset mẫu, `p05_acceptance` **6/6 PASS từ site
      trắng** + mutation-check guard adopt JE; regression P0–P1F xanh. Commit `5f05dbe`. Còn lại:
      chạy import với file Excel/CSV dữ liệu KHÁCH THẬT khi go-live (path đã sẵn `scripts/migration/`)

## B0b. Review round sau P1G (2026-09-17) — tự soát, tìm ra lỗi ở phần KIỂM CHỨNG

- [x] Review code P1G: **không có lỗi trong code P1G/P1D**; tìm ra 2 lỗ hổng ở cách kiểm chứng, đã đóng bằng code
- [x] `.agent/bench_wait.py` — **serialise** các lệnh bench dài: trước đây `debug` chạy khi `cleanup`
      còn đang xoá ⇒ đọc DB **đang bị xoá dở** ⇒ `8/9` vô nghĩa (bằng chứng bị nhiễm do race)
- [x] C9 giờ so **SỐ ĐÚNG** tài liệu với `shape` builder đã lưu (+ `ensure_dataset` dọn dataset dở
      trước khi build lại) — đóng lỗ hổng "1 Sales Order lạc từ build chết, suite vẫn xanh"
- [x] Lỗi tự gây do C9 bắt được và đã sửa: biến comprehension bị dùng lại làm biến vòng lặp
      (`UnboundLocalError`); thêm assert số dòng SO→SI; `if not debt: continue` trước khi đọc cột nợ
- [x] Tái xác minh trên revision đã review (dùng `bench_wait` + `clear-cache`): **P1G INTEGRITY 9/9** ·
      **P1G REPORTS 4/4** · **P1D 8/8** (log `/tmp/rv2_{p1g,rep,p1d}.log`); số liệu trùng khớp bản đã commit
- [x] Đóng gói bài học: skill §8 + `LESSONS_LEARNED.md` 42b–46 (race `--to-file`, so số giữa 2 lần chạy,
      exact counts, 2 bẫy Python/Frappe, lỗi từ app khác)

## B. Change `p0-feed-dealer-foundation` (44/46 task, `openspec validate` OK)

- [x] App `feed_dealer` đẩy lên Mac (83/83 file khớp) — bind-mount vào bench site `frontend`
- [x] `bench migrate` chạy sạch **2 lần**, log trong container đều `=== EXIT 0 ===` (`/tmp/mig1.log`, `/tmp/mig2.log`)
- [x] 19 DocType module `Feed Dealer`, `custom = 0`; ERPNext `Batch` trả lại module `Stock`
- [x] Sửa lỗi đè tên DocType: `Batch` → `Feed Batch` (giữ mã `LOT-{YYYY}-{#####}`), sửa hooks/test/README
- [x] Seeder masters idempotent (v16): UOM + `UOM Conversion Factor` (category Mass), item/customer
      groups, price lists, `Feed Dealer Settings.default_company`
- [x] Acceptance `p0_acceptance.run`: **9/9 PASS** chạy lại sau 2 migrate, fixtures idempotent
- [x] Cập nhật artifacts openspec (proposal/design/tasks + 3 spec) theo thực tế v16 + kiến trúc thật
- [x] README `apps/feed_dealer`: layout, generated-controller warning, v16 gotchas, Phase 1 handoff
- [x] working.md tạo + cập nhật

## B2. P1A vá + P1B Payment Allocation (đã verify trên site thật)

- [x] P1A-1 — khoá idempotency = đúng bộ ba `(sales_invoice, batch, item_tax_template)`; thêm field
      `item_tax_template` trên Batch Debt (sinh từ generator). Bằng chứng: **T6** 1 hoá đơn / 1 lứa /
      2 nhóm thuế → 2 debt (trước khi vá: 1 debt, mất 1.000.000 tiền nợ)
- [x] P1A-2 — nhóm còn draft thì submit nốt (không skip mãi); chỉ skip khi đã có debt docstatus=1
- [x] P1B — `Payment Allocation` thành **DocType độc lập, submittable** (`ALLOC-{YYYY}-{#####}`,
      link `payment_entry`) thay vì child table (on_submit chạy sau khi doc cha đã ghi DB)
- [x] P1B — FIFO theo `due_date`; cancel PE đảo ngược + tính lại nợ; lãi chậm trả theo Settings
- [x] Bằng chứng: P1B `6 PASS: 6 FAIL: 0` và `P1B ACCEPTANCE: ALL PASS`; P1A `6 PASS: 6 FAIL: 0`;
      P0 `9 PASS: 9 FAIL: 0` (A7 giờ liệt kê 6 handler); `migrate` → `=== EXIT 0 ===`
- [x] Cập nhật artifacts: data-model spec (Payment Allocation độc lập + `item_tax_template`),
      design D12–D14, tasks mục 10, working.md/features.md/next.md
- [x] **Review lại chính code vừa viết** (OCR không có trong phiên → tự soát + probe thật):
      sửa HIGH — refund PE (`Pay` + Customer) từng bị cấp phát như thu tiền (gate `payment_type ==
      "Receive"`, test P1B T7); sửa MEDIUM — khoá idempotency không match `NULL` (dùng `["is",
      "not set"]`, test P1A T7); thêm T8 cho nhánh draft còn sót; bỏ `save()` thừa.
      **Mutation check**: khôi phục 2 hành vi cũ → T7/T8 đỏ đúng như mong đợi, khôi phục lại → xanh.
      Kết quả cuối: P1A 8/8, P1B 7/7, P0 9/9 PASS (`result_2026-09-16_1405.txt`).
- [ ] Commit vòng review này + dọn 58 file `__pycache__` khỏi git index — chờ user duyệt

## C. Chưa làm / cần làm tiếp

- [x] **Drop 12 cột rác trên `tabBatch`** — user duyệt 2026-09-17, đã làm bằng patch idempotent sau
      khi backup DB (`20260917_090031-frontend-database.sql.gz`); `tabBatch` 30 cột / 0 orphan; P0–P1F
      regression xanh sau đó (tasks.md 5.17, `result_2026-09-17_0430_P1F.txt`)
- [x] **P1D** (Sales Return → credit note → `returned_amount`) — commit `c5152dd`; 7/7 PASS
- [x] **P1F** (Consent + Debt Slip + Livestock Offset + Batch Split/Merge) — commit `b48d723`; 7/7 PASS
- [!] **P1E e-invoice = BLOCKED** — thiếu sandbox provider (VNPT/Viettel/MISA) + mã số thuế;
      `result_P1E_BLOCKED_2026-09-17.txt` nêu đúng 4 thứ cần user cấp. ĐÚNG thứ tự chain: dừng đây,
      không nhảy phase, không viết mock provider
- [x] **P1G** (7 báo cáo Desk + script đối chiếu AR vs Batch Debt + Exit Gate Phase 1) — DONE,
      xem mục B0 (INTEGRITY 9/9 · REPORTS 4/4 · Exit Gate 5/7 PASS + Performance PASS with caveat)
- [x] **P0.5 (import nợ đầu kỳ)** — DONE 2026-09-17 (chi tiết mục B0; commit `5f05dbe`)
- [x] **Commit ban đầu** — đã làm: `eb75222` (P0+P1A+P1B), `0355d6b` (vá refund + khoá NULL),
      `0e89ce7` (bỏ track `__pycache__`)
- [x] **Commit P1C** — đã duyệt + commit `7c62129` (task 11.9 đóng)
- [x] P0.5: import nợ đầu kỳ + `opening_journal_entry` thật (JE per khoản nợ, reconcile diff = 0)
- [x] ~~2 lỗ hổng P1B~~ — **đã đóng ở P1C**: hook `Unreconcile Payment` (P1B T8) + row lock FIFO
      (P1B T9); phần còn lại của v16 (cancel Unreconcile Payment không re-link) đã ghi rõ ở D16
- [~] P1B còn thiếu so với plan: phân bổ **thủ công** (hiện chỉ FIFO) — còn mở; ~~nhánh Journal Entry
      riêng cho nợ đầu kỳ~~ (đóng bởi P0.5); ~~đẩy `returned_amount` từ credit note~~ (đóng bởi P1D)

## C2. P2 — Internal Mobile (bắt đầu 2026-09-18)

- [x] Quyết định chủ dự án: **Android-only** cho P2 (nhóm Chủ/NV/Tài xế dùng Android 100%) —
      không cần Xcode/iOS; nếu sau này cần thì làm bổ sung riêng, không chặn P2
- [x] Môi trường build: Flutter 3.47.2 + Android SDK **trên GH Actions** (cấm build local —
      owner policy 2026-09-18, tool local đã xoá); workflow `build-debug-apk.yml` gradle trực tiếp,
      không keystore/EAS → **GREEN run 6** (`35299833093`), artifact `camviet-debug-apk` ~72 MB
- [x] Test accounts + role Driver sẵn sàng (mục B0)
- [x] Mốc 1: scaffold + login REST + multi-role switcher — DONE (commit `d793c64`, review-fix `01c343f`,
      APK đã tải về `dist/app-debug.apk` commit `01c343f`)
- [x] Mốc 1.5: Settings + auto-login (Keystore) — DONE 2026-09-18: HTTPS-guard có test chứng minh
      (chặn cả ở tầng URL lẫn tầng login, ngoại lệ loopback chỉ trong `kDebugMode`), test server-wins
      viết lại thành **widget test thật** trên `_bootstrap()`, vá 3 lỗi CI bắt được (thiếu dep
      `flutter_secure_storage`, key `token_pair` không writer nào tạo → auto-login token chết,
      fallback token không ghép lại `user:password`). Commit `c70a92c` → `d4a8008`
- [x] Mốc 2: Owner dashboard (khách/lứa/nợ/duyệt SO) — DONE 2026-09-18 (commit `c70a92c`, CI xanh
      `35306182930`: 35 test PASS + artifact `camviet-debug-apk` 79 MB). Duyệt SO qua REST PUT
      `docstatus=1` → hook hạn mức phía server vẫn chạy, bị từ chối thì hiện message server
      (server-wins, không force). Chờ chủ dự án review trước Mốc 3.
- [x] Mốc 3: Driver flow (giao hàng, OTP/chữ ký/ảnh/GPS) — **DONE 2026-09-18**
      - [x] **Backend DONE** 2026-09-18: DocType `Delivery Confirmation` + child `Delivery Proof Photo`
            + `feed_dealer/api.py` (confirm_delivery idempotent, approve/reject chỉ Manager) —
            `p2_delivery_acceptance` **15/15 PASS** trên site thật (T1–T11). Commit `ec4e3cd`.
      - [x] **Delivery Note DONE** 2026-09-18 (theo quyết định của chủ dự án): field `delivery_note`
            trên DC, `build_delivery_note()` dùng `make_delivery_note` + `default_warehouse` từ
            Feed Dealer Settings (không tạo warehouse mới); OTP → DN ngay, Photo Only → DN khi chủ
            duyệt, từ chối → không DN. `p2_delivery_acceptance` **33/33 PASS** (T15a–d, T16a–d thiếu
            hàng chặn ở cả 2 đường, tồn kho có kiểm chứng). Commit `a52a885`.
      - [x] **Màn hình Driver DONE** 2026-09-18 (`mobile-dealer/lib/driver_delivery.dart`): danh sách
            giao + sheet xác nhận 3 nhánh (OTP / chữ ký vẽ tay / ảnh+GPS), nén ảnh phía client
            (`image_picker maxWidth/quality`), `idempotency_key` sinh 1 lần cho cả sheet (bấm lại
            không sinh khoá mới), ONLINE-ONLY như Mốc 2. Không hiện thu tiền/đổi hạn mức cho Driver.
            CI `35314907159` **success**: `🎉 40 tests passed` + artifact `camviet-debug-apk`
            80.683.784 B (`BUILD SUCCESSFUL in 4m 11s`). Commit `c98d1c3` → `afafa8d` → `552ea61`
            → `89f9541` → `8cffc35`.
      - [x] **Hợp đồng API cho UI** 2026-09-18: `driver_deliveries` trả thêm `delivery_note` +
            `reject_reason` (màn Driver cần hiện số phiếu xuất và lý do bị từ chối) — deploy lên site
            + `p2_delivery_acceptance` **36/36 PASS** (T17a–c assert đúng hợp đồng này). Commit `58f21d1`.
      - [x] **ĐÃ CHỐT 2026-09-18:** Signature **không** xuất kho ngay (cùng nhóm Photo Only, chờ
            Manager duyệt); OTP = final → DN ngay. Khớp code hiện tại, UI Driver ghi rõ cho tài xế.
      - [x] Cần chủ dự án quyết: gửi OTP qua SMS → chốt 2026-09-18: **BLOCKED** (không có provider),
            giữ nhập OTP tay + nhánh Signature/Photo
- [x] Mốc 4: Offline queue + idempotency_key cho create được phép offline — **DONE 2026-09-18**
      (`mobile-dealer/lib/offline_queue.dart` + `queue_screen.dart`). Hàng đợi lưu trong
      SharedPreferences, đúng 10 field theo `phase_02_internal_mobile.md` §17 (`id, operation, entity,
      entity_id, payload, created_at, retry_count, status, idempotency_key, last_error`).
      **CHỈ `delivery_confirm` được xếp hàng** (`kQueueableOperations` là whitelist — thao tác khác bị
      `QueueRejected`, nên thu tiền/đổi hạn mức/submit SI không thể lọt vào hàng đợi cả khi code sau
      này viết sai); `isOnline` bỏ dev toggle → **suy ra từ kết quả request thật** (một interface-probe
      sẽ báo "có wifi" ở chuồng trại có router nhưng mất uplink). Server-wins: payload bị server TỪ CHỐI
      không bao giờ được tự gửi lại → thành `conflict` + giữ nguyên câu thông báo của server; 401
      (`AuthExpired`) thì GIỮ row và yêu cầu đăng nhập lại, không đánh dấu chết.
      CI `35320908195` **success**: `No issues found` (analyze) + `50 tests passed` (+10 test Mốc 4,
      trước là 40) + artifact `camviet-debug-apk` 80.704.960 B (`BUILD SUCCESSFUL in 3m 38s`).
      Commit `882fa0b` → `0bb7054` → `dde05c7`.
      - [x] Acceptance #5 (offline confirm → sync khi có mạng, không trùng): key sinh 1 lần trong sheet,
            row giữ nguyên key đó khi replay; **3 lớp chống trùng** — client từ chối xếp cùng đơn 2 lần,
            cùng key khi gửi lại, và server (T7 của `p2_delivery_acceptance`) trả bản ghi đã có.
      - [x] Acceptance #6 (offline không cho bấm Thu tiền / Đổi hạn mức): vẫn bằng guard hiện có
            (`FinancialAction.guard()` ném lỗi khi offline); việc **ẩn hẳn khỏi cây widget** là Mốc 5.
      - [x] Test case #2 của phase (§7): token hết hạn lúc sync → row giữ nguyên + nhắc đăng nhập lại.
- [ ] Mốc 5: Guard cứng — nút thu tiền/đổi hạn mức KHÔNG THẤY (không phải disable mờ) khi offline
      (nền đã có sẵn từ Mốc 4: `isOnline` giờ là tín hiệu thật, không còn toggle tay)
- [ ] Mốc 6: test máy thật/emulator + sóng yếu + APK debug cuối cùng
- Quy tắc xuyên suốt: KHÔNG mutation tài chính khi offline · conflict server-wins · báo cáo theo mốc

## D. Cần hỏi lại user

- [x] Drop cột rác trên `tabBatch`? → **đã duyệt và đã làm 2026-09-17** (patch + backup + verify)
- [?] Commit ban đầu có thực hiện không? (acceptance #6 của prompt P0) → **đã xong, 4 commit**
      (`eb75222`, `0355d6b`, `0e89ce7`, `7c62129`), các file md root + result/handoff **có** nằm trong
      git. Acceptance #6 của prompt P0 coi như đóng.
- [x] Hành vi hạn mức (MUST-3) → **đã quyết ở review round trước: giữ nguyên v2.2 MUST-3** (chặn
      đơn nháp vượt hạn mức NGAY LÚC TẠO). Đóng câu hỏi, không mở lại.
- [x] **Warehouse** → **đã quyết ở review round trước: giữ nguyên cách tự chọn warehouse có sẵn**
      (seeder chọn `default_warehouse` từ warehouse hiện có, ưu tiên tên có "cám/cam"), KHÔNG tạo
      "Warehouse Main" mới. Đóng câu hỏi, không mở lại.
- [x] `alpine:3.19` → **user quyết: không đụng** (có trước dự án, không liên quan) — đóng câu hỏi này.
- [?] P1E cần: provider (VNPT/Viettel/MISA), credential sandbox, mã số thuế công ty, và quy ước
      NĐ 123 khi SI huỷ/điều chỉnh (Cancel vs Adjust vs Replace khi nào) — xem
      `result_P1E_BLOCKED_2026-09-17.txt`
