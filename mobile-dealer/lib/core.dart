import 'dart:convert';

import 'package:flutter/foundation.dart' show kDebugMode;
import 'package:http/http.dart' as http;

/// P2 internal app core: ERP REST client + hard project rules.
///
/// Hard rules (phase_02_internal_mobile.md — enforced in code, not comments):
/// * NO financial mutation while offline: [FinancialAction.guard] throws unless
///   the device is online.
/// * Server wins on conflict: failed/conflicting writes are surfaced, never
///   force-overwritten locally.
/// * Every create carries an idempotency key ([newIdempotencyKey]).
final ValueNotifier<bool> isOnline = ValueNotifier<bool>(true);

int _idemSeq = 0;

/// Stable per logical operation: the caller computes it once when the op is
/// created and stores it with the queued row (never per retry).
String newIdempotencyKey() {
  _idemSeq += 1;
  return 'mob-${DateTime.now().toUtc().microsecondsSinceEpoch}-$_idemSeq';
}

/// Vietnamese money rendering: `1500000` → `1,500,000đ` (grouped, no
decimals — debt amounts are whole dong).
String vnd(num amount) {
  final s = amount.round().abs().toString();
  final buf = StringBuffer();
  for (var i = 0; i < s.length; i++) {
    buf.write(s[i]);
    final rest = s.length - 1 - i;
    if (rest > 0 && rest % 3 == 0) buf.write(',');
  }
  return '${amount < 0 ? '-' : ''}$bufđ';
}

/// Hard gate for money-touching actions (payment, credit override, debt edit).
class FinancialAction {
  static void guard() {
    if (!isOnline.value) {
      throw StateError('Financial mutation is forbidden while offline');
    }
  }
}

/// The password field doubles as a token field: `api_key:api_secret` (exactly
/// one colon, both sides non-empty) logs in via the token path; anything else
/// is a password. One colon only — a password may itself contain colons.
bool looksLikeTokenPair(String s) {
  final i = s.indexOf(':');
  if (i <= 0 || i == s.length - 1) return false;
  return s.indexOf(':', i + 1) == -1;
}

/// Owner policy: credentials travel ONLY over HTTPS. Non-HTTPS is rejected
/// with a clear message; the ONLY exception is plain-http loopback hosts
/// (localhost / 127.0.0.1 / 10.0.2.2 Android emulator) AND only in debug
/// builds — `kDebugMode` is tree-shaken false in release, so the exception
/// cannot ship.
String? validateBaseUrl(String raw) {
  final u = Uri.tryParse(raw.trim());
  if (u == null || u.host.isEmpty) return 'URL máy chủ không hợp lệ';
  if (u.scheme == 'https') return null;
  const loopback = {'localhost', '127.0.0.1', '10.0.2.2'};
  if (kDebugMode && u.scheme == 'http' && loopback.contains(u.host)) return null;
  return 'Chỉ hỗ trợ kết nối HTTPS để bảo vệ mật khẩu';
}

/// Auth outcome. `error` is a human-friendly Vietnamese message when `ok`.
class AuthResult {
  const AuthResult({required this.ok, this.fullName, this.error});

  final bool ok;
  final String? fullName;
  final String? error;
}

/// REST client against the ERPNext site.
///
/// Two auth paths, same two credential fields:
/// 1. Password: POST /api/method/login → session cookie `sid` (kept here and
///    sent as `Cookie: sid=...`; the `http` package has no cookie jar).
/// 2. API token: `Authorization: token <api_key>:<api_secret>`.
///
/// `client` is injectable so tests drive it with a MockClient — no real
/// network in unit tests.
class ErpClient {
  ErpClient({required this.baseUrl, http.Client? client}) : _http = client ?? http.Client();

  final String baseUrl;
  final http.Client _http;
  String? sid;
  String? tokenPair; // '<api_key>:<api_secret>' when token auth succeeded

  Map<String, String> get _authHeaders => {
        if (sid != null) 'Cookie': 'sid=$sid',
        if (tokenPair != null) 'Authorization': 'token $tokenPair',
      };

  /// Password login first; on failure retry the fields as an API token pair.
  Future<AuthResult> login(String user, String password) async {
    final urlError = validateBaseUrl(baseUrl);
    if (urlError != null) return AuthResult(ok: false, error: urlError);
    if (looksLikeTokenPair(password)) {
      return loginWithToken(user: user, pair: password);
    }
    try {
      final res = await _http
          .post(
            Uri.parse('$baseUrl/api/method/login'),
            body: {'usr': user, 'pwd': password},
          )
          .timeout(const Duration(seconds: 15));
      if (res.statusCode == 200) {
        final data = _decodeObject(res);
        sid = RegExp(r'sid=([^;]+)').firstMatch(res.headers['set-cookie'] ?? '')?.group(1);
        return AuthResult(ok: true, fullName: data['full_name'] as String?);
      }
      final friendly = _friendlyAuthError(res.body);
      final tokenResult = await loginWithToken(user: user, pair: password);
      if (tokenResult.ok) return tokenResult;
      return AuthResult(ok: false, error: friendly ?? tokenResult.error);
    } on FormatException {
      // non-JSON / wrong-shape body (proxy pages, null) — not a network fault
      return const AuthResult(ok: false, error: 'Phản hồi không hợp lệ từ máy chủ');
    } on Exception {
      return const AuthResult(ok: false, error: 'Không kết nối được máy chủ');
    }
  }

  /// API-token login. Also used directly when the user types
  /// `api_key:api_secret` into the password field.
  Future<AuthResult> loginWithToken({required String user, required String pair}) async {
    final urlError = validateBaseUrl(baseUrl);
    if (urlError != null) return AuthResult(ok: false, error: urlError);
    try {
      final res = await _http
          .get(
            Uri.parse('$baseUrl/api/method/frappe.auth.get_logged_user'),
            headers: {'Authorization': 'token $pair'},
          )
          .timeout(const Duration(seconds: 15));
      if (res.statusCode != 200) return const AuthResult(ok: false, error: 'Đăng nhập thất bại');
      tokenPair = pair;
      final data = _decodeObject(res);
      return AuthResult(ok: true, fullName: data['message'] as String?);
    } on FormatException {
      return const AuthResult(ok: false, error: 'Phản hồi không hợp lệ từ máy chủ');
    } on Exception {
      return const AuthResult(ok: false, error: 'Không kết nối được máy chủ');
    }
  }

  /// jsonDecode + shape check. The `as Map` on a non-map JSON (proxy pages,
  /// literal `null`) raises TypeError — an Error, NOT an Exception — which the
  /// `on Exception` clauses do NOT catch and the app would crash. Converting
  /// it to FormatException keeps every caller on the handled path.
  static Map<String, dynamic> _decodeObject(http.Response res) {
    final decoded = jsonDecode(res.body);
    if (decoded is! Map<String, dynamic>) {
      throw const FormatException('Response body is not a JSON object');
    }
    return decoded;
  }

  /// Maps frappe's raw auth-failure payloads to Vietnamese messages. Checks the
  /// `exc` traceback, NOT the English `Message` (text varies by version).
  static String? _friendlyAuthError(String body) {
    if (body.contains('currentsite.txt')) return 'Sai tài khoản hoặc mật khẩu';
    if (body.contains('QuotaExceededError')) return 'Hết lượt đăng nhập — thử lại sau';
    return null;
  }

  /// Authenticated GET of an /api/resource path (session or token headers).
  Future<dynamic> getResource(String path) async {
    final res = await _http
        .get(Uri.parse('$baseUrl$path'), headers: _authHeaders)
        .timeout(const Duration(seconds: 15));
    if (res.statusCode != 200) throw Exception('HTTP ${res.statusCode}: ${res.body}');
    return jsonDecode(res.body);
  }

  /// Current user's roles. frappe has shipped TWO shapes for role payloads;
  /// parse both instead of assuming one:
  ///   A: {"message": [{"role": "X"}, ...]}
  ///   B: {"message": {"roles": {"X": {...}, ...}}}
  Future<List<String>> fetchUserRoles() async {
    // The logged-in email is interpolated into the JSON filters value. Raw `+`
    // in a query string decodes to a SPACE server-side, so `user+x@...` would
    // silently become `user x@...` and match NO rows -> lockout.
    final email = Uri.encodeComponent(await loggedUser());
    final data = await getResource('/api/method/frappe.client.get_list'
        '?doctype=Has%20Role&parenttype=User'
        '&fields=["role"]&filters=[["parent","=","$email"]]'
        '&limit_page_length=0');
    final message = data['message'];
    if (message is List) {
      return message.map((row) => row['role'] as String).toList();
    }
    if (message is Map && message['roles'] is Map) {
      return (message['roles'] as Map).keys.cast<String>().toList();
    }
    throw const FormatException('Unrecognised roles payload');
  }

  Future<String> loggedUser() async {
    final data = await getResource('/api/method/frappe.auth.get_logged_user');
    return data['message'] as String;
  }

  /// Typed list query via `frappe.client.get_list` (Mốc 2). Params go through
  /// Uri queryParameters so values are percent-encoded — same lesson as the
  /// `+`-in-email fix: never interpolate into a raw query string.
  Future<List<Map<String, dynamic>>> getList(
    String doctype, {
    required List<String> fields,
    Map<String, dynamic>? filters,
    int limit = 20,
    String? orderBy,
  }) async {
    final data = await getResource(
      Uri(path: '/api/method/frappe.client.get_list', queryParameters: {
        'doctype': doctype,
        'fields': jsonEncode(fields),
        'limit_page_length': '$limit',
        if (filters != null) 'filters': jsonEncode(filters),
        if (orderBy != null) 'order_by': orderBy,
      }).toString(),
    );
    final message = data['message'];
    if (message is! List) {
      throw const FormatException('Unrecognised get_list payload');
    }
    return message.cast<Map>().map((row) => row.cast<String, dynamic>()).toList();
  }

  /// Submit a draft document from the app (Mốc 2: Sales Order approval).
  /// Server-side hooks (credit limit) still run — a rejection is surfaced as
  /// [SubmitRejected] with the server's message. NEVER retried blindly and
  /// NEVER forced through locally (hard rule #2: server wins).
  Future<void> submitDoc(String doctype, String name) async {
    final res = await _http
        .put(
          Uri.parse('$baseUrl/api/resource/${Uri.encodeComponent(doctype)}/${Uri.encodeComponent(name)}'),
          headers: {..._authHeaders, 'Content-Type': 'application/json'},
          body: jsonEncode({'docstatus': 1}),
        )
        .timeout(const Duration(seconds: 15));
    if (res.statusCode == 200) return;
    throw SubmitRejected(_serverMessage(res.body) ?? 'Máy chủ từ chối (HTTP ${res.statusCode})');
  }

  /// frappe wraps validation errors in `_server_messages`; extract the first
  /// human-readable one instead of dumping raw JSON at the user.
  static String? _serverMessage(String body) {
    try {
      final decoded = jsonDecode(body);
      if (decoded is! Map) return null;
      final messages = decoded['_server_messages'];
      final list = messages is String ? jsonDecode(messages) : messages;
      if (list is List && list.isNotEmpty) return list.first.toString();
      final exc = decoded['exception'] ?? decoded['_error_message'];
      return exc is String ? exc : null;
    } catch (_) {
      return null;
    }
  }
}

/// The server refused the submit (credit limit, permissions, workflow...).
/// The app shows the message and keeps the doc draft — server state wins.
class SubmitRejected implements Exception {
  const SubmitRejected(this.message);
  final String message;

  @override
  String toString() => message;
}
