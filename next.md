# next.md — roadmap (cập nhật 2026-09-16 09:45)

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

## Sắp tới — ngắn hạn (chờ quyết định của user)

1. **[?] Drop 12 cột rác trên `tabBatch`** — script đã chuẩn bị, DDL không hoàn tác → cần duyệt
2. **[?] Commit** — repo chưa có commit nào; sau khi user duyệt thì commit `apps/feed_dealer` +
   `openspec/` + `deploy/` + các file md (gồm cả lô P1A fix + P1B)
3. **[?] 2 lỗ hổng P1B** (design.md D14): *Unreconcile Payment* không bị đảo ngược; thanh toán đồng
   thời cùng khách có thể cấp phát vượt → quyết định làm ngay hay để P1C
4. (Tuỳ chọn) Dọn fixture acceptance: `bench execute feed_dealer.setup.p1b_acceptance.cleanup`
   (P1B `run()` tự dọn trước mỗi lần chạy; P1A có `p1a_acceptance.cleanup`)

## Roadmap phase tiếp theo

| Phase | Nội dung chính | Điều kiện tiên quyết |
|---|---|---|
| **P0.5** | Import nợ đầu kỳ, tạo `opening_journal_entry`, import trại/lứa | P0 ✓ |
| **P1A** | ✅ Sales Invoice → Batch Debt theo lứa (kể cả nhiều nhóm thuế/lứa); cascade cancel có bảo vệ `paid_amount > 0` | P0 ✓ |
| **P1B** | ✅ Payment Entry → Payment Allocation FIFO; cancel đảo ngược. Còn: phân bổ thủ công, nhánh JE cho nợ đầu kỳ, unreconcile | P1A ✓ |
| **P1C** | Credit Score + hạn mức theo tier, chặn đơn vượt hạn | P1B |
| **P1D** | Sales Return Request → credit note → chỉnh nợ | P1B |
| **P1E–G** | Batch Operation tách/gộp; Debt Confirmation Slip; Outbreak Alert (cần DocType mới) | P1B |
| **P2/P4** | App nhân viên (Flutter) + Zalo Mini App nông dân | P1A–B |
| **P3** | AI: kill switch vận hành, voice→action, Action Item (read-only posture) | P1 + config |

## Việc bảo trì nhỏ khi rảnh
- Bổ sung test riêng cho `Feed Batch.validate_dates` (đang được phủ gián tiếp qua acceptance)
- Cân nhắc chuyển `expected_end_date` từ suy-đoán-cứng (`DEFAULT_CYCLE_DAYS`) sang cấu hình Settings (P1)
