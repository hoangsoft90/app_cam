import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

void main() => runApp(const CamVietApp());

/// P2 internal mobile app (Cám Việt) — Owner / Staff / Driver, one codebase
/// (`mobile-dealer`). Mốc 1: scaffold + login REST + multi-role switcher.
///
/// Hard rules (phase_02_internal_mobile.md — enforced in code, not comments):
/// * NO financial mutation while offline: [FinancialAction.guard] throws unless
///   the device is online, so a payment / credit change can never silently queue.
/// * Server wins on conflict: failed/conflicting writes are surfaced, never
///   force-overwritten locally.
/// * Every create carries an idempotency key ([newIdempotencyKey]).
class CamVietApp extends StatelessWidget {
  const CamVietApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Cám Việt',
      theme: ThemeData(colorSchemeSeed: const Color(0xFF2E9E57), useMaterial3: true),
      home: const LoginScreen(),
    );
  }
}

/// Online flag — dev toggle for now; Mốc 4 swaps in connectivity_plus.
final ValueNotifier<bool> isOnline = ValueNotifier<bool>(true);

int _idemSeq = 0;

/// Stable per logical operation: the caller computes it once when the op is
/// created and stores it with the queued row (never per retry).
String newIdempotencyKey() {
  _idemSeq += 1;
  return 'mob-${DateTime.now().toUtc().microsecondsSinceEpoch}-$_idemSeq';
}

/// Hard gate for money-touching actions (payment, credit override, debt edit).
class FinancialAction {
  static void guard() {
    if (!isOnline.value) {
      throw StateError('Financial mutation is forbidden while offline');
    }
  }
}

/// Auth outcome. `error` is a human-friendly Vietnamese message when `ok`.
class AuthResult {
  const AuthResult({required this.ok, this.fullName, this.error});

  final bool ok;
  final String? fullName;
  final String? error;
}

/// REST client against the ERPNext site (Mốc 1 scope: login + reads).
///
/// Two auth paths, same two credential fields:
/// 1. Password: POST /api/method/login → session cookie `sid` (kept here and
///    sent as `Cookie: sid=...`; the `http` package has no cookie jar).
/// 2. API token fallback: the same fields interpreted as
///    `Authorization: token <api_key>:<api_secret>` (useful for dev machines).
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
      final tokenResult = await _loginWithToken(user, password);
      if (tokenResult.ok) return tokenResult;
      return AuthResult(ok: false, error: friendly ?? tokenResult.error);
    } on FormatException {
      // non-JSON / wrong-shape body (proxy pages, null) — not a network fault
      return const AuthResult(ok: false, error: 'Phản hồi không hợp lệ từ máy chủ');
    } on Exception {
      return const AuthResult(ok: false, error: 'Không kết nối được máy chủ');
    }
  }

  Future<AuthResult> _loginWithToken(String key, String secret) async {
    try {
      final res = await _http
          .get(
            Uri.parse('$baseUrl/api/method/frappe.auth.get_logged_user'),
            headers: {'Authorization': 'token $key:$secret'},
          )
          .timeout(const Duration(seconds: 15));
      if (res.statusCode != 200) return const AuthResult(ok: false, error: 'Đăng nhập thất bại');
      tokenPair = '$key:$secret';
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

  /// Current user's roles. frappe has shipped TWO shapes for `frappe.client
  /// get_list`-style role payloads; parse both instead of assuming one:
  ///   A: {"message": [{"role": "X"}, ...]}
  ///   B: {"message": {"roles": {"X": {...}, ...}}}
  Future<List<String>> fetchUserRoles() async {
    // The logged-in email is interpolated into the JSON filters value. Raw `+`
    // in a query string decodes to a SPACE server-side, so `user+x@...` (very
    // common with Gmail) would silently become `user x@...` and match NO rows
    // -> all roles disabled -> lockout. Encode the email only; the server
    // URL-decodes the whole value once, yielding the original email in JSON.
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
}

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _url = TextEditingController();
  final _user = TextEditingController();
  final _pass = TextEditingController();
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    SharedPreferences.getInstance().then((p) {
      _url.text = p.getString('base_url') ?? '';
      _user.text = p.getString('user') ?? '';
    });
  }

  Future<void> _login() async {
    setState(() => _busy = true);
    final erp = ErpClient(baseUrl: _url.text.trim().replaceAll(RegExp(r'/+$'), ''));
    final result = await erp.login(_user.text.trim(), _pass.text);

    if (result.ok) {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('base_url', erp.baseUrl);
      await prefs.setString('user', _user.text.trim());
    }
    if (!mounted) return;
    setState(() => _busy = false);
    if (result.ok) {
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => HomeScreen(erp: erp, fullName: result.fullName)),
      );
    } else {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(result.error ?? 'Đăng nhập thất bại')));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Cám Việt — Đăng nhập')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          children: [
            TextField(controller: _url, decoration: const InputDecoration(labelText: 'URL máy chủ ERPNext')),
            const SizedBox(height: 8),
            TextField(controller: _user, decoration: const InputDecoration(labelText: 'Tài khoản')),
            const SizedBox(height: 8),
            TextField(
              controller: _pass,
              obscureText: true,
              onSubmitted: (_) => _busy ? null : _login(),
              decoration: const InputDecoration(
                labelText: 'Mật khẩu',
                helperText: 'Đăng nhập bằng mật khẩu, hoặc API key/secret của bạn',
              ),
            ),
            const SizedBox(height: 20),
            FilledButton(
              onPressed: _busy ? null : _login,
              child: Text(_busy ? 'Đang đăng nhập…' : 'Đăng nhập'),
            ),
          ],
        ),
      ),
    );
  }
}

/// Multi-role switcher: role lives in session state; switching never logs out
/// (acceptance #5). Roles come from the SERVER, filtered to what this app
/// understands — an account with none of them falls back to Staff read-only.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.erp, this.fullName});

  final ErpClient erp;
  final String? fullName;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  static const _appRoles = {'Feed Dealer Manager', 'Feed Dealer Staff', 'Driver'};

  List<String> _serverRoles = const [];
  String _role = 'Feed Dealer Staff';
  String _status = 'Đang tải…';
  bool _loadingRoles = true;

  @override
  void initState() {
    super.initState();
    _bootstrap();
  }

  Future<void> _bootstrap() async {
    try {
      _serverRoles = (await widget.erp.fetchUserRoles()).where(_appRoles.contains).toList();
      final prefs = await SharedPreferences.getInstance();
      final saved = prefs.getString('role');
      if (saved != null && _serverRoles.contains(saved)) _role = saved;
      if (_serverRoles.isNotEmpty && !_serverRoles.contains(_role)) {
        _role = _serverRoles.first; // trust the server over the saved pref
      }
      await _loadSummary();
    } catch (e) {
      setState(() => _status = 'Lỗi tải vai trò/dữ liệu: $e');
    } finally {
      if (mounted) setState(() => _loadingRoles = false);
    }
  }

  Future<void> _loadSummary() async {
    final data = await widget.erp.getResource(
        '/api/resource/Feed Batch?fields=["name","total_debt"]&limit_page_length=5&order_by=modified desc');
    final rows = (data['data'] as List).cast<Map>();
    setState(() => _status = rows.isEmpty ? 'Chưa có lứa nào' : rows.map((r) => r['name']).join(', '));
  }

  Future<void> _switchRole(String role) async {
    setState(() => _role = role);
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('role', role);
    if (!mounted) return; // async gap: the widget may be gone after await
    Navigator.of(context).pop(); // close the drawer
  }

  @override
  Widget build(BuildContext context) {
    final driverMode = _role == 'Driver';
    return Scaffold(
      appBar: AppBar(
        title: const Text('Cám Việt'),
        actions: [
          ValueListenableBuilder<bool>(
            valueListenable: isOnline,
            builder: (_, online, _) => IconButton(
              onPressed: () => isOnline.value = !isOnline.value, // dev toggle until Mốc 4
              icon: Icon(online ? Icons.cloud_done : Icons.cloud_off),
              tooltip: online ? 'Online (bấm để mô phỏng offline)' : 'Offline (bấm để online)',
            ),
          ),
        ],
      ),
      drawer: Drawer(
        child: ListView(children: [
          DrawerHeader(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('Chọn vai trò', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
                const SizedBox(height: 8),
                Text(widget.fullName ?? '', style: const TextStyle(color: Colors.black54)),
                if (_loadingRoles)
                  const Text('Đang tải vai trò…', style: TextStyle(color: Colors.black45))
                else
                  Text('Máy chủ cấp: ${_serverRoles.join(', ')}',
                      style: const TextStyle(color: Colors.black45, fontSize: 12)),
              ],
            ),
          ),
          for (final r in _appRoles)
            ListTile(
              title: Text(r),
              trailing: r == _role ? const Icon(Icons.check) : null,
              // A role the server did NOT grant is shown disabled (drawer lists
              // app roles so the user sees they exist but lack access).
              enabled: _serverRoles.contains(r),
              onTap: () => _switchRole(r),
            ),
        ]),
      ),
      body: RefreshIndicator(
        onRefresh: _loadSummary,
        child: ListView(padding: const EdgeInsets.all(16), children: [
          Text('Vai trò: $_role', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          Text(_status),
          const SizedBox(height: 24),
          if (driverMode)
            FilledButton.icon(
              onPressed: () => setState(() => _status = 'Màn giao hàng — Mốc 3'),
              icon: const Icon(Icons.local_shipping),
              label: const Text('Giao hàng (Mốc 3)'),
            )
          else
            // MONEY: guarded; Mốc 5 will REMOVE it from the tree when offline.
            FilledButton.icon(
              onPressed: () {
                FinancialAction.guard();
                setState(() => _status = 'Màn thu tiền (Mốc 2)');
              },
              icon: const Icon(Icons.payments),
              label: const Text('Thu tiền (online only)'),
            ),
        ]),
      ),
    );
  }
}
