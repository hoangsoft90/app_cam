# next.md — roadmap (cập nhật 2026-09-17 11:00)

## Đã hoàn thành (P0 foundation — bằng chứng đầy đủ)

| # | Việc | Bằng chứng |
|---|---|---|
| 1 | Xoá cài đặt Docker ERPNext khỏi máy này | 0 container/volume/build-cache; image v15+postgres+redis đã xoá |
| 2 | App `feed_dealer` lên site ERPNext v16 thật (Mac) | push 83/83 file; `get_versions` có `feed_dealer: 0.0.1` |
| 3 | Migrate sạch + idempotent | `/tmp/mig1.log`, `/tmp/mig2.log` → `=== EXIT 0 ===` cả hai |
| 4 | 19 DocType trong app (không Custom) | `tabDocType where module='Feed Dealer'` → 19, custom=0 |
| 5 | Sửa sự cố đè DocType `Batch` của ERPNext | `Batch` trả lại module `Stock`; app đổi sang `Feed Batch` |
| 6 | Masters v16 seed idempotent | output seeder; A9 PASS (`1 Bao = 25.0 Kg`) |
| 7 | Acceptance 9/9 PASS (chạy 2 lần) | `TOTAL: 9 PASS: 9 FAIL: 0` → `P0 ACCEPTANCE: ALL PASS` |
| 8 | OpenSpec artifacts khớp thực tế | `openspec validate` → valid, 32/36 task |
| 9 | P1A: Sales Invoice → Batch Debt (và vá idempotency theo nhóm thuế) | `TOTAL: 6 PASS: 6 FAIL: 0` (T1–T6) |
| 10 | P1B: Payment Entry → Payment Allocation FIFO + cancel đảo ngược | `TOTAL: 6 PASS: 6 FAIL: 0` → `P1B ACCEPTANCE: ALL PASS` |

## ✅ NỢ KỸ THUẬT ĐÃ ĐÓNG

- **P0.5 (`prompt_P0_5_migration.md`) — import nợ đầu kỳ — DONE 2026-09-17 10:42.** 6/6 PASS từ site
  trắng (T1 reconcile diff=0 → T4 re-import không nhân đôi → T5 crash-replay adopt JE mồ côi),
  mutation-check có răng (đảo needle adoption → T5 đỏ `11→12`, khôi phục → xanh). Không tạo Sales
  Invoice ảo; mỗi khoản nợ = 1 JE (Dr AR party=Customer / Cr tài khoản đầu kỳ) + Batch Debt
  `is_opening_balance=1`. Xem `result_2026-09-17_1042_P05_migration.txt`.
  **Lưu ý go-live:** chỉ cần tender lại cùng đường import với file khách hàng thật (mẫu ở
  `scripts/migration/`), không phải làm lại code.

## Sắp tới — ngắn hạn

1. **[x] P1G ĐÃ XONG (2026-09-17)** — integrity `9/9`, reports `4/4`, Exit Gate đã viết
   (review round sau đó đã cứng hóa **cách kiểm chứng** — xem mục 1c):
   - Integrity: dataset 60 giao dịch (seed cố định) + 2 **đẳng thức tuyệt đối** (tolerance 0 VND):
     `BD = TT − PA − OF` và `BD − TE = (−U)+ER+(−PA)+(−OF)`. Bằng chứng: `P1G INTEGRITY: ALL PASS`
   - Dataset phát hiện **1 bug thật của P1D** (trả 1 dòng của hoá đơn nhiều dòng) → đã vá + thêm `T8`
   - 7 báo cáo Desk (6 Query + 1 Script) — bảng slug ↔ nhãn tiếng Việt nằm trong `EXIT_GATE_PHASE1.md`
   - ~~Số liệu chờ quyết định~~ → **ĐÓNG 2026-09-17:** gap −102.478.500 (31/45 phiếu thu để trống
     `references`) nhưng **GIỮ NGUYÊN FIFO, không sửa** — hệ quả đúng của kiến trúc view-layer
     (Batch Debt là view trên AR, `residual=0` chứng minh không mất/đúp tiền), không phải bug
1c. **[x] Review round sau P1G (2026-09-17)** — không có lỗi trong code P1G/P1D; đóng 2 lỗ hổng
   kiểm chứng bằng code: `.agent/bench_wait.py` (serialise lệnh bench dài — hết race `--to-file`) và
   `C9` so **số đúng** với `shape` (hết "SO lạc từ build chết mà suite vẫn xanh"). Tái xác minh:
   P1G INTEGRITY 9/9 · P1G REPORTS 4/4 · P1D 8/8. Xem `result_2026-09-17_0805_review_P1G.txt` +
   `openspec/.../design.md` **D26** + `tasks.md` **14c**.
2. **[x] Tài khoản Desk + role `Driver` cho P2 (2026-09-18)** — role `Driver` + 3 user test
   `p2-test-{owner,staff,driver}@example.com` đã tạo trên site (`p2_test_accounts.run` EXIT 0);
   mật khẩu random in 1 lần khi tạo (không lưu repo). Tài khoản người dùng THẬT vẫn để dành cho
   go-live checklist (không chặn dev).
3. **[x] Đo hiệu năng trên dữ liệu lớn (2026-09-17)** — `p1g_perf` chạy 2.000 giao dịch: on_submit
   651 ms, integrity 9/9 (0,75 s), reports ≤ 0,16 s; Exit Gate mục 6 chuyển sang PASS-with-caveat.
   Còn lại (không chặn): ngưỡng SLA chính thức từ chủ dự án nếu muốn đối chiếu.
4. **[ ] Dọn dataset P1G** khi chủ dự án đã review số thủ công:
   `bench execute feed_dealer.setup.p1g_integrity.cleanup` (xoá theo thứ tự phụ thuộc + xoá marker)
2b. **[!] P1E e-invoice = BLOCKED** — cần provider (VNPT/Viettel/MISA) + credential sandbox + mã số
   thuế + quy ước NĐ 123 (Cancel/Adjust/Replace). Xem `result_P1E_BLOCKED_2026-09-17.txt`
3. **[?] Hành vi hạn mức đã chốt** (xem checklist.md mục D): chặn đơn nháp vượt hạn mức **ngay lúc
   tạo** (theo v2.2 MUST-3) — nếu muốn theo prompt cũ (chỉ chặn lúc submit) thì nói, tôi đổi
4. **[x] Drop 12 cột rác `tabBatch`** — đã làm 2026-09-17 (patch + backup + verify)
5. (Tuỳ chọn) Dọn fixture acceptance: mọi bộ `run()` đều tự dọn trước khi chạy, không cần tay

## P2 — Internal Mobile `mobile-dealer` (Flutter, Android-only)

- **Repo:** https://github.com/hoangsoft90/app_cam (push lên `main` tự build APK debug qua GH Actions)
- **Chính sách build (owner 2026-09-18):** CẤM build APK local; mọi build qua workflow
  `.github/workflows/build-debug-apk.yml` — gradle trực tiếp (`./gradlew assembleDebug`),
  debug-signed, không keystore, không EAS token. Workflow xanh từ run 6; artifact
  `camviet-debug-apk` (~72 MB, giữ 14 ngày).
- **Trạng thái:** Mốc 1 DONE (login 2 đường password/token, multi-role switcher server-driven,
  Settings + auto-login secure storage; CI run `35302106214` GREEN, 11 unit test PASS; APK Mốc 1
  đã tải về `dist/app-debug.apk`). Mốc 2 (Owner dashboard online-only) là việc kế tiếp.
- Tiến độ chi tiết theo mốc: `checklist.md` mục C2.

## Roadmap phase tiếp theo

| Phase | Nội dung chính | Điều kiện tiên quyết |
|---|---|---|
| **P0.5** | ✅ Import nợ đầu kỳ (JE Dr AR/Cr đầu kỳ + Batch Debt `is_opening_balance=1`), templates CSV + reconcile diff=0 (6/6 PASS) | P0 ✓ |
| **P1A** | ✅ Sales Invoice → Batch Debt theo lứa (kể cả nhiều nhóm thuế/lứa); cascade cancel có bảo vệ `paid_amount > 0` | P0 ✓ |
| **P1B** | ✅ Payment Entry → Payment Allocation FIFO; cancel đảo ngược. Còn: phân bổ thủ công, nhánh JE cho nợ đầu kỳ, unreconcile | P1A ✓ |
| **P1C** | ✅ Credit Score + hạn mức theo tier, chặn đơn vượt hạn (commit `7c62129`) | P1B ✓ |
| **P1D** | ✅ Sales Return Request → credit note thật → `returned_amount` DERIVED (commit `c5152dd`) | P1B ✓ |
| **P1E** | ⛔ BLOCKED: e-invoice VN — cần sandbox provider + mã số thuế (không mock) | P1A ✓ |
| **P1F** | ✅ Consent + Debt Confirmation Slip (print format) + Livestock offset (JE) + Batch tách/gộp bảo toàn nợ (commit `b48d723`) | P1A ✓ |
| **P1G** | ✅ 7 báo cáo Desk + script AR vs Batch Debt (integrity 9/9) + Exit Gate Phase 1 (`EXIT_GATE_PHASE1.md`); review round đã cứng hóa kiểm chứng (D26) | P1A–P1F (P1E blocked, phần e-invoice không chặn báo cáo) |
| **P2/P4** | App nhân viên (Flutter) + Zalo Mini App nông dân | P1A–B (môi trường build: xem `handoff`/result P2 — sandbox có Flutter+Android SDK, **không có Xcode ⇒ chỉ build Android**) |
| **P3** | AI: kill switch vận hành, voice→action, Action Item (read-only posture) | P1 + config |

## Việc bảo trì nhỏ khi rảnh
- Bổ sung test riêng cho `Feed Batch.validate_dates` (đang được phủ gián tiếp qua acceptance)
- Cân nhắc chuyển `expected_end_date` từ suy-đoán-cứng (`DEFAULT_CYCLE_DAYS`) sang cấu hình Settings (P1)
