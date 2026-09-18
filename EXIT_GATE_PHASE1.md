# EXIT GATE — PHASE 1 (Core ERP) · app_cam / feed_dealer

Scope: **P1A–P1F gộp lại** (đây là cổng ra khỏi Phase 1, không phải cổng của từng phase lẻ),
cộng thêm P1G (integrity + reports) và P0 (nền DocType).
Site: ERPNext **v16** (`frontend`), app `feed_dealer`, module `Feed Dealer`.
Ngày soát: **2026-09-17**. Người soát: agent (tự đánh giá) — **cần chủ dự án xác nhận** các mục
UX/Audit trước khi coi là cổng chính thức.

Legend: `PASS` = có bằng chứng chạy thật · `BLOCKED` = phụ thuộc bên ngoài · `NOT ASSESSABLE` = chưa
đủ dữ liệu/điều kiện để đánh giá (không phải "đã đạt").

---

## 0. Bằng chứng nền (chạy trên site thật, sau `clear-cache`)

| Bộ | Kết quả | Log |
|---|---|---|
| P0 acceptance | **9/9 PASS** | `/tmp/p0_fin.log` |
| P1A order→batch debt | **8/8 PASS** | `/tmp/p1a_fin.log` |
| P1B payment allocation | **10/10 PASS** | `/tmp/p1b_fin.log` |
| P1C credit limit | **10/10 PASS** | `/tmp/p1c_fin.log` |
| P1D sales return | **8/8 PASS** (có T8 mới) | `/tmp/p1d_fin.log` |
| P1F legal / livestock / batch ops | **9/9 PASS** | `/tmp/p1f_fin.log` |
| **P1G integrity (AR vs Batch Debt)** | **9/9 PASS** | `/tmp/p1g_fin.log` |
| **P1G reports** | **4/4 PASS** | `/tmp/p1g_rep_fin.log` |
| **P0.5 opening-balance migration** | **6/6 PASS** (từ site trắng) | `/tmp/p05_run*.log` |
| **Performance smoke (2.000 giao dịch)** | integrity **9/9 PASS**, on_submit 651 ms, reports ≤ 0,16 s | `/tmp/perf_big.log` (`/tmp/perf_pilot.log` cho mốc 100) |
| Mutation-check C1/C7 (bỏ `offset_amount` khỏi công thức nợ) | C1+C7 **đỏ**, diff đúng 1.205.000 | `/tmp/p1g_mut.log` |
| Mutation-check P0.5 (đảo needle adoption) | T5 **đỏ** (`11→12`), khôi phục → xanh 6/6 | `/tmp/p05_mut*.log` |

---

## 1. Functional completeness — **PASS (một phần BLOCKED)**

- Chuỗi nghiệp vụ chạy thật đầu-cuối: SO → SI (nhiều lứa / nhiều thuế suất trong 1 hoá đơn) →
  Batch Debt → Payment Entry (FIFO) → Payment Allocation → Sales Return → Livestock Offset →
  tách/gộp lứa → phiếu xác nhận nợ. Mỗi mắt xích có acceptance riêng (bảng mục 0).
- P1G dựng dataset ngẫu nhiên **60 giao dịch** (seed cố định) sinh ra: 70 hoá đơn (10 credit note),
  13 SO→SI, 45 phiếu thu (14 có `references`, 31 để trống), 5 phiếu thu vượt nợ (advance),
  10 lần trả hàng, 3 cút toán cấn trừ, 94 khoản nợ đang mở, 18 lứa.
- **BLOCKED: P1E hoá đơn điện tử** — xem `result_P1E_BLOCKED_2026-09-17.txt`. Thiếu provider
  (VNPT/Viettel/MISA) + credential sandbox + MST + quy ước NĐ123. Không có mock thay thế.
- Acceptance gốc của prompt P1G, mục 3 ("credit limit + e-invoice smoke"): credit limit **PASS**,
  e-invoice **BLOCKED** ⇒ tiêu chí này *chưa đóng được* vì một nửa của nó phụ thuộc bên ngoài.

## 2. Security / phân quyền — **PASS (có 1 mục mở)**

- Ngưỡng hạn mức chỉ Manager override được, và override luôn bị ghi vết (P1C T6: reason bắt buộc,
  `override_by`/`override_date` do hệ thống đóng dấu, không lấy từ payload).
- Cùng một luật hạn mức cho mọi đường vào: hook Sales Order và API dùng chung
  `feed_dealer.credit_limit.credit_position` (P1C T9/T10, và P1G R4 so lại đúng con số đó).
- Consent dữ liệu cá nhân là gate *cảnh báo* có chủ đích, và không thể "bật lại" một phiếu đã rút
  (P1F T1/T2) — lịch sử chỉ ghi thêm, không sửa.
- Cấn trừ chỉ hợp lệ khi nhà cung cấp trên hoá đơn mua **trùng chủ thể** khách hàng (P1F T8/T9).
- Cấn trừ tiền qua Journal Entry: guard chủ thể nằm ở `validate` (chạy TRƯỚC khi ghi), P1F T9 chứng
  minh JE **không bao giờ được insert** khi sai chủ thể.
- 7 report gắn role theo đúng vai trò (Manager/Staff) — đã đối chiếu `Has Role` trên site.
- Đã grep diff: **không có secret/credential** trong mã nguồn (`.env` được gitignore; credential
  MCP nằm ở `/tmp`, ngoài repo).
- **Mở (không phải lỗi):** chưa có **tài khoản người dùng thật** cho Desk. Hiện chỉ có user test
  (`p0-acceptance-*`, `p1c-acceptance-*`). Vai trò `Feed Farmer`/Manager/Staff đã tồn tại, **chưa có
  vai trò `Driver`** (thuộc P2). Cần chủ dự án cấp tài khoản + chính sách mật khẩu trước P2.

## 3. Data integrity (SoT) — **PASS**

Hai đẳng thức phải đúng **tuyệt đối** trên dataset ngẫu nhiên, và đều đúng (sai số **0 đồng**):

```
(1) BD = TT − PA − OF
    BD 11.289.000 = TT 156.345.000 − PA 143.851.000 − OF 1.205.000     diff = 0
(2) BD − TE = (−U) + ER + (−PA) + (−OF)
    −121.467.000 = −17.783.500 + 41.372.500 − 143.851.000 − 1.205.000  residual = 0
```

- **Ngưỡng dung sai = 0 đồng**, lý do: mọi đại lượng là tổng số nguyên VND trên cùng các cột —
  không quy đổi ngoại tệ, không làm tròn phần trăm, không phân bổ theo tỷ lệ. Sai số ≠ 0 luôn là
  thiếu một số hạng trong đẳng thức hoặc lỗi thật, không bao giờ là nhiễu số thực. **Ngưỡng không
  được nới để cho qua** — mutation-check ở mục 0 chứng minh 2 check này có răng.
- Kiểm tra kèm: không khoản nợ đang mở nào âm; `status` khớp tiền; `Feed Batch.total_debt` = tổng
  nợ mở của lứa (18 lứa); phân bổ FIFO không bao giờ vượt số tiền phiếu thu đã nhận; số ERPNext
  đã cấn trừ ở cấp hoá đơn = tổng `references`; credit note khớp `returned_amount`.
- Nguyên tắc một-nguồn-sự-thật được giữ: AR là SoT; Batch Debt chỉ là lớp view;
  `allocated_amount` không bị sửa tay; `returned_amount`/`offset_amount`/`paid_amount` chỉ do
  controller của chúng ghi.

**Số liệu để chủ dự án quyết về FIFO (VIỆC 1 kỳ trước):**

```
PA (FIFO phân bổ theo lứa)          = 143.851.000
ER (ERPNext cấn trừ ở cấp hoá đơn)  =  41.372.500
gap (ER − PA)                       = −102.478.500
31/45 phiếu thu KHÔNG điền `references` (đúng giả định D19)
```

Nghĩa là: với 71% số phiếu thu, ERPNext ghi "hoá đơn còn nợ" trong khi Batch Debt ghi "đã trả theo
lứa" — đúng như D19 đã tuyên bố, không phải lỗi. Nhưng nó là **102,5 triệu đồng** mà hai lớp kể hai
câu chuyện khác nhau, nên đây là con số cần trước khi quyết có sửa FIFO hay không.

> **QUYẾT ĐỊNH ĐÃ CHỐT (2026-09-17):** giữ nguyên cơ chế FIFO, **không sửa**. Đây là hệ quả đúng của
> kiến trúc view-layer (Batch Debt là lớp view trên AR), không phải bug — `residual = 0` trên đẳng
> thức (2) đã chứng minh không mất tiền, không đúp tiền. Mục này **đóng**, giữ lại vết quyết định.

## 4. Failure recovery / không phá sổ khi huỷ — **PASS**

- Huỷ SI có nợ đã thu tiền → **chặn trước khi ghi** (`before_cancel`), SI vẫn ở trạng thái submit
  (P1A T5). Huỷ PE → allocation bị huỷ, nợ mở lại (P1B T4). Huỷ credit note → `returned_amount`
  giảm, nợ mở lại (P1D T5). Huỷ JE cấn trừ → nợ mở lại (P1F T7). Unreconcile Payment (ERPNext gỡ
  hoà giải nhưng PE vẫn submit) cũng được đảo allocation (P1B T8).
- Hook idempotent: submit lại SI/PE không nhân đôi tiền (P1A T7/T8, P1B T7).
- DDL cột rác `tabBatch` chạy bằng patch idempotent, tự chặn nếu còn dữ liệu, và đã **backup
  trước**: `private/backups/20260917_090031-frontend-database.sql.gz` (2,2 MiB).
- Patch in report (`reimport_reports`) idempotent; patch in Print Format không bao giờ ghi đè bản
  người dùng đã sửa.
- **AI tắt không ảnh hưởng:** Phase 1 không có đường code AI nào (`AI Workflow Config` chỉ là
  skeleton, không import LLM, không ghi tài chính). Toàn bộ logic tiền nằm trong Python controller.

## 5. Audit trail — **PASS**

- Mọi DocType động tiền đều `track_changes=1`: Batch Debt, Payment Allocation, Sales Return Request,
  Batch Operation, Debt Confirmation Slip (+ Credit Score, Collateral, E-Invoice Log).
- Report **Nhật ký phê duyệt** trả về **40 dòng** thật, gồm cả quyết định trả hàng và override hạn
  mức (`kind` = `Sales Return Request`, `Credit Limit Override`).
- Phê duyệt trả hàng ghi `approved_by`/`approved_at` cùng lúc với `batch_debt_adjusted` trong một
  lần save; nếu save lỗi thì credit note bị huỷ+xoá và nợ được tính lại (P1D).

## 6. Performance — **PASS (with caveat)**

Đo thật ngày **2026-09-17** bằng `feed_dealer.setup.p1g_perf` (tái dùng đúng generator của P1G, chạy
under prefix `P1G-PERF` riêng, log `/tmp/perf_pilot.log` + `/tmp/perf_big.log`).

**Dataset đo:** 2.000 giao dịch ngẫu nhiên (seed cố định) → 2.285 hoá đơn (285 credit note), 400
SO→SI, 1.425 phiếu thu (400 có `references`, 1.025 để trống), 127 phiếu thu vượt nợ, 163 bút toán
cấn trừ vật nuôi, 879 hoá đơn nhiều lứa, 537 hoá đơn 2 thuế suất. Build: **865,8 s** (432,9 ms/giao
dịch).

| Đo | Dataset 100 giao dịch (pilot) | Dataset 2.000 giao dịch | Ngưỡng |
|---|---|---|---|
| (a) 1 `Sales Invoice` insert+submit | 486 ms | **651 ms** | — |
| (b) integrity AR-vs-Batch-Debt (9 check) | 0,129 s | **0,747 s** | — |
| (c) `debt_by_batch` | 0,141 s / 142 dòng | 0,137 s / 154 dòng | — |
| (c) `payment_allocation_detail` | 0,005 s / 285 dòng | 0,029 s / 3.857 dòng | — |
| (c) `customer_credit_limit` (Script, gọi gate) | 0,134 s / 40 dòng | **0,160 s** / 40 dòng | — |
| (c) `overdue_batch_debts` / `cash_flow_30_60_90` / `batch_profit_loss` / `approval_audit_log` | 0,002–0,006 s | 0,004–0,008 s | — |
| integrity trên dữ liệu lớn | — | **9/9 PASS** (C1 diff = 0 đúng) | 0 đồng |

**Đọc số:**
- `on_submit` một hoá đơn tăng từ 486 ms → 651 ms khi lịch sử khách tăng ~20 lần (100 → 2.000 hoá
  đơn). Đây là **quan hệ dưới tuyến tính** — không có vụ nổ tổ hợp (N+1) nào trong hook; hoá đơn mới
  nhất vẫn ở mức dưới 1 giây.
- Toàn bộ 9 check integrity chạy dưới 1 giây trên 2.285 hoá đơn.
- Báo cáo nặng nhất là `customer_credit_limit` (Script Report **gọi `credit_position()` cho từng
  khách** thay vì tự viết SQL — chọn đúng để không lệch với gate, xem mục 2) ở 0,16 s cho 40 khách.
  Nếu số khách tăng lên hàng nghìn thì đây là chỗ cần để ý trước (mỗi khách = 1 lượt tính lại nợ
  mở + SO chưa xuất hoá đơn + SO nháp), **không phải chỗ để tối ưu ngay**.
- **Caveat:** đây là smoke test trên **một** máy và **một** luồng đơn (600 giao dịch sớm nhất trên
  site, single-process bench). Chưa đo: đồng thời nhiều người dùng, dữ liệu lớn hơn nữa, DB đã
  chạy lâu (bloat), EXPLAIN/index cho report theo lứa. Vì vậy là **PASS with caveat**, không phải
  "đã tối ưu".
- Dataset đo **đã được dọn** sau khi đo (`p1g_perf.cleanup`); dataset P1G (60 giao dịch) **không bị
  đụng** vì perf dùng prefix + Site Default key riêng.

## 7. UX / Desk — **NOT ASSESSABLE (lớn)**

- Report đã cài đúng chuẩn, hiện trong Desk, có role; Print Format phiếu xác nhận nợ đã cài
  (P1F T3 render Jinja thật).
- **Chưa** ai (người thật) mở Desk để xác nhận: chưa có tài khoản người dùng thật, chưa chụp màn
  hình, chưa kiểm layout/tiếng Việt trên UI. "Báo cáo khớp vài case thủ công để Owner review được"
  ⇒ số liệu đã sẵn (mục 3) nhưng **việc review thủ công là của chủ dự án**, agent không tự ký duyệt.

---

## Kết luận cổng ra Phase 1

| Tiêu chí DoD | Kết quả | Ghi chú |
|---|---|---|
| Functional | PASS (trừ e-invoice) | P1E BLOCKED, cần provider |
| Security | PASS | mở: tài khoản người dùng thật + role Driver (P2) |
| Data integrity | PASS | 2 đẳng thức, sai số 0 đồng, có mutation-check |
| Failure recovery | PASS | huỷ/khôi phục + idempotent + backup |
| Audit | PASS | track_changes + report nhật ký 40 dòng |
| Performance | PASS (with caveat) | đo thật trên 2.000 giao dịch (xem mục 6) |
| UX Desk | **NOT ASSESSABLE** | chưa có user thật/ảnh chụp |

**⇒ Cổng Phase 1: 6/7 PASS (1 có caveat), 1/7 chưa đủ điều kiện đánh giá, 1 hạng mục BLOCKED bên
goài (P1E).** Chưa thể coi là "thoát Phase 1" cho tới khi: (a) P1E có provider, (b) có tài khoản
người dùng thật để review UX. Mục Performance giờ có số thật (mục 6) nên đã chuyển từ NOT
ASSESSABLE sang PASS-with-caveat — chưa có ngưỡng chính thức từ chủ dự án, agent **không tự đặt
ngưỡng** để tự ký duyệt.

## Việc còn treo (đã ghi vào `next.md`)

1. P1E provider + credential sandbox.
2. Tài khoản Desk thật (Manager/Staff/Driver) + chính sách mật khẩu.
3. ~~Ngưỡng hiệu năng + đo trên dữ liệu lớn~~ — **đã đo 2026-09-17** (mục 6, 2.000 giao dịch). Còn
   lại: nếu chủ dự án muốn một ngưỡng SLA chính thức (vd "on_submit < 2 s ở 10k hoá đơn") thì nêu
   ra để lần đo sau đối chiếu, chứ hiện chỉ có số, chưa có ngưỡng.

### Đã đóng

- ~~**P0.5 import nợ đầu kỳ**~~ — **DONE 2026-09-17 10:42** (6/6 PASS từ site trắng, mutation-check
  có răng). KHÔNG còn là nợ trước go-live; chỉ cần chạy lại với file khách hàng thật khi go-live.
  Xem `working.md` 2026-09-17 10:42 + `result_2026-09-17_1042_P05_migration.txt`.
- ~~**Quyết định FIFO vs `references`**~~ — **ĐÓNG 2026-09-17**: giữ nguyên FIFO (lý do ở mục 3).
