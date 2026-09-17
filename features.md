# features.md — tính năng hiện có & tương lai (cập nhật 2026-09-17)

Nguồn tổng hợp: `.plan/plan_final*.md` + v2.1/v2.2/v2.3 patches, OpenSpec change `p0-feed-dealer-foundation`, app `apps/feed_dealer`.

## 1. Đã có (P0 schema + P1A/P1B business logic)

### Nền tảng
| Thành phần | Chi tiết |
|---|---|
| App `feed_dealer` | ERPNext **v16** (frappe 16.17.2 / erpnext 16.16.0), module `Feed Dealer`, cài trên site `frontend` của Mac qua bind-mount |
| 19 DocType | `Feed Dealer Settings` (Single), `Feed Batch` (lứa nuôi, mã `LOT-YYYY-#####`), `Batch Debt`, `Payment Allocation`, `Credit Score`, `Collateral`, `Action Item`, `Data Processing Consent` (+`Consent Scope Item`), `Debt Confirmation Slip` (+`Debt Confirmation Item`), `Batch Operation` (+`Source`/`Target`), `Sales Return Request` (+`Sales Return Item`), `AI Workflow Config`, `E-Invoice Log`, `Livestock Sale` (child) |
| Bất biến tài chính | `Batch Debt` là **lớp phân bổ thôi**: `paid_amount`, `returned_amount`, `outstanding_amount`, `status`, `overdue_days`, `late_payment_fee` = read-only + controller tính lại; ERPNext AR vẫn là nguồn sự thật tiền |
| Quy tắc hoá đơn | `sales_invoice` reqd=0 + `mandatory_depends_on` + **controller bắt buộc**: nợ thường thiếu hoá đơn → từ chối; nợ đầu kỳ (`is_opening_balance=1`) → cho phép, cột `opening_journal_entry` (P0.5 mới tạo JE thật) |
| Hooks | `Sales Invoice` on_submit + **before_cancel** + on_cancel (P1A), `Payment Entry` on_submit + on_cancel (P1B), `Feed Batch` on_update (còn stub) |
| Field tuỳ biến | `custom_batch` (Link → Feed Batch) trên **Sales Invoice Item**, sync qua `after_migrate` (`feed_dealer/setup/custom_fields.py` + `custom_field.json`) |
| Roles | `Feed Dealer Manager` (full), `Feed Dealer Staff` (đọc tài chính), `Feed Driver` (đọc), `Feed Farmer` (if_owner) — chưa có role AI (P3, posture read-only đã chặn sẵn) |
| Masters seed | UOM `Bao`/`Tấn`/`Kg` + conversion (1 Bao = 25 Kg, 1 Tấn = 1000 Kg, category Mass), Item Groups cám lợn/gà/cá + thuốc thú y, Customer Groups 4 bậc, Price Lists Giá sỉ/Giá lẻ — create-if-absent, không đè dữ liệu thật |
| Bằng chứng tự động | `bench execute feed_dealer.setup.p0_acceptance.run` → 9/9 PASS, `debug()` in traceback thật |

### 1b. P1A + P1B — business logic đã chạy thật (2026-09-16)

| Việc | Chi tiết | Bằng chứng |
|---|---|---|
| SI → Batch Debt | `on_submit` gộp theo `(custom_batch, item_tax_template)`, idempotent theo đúng bộ ba đó; dòng không lứa bỏ qua (tiền vẫn ở AR) | P1A T1/T2/T3/T6 |
| Chặn huỷ hoá đơn | guard `paid_amount > 0` đặt ở **before_cancel** (frappe ghi docstatus=2 trước on_cancel) | P1A T5 |
| Huỷ hoá đơn | cascade cancel debt + tính lại `Feed Batch.total_debt` | P1A T4 |
| PE → Payment Allocation | `Payment Entry.on_submit` cấp phát **FIFO theo `due_date`** trên các nợ `outstanding_amount > 0`; mỗi lát cắt = 1 `Payment Allocation` độc lập, submittable (`ALLOC-…`) | P1B T1/T3 |
| Đảo ngược | cancel PE → cancel allocation → tính lại nợ + `total_debt`; thanh toán lại được | P1B T4 |
| Chống trùng | hook chạy lại không cấp phát lần 2; tổng cấp phát ≤ `paid_amount` | P1B T6 |
| Lãi chậm trả | đọc `Feed Dealer Settings.late_payment_interest_rate` (fallback 0.00022), tính trên outstanding còn lại | P1B T5 |

### 1c. P1C — hạn mức tín dụng + đóng 2 lỗ hổng P1B (2026-09-16)
- **Gate hạn mức dùng chung** `feed_dealer/credit_limit.py`: công thức v2.2 MUST-3 (nợ đã xuất HĐ +
  đơn đã submit chưa xuất HĐ + đơn nháp); **một hàm** cho cả hook Desk lẫn API
- **Sales Order**: `validate` (đếm cả draft — chặn bypass nhiều đơn nháp) + `before_submit`
  (row lock `Credit Score` + re-check); từ chối thì đơn **vẫn là draft** (không ghi rồi mới báo lỗi)
- **Credit Score**: score/tier/`limit_by_score`/`limit_by_collateral`/`credit_limit` tính
  deterministic (không AI); **override chỉ Manager**, bắt buộc lý do, tự stamp `override_by/date`
- **Khách chưa có Credit Score = chặn** (hạn mức 0) — quyết định 2026-09-16, message chỉ rõ cần tạo gì
- **API** `get_credit_position` / `check_order_credit` (`@frappe.whitelist`) — có kiểm tra quyền đọc
  Credit Score của đúng khách đó (fail closed)
- **Đóng lỗ hổng P1B**: hook `Unreconcile Payment` đảo allocation khi ERPNext delink payment; FIFO
  lock row `Batch Debt` (`select … for update`) chống cấp phát trùng khi thanh toán đồng thời
- **Còn lại** (không giấu): phân bổ **thủ công** (hiện chỉ FIFO); nhánh Journal Entry riêng cho nợ đầu
  kỳ; `seasonal_limit_cap` là input tay; "trả đúng hạn/trả trễ" chỉ thấy nợ đang sống (settled debt
  bị reset `overdue_days`) → cần field lưu chuỗi quá hạn, để sau

### Công cụ triển khai (ngoài app, trong `.agent/`, không commit)
`gen_feed_dealer.py` (generator DocType), `push_to_mac.py` (sync app), `bench.py`/`mac.py`/`mcp_client.py` (cầu MCP sang Mac), `push_file.py`.

## 1h. P1G — Báo cáo Desk + đối chiếu AR vs Batch Debt (đã có, 2026-09-17)

| Nhóm | Chi tiết |
|---|---|
| 7 báo cáo Desk | `debt_by_batch` (Nợ theo lứa) · `overdue_batch_debts` (Nợ quá hạn) · `payment_allocation_detail` (Phân bổ thanh toán) · `cash_flow_30_60_90` (Dòng tiền 30-60-90) · `batch_profit_loss` (Lời/Lỗ theo lứa — mới có revenue, cost=NULL có chủ ý) · `customer_credit_limit` (Hạn mức tín dụng — Script Report gọi thẳng gate) · `approval_audit_log` (Nhật ký phê duyệt). Role: Manager/Staff tuỳ báo cáo |
| Script integrity | `feed_dealer.setup.p1g_integrity` — sinh dataset thật (seed cố định) rồi kiểm 2 đẳng thức tiền tuyệt đối (dung sai 0 đồng); có `cleanup()` để dọn |
| Exit Gate Phase 1 | `EXIT_GATE_PHASE1.md` — 7 tiêu chí DoD, ghi rõ tiêu chí nào PASS bằng chứng nào, tiêu chí nào NOT ASSESSABLE |
| Chưa làm (thuộc P1G nhưng cần bên ngoài) | Tài khoản Desk thật + role `Driver` (P2); ngưỡng & đo hiệu năng dữ liệu lớn |

### 1i. Review round sau P1G — cứng hóa KIỂM CHỨNG (2026-09-17)

Review **không** tìm ra lỗi trong code P1G/P1D, nhưng tìm ra 2 lỗ hổng ở cách kiểm chứng và đã đóng bằng code:

| Vấn đề | Cách đóng |
|---|---|
| Gọi lệnh `bench` kế tiếp ngay sau `--to-file` là **race**: `debug` đọc DB khi `cleanup` còn đang xoá ⇒ `8/9` vô nghĩa | `.agent/bench_wait.py` — chờ đúng dòng `=== EXIT n ===` của lần chạy đó rồi mới trả về (serialise) |
| Fixture kiểm bằng "đủ dòng" ⇒ 1 Sales Order lạc từ build chết vẫn để suite xanh | Check `C9` so **số đúng** (hoá đơn/SO/phiếu thu/credit note) với `shape` builder đã lưu; `ensure_dataset` dọn dataset dở trước khi build lại |
| Mutation-check / lỗi tự gây | C9 bắt được lỗi biến comprehension trùng tên (`UnboundLocalError`) — đã sửa + thêm assert số dòng SO→SI |

Tái xác minh trên revision đã review: `P1G INTEGRITY 9/9` · `P1G REPORTS 4/4` · `P1D 8/8` (log `/tmp/rv2_{p1g,rep,p1d}.log`).

## 2. Tương lai — theo plan & phase

### P0.5 — Migration dữ liệu
- Import nợ đầu kỳ thật; tạo `opening_journal_entry` cho từng dòng nợ; bắt buộc JE từ đây
- Import danh sách trại (Customer), lứa đang nuôi (`Feed Batch`), nợ theo lứa

### P1A — Bán cám & phân bổ nợ (✅ đã xong, xem mục 1b)
- Chưa làm: phân bổ **invoice-level discount** và xử lý thuế khi trả hàng (để P1D, khi có credit note)

### P1B — Thanh toán (✅ đã xong, xem mục 1b)
- Chưa làm: phân bổ **thủ công** (hiện chỉ FIFO), đối ứng JE riêng cho nợ đầu kỳ (FIFO đã phủ, chưa tách nhánh JE)
### P1D — Trả hàng (kế tiếp)
- `Sales Return Request` duyệt → credit note → chỉnh `batch_debt_adjusted`

### P1E–G — Tách/gộp lứa, xác nhận nợ, cảnh báo dịch bệnh
- `Batch Operation` tách/gộp kèm phân bổ nợ; `Debt Confirmation Slip` (kyc ảnh chụp ký); `linked_outbreak_alert` (chưa có DocType — đang cố ý bỏ trống)

### P2/P4 — Mobile
- Web Admin = Desk; App nhân viên + Zalo Mini App nông dân (đọc lứa/nợ của mình)

### P3 — AI
- `AI Workflow Config` kill switch, voice→action, `Action Item` duyệt/thực thi, posture read-only

## 3. Không thuộc phạm vi (non-goal)
Tự dựng stack ERPNext bằng Docker trong repo này (runtime thuộc về máy user) · UI Desk tùy biến sâu · HA/backup/monitoring production.
