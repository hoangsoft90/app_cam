## MỤC LỤC & THỨ TỰ THỰC THI TỔNG THỂ

File này dài vì bao phủ nhiều tình huống — nhưng luồng thực thi cho 1 task **lớn** luôn theo đúng thứ tự sau (không theo thứ tự xuất hiện trong file):

```
Session Start → (Retrieval nếu cần tra cứu) → làm việc/code
  → [Nếu task lớn] Git & Kiểm thử (test/lint)
    → Code Review (OCR [open-code-review] → Ponytail → Impact → Dead code → Pattern → Scope → Lessons → OCR re-check)
      → [Nếu OCR re-check vừa tự fix code] quay lại Git & Kiểm thử (test/lint) trước khi đi tiếp
      → [Nếu chạm vùng rủi ro] DỪNG, chờ user xác nhận
    → Task Completion Hook (ADR/AgentMemory/index/working.md)
  → Commit
```

Tra cứu nhanh theo mục: Quy ước định danh & Priority Resolution · Ponytail (vùng loại trừ an toàn) · Ngưỡng task lớn · Retrieval (4 bước) · Persistence & Task Completion Hook · Git & Kiểm thử · Code Review · Graceful Degradation.

## QUY ƯỚC ĐỊNH DANH (BẮT BUỘC — dùng xuyên suốt mọi tool)

- **`project`**: LUÔN LUÔN dùng tên thư mục gốc của repo (kebab-case, lấy từ `basename $(git rev-parse --show-toplevel)` hoặc tên folder hiện tại nếu không phải git repo). Dùng thống nhất giá trị này cho AgentMemory (`project`), codebase-memory-mcp (`project`), cocoindex-code (thư mục làm việc). KHÔNG tự đặt tên khác hoặc viết tắt.
  > Lưu ý: với AgentMemory, `project` là tham số chính thức chỉ ở một số endpoint (`/session/start`, `/observe`, `/context`). Các endpoint tra cứu/tìm kiếm khác (`/search`, `/memories`, `/lessons`, `/crystals`, `/semantic`, `/procedural`) không có filter `project` riêng — phải tự nhúng tên project vào `query`/`content`/`concepts` theo đúng convention ở mục "Quy tắc Cách ly Đa Dự án" và "Persistence Strategy", không giả định có tham số lọc sẵn.
- **`sessionId`**: Sinh MỘT LẦN khi session chính khởi động, theo format `<project>-<YYYYMMDD-HHmm>` (giờ local lúc bắt đầu). Toàn bộ sub-agent/worker được tạo ra từ session này PHẢI dùng lại đúng `sessionId` đó khi gọi AgentMemory (`/observe`, `/remember` với `sessionIds: [sessionId]`) — không tự sinh sessionId riêng, tránh phân mảnh dữ liệu.
- Nếu không chắc `project`/`sessionId` hiện tại là gì, gọi `GET /sessions` (AgentMemory) để tra lại session gần nhất thay vì đoán.

## QUY TẮC PHỐI HỢP OPENSPEC + SUPERPOWERS + MATTPOCOCK SKILLS + PONYTAIL (BẮT BUỘC — tránh chồng lấn)

File này (AGENTS.md) là **lớp hạ tầng retrieval & memory** — nó KHÔNG thay thế, không quyết định workflow hay tác phong kỹ thuật. Dưới đây là thứ tự bắt buộc khi phối hợp 4 lớp: OpenSpec (workflow), Superpowers (tác phong kỹ thuật), mattpocock/skills (công cụ rời), Ponytail (chống over-engineering).

### Thứ tự bắt buộc

1. Nếu đang trong 1 OpenSpec change (đã có thư mục `openspec/changes/<tên>/`), KHÔNG kích hoạt Superpowers brainstorming skill để hỏi lại "vấn đề là gì/scope là gì" — `proposal.md` đã trả lời việc này. Chỉ hỏi nếu `proposal.md` thiếu chi tiết CỤ THỂ cho task đang làm (không phải hỏi lại toàn bộ context).
2. Nếu CHƯA có OpenSpec change nào cho việc đang bàn, brainstorming skill của Superpowers được phép chạy tự do — đây là lúc để làm rõ ý tưởng TRƯỚC KHI tạo proposal.
3. Không dùng các skill sau của mattpocock trong bất kỳ tình huống nào, kể cả khi đã lỡ cài: `grill-me`, `grill-with-docs`, `tdd`, `diagnosing-bugs`, `to-prd`, `to-issues`, `triage` — toàn bộ chức năng này đã được OpenSpec (spec/task) và Superpowers (brainstorming/TDD/debugging) đảm nhiệm.
4. mattpocock skills (`prototype`, `handoff`, `git-guardrails`, `improve-codebase-architecture`, `writing-great-skills`) chỉ được gọi khi user CHỦ ĐỘNG nhắc tên skill đó trong prompt. Không tự suy luận và tự kích hoạt chúng.
5. TDD/systematic-debugging của Superpowers luôn áp dụng trong bước `/opsx:apply`, không cần user nhắc riêng. (Xem thêm mục "Git & Kiểm thử" bên dưới — đây chính là nơi TDD của Superpowers được thực thi cụ thể cho từng task lớn.)
6. Ponytail chỉ chi phối LƯỢNG CODE IMPLEMENTATION, không được phép bỏ qua hoặc rút gọn bước viết test/verification mà Superpowers TDD yêu cầu — kể cả ở mức "ultra". Test tối thiểu luôn phải có, dù ngắn. (Không mâu thuẫn với mục "Git & Kiểm thử" — test là bắt buộc bất kể Ponytail đang ở mức nào.)
7. Nếu một quyết định kiến trúc phát sinh trong lúc OpenSpec đang chạy 1 task: vẫn lưu ADR theo Bước 2 (retrieval flow) như bình thường — OpenSpec quản lý workflow, ADR quản lý lý do/quyết định lâu dài, hai cái không thay thế nhau.

### Thứ tự ưu tiên khi có xung đột (Priority Resolution)

Khi 2 nguồn chỉ dẫn mâu thuẫn nhau trong cùng 1 tình huống, áp dụng đúng thứ tự sau (cao hơn thắng):

1. **Chỉ dẫn tường minh của user trong prompt hiện tại** (vd: "bỏ qua warning này", "làm theo cách X") — LUÔN thắng, TRỪ vùng an toàn ở mục 2.
2. **Vùng loại trừ an toàn của Ponytail + "KHÔNG được AI tự ký duyệt"** (mục Code Review) — không thể bị user "lờ đi" bằng câu nói thông thường giữa chừng; chỉ được vượt qua bằng xác nhận rõ ràng đúng quy trình đã định nghĩa ở mục đó (không phải một câu nhắc nhở ngẫu nhiên).
3. **Phạm vi đã chốt trong OpenSpec** (`proposal.md`/`tasks.md`) — nếu đang có change đang chạy, không tự ý làm khác phạm vi đã duyệt.
4. **`operating_rules.md`** (rule riêng của project).
5. **TDD/systematic-debugging của Superpowers** — luôn bắt buộc, không được Ponytail cắt giảm (xem mục 6 ở trên).
6. **Ponytail** (chỉ chi phối lượng code implementation, không chi phối test/an toàn).
7. **Gợi ý của OCR/công cụ review khác** — mang tính tư vấn (advisory): High/Medium phải xử lý hoặc giải thích rõ lý do giữ nguyên, nhưng không tự động override các mục 1-6 ở trên.

Nếu vẫn không rõ ai thắng sau khi áp bảng trên (hiếm gặp): dừng lại, hỏi user thay vì tự chọn.

**Decision Rule (áp dụng khi bảng trên chưa xử lý dứt điểm):**
- Nếu 2 rule cùng mức ưu tiên mâu thuẫn nhau và không rõ cái nào nên thắng: hỏi user, không tự suy luận.
- Nếu phải chọn giữa 2 cách làm mà một cách rủi ro cao hơn cách kia (dù cùng mức ưu tiên): chọn phương án an toàn hơn.
- Nếu user override vùng an toàn Ponytail (mục 2) bằng xác nhận rõ ràng đúng quy trình: phải ghi lại qua `POST /observe {hookType: "safety-override", ...}` VÀ yêu cầu user xác nhận thêm lần 2 trước khi tiếp tục — không tiến hành chỉ với 1 lần xác nhận.

### Quy ước đặt tên OpenSpec change/spec (BẮT BUỘC)

- Tên thư mục change (`openspec/changes/<tên>/`) và tên thư mục spec (`openspec/specs/<tên>/`) **KHÔNG được bắt đầu bằng ký tự số**.
- Nếu tên tự nhiên bắt đầu bằng số (vd: "2fa-login", "3d-viewer"), phải thêm tiền tố chữ hoặc đổi cách viết để ký tự đầu tiên là chữ cái (vd: `two-factor-login`, `add-2fa-login`, `x3d-viewer`) — không giữ nguyên số ở đầu.
- Áp dụng cho MỌI change/spec mới tạo, kể cả khi user gõ tên gợi ý có số ở đầu trong prompt — agent phải tự điều chỉnh tên cho đúng quy ước trước khi tạo thư mục, và báo cho user biết tên đã đổi thành gì.

### Ponytail — vùng loại trừ an toàn (universal, mọi project)

Ponytail mặc định BẬT. TẮT ponytail (gõ "stop ponytail" trước khi code, bật lại bằng "/ponytail full" sau khi xong) khi code thuộc bất kỳ đặc điểm nào sau đây — xét theo TÍNH CHẤT của đoạn code, không theo tên dự án:

1. **KHÔNG THỂ HOÀN TÁC (irreversible):** Thao tác ghi/xóa/gửi mà một khi chạy xong không rollback được (xóa dữ liệu, gửi ra ngoài hệ thống, ghi đè file).
2. **LIÊN QUAN TỚI SỐ (money, quantity, measurement):** Bất kỳ phép tính nào mà kết quả sai lệch dẫn tới thiệt hại thực tế nếu dùng sai — không giới hạn ở tiền, có thể là số lượng, tỷ lệ, ngưỡng giới hạn.
3. **XÁC THỰC / KIỂM SOÁT TRUY CẬP (trust boundary):** Bất kỳ đoạn code nào quyết định "ai được phép làm gì" — check quyền, check danh tính, validate input từ nguồn không tin cậy.
4. **RÀNG BUỘC CỨNG (hard constraint / limit):** Code có nhiệm vụ chặn 1 hành vi vượt ngưỡng — validation, giới hạn min/max, điều kiện dừng, cảnh báo an toàn.
5. **KHÓ TÁI TẠO ĐỂ TEST (hard to reproduce):** Lỗi ở đây khó phát hiện qua test thông thường — chỉ lộ ra khi chạy thật, dữ liệu thật, hoặc điều kiện biên hiếm gặp.

Nếu không chắc đoạn code đang viết có rơi vào nhóm nào ở trên: MẶC ĐỊNH TẮT ponytail và hỏi lại user thay vì tự phán đoán.

Sau khi hoàn thành phần thuộc vùng loại trừ, có thể chạy `/ponytail-review` riêng để tìm chỗ thừa KHÔNG liên quan an toàn (import thừa, class bọc không cần thiết) — nhưng bỏ qua mọi gợi ý cắt bớt phần thuộc 5 nhóm trên.

**Liên kết với ADR:** Nếu logic thuộc nhóm 2/3/4 ở trên (đặc biệt: công thức tính toán tài chính/trading, luật phân quyền, ngưỡng giới hạn nghiệp vụ) là quyết định nghiệp vụ quan trọng và lâu dài (vd: công thức tính SL/TP theo USD offset), cân nhắc lưu luôn thành ADR (Bước 2, retrieval flow) thay vì chỉ để trong code comment — để agent/người khác sau này tra cứu lại lý do mà không cần đọc lại toàn bộ code.

## NGƯỠNG "TASK LỚN" (áp dụng cho Task Completion Hook & Git/Kiểm thử bên dưới)

Một task được coi là "lớn" — cần chạy đầy đủ Task Completion Hook + Git & Kiểm thử — nếu thỏa ít nhất 1 trong các điều kiện:
- Thay đổi ≥ 3 file, hoặc thay đổi schema/API/kiến trúc.
- OpenSpec đã tạo spec/task riêng cho việc này.
- User yêu cầu rõ ("làm xong nhớ lưu lại", "đây là quyết định quan trọng").

**Ngoại lệ:** Nếu thay đổi chỉ mang tính cosmetic (format code, sửa comment, đổi tên biến không ảnh hưởng logic/API) — KHÔNG tính là task lớn dù chạm ≥ 3 file, trừ khi cosmetic đó nằm trong vùng loại trừ Ponytail (vd: đổi tên biến trong logic tính tiền vẫn cần cẩn trọng).

Task nhỏ (sửa 1 dòng, đổi text, fix typo, câu hỏi thông tin) → KHÔNG cần chạy Task Completion Hook, chỉ cần trả lời/sửa trực tiếp, tránh lãng phí token gọi tool.

**Có nên tạo OpenSpec change không?** File này không bắt buộc mọi task lớn phải có OpenSpec change — nhưng nếu task lớn thay đổi schema/API công khai/kiến trúc VÀ CHƯA có change nào đang chạy cho việc này: nên hỏi user hoặc chủ động tạo `openspec/changes/<tên>/` trước khi code (không phải sau), để có `proposal.md`/`tasks.md` làm căn cứ cho bước "Scope compliance" trong Code Review. Nếu chỉ là task lớn về mặt số file (≥3) nhưng logic đơn giản, không bắt buộc — dùng phán đoán, tránh tạo change chỉ vì thủ tục.

## KIỂM SOÁT CHI PHÍ GỌI TOOL (Retrieval)

- Với câu hỏi đơn giản, xác định rõ (vd: "hàm X nằm ở file nào", "biến Y định nghĩa ở đâu"): chỉ cần gọi 1 tool phù hợp nhất (thường là `cocoindex-code search` hoặc `search_graph`), tìm ra là DỪNG NGAY — không cần chạy tuần tự hết Bước 1→4.
- Chỉ đi hết cả 4 bước retrieval khi câu hỏi thực sự mơ hồ, cần đối chiếu nhiều nguồn, hoặc 1-2 bước đầu không ra kết quả.
- Không gọi AgentMemory/ADR cho câu hỏi thuần kỹ thuật không liên quan lịch sử/quyết định (vd: "cú pháp X trong ngôn ngữ Y là gì").

## KHUNG TỐI THIỂU CHO CÁC FILE CONTEXT (context.md / working.md / operating_rules.md)

Để tránh mỗi session ghi vào các file này theo format khác nhau (gây rác/không nhất quán), tuân thủ khung tối thiểu sau khi tạo/cập nhật:

- **`context.md`** (tương đối tĩnh, ít đổi): Tổng quan project — mục đích, tech stack, cấu trúc thư mục chính, các quyết định kiến trúc quan trọng nhất (link tới ADR tương ứng thay vì lặp lại nội dung). Chỉ cập nhật khi có thay đổi lớn về bản chất project.
- **`working.md`** (đổi thường xuyên, coi như "nhật ký đang làm"): Danh sách task đang làm/đã xong gần đây, dạng gạch đầu dòng có ngày tháng theo ĐÚNG format ISO `YYYY-MM-DD` (không dùng `DD/MM/YYYY` hay `MM/DD/YYYY` để tránh lẫn lộn), ví dụ: `- [2026-07-08] Xong: thêm API X (task lớn, ADR-003)`. Xóa/dọn các mục đã xong quá 1-2 tuần để tránh phình file — nội dung cũ đã có trong AgentMemory/ADR nên không mất.
- **`operating_rules.md`**: Chỉ chứa RULE cụ thể của riêng project (không lặp lại nội dung đã có trong AGENTS.md này), ví dụ: quy tắc đặt tên biến riêng, giới hạn nghiệp vụ (vd: quy tắc kế toán VN), điều cấm kỵ riêng của project.
- Khi không chắc nên ghi vào file nào hay vào AgentMemory/ADR: dùng quy tắc ở mục "Phân biệt với AgentMemory" (Bước 2, retrieval flow) làm tham chiếu — quyết định lâu dài/kiến trúc → ADR hoặc context.md; việc đang làm/tạm thời → working.md hoặc AgentMemory.

## SESSION START (BẮT BUỘC)

Khi được gọi vào một session mới:
- **Nếu là session chính:**
  1. Gọi `POST /session/start {sessionId, project, cwd}` (AgentMemory) để đánh dấu bắt đầu phiên làm việc.
  2. Đọc `context.md`
  3. Đọc `working.md`
  4. Đọc `operating_rules.md`
  5. Nếu project chưa được index bởi cocoindex-code, chạy `ccc status` để kiểm tra; nếu chưa có index thì `ccc init` && `ccc index`.
  6. Bắt đầu xử lý yêu cầu
- **Nếu là sub-agent hoặc worker (được tạo từ session hiện tại):**
  - Kế thừa context đã có sẵn.
  - **KHÔNG** gọi lại `/session/start` hoặc đọc lại các file trên để tiết kiệm token, trừ khi thiếu context cần thiết.
- **Khi kết thúc phiên làm việc (hoặc trước khi compact/đóng session):**
  - Gọi `POST /session/end {sessionId}`.
  - Nếu vừa commit git, gọi thêm `POST /session/commit {sessionId, sha}` để gắn commit vào session.

## GIAO THỨC TRUY XUẤT MEMORY & CODE (Retrieval)

Khi cần tìm kiếm thông tin để trả lời câu hỏi của người dùng, bạn PHẢI tuân thủ luồng ưu tiên dưới đây. Dừng lại ngay khi tìm thấy câu trả lời phù hợp.

**Tra nhanh (Capability Matrix) — dùng để quyết định ngay không cần đọc hết 4 bước bên dưới nếu câu hỏi đã khớp rõ 1 dòng:**

| Cần gì | Tool |
| --- | --- |
| Tìm file/hàm/symbol theo tên hoặc ngữ nghĩa | `cocoindex-code search` |
| Khớp cú pháp/AST chính xác (bỏ qua format) | `ccc grep` |
| Trace caller/callee, phân tích tác động | `codebase-memory-mcp` (`trace_path`, `detect_changes`) |
| Tổng quan kiến trúc | `codebase-memory-mcp` (`get_architecture`) |
| Quyết định kiến trúc đã chốt ("tại sao chọn X") | ADR (`manage_adr`) |
| Lịch sử làm việc/preference/lý do gần đây | AgentMemory (`/search`, `/memories`) |
| Bài học/lỗi lặp lại đã đúc kết (trong phạm vi project) | AgentMemory (`/lessons`, `/crystals`) |
| Bài học lỗi ĐÃ XÁC ĐỊNH CHẮC CHẮN nguyên nhân, dùng chung nhiều project | Simplenote (tag project hiện tại + tag kỹ thuật để project khác cũng tìm được) — xem mục "Ghi bài học lỗi vào Simplenote" |
| Không tool nào ở trên ra kết quả | Bước 4 — grep/file thủ công |
| Ý tưởng/note cá nhân xuyên nhiều project (ngoài phạm vi project hiện tại) | Tolaria — xem ranh giới sử dụng & quy tắc đọc/ghi tại `~/.agents/tolaria.md` (không đọc file này vào đây, chỉ tham chiếu; không tự động tra mỗi session, chỉ khi câu hỏi rõ ràng cross-project hoặc user chủ động nhắc) |

Bảng trên chỉ để định vị nhanh; nếu câu hỏi mơ hồ hoặc cần đối chiếu nhiều nguồn, vẫn đi theo đúng thứ tự 4 bước dưới đây.

### Bước 1 (Ưu tiên cao nhất): Dùng codebase-memory-mcp + cocoindex-code cho câu hỏi về CODE

- **Khi nào:** Câu hỏi liên quan đến cấu trúc code, luồng gọi hàm, định nghĩa symbol, phân tích tác động, tìm đoạn code liên quan theo ngữ nghĩa, hoặc tìm mẫu code theo cú pháp chính xác.
- **Công cụ:**
  - **cocoindex-code** (`search`, `ccc grep`) — dùng để khám phá nhanh: `search` cho tìm kiếm ngữ nghĩa (natural language hoặc code snippet), `ccc grep` cho khớp cấu trúc AST chính xác (bỏ qua khoảng trắng/format).
  - **codebase-memory-mcp** (`search_graph`, `get_code_snippet`, `trace_path`, `detect_changes`, `get_architecture`, `query_graph`) — dùng để đào sâu quan hệ: trace luồng gọi hàm (callers/callees), phân tích tác động khi sửa code, xem tổng quan kiến trúc, hoặc truy vấn Cypher phức tạp trên đồ thị symbol.
- **Thứ tự khuyến nghị:** Dùng `cocoindex-code search` (hoặc `ccc grep` nếu cần khớp cú pháp chính xác) để định vị file/đoạn code liên quan trước → sau đó dùng `codebase-memory-mcp` (`get_code_snippet`, `trace_path`, `detect_changes`) để đào sâu quan hệ gọi hàm/tác động.
- **Bắt buộc (Enforcement):** Bạn PHẢI ưu tiên dùng hai MCP tools trên TRƯỚC KHI dùng lệnh grep thủ công hoặc đọc file thủ công. Đây là nguồn dữ liệu đã được index sẵn, nhanh và chính xác hơn.
- **Sau khi sửa code:** Nếu dùng `cocoindex-code`, index tự cập nhật khi `refresh_index=true` (mặc định) hoặc chạy `ccc index` thủ công. Nếu dùng `codebase-memory-mcp`, kiểm tra lại `index_status` hoặc chạy `index_repository` nếu có thay đổi lớn về cấu trúc.
- **Hành động sau:** Nếu tìm thấy câu trả lời thỏa đáng → **DỪNG LẠI, trả lời ngay**. Nếu không → Chuyển sang Bước 2.

### Bước 2: Dùng ADR (codebase-memory-mcp) cho câu hỏi về QUYẾT ĐỊNH KIẾN TRÚC

- **Khi nào:** Câu hỏi kiểu "tại sao chọn X thay vì Y", "quy ước/convention của project là gì", "quyết định kiến trúc/schema/tech stack đã chốt là gì" — tức là quyết định CHÍNH THỨC, có tính lâu dài, ảnh hưởng nhiều phần code.
- **Công cụ:** `manage_adr{project, mode: "list"/"get"/"create"/"update", content/sections}` (codebase-memory-mcp).
- **Phân biệt với AgentMemory (Bước 3):**
  - **ADR** = quyết định kiến trúc chính thức, đã chốt, cần tra cứu lại khi review/onboard người mới hoặc agent mới → lưu ở đây.
  - **AgentMemory** = lịch sử làm việc, preference cá nhân, lý do chọn giải pháp trong lúc code, pattern quen thuộc, quan sát/log tạm thời → lưu ở Bước 3.
  - Nếu không chắc, ưu tiên tạo ADR cho quyết định ảnh hưởng kiến trúc/schema/tech stack; còn lại lưu AgentMemory.
  - **Nếu ADR (Bước 2) và kết quả AgentMemory (Bước 3) mâu thuẫn nhau:** ADR luôn thắng (quyết định chính thức đã chốt). Nếu nghi ngờ ADR đã lỗi thời so với thực tế code hiện tại, báo cho user biết có mâu thuẫn thay vì tự chọn theo memory.
- **Hành động sau:** Nếu tìm thấy ADR phù hợp → **DỪNG LẠI, trả lời ngay**. Nếu không → Chuyển sang Bước 3.

### Bước 3: Dùng AgentMemory cho câu hỏi về LỊCH SỬ LÀM VIỆC

- **Khi nào:** Quyết định gần đây, session gần đây, lý do chọn giải pháp, preferences, pattern làm việc.
- **Công cụ (AgentMemory API, base `http://localhost:3111/agentmemory/`):**
  - `POST /search {query}` hoặc `GET /memories?search=<text>&type=` — tìm kiếm nội dung memory theo từ khóa/ngữ nghĩa.
  - `GET /sessions` — liệt kê các session gần đây.
  - `GET /observations?sessionId=` — xem lại narrative/quan sát của một session cụ thể.
  - `GET /lessons`, `GET /crystals`, `GET /semantic`, `GET /procedural` — tra cứu tri thức đã đúc kết (bài học, quyết định ổn định, quy trình).
  - `POST /context {sessionId, project?}` — lấy context tổng hợp cho session/project hiện tại.
- **Quy tắc Chọn Tool & Dịch vụ:**
  - LUÔN LUÔN cố gắng sử dụng API của AgentMemory trước tiên.
  - Dịch vụ AgentMemory đã và đang chạy. Chỉ được truy cập qua HTTP API; TUYỆT ĐỐI KHÔNG thử khởi động, chạy, hoặc khởi tạo lại dịch vụ.
  - Sau khi lưu hoặc xử lý xong bộ nhớ, KHÔNG được tắt dịch vụ AgentMemory. Hãy để nó tiếp tục chạy.
- **Quy tắc Cách ly Đa Dự án (QUAN TRỌNG):**
  - Vì nhiều dự án cùng chia sẻ chung database của AgentMemory, bạn PHẢI cách ly thông tin bằng tên dự án để tránh nhầm lẫn.
  - Khi TÌM KIẾM (`POST /search`, `GET /memories?search=`): Luôn phải bao gồm tên dự án (ProjectName) như một từ khóa bắt buộc trong `query` (ví dụ: "ProjectName authentication system").
  - **Áp dụng TƯƠNG TỰ cho `GET /lessons`, `GET /crystals`, `GET /semantic`, `GET /procedural`:** các endpoint này KHÔNG có tham số filter project chính thức, nên nếu API hỗ trợ query/keyword param thì phải truyền tên dự án vào đó; nếu không, phải tự lọc lại kết quả trả về theo prefix `[ProjectName]` đã gắn lúc lưu (mục "Persistence Strategy") trước khi dùng — KHÔNG dùng thẳng kết quả trần vì có thể lẫn lesson/crystal của project khác.
- **Hành động sau:** Nếu tìm thấy câu trả lời với độ tin cậy cao → **DỪNG LẠI, trả lời**. Nếu không → Chuyển sang Bước 4.

### Bước 4: Fallback Cuối Cùng (nếu Bước 1, 2 & 3 đều không có kết quả)
- Dùng `grep` / file search thủ công
- Dùng `MEMORY.md` hoặc `CLAUDE.md`
- Không đoán hay bịa thông tin.

## GHI NHỚ & QUẢN LÝ CONTEXT (Persistence & Context Management)

### 1. Persistence Strategy (Chiến lược lưu trữ)
Ghi nhớ (Persistence) chạy độc lập với việc truy xuất.
- **Working memory / quyết định / preference:** Trước khi gọi `/remember`, gọi nhanh `POST /search` hoặc `GET /memories?search=` với từ khóa chính của nội dung sắp lưu (kèm prefix `[ProjectName]`) — nếu kết quả trả về có memory cùng chủ đề/cùng đối tượng (cùng file, cùng quyết định, cùng loại preference) trong cùng project, lấy `id`/`memoryId` của memory đó từ kết quả trả về và dùng `POST /evolve` để cập nhật thay vì tạo memory mới trùng lặp. "Gần giống" xét theo chủ đề/đối tượng trùng nhau, không dựa vào điểm số cụ thể nào (API không đảm bảo trả về trường điểm tương đồng). Nếu không chắc có phải cùng chủ đề không, ưu tiên tạo mới hơn là evolve nhầm đè lên quyết định khác. Lưu vào **AgentMemory** qua `POST /remember {content, title?, type?, strength?, concepts?, files?, sessionIds?}`. Bắt buộc thêm tiền tố (prefix) tên dự án vào `content` hoặc `concepts` (ví dụ: content: "[ProjectName] Quyết định: ...", concepts: ["ProjectName", "auth"]). Lưu ý `strength` có sàn tối thiểu là 7.
- **Cập nhật memory đã lưu:** Dùng `POST /evolve {memoryId, newContent, strength?}` — tạo bản mới, bản cũ tự động bị supersede (không dùng để tạo memory mới).
- **Xóa memory sai/lỗi thời:** `POST /forget {memoryId}`.
- **Ghi lại quan sát trong lúc làm việc (không phải quyết định chính thức):** `POST /observe {hookType, sessionId, project, cwd, timestamp, narrative, title?, type?, importance?}`.
  > **Điểm kích hoạt cụ thể** (để tránh endpoint này chỉ nằm trong danh sách API mà không bao giờ được gọi thực tế): gọi `/observe` ngay sau mỗi bước có phát hiện đáng chú ý nhưng CHƯA đủ tầm để thành ADR/lesson chính thức — ví dụ: sau mỗi bước trong "Code Review" (mục dưới) phát hiện điều gì đó bất thường nhưng không phải finding chính, hoặc giữa chừng 1 task lớn khi có quyết định nhỏ tạm thời chưa chốt. Set `hookType` mô tả đúng ngữ cảnh gọi (vd: `"code-review"`, `"mid-task-decision"`). Khác với `/remember`: `/observe` ghi log tạm thời không cần strength/đánh giá lâu dài, `/remember` mới là nơi lưu chính thức có giá trị nhiều phiên.
- **Kiến trúc/index code:** Không cần lưu thủ công — `codebase-memory-mcp` và `cocoindex-code` tự cập nhật index khi codebase thay đổi (`detect_changes`, `refresh_index`, `ccc index`).
- **Bài học lỗi ĐÃ XÁC ĐỊNH CHẮC CHẮN nguyên nhân → Simplenote (cross-project, KHÁC AgentMemory):**
  > MCP Simplenote đã được kết nối sẵn và đã có skill tương ứng — chỉ gọi tool, không cần setup thêm.
  - **Điểm kích hoạt:** Ngay khi AI xác định được nguyên nhân GỐC của một lỗi với độ chắc chắn cao (không phải suy đoán/nghi ngờ) — dù user không yêu cầu rõ trong prompt. Đây thường là lúc lỗi vừa được fix xong và đã verify (test pass/user xác nhận hết lỗi), hoặc user hỏi kiểu "bạn có rút ra bài học không?", "tạo/cập nhật note lại đi", "hết lỗi rồi, chắc bạn hiểu lý do chính xác rồi". KHÔNG ghi khi nguyên nhân còn chưa chắc chắn (mới là giả thuyết) — chờ tới khi đã verify xong mới ghi.
  - **Trước khi tạo mới, LUÔN tìm trước:** Tìm trên Simplenote theo cả tag kỹ thuật (loại lỗi/framework/tầng hệ thống liên quan — xem cách chọn tag bên dưới) LẪN tag project hiện tại, vì note phù hợp có thể do project khác tạo trước đó. Chỉ lọc riêng theo tag project hiện tại khi tìm theo tag kỹ thuật ra quá nhiều kết quả không liên quan.
    - Nếu tìm thấy note phù hợp → **cập nhật** note đó (bổ sung case/biến thể mới, làm rõ thêm nguyên nhân nếu hiểu sâu hơn) — KHÔNG tạo note trùng nội dung.
    - Nếu không tìm thấy → **tạo note mới**.
  - **Nội dung note PHẢI viết tổng quát, áp dụng được cho NHIỀU project** (nguyên nhân gốc, dấu hiệu/triệu chứng nhận biết, cách tránh hoặc hướng fix chung) — không mô tả riêng theo cách đặc thù của project hiện tại, không dán nguyên khối code cụ thể trừ khi cần 1 đoạn ngắn minh họa pattern lỗi.
  - **Tag:** Note luôn phải được gắn tag = tên project hiện tại (đúng giá trị biến `project` theo "QUY ƯỚC ĐỊNH DANH" ở đầu file) để biết note phát sinh từ đâu. NGOÀI RA, vì mục đích chính của kho này là để **project KHÁC** cũng tìm lại được, phải gắn THÊM các tag mô tả bản chất lỗi (không chỉ dừng ở tag project) để tra cứu xuyên project dễ dàng, ví dụ:
    - Ngôn ngữ/framework/runtime liên quan (vd: `nodejs`, `react`, `postgresql`, `docker`).
    - Loại lỗi/chủ đề kỹ thuật (vd: `race-condition`, `null-pointer`, `cors`, `memory-leak`, `auth`, `migration`).
    - Tầng hệ thống liên quan nếu rõ ràng (vd: `database`, `api`, `frontend`, `ci-cd`).
    - Chỉ gắn tag nào THỰC SỰ mô tả đúng bản chất lỗi, không gắn tràn lan cho "chắc ăn". Trước khi đặt tag mới, ưu tiên tìm/liệt kê tag đã có sẵn trong Simplenote và dùng lại nếu trùng ý nghĩa, tránh tạo tag trùng nghĩa nhưng khác cách viết (vd: vừa có `nodejs` vừa có `node-js`).
    - Nếu chắc chắn bài học này cũng từng áp dụng cho project khác đã biết, có thể gắn thêm tag project đó — không tự đoán tag của project chưa từng làm.
  - **Khác biệt với AgentMemory `/lessons`/`/crystals`:** AgentMemory lưu bài học có cách ly theo từng project (chỉ tra được trong phạm vi project đó); Simplenote ở đây là kho **tập trung, dùng chung giữa các project** (universal debugging knowledge) — hai nơi không thay thế nhau, có thể trùng nội dung ở cả 2 nếu phù hợp (AgentMemory cho ngữ cảnh trong project, Simplenote để agent ở project KHÁC cũng tra được).
  - Áp dụng đúng quy tắc "TUYỆT ĐỐI KHÔNG LƯU secrets/credentials" bên dưới cho cả nội dung note Simplenote.
- **Ngưỡng lưu (KHÔNG LƯU):** Kết quả tạm thời, suy đoán, thông tin ngắn hạn, log debug, error tạm thời. Chỉ lưu khi thông tin có khả năng còn giá trị sau nhiều phiên làm việc.
- **TUYỆT ĐỐI KHÔNG LƯU secrets/credentials:** Không đưa API key, token, password, connection string có mật khẩu, private key, hoặc bất kỳ nội dung file `.env` nào vào `content`/`concepts` của `POST /remember`, vào `manage_adr`, hay vào `context.md`/`working.md`/`operating_rules.md`. Nếu cần ghi chú về việc dùng biến môi trường nào, chỉ ghi TÊN biến (vd: "dùng biến `DB_PASSWORD` từ `.env`"), không ghi giá trị thật.

### 2. Task Completion Hook
Sau khi hoàn thành một **task lớn** (xem định nghĩa ở mục "NGƯỠNG TASK LỚN" đầu file), bắt buộc phải:
1. Xác định có quyết định mới nào được đưa ra không.
2. Nếu là quyết định kiến trúc/schema/tech stack chính thức: Gọi `manage_adr{project, mode: "create"/"update"}` để lưu ADR.
3. Nếu là lịch sử làm việc/preference/lý do chọn giải pháp thông thường: Gọi `POST /remember` để lưu vào AgentMemory (kèm prefix `[ProjectName]` trong `content`/`concepts`, `sessionIds: [sessionId]`).
3b. Nếu task lớn này vừa fix xong 1 lỗi và nguyên nhân đã được xác định CHẮC CHẮN (đã verify): áp dụng mục "Bài học lỗi ĐÃ XÁC ĐỊNH CHẮC CHẮN nguyên nhân → Simplenote" (Ghi nhớ & Quản lý Context, phần Persistence Strategy) — tìm note cũ theo tag project trước, cập nhật nếu có, tạo mới nếu chưa có.
4. Nếu code có thay đổi cấu trúc lớn VÀ chưa được đồng bộ ở bước 6 của "Git & Kiểm thử" (hoặc có thay đổi thêm sau đó): chạy `ccc index` và/hoặc `index_repository` (codebase-memory-mcp) để cập nhật index — không chạy lại nếu đã đồng bộ rồi, tránh tốn tool call trùng lặp.
5. Cập nhật file `working.md`.
6. Trả kết quả cho user.

### 3. Long Session Handling (Compress)
Quản lý Context chạy độc lập. Khi:
- Context window > 70%
- Hội thoại kéo dài nhiều giờ
- Có nguy cơ mất các quyết định quan trọng
=> Trước khi gọi API tóm tắt, bỏ qua/không nhắc lại output log tạm thời, kết quả tool call trung gian không còn cần thiết trong ngữ cảnh hiện tại (tránh việc gọi summarize cũng tốn token vì context đang phình). Sau đó gọi `POST /summarize {sessionId}` để tóm tắt phiên hiện tại, rồi `POST /consolidate {sessionId}` để hợp nhất/nén memory của session đó, rồi tiếp tục làm việc dựa trên summary. Nếu context vẫn còn quá lớn sau khi consolidate (ít cải thiện): báo cho user, đề xuất tóm tắt thủ công hoặc tách task hiện tại thành các task nhỏ hơn/session mới thay vì cố nén tiếp.

## Xác minh & Khắc phục sự cố (Verification & Troubleshooting)
- Kiểm tra AgentMemory: `curl http://localhost:3111/agentmemory/health`
  - Nếu không chạy: KHÔNG tự động khởi động lại, và KHÔNG BAO GIỜ tắt dịch vụ sau khi sử dụng xong. Nhắc người dùng tự chạy nếu cần: `npx @agentmemory/agentmemory`
- Kiểm tra cocoindex-code: `ccc doctor -v` (health check settings/daemon/model), `ccc status` (index stats)
- Kiểm tra codebase-memory-mcp: `index_status{project}` (trạng thái index), `list_projects{}` (danh sách project đã index)

### Graceful Degradation (khi 1 tool lẻ bị down giữa chừng)

**Trước khi coi 1 tool là "down":** nếu lỗi trả về có dấu hiệu tạm thời (timeout, network drop, treo không phản hồi) — thử gọi lại đúng 1 lần. Nếu vẫn lỗi/treo, coi là down và áp dụng fallback bên dưới, không thử thêm lần nào nữa (tránh tốn thời gian/token chờ vô ích). Nếu 1 tool được nhắc tới trong file này không có trong danh sách tool khả dụng thực tế của session hiện tại (chưa được kết nối/cấu hình) — đừng cố gọi rồi tự suy diễn kết quả; báo cho user biết tool đó chưa sẵn sàng.

Phân biệt rõ 2 tình huống:
- **Chỉ 1 tool bị down** (vd: `ccc doctor` báo daemon chết, hoặc AgentMemory `/health` không phản hồi): KHÔNG dừng cả luồng. Tự động dùng tool còn lại cùng bước đó để thay thế tạm thời:
  - `cocoindex-code` down → dùng `codebase-memory-mcp` (`search_graph`, `search_code`) thay cho bước khám phá code.
  - `codebase-memory-mcp` down → dùng `cocoindex-code search`/`ccc grep` thay cho bước trace/khám phá (chấp nhận không có trace_path/detect_changes, báo cho user biết đang thiếu tính năng gì).
  - `AgentMemory` down → bỏ qua Bước 3 (lịch sử làm việc), báo cho user biết context lịch sử có thể không đầy đủ, tiếp tục các bước còn lại bình thường.
  - `open-code-review` (OCR) chưa cài, không chạy được, HOẶC chạy nhưng lỗi kết nối LLM (auth/timeout/network) → bỏ qua bước 1 của "Code Review", KHÔNG dừng cả luồng review (OCR là tool bổ sung, không phải 1 trong 3 tool cốt lõi — thiếu OCR chỉ mất khả năng phát hiện bug cụ thể như NPE/XSS/SQLi, các bước Ponytail/impact/scope/lessons vẫn chạy bình thường như review mặc định). "Review mặc định" nghĩa là: tự đọc `git diff` của thay đổi, kiểm tra thủ công các lỗi phổ biến hay gặp (null/undefined chưa check, thiếu validate input, SQL không parameterized, input user đưa thẳng vào eval/prompt/shell, lộ secret, race condition rõ ràng), rồi làm tiếp bước 2 trở đi bình thường — không có nghĩa là bỏ qua toàn bộ Code Review, chỉ là thiếu riêng lớp phát hiện tự động của OCR. Không cần phân biệt rõ lý do lỗi cụ thể để xử lý khác nhau — mọi trường hợp OCR không khả dụng đều fallback giống nhau. TUYỆT ĐỐI KHÔNG tự cài đặt/cấu hình lại OCR (việc cài & config LLM cho OCR là setup thủ công của user, ngoài phạm vi file này) — chỉ báo ngắn cho user biết OCR đang không khả dụng.
  - Ghi chú ngắn cho user biết tool nào đang down, không im lặng bỏ qua.
- **Cả 2-3 tool cùng không có kết quả/không khả dụng** (không phải lỗi kỹ thuật mà do không tìm thấy dữ liệu phù hợp): đây mới là lúc chuyển sang Bước 4 (Fallback Cuối Cùng).
- KHÔNG được tự ý khởi động lại/khởi tạo lại bất kỳ service nào (AgentMemory, cocoindex-code daemon, codebase-memory-mcp) — chỉ báo cáo và nhắc user tự xử lý nếu cần restart.

# PROJECT
<!-- Từ đây trở xuống là phần RIÊNG của từng project — điền cụ thể khi khởi tạo, không để trống các mục đánh dấu ĐIỀN -->

## Role & Context
<!-- ĐIỀN: project này làm gì, tech stack chính, vai trò của agent (vd: full-stack dev cho SaaS X) -->

## Initial Setup & Navigation
`context.md`/`working.md`/`operating_rules.md` đã được đọc tự động ở "SESSION START" (đầu file) — KHÔNG đọc lại ở đây, tránh tốn token 2 lần. Mục này chỉ để ĐIỀN thêm bước navigation RIÊNG của project, ví dụ: đọc sâu hơn trong `.project/README.md` nếu cần tra cứu cấu trúc schema/API, hoặc thứ tự đọc các service trong monorepo.

<!-- ĐIỀN thêm nếu project có quy trình navigation riêng (vd: cấu trúc monorepo, service nào đọc trước) -->

## Critical Rules (Must Follow)
<!-- ĐIỀN các rule RIÊNG của project ở đây (không lặp lại nội dung đã có ở phần trên của file này) -->
1. <!-- ĐIỀN -->
2. <!-- ĐIỀN -->
<!-- ... thêm rule riêng nếu cần ... -->
n. **Xem thêm phần hạ tầng chung ở đầu file** (không lặp lại ở đây): luôn ưu tiên tool MCP nội bộ trước grep thủ công · không báo "hoàn thành" nếu chưa test/lint/Code Review · không tự commit code thuộc vùng loại trừ Ponytail khi chưa có xác nhận user.

## Workflow
<!-- ĐIỀN workflow riêng của project, ví dụ mẫu bên dưới — sửa/xóa/thêm theo thực tế -->
- **Bug fixing**: <!-- ĐIỀN: check ở đâu trước, log ở đâu -->
- **Feature add**: 
  - Nghĩ kỹ xem là Client hay Server Component.
  - Update `working.md` sau khi làm xong.
  - Test kĩ tính năng trước khi báo cáo cho user.

## Git & Kiểm thử (BẮT BUỘC cho **task lớn** — xem định nghĩa đầu file; tuân theo phương pháp TDD/debugging của Superpowers)

> Lưu ý: Ponytail (chống over-engineering) không bao giờ được phép cắt giảm bước test dưới đây — xem mục "Ponytail — vùng loại trừ an toàn" ở đầu file. Test tối thiểu luôn bắt buộc, kể cả khi Ponytail đang ở mức "ultra".

- **Yêu cầu tiên quyết:** Project PHẢI đã `git init`. Nếu chưa chắc, chạy `git rev-parse --is-inside-work-tree` để xác nhận trước khi dùng bất kỳ lệnh git nào bên dưới. Không có git → `detect_changes` (codebase-memory-mcp) không hoạt động và mục "Rollback" bên dưới không áp dụng được — báo cho user biết thay vì tự bỏ qua. Lưu ý: fallback của "Targeted testing" bên dưới (`git diff --name-only`) CŨNG cần git để chạy — nếu hoàn toàn không có git, bỏ qua cả "Targeted testing" lẫn fallback của nó, chạy thẳng full test suite (không cố suy luận phạm vi thay đổi thủ công).
- **Ưu tiên đọc diff thay vì đọc lại nguyên file (tiết kiệm token):**
  - Khi cần xem lại/review thay đổi vừa làm: dùng `git diff` (chưa commit) hoặc `git log -p -1`/`git show <sha>` (đã commit) để chỉ đọc phần THAY ĐỔI, thay vì `view`/`cat` lại nguyên file.
  - Chỉ đọc lại nguyên file khi diff không đủ ngữ cảnh để hiểu (vd: cần xem toàn bộ hàm bao quanh đoạn diff) hoặc đây là lần đầu tiếp cận file đó.
  - Áp dụng tương tự cho bước Code Review (impact review): ưu tiên `git diff`/`detect_changes{since}` để khoanh vùng, không quét lại toàn bộ codebase.
- **Branch/Commit convention:** ...
  <!-- Điền quy ước riêng của project, ví dụ: feat/, fix/, chore/ prefix; Conventional Commits; branch từ main/develop -->
- **Targeted testing (ưu tiên, để tiết kiệm thời gian/token với project lớn):**
  - Trước khi chạy test, gọi `detect_changes{project, scope, since}` (codebase-memory-mcp) để xác định phạm vi file/module bị ảnh hưởng bởi thay đổi.
  - Nếu `detect_changes` không khả dụng (codebase-memory-mcp down): dùng `git diff --name-only` để lấy danh sách file thay đổi, suy ra test liên quan theo quy ước đặt tên test của project; nếu không suy ra được, chạy full test suite (chấp nhận tốn thời gian hơn là bỏ sót).
  - Luôn chạy test cho đúng file vừa sửa + test của caller chính (nếu `detect_changes`/kết quả trace tìm thấy caller quan trọng) — không chỉ chạy test của riêng file đã sửa.
  - Chỉ chạy test/suite liên quan trực tiếp đến phạm vi đó trước; chỉ chạy full test suite khi thay đổi ảnh hưởng rộng (core module, shared utils) hoặc trước khi merge/release.
- **Trước khi báo "hoàn thành" một task:**
  1. Chạy test liên quan (theo targeted testing ở trên) — KHÔNG báo hoàn thành nếu test đỏ hoặc chưa chạy.
  2. Chạy lint/build check của project (nếu có).
  3. **Code Review (xem chi tiết mục "Code Review" bên dưới)** — chạy TRƯỚC KHI commit.
  4. Kiểm tra không có secrets/credentials/`.env` nào bị đưa vào diff sắp commit (grep nhanh các pattern như `API_KEY`, `SECRET`, `PASSWORD`, hoặc file `.env` không nằm trong `.gitignore`).
  5. Nếu có thay đổi schema/API/kiến trúc → cập nhật ADR (xem Bước 2 ở trên) trước khi commit.
  6. Chạy `ccc index` / `index_repository` để đồng bộ index code nếu cấu trúc thay đổi lớn.
- **Nếu test/build fail:** Dừng lại, báo cáo rõ lỗi cho user, KHÔNG tự ý commit code đang lỗi trừ khi được yêu cầu.
- **Rollback:** Nếu một thay đổi gây lỗi nghiêm trọng hoặc không đạt yêu cầu, ưu tiên `git revert`/`git checkout` về trạng thái trước đó thay vì vá chồng thêm, trừ khi user yêu cầu khác.

## Code Review (BẮT BUỘC cho **task lớn**, chạy sau khi test pass, trước khi commit)

Đây là bước AI tự review lại code vừa viết, KHÔNG thay thế việc user tự xem lại — chỉ là lớp lọc trước để bắt lỗi rõ ràng, tránh báo "hoàn thành" quá sớm.

> Việc cài đặt và cấu hình OCR (skill/plugin, LLM endpoint, API key) là setup thủ công của user, KHÔNG thuộc phạm vi file này. Agent chỉ gọi tool khi đã có sẵn; nếu không khả dụng thì fallback theo mục "Graceful Degradation" ở trên, không tự cài/tự sửa cấu hình.

> **Phạm vi diff khi gọi OCR:** Gọi bare `/open-code-review:review` (không tham số) chỉ review phần **chưa commit** (staged + unstaged + untracked) tại thời điểm gọi. Nếu trong task hiện tại đã có commit trung gian (vd: Task Completion Hook chạy giữa chừng, hoặc agent tự commit theo bước), phần đã commit đó sẽ KHÔNG được OCR nhìn thấy nếu gọi bare — phải dùng `--from <commit-đầu-task> --to HEAD` để review đúng "toàn bộ thay đổi của task", không chỉ phần chưa commit.
>
> **Ngôn ngữ output:** OCR mặc định trả comment bằng tiếng Trung trừ khi đã set `language: English` trong config của OCR (việc này thuộc setup của user, không phải của agent) — nếu thấy comment trả về không phải tiếng Việt/Anh như mong đợi, đây là do config OCR chứ không phải lỗi review, báo cho user biết thay vì tự dịch/diễn giải sai lệch nội dung gốc.

### AI tự làm được (bắt buộc chạy tuần tự, dừng ngay nếu phát hiện vấn đề để sửa trước khi qua bước tiếp):

> Lưu ý: "dừng ngay để sửa" nghĩa là dừng ở CẤP BƯỚC (vd: không sang bước 2-Ponytail khi bước 1-OCR còn High/Medium chưa xử lý) — không phải sửa từng issue riêng lẻ trong 1 bước rồi gọi lại tool đó ngay sau mỗi issue. Mỗi bước (OCR, Ponytail...) trả về TOÀN BỘ findings của nó trong 1 lần gọi; xử lý gộp hết findings của bước đó trước khi coi bước đó là xong.

1. **OCR bug/security scan:** Chạy `/open-code-review:review` (open-code-review) trên diff vừa thay đổi — phát hiện lỗi kỹ thuật cụ thể (NPE, thread-safety, XSS, SQL injection...) mà các bước còn lại không kiểm tra tới. Nếu OCR không khả dụng (chưa cài, không chạy được, hoặc lỗi kết nối LLM), xem mục "Graceful Degradation" ở trên — fallback về review mặc định, KHÔNG dừng cả luồng.
   - Lệnh này tự phân loại comment theo 3 mức **High / Medium / Low** và tự loại bỏ Low (khả năng false positive/nitpick) khỏi output. Khi báo cáo lại cho user, giữ nguyên đúng 2 nhãn High/Medium mà OCR trả về — KHÔNG gộp chung thành một câu "phát hiện vấn đề" chung chung, và nêu rõ số lượng Low đã bị lọc để user biết là có (không cần tự đi verify lại từng cái).
2. **Ponytail review:** Chạy `/ponytail-review` — tìm code thừa không liên quan an toàn (import thừa, class/abstraction bọc không cần thiết). Đây là mối quan tâm khác với OCR (over-engineering, không phải bug), không thể thay thế nhau.
3. **Impact review:** Gọi `detect_changes{project, since}` (codebase-memory-mcp) — xác nhận thay đổi không bỏ sót chỗ cần sửa theo (caller, config, test liên quan).
4. **Dead code check:** Nếu vừa refactor/xóa hàm, gọi `search_graph{max_degree: 0, exclude_entry_points: true}` — tìm hàm/biến không còn ai gọi.
5. **Pattern consistency:** Dùng `ccc grep` để đối chiếu code mới có theo đúng convention/pattern đang dùng trong repo không (vd: cùng shape try/catch, cùng cách đặt tên).
6. **Scope compliance (nếu đang trong OpenSpec change):** Đối chiếu diff với `proposal.md`/`tasks.md` — code có đúng phạm vi đã chốt không, có lan ra ngoài scope không.
7. **Lessons check:** Gọi `GET /lessons`, `GET /crystals` (AgentMemory) — xem có bài học/lỗi cũ nào liên quan tới loại thay đổi này không, tránh lặp lại. Nếu nghi ngờ đây là loại lỗi phổ biến/dùng chung nhiều project, tìm thêm trên Simplenote (theo tag kỹ thuật của loại lỗi, không chỉ tag project hiện tại) — xem mục "Bài học lỗi ĐÃ XÁC ĐỊNH CHẮC CHẮN nguyên nhân → Simplenote" (Ghi nhớ & Quản lý Context).
   - Nếu bước này (hoặc bất kỳ bước 1-6 nào ở trên) phát hiện điều gì ĐÁNG được ghi lại làm bài học cho sau này (vd: lỗi lặp lại lần 2, pattern sai hay gặp trong repo này) — đừng để trôi mất: đánh dấu lại, và đưa vào bước 1 của "Task Completion Hook" (mục "Ghi nhớ & Quản lý Context" bên dưới) làm input cho `POST /remember`. Vì Code Review chỉ chạy cho "task lớn" (cùng ngưỡng với Task Completion Hook), hai bước này LUÔN đi liền nhau trong cùng 1 task — không cần gọi `/remember` riêng ngay tại bước 7, chỉ cần không bỏ sót phát hiện khi tới Task Completion Hook.
   - Nếu nguyên nhân lỗi phát hiện được ở đây đã CHẮC CHẮN (không còn nghi ngờ) và mang tính chất áp dụng được cho project khác (không chỉ riêng repo này): áp dụng thêm quy tắc Simplenote nói trên (tạo/cập nhật note), độc lập với việc có ghi AgentMemory hay không.
8. **OCR re-check trên diff cuối (có điều kiện, TỐI ĐA 1 LẦN — chống vòng lặp vô hạn):** Nếu bất kỳ bước 2–7 nào ở trên đã THỰC SỰ sửa code (kiểm bằng `git diff` không rỗng so với lúc kết thúc bước 1), chạy lại `/open-code-review:review` **đúng 1 lần duy nhất** trên diff mới nhất trước khi commit — vì diff đã đổi thì kết quả OCR ở bước 1 không còn phản ánh đúng bản cuối. Nếu bước 2–7 không sửa gì thêm (diff không đổi), KHÔNG rerun OCR — tránh nhân đôi chi phí gọi LLM một cách không cần thiết, đúng tinh thần "KIỂM SOÁT CHI PHÍ GỌI TOOL". **Nếu lần re-check này (bước 8) lại tự động áp fix tiếp và tạo thêm thay đổi:** DỪNG NGAY, KHÔNG chạy lại bước 8 thêm lần nữa — trình bày diff cuối cùng cho user xem trực tiếp và chờ xác nhận, tuyệt đối không để rơi vào vòng lặp review→fix→review→fix vô hạn. Nếu OCR tự fix ở bước 1 hoặc bước 8, phải chạy lại test/lint liên quan (mục "Git & Kiểm thử") trước khi coi code đã sẵn sàng để trình user — code do OCR tự sửa chưa chắc còn pass test. Nếu fix ở bước 8 chạm vào file MỚI nằm ngoài phạm vi mà "Targeted testing"/`detect_changes` đã quét ban đầu: phải tính lại phạm vi (gọi lại `detect_changes` hoặc ít nhất chạy thêm test của (các) file mới đó), không chỉ chạy lại đúng bộ test cũ đã xác định trước khi có fix này — tránh bỏ sót.

### KHÔNG được AI tự ký duyệt — bắt buộc dừng lại hỏi user:

Nếu code thuộc bất kỳ nhóm nào trong "Ponytail — vùng loại trừ an toàn" (irreversible, liên quan tới số/tiền, xác thực/phân quyền, ràng buộc cứng, khó tái tạo để test) **HOẶC** nếu OCR (bước 1) phát hiện risk ở mức nghiêm trọng (vd: SQL injection, XSS, lỗi thread-safety) **HOẶC** nếu OCR đã tự động áp fix vào file: sau khi hoàn thành các bước AI-review ở trên, **KHÔNG tự báo "hoàn thành"/tự commit**. Phải trình bày rõ cho user: đoạn code nào, thuộc nhóm rủi ro nào (Ponytail và/hoặc OCR), OCR có tự sửa gì hay không, đã review những gì, và chờ user xác nhận trước khi commit. Fix do OCR tự áp KHÔNG được coi là đã "duyệt" — vẫn phải qua đúng quy trình xác nhận này nếu chạm vùng rủi ro trên. Đây không phải bước có thể rút gọn hay bỏ qua ở bất kỳ mức Ponytail nào.

> **Lưu ý:** với lệnh `/open-code-review:review` (plugin) đang dùng ở bước 1, tự-động-áp-fix cho các comment High/Medium là **hành vi mặc định của chính lệnh này** (bước cuối trong workflow của plugin là tự sửa các vấn đề/gợi ý đáng áp dụng), không phải một khả năng hiếm gặp. Vì vậy coi như MẶC ĐỊNH sau mỗi lần chạy OCR, file có thể đã bị sửa — luôn kiểm tra `git diff` sau bước 1 trước khi đi tiếp, không giả định "OCR chỉ báo cáo".
