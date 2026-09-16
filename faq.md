# faq.md — thắc mắc, hiểu sai & giải thích (cập nhật 2026-09-16)

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

## 10. `open-code-review` (OCR) có chạy không?

Không khả dụng trong phiên này (chưa cài/cấu hình). Theo AGENTS.md, review fallback về chế độ thủ công:
tự đọc diff, tự soi các nhóm lỗi phổ biến + Ponytail + scope compliance — đã thực hiện trong phiên khi
sửa các lỗi trên (kèm run test thật sau mỗi fix). OCR được user cài đặt thủ công, agent không tự cài.
