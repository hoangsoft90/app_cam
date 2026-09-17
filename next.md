# next.md — roadmap (cập nhật 2026-09-17 08:05)

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

## ⚠️ NỢ KỸ THUẬT BẮT BUỘC TRƯỚC GO-LIVE

- **P0.5 (`prompt_P0_5_migration.md`) — import nợ đầu kỳ — CHƯA LÀM.** Theo `00_PROMPT_CHAIN.md`,
  P1A lẽ ra phải đứng sau P0.5 DONE; dự án đã bỏ qua bước đó (chấp nhận được vì hiện chỉ test trên
  dữ liệu giả, `is_opening_balance` + `opening_journal_entry` đã có sẵn trên Batch Debt).
  **Không được go-live với dữ liệu khách hàng thật khi chưa chạy prompt P0.5**: chưa có đường
  import nợ cũ ⇒ Batch Debt sẽ thiếu toàn bộ số dư lịch sử, và mọi con số AR-vs-Batch-Debt ở P1G
  sẽ lệch đúng bằng phần nợ cũ chưa nhập.

## Sắp tới — ngắn hạn

1. **[x] P1G ĐÃ XONG (2026-09-17)** — integrity `9/9`, reports `4/4`, Exit Gate đã viết
   (review round sau đó đã cứng hóa **cách kiểm chứng** — xem mục 1c):
   - Integrity: dataset 60 giao dịch (seed cố định) + 2 **đẳng thức tuyệt đối** (tolerance 0 VND):
     `BD = TT − PA − OF` và `BD − TE = (−U)+ER+(−PA)+(−OF)`. Bằng chứng: `P1G INTEGRITY: ALL PASS`
   - Dataset phát hiện **1 bug thật của P1D** (trả 1 dòng của hoá đơn nhiều dòng) → đã vá + thêm `T8`
   - 7 báo cáo Desk (6 Query + 1 Script) — bảng slug ↔ nhãn tiếng Việt nằm trong `EXIT_GATE_PHASE1.md`
   - **Số liệu CHỜ QUYẾT ĐỊNH:** FIFO phân bổ 143.851.000 nhưng ERPNext chỉ cấn trừ cấp hoá đơn
     41.372.500 (31/45 phiếu thu để trống `references`) ⇒ **gap −102.478.500**. Chủ dự án xem số rồi
     quyết có đổi cơ chế phân bổ hay không — agent chưa đụng vào FIFO
1c. **[x] Review round sau P1G (2026-09-17)** — không có lỗi trong code P1G/P1D; đóng 2 lỗ hổng
   kiểm chứng bằng code: `.agent/bench_wait.py` (serialise lệnh bench dài — hết race `--to-file`) và
   `C9` so **số đúng** với `shape` (hết "SO lạc từ build chết mà suite vẫn xanh"). Tái xác minh:
   P1G INTEGRITY 9/9 · P1G REPORTS 4/4 · P1D 8/8. Xem `result_2026-09-17_0805_review_P1G.txt` +
   `openspec/.../design.md` **D26** + `tasks.md` **14c**.
2. **[ ] Tài khoản Desk thật + role `Driver` (P2)** — cần chủ dự án cấp (hiện chỉ có user test
   `p0-acceptance-*` / `p1c-acceptance-*`); role Manager/Staff/Farmer đã tổn tại
3. **[ ] Ngưỡng hiệu năng + đo dữ liệu lớn** — mục Performance của Exit Gate đang NOT ASSESSABLE
4. **[ ] Dọn dataset P1G** khi chủ dự án đã review số thủ công:
   `bench execute feed_dealer.setup.p1g_integrity.cleanup` (xoá theo thứ tự phụ thuộc + xoá marker)
2b. **[!] P1E e-invoice = BLOCKED** — cần provider (VNPT/Viettel/MISA) + credential sandbox + mã số
   thuế + quy ước NĐ 123 (Cancel/Adjust/Replace). Xem `result_P1E_BLOCKED_2026-09-17.txt`
3. **[?] Hành vi hạn mức đã chốt** (xem checklist.md mục D): chặn đơn nháp vượt hạn mức **ngay lúc
   tạo** (theo v2.2 MUST-3) — nếu muốn theo prompt cũ (chỉ chặn lúc submit) thì nói, tôi đổi
4. **[x] Drop 12 cột rác `tabBatch`** — đã làm 2026-09-17 (patch + backup + verify)
5. (Tuỳ chọn) Dọn fixture acceptance: mọi bộ `run()` đều tự dọn trước khi chạy, không cần tay

## Roadmap phase tiếp theo

| Phase | Nội dung chính | Điều kiện tiên quyết |
|---|---|---|
| **P0.5** | Import nợ đầu kỳ, tạo `opening_journal_entry`, import trại/lứa | P0 ✓ |
| **P1A** | ✅ Sales Invoice → Batch Debt theo lứa (kể cả nhiều nhóm thuế/lứa); cascade cancel có bảo vệ `paid_amount > 0` | P0 ✓ |
| **P1B** | ✅ Payment Entry → Payment Allocation FIFO; cancel đảo ngược. Còn: phân bổ thủ công, nhánh JE cho nợ đầu kỳ, unreconcile | P1A ✓ |
| **P1C** | ✅ Credit Score + hạn mức theo tier, chặn đơn vượt hạn (commit `7c62129`) | P1B ✓ |
| **P1D** | ✅ Sales Return Request → credit note thật → `returned_amount` DERIVED (commit `c5152dd`) | P1B ✓ |
| **P1E** | ⛔ BLOCKED: e-invoice VN — cần sandbox provider + mã số thuế (không mock) | P1A ✓ |
| **P1F** | ✅ Consent + Debt Confirmation Slip (print format) + Livestock offset (JE) + Batch tách/gộp bảo toàn nợ (commit `b48d723`) | P1A ✓ |
| **P1G** | ✅ 7 báo cáo Desk + script AR vs Batch Debt (integrity 9/9) + Exit Gate Phase 1 (`EXIT_GATE_PHASE1.md`); review round đã cứng hóa kiểm chứng (D26) | P1A–P1F (P1E blocked, phần e-invoice không chặn báo cáo) |
| **P2/P4** | App nhân viên (Flutter) + Zalo Mini App nông dân | P1A–B |
| **P3** | AI: kill switch vận hành, voice→action, Action Item (read-only posture) | P1 + config |

## Việc bảo trì nhỏ khi rảnh
- Bổ sung test riêng cho `Feed Batch.validate_dates` (đang được phủ gián tiếp qua acceptance)
- Cân nhắc chuyển `expected_end_date` từ suy-đoán-cứng (`DEFAULT_CYCLE_DAYS`) sang cấu hình Settings (P1)
