# faq.md — thắc mắc, hiểu sai & giải thích (cập nhật 2026-09-17)

## 1. Vì sao vẫn còn 12 cột lạ trên `Batch` của ERPNext?

**Hiểu sai thường gặp:** "đổi tên xong là sạch hoàn toàn."

**Thực tế:** khi app từng khai báo DocType `Batch` trùng với ERPNext, Frappe đã **thêm cột** vào bảng
`tabBatch` (`customer`, `animal_type`, `start_date`, `expected_end_date`, `quantity`, `start_weight`,
`current_weight`, `status`, `split_operation`, `has_been_split`, `total_debt`, `notes`). Sau khi sửa
sang `Feed Batch`, migrate khôi phục **metadata** (fields, module `Stock`) nhưng **không bao giờ drop
cột**. Cột rác không gây lỗi gì — chỉ là rác schema — nhưng xoá là DDL không hoàn tác trên site thật
nên tôi để user quyết.

## 2. "ERPNext chạy ở máy Mac" — vậy máy này làm gì?

Máy này là **workspace viết code**, không phải nơi chạy ERPNext:
- Code viết tại repo `app_cam` (máy này)
- Đẩy sang Mac qua MCP bridge (Tailscale) → bind-mount vào bench container
- Chạy `bench migrate`/`execute` trên Mac qua bridge
- Đọc/ghi dữ liệu nhanh qua REST ngrok (`ERPNEXT_URL` + token)

Vì vậy **không cần** Docker ERPNext trên máy này → đã xoá theo yêu cầu.

## 3. `.env` là của Docker à? Sao không xoá?

Không. `.env` (phiên mới) chứa `ERPNEXT_URL`, `ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET` — là **đường vào
site thật qua ngrok**, không liên quan Docker (bản cũ có biến DB/admin cho stack Docker local, đã bị
bạn ghi đè). `.env` được `.gitignore` chặn khỏi commit.

## 4. `bench execute ... p0_acceptance.run` fail với `NameError: name 'feed_dealer' is not defined` — lỗi gì?

**Hiểu sai thường gặp:** tưởng là lỗi import app.

**Thực tế:** `bench execute` nuốt exception thật của hàm được gọi, rồi fallback sang
`eval(method)` → sinh `NameError` giả. Lỗi thật nằm sâu bên trong. Dùng
`p0_acceptance.debug` để in traceback thật của từng check. (Đã ghi vào README + LESSONS_LEARNED.)

## 5. Tại sao phải đặt `category` khi tạo `UOM Conversion Factor`?

ERPNext **v16** đổi model: `UOM` không còn `conversion_factor`/`reference_uom`; conversion là DocType
riêng và `category` (Link → `UOM Category`) là **bắt buộc**. Bỏ qua → `MandatoryError ...: category`.
Đây là khác biệt v15→v16 đầu tiên trong loạt gotchas đã ghi lại.

## 6. DocType app tôi phải tránh trùng tên với ERPNext? Như nào là "trùng"?

Tên DocType là **toàn cục** (global) trên một site, không phân biệt app. Hai app cùng khai báo 1 tên →
metadata của app sau **đè** app trước (module bị đổi, field mất). Cách kiểm tra: so tên DocType mới với
thư mục `doctype/` trong source `frappe` + `erpnext` trên bench (DB chỉ thấy 1 dòng/tên, không đủ).
Sự cố `Batch` của phiên này là ví dụ thật.

## 7. Sao acceptance test set `doc.owner = farmer` mà quyền if_owner không ăn?

Vì Frappe **ghi đè `owner` bằng session user** khi insert. Muốn fixture thuộc user khác phải
`db_set("owner", ...)` **sau** insert. Sai chỗ này thì test A5 "farmer thấy đúng lứa của mình" vẫn
PASS giả (vì fixture nằm dưới owner Administrator). Đã sửa + ghi lại.

## 8. Migrate chạy 2 lần có an toàn không? Sao lần 2 báo lock error trước đó?

An toàn — migrate được thiết kế idempotent. Lock error trước đó là do tôi vô tình chạy 2 migrate
**đồng thời**: Frappe giữ file lock `bench_migrate` → lần sau vào phải chờ, hết hạn thì
`LockTimeoutError` (không phải lỗi migrate). Chạy tuần tự là hết. Bài học: lệnh dài phải ghi log vào
file trong container rồi đọc sau, không giữ kết nối MCP chờ.

## 9. Tại sao không dùng "Custom DocType" (tạo qua REST/API) cho nhanh?

Custom DocType sống **chỉ trong DB** của site: không versioned trong git, không migrate được sang site
khác, và **không có Python controller** → P0 không viết được validate/bắt buộc bằng code. Nên P0 chấp
nhận đường dài hơn (push app + migrate) để schema nằm trong app.

## 10b. P1G đo được gì về chuyện FIFO bỏ qua `references`? Có phải lỗi không?

Không phải lỗi — là giả định D19 đang chạy đúng thiết kế, nhưng giờ đã có **số thật**: trên dataset
60 giao dịch, FIFO theo lứa phân bổ **143.851.000đ** trong khi ERPNext chỉ cấn trừ ở cấp hoá đơn
**41.372.500đ** (31/45 phiếu thu để trống `references`). Nghĩa là với 71% số phiếu thu, "hoá đơn còn
nợ" và "nợ theo lứa đã trả" nói hai chuyện khác nhau. Agent **không tự sửa** cơ chế phân bổ — con số
này để chủ dự án quyết. Đẳng thức AR-vs-Batch-Debt vẫn đúng tuyệt đối (lệch 0 đồng) và đã phân rã
thành 4 nhóm có tên, trong đó `ER − PA` chính là khoản lệch này.

## 10c. Vì sao check integrity dùng dung sai **0 đồng** mà không phải một ngưỡng "hợp lý"?

Vì mọi số hạng đều là tổng số nguyên VND trên cùng các cột — không quy đổi ngoại tệ, không làm tròn
phần trăm, không phân bổ theo tỷ lệ. Lệch khác 0 **luôn** là thiếu một số hạng trong đẳng thức hoặc
lỗi thật, không bao giờ là nhiễu số thực. Nới ngưỡng sẽ biến "phát hiện lệch" thành "che lệch".
Để chứng minh đẳng thức không phải đồ trang trí, tôi chạy **mutation-check**: bỏ `offset_amount` khỏi
công thức nợ trong code sản xuất → C1+C7 **đỏ**, lệch đúng 1.205.000; khôi phục → xanh lại.

## 10d. Vì sao chọn Script Report cho "Hạn mức tín dụng" thay vì SQL như 6 báo cáo kia?

Vì báo cáo này nói về **một luật** (P1C). Nếu viết lại bằng SQL ("hạn mức − nợ lứa"), nó sẽ hiện
NHIỀU hạn mức hơn số gate thực sự cho phép — gate còn trừ đơn đã submit chưa xuất hoá đơn và đơn nháp
đang giữ hạn mức. Script Report gọi thẳng `credit_position()`, tức là không có bản sao thứ hai của
luật; acceptance kiểm "report == gate" cho từng khách và còn kiểm phải có ít nhất 1 khách có cam kết
cấp đơn (nếu không thì không phân biệt được hai công thức).

## 10e. Sửa file báo cáo rồi `migrate` mà Desk vẫn hiện bản cũ?

Đúng bẫy đã gặp: `bench migrate` **chỉ import standard doc khi file mới hơn** dòng trong DB. Vì
`.agent/gen_reports.py` ghi `modified` cố định (để `--check` so byte-for-byte được), lần sửa sau bị
im lặng bỏ qua — đo được: `tabReport.query` vẫn là bản cũ dù file trên đĩa đã đúng. Cách xử lý: patch
`v1_0/reimport_reports` gọi `frappe.reload_doc(..., force=True)`, idempotent, chạy mỗi migrate. Kèm
một bẫy nữa: Query Report **phải bắt đầu bằng `SELECT`** — comment `--` ở đầu bị
`check_safe_sql_query` từ chối.

## 10f. Vừa sửa code xong là một loạt check đỏ — có phải tôi vừa gây regression?

**Chưa chắc.** Lần review P1G đã gặp đúng ca này: sau khi sửa `p1g_integrity.py`, chạy lại ra `8/9`
(C9 báo "dataset thoái hoá" — 0 return, 0 offset) và rất dễ kết luận code mới tự gây lỗi. Sự thật:
**bằng chứng bị nhiễm**. `--to-file` chỉ ghi log khi lệnh trong container kết thúc, nhưng call MCP có
thể trả về sớm hơn ⇒ `debug` chạy khi `cleanup` **còn đang xoá** và đọc một dataset bị xoá dở.

Dấu hiệu nhận biết: những con số **lẽ ra độc lập** với thay đổi của mình (PA, ER) **giống hệt** lần
trước, còn đúng nhóm số liên quan bị mất sạch (RT/OF = 0) ⇒ **dataset khác**, không phải logic khác.
Builder chạy trên site sạch vẫn cho `returns=10, offsets=3`. Kết luận: **chỉ được sửa code sau khi tái
lập được bằng chứng sạch**; dùng `.agent/bench_wait.py <log> <bench args>` (chờ dòng `=== EXIT n ===`)
để serialize các lệnh bench dài.

## 10g. Làm sao biết bộ test đang chạy đúng "dataset của tôi" mà không phải rác từ lần build chết?

Một lần build chết vì hook `Sales Invoice.on_submit` của **app khác** (`custom_app`) để lại 1 Sales
Order đã submit; lần retry build đè lên rác đó ⇒ DB có **13 SO trong khi builder khai 12**, mà suite
vẫn **xanh** vì đẳng thức tiền không liên quan tới đơn lẻ loi. Nên fixture **phải kiểm bằng SỐ ĐÚNG**
(hoá đơn / SO / phiếu thu / credit note) so với `shape` builder đã lưu — đó là việc của check `C9`, và
`ensure_dataset` dọn dataset dở (đã có customer nhưng thiếu marker) trước khi build lại.

## 10. `open-code-review` (OCR) có chạy không?

Không khả dụng trong phiên này (chưa cài/cấu hình). Theo AGENTS.md, review fallback về chế độ thủ công:
tự đọc diff, tự soi các nhóm lỗi phổ biến + Ponytail + scope compliance — đã thực hiện trong phiên khi
sửa các lỗi trên (kèm run test thật sau mỗi fix). OCR được user cài đặt thủ công, agent không tự cài.
