# checklist.md — trạng thái công việc P0 + P1A/P1B (cập nhật 2026-09-16)

Legend: `[x]` đã làm CÓ BẰNG CHỨNG · `[~]` làm một phần · `[ ]` chưa làm · `[?]` cần hỏi lại user

## A. Yêu cầu trực tiếp của user (phiên này)

- [x] Đọc `.env` mới (ERPNEXT_URL, ERPNEXT_API_KEY, ERPNEXT_API_SECRET — không có biến Docker nào)
- [x] Xoá cài đặt Docker ERPNext khỏi **máy này**: 0 container, 0 volume, 0 build cache,
      xoá network `app-cam_default`, xoá image `frappe/erpnext:v15.121.2` + `postgres:15` +
      `redis:7-alpine`. File repo `docker-compose.yml`, `docker/`, `.dockerignore` đã xoá từ phiên trước.
      Còn `alpine:3.19` (11.6MB) — có TRƯỚC khi làm, chưa đụng.
- [x] Verify endpoint ngrok sau khi xoá: `ping` HTTP 200, `get_logged_user` → Administrator,
      versions → frappe 16.17.2 / erpnext 16.16.0 + 3 app custom (camvlxd, custom_app, feed_dealer)

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

- [ ] **Drop 12 cột rác trên `tabBatch`** (xem FAQ #1) — chờ user duyệt vì DDL không hoàn tác trên site thật
- [x] **Commit ban đầu** — đã làm: `eb75222` (P0+P1A+P1B), `0355d6b` (vá refund + khoá NULL),
      `0e89ce7` (bỏ track `__pycache__`)
- [x] **Commit P1C** — đã duyệt + commit `7c62129` (task 11.9 đóng)
- [ ] P0.5: import nợ đầu kỳ + tạo `opening_journal_entry` thật
- [x] ~~2 lỗ hổng P1B~~ — **đã đóng ở P1C**: hook `Unreconcile Payment` (P1B T8) + row lock FIFO
      (P1B T9); phần còn lại của v16 (cancel Unreconcile Payment không re-link) đã ghi rõ ở D16
- [ ] P1B còn thiếu so với plan: phân bổ **thủ công** (hiện chỉ FIFO), nhánh Journal Entry riêng cho
      nợ đầu kỳ, đẩy `returned_amount` từ credit note (P1D)

## D. Cần hỏi lại user

- [?] Drop cột rác trên `tabBatch`? (script sẵn, chỉ chạy khi duyệt)
- [?] Commit ban đầu có thực hiện không? (acceptance #6 của prompt P0) → **đã xong, 4 commit**
      (`eb75222`, `0355d6b`, `0e89ce7`, `7c62129`), các file md root + result/handoff **có** nằm trong
      git. Acceptance #6 của prompt P0 coi như đóng.
- [?] Hành vi sai khác giữa prompt P1C (acceptance #1: "3 draft SO 20tr") và `plan_final_v2.2_mustfix.md`
      MUST-3: bản v2.2 (mới hơn, ghi MUST) đếm **cả draft khi tạo**, nên đơn nháp thứ 3 bị chặn ngay lúc
      tạo chứ không phải lúc submit. Tôi làm theo v2.2 và test **cả hai** hành vi (P1C T1/T2/T3).
      Nếu user muốn đúng theo prompt cũ thì phải bỏ số hạng draft khi tạo (mở lại bypass 10 đơn nháp).
- [?] **Warehouse "Main"**: prompt yêu cầu tạo, nhưng site thật đã có warehouse. Seeder đang CHỌN
      default_warehouse từ warehouse có sẵn (ưu tiên tên có "cám/cam") chứ KHÔNG tạo "Warehouse Main"
      mới. Giữ nguyên cách này, hay cần tạo warehouse riêng cho dự án?
- [?] `alpine:3.19` trên máy này có được phép xoá luôn không?
