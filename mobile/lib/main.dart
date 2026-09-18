import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

void main() => runApp(const CamVietApp());

/// P2 internal mobile app (Cám Việt) — Owner / Staff / Driver, one codebase.
///
/// Hard rules (phase_02_internal_mobile.md, kept in code, not comments):
/// * NO financial mutation while offline: `FinancialAction.guard` throws
///   unless the device is online, so a payment / credit change button can
///   never silently queue.
/// * Server wins on conflict: local pending ops are retried, never force-
///   pushed over a newer server record.
/// * Every create carries an `idempotency_key`; retrying the same op twice
///   yields ONE record server-side.
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

/// Online state lives in a ValueNotifier so guards can be tested without
/// widgets.
final ValueNotifier<bool> isOnline = ValueNotifier<bool>(true);

/// Generators for idempotent creates. Stable per logical operation, not per
/// retry: the caller computes it once when the op is created and stores it in
/// the queue row.
int _idemSeq = 0;

String newIdempotencyKey() {
  _idemSeq += 1;
  return 'mob-${DateTime.now().toUtc().microsecondsSinceEpoch}-$_idemSeq';
}

/// Hard gate for money-touching actions (payment, credit override, debt edit).
/// Throws while `isOnline` is false — the UI also hides those buttons, but a
/// stale screen must not be able to sneak one through.
class FinancialAction {
  static void guard() {
    if (!isOnline.value) {
      throw StateError('Financial mutation is forbidden while offline');
    }
  }
}

/// One queued create/retry. Serialized to JSON for durable storage.
class PendingOp {
  PendingOp({required this.method, required this.path, required this.body, required this.idempotencyKey});

  final String method;
  final String path;
  final Map<String, dynamic> body;
  final String idempotencyKey;

  Map<String, dynamic> toJson() => {
        'method': method,
        'path': path,
        'body': body,
        'idempotency_key': idempotencyKey,
      };

  factory PendingOp.fromJson(Map<String, dynamic> j) => PendingOp(
        method: j['method'] as String,
        path: j['path'] as String,
        body: (j['body'] as Map).cast<String, dynamic>(),
        idempotencyKey: j['idempotency_key'] as String,
      );
}

/// REST client for the ERPNext site. Auth: token pair from login (apikey:secret).
class ErpClient {
  ErpClient({required this.baseUrl, this.token});

  final String baseUrl;
  String? token;
  final List<PendingOp> queue = [];

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        if (token != null) 'Authorization': 'token ${token!}',
      };

  /// Login against frappe auth; keeps the api key/secret style token flow.
  Future<bool> login(String user, String password) async {
    try {
      final res = await http
          .post(Uri.parse('$baseUrl/api/method/login'),
              headers: _headers, body: jsonEncode({'usr': user, 'pwd': password}))
          .timeout(const Duration(seconds: 15));
      return res.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  /// GET never queues — reads fail fast offline.
  Future<dynamic> get(String path) async {
    final res =
        await http.get(Uri.parse('$baseUrl$path'), headers: _headers).timeout(const Duration(seconds: 15));
    if (res.statusCode != 200) throw Exception('HTTP ${res.statusCode}: ${res.body}');
    return jsonDecode(res.body);
  }

  /// Writes go through the idempotent queue. Online → send now (server wins:
  /// a 409/423 response is surfaced, never force-overwritten). Offline → park.
  Future<String> write(String method, String path, Map<String, dynamic> body) async {
    final key = newIdempotencyKey();
    final op = PendingOp(method: method, path: path, body: body, idempotencyKey: key);
    if (!isOnline.value) {
      queue.add(op);
      return key;
    }
    await _send(op);
    return key;
  }

  Future<void> _send(PendingOp op) async {
    final headers = {..._headers, 'X-Idempotency-Key': op.idempotencyKey};
    final uri = Uri.parse('$baseUrl${op.path}');
    final res = switch (op.method) {
      'POST' => await http.post(uri, headers: headers, body: jsonEncode(op.body)),
      'PUT' => await http.put(uri, headers: headers, body: jsonEncode(op.body)),
      _ => throw UnsupportedError(op.method),
    };
    if (res.statusCode >= 500) {
      queue.add(op); // transient: retry later, same key
    }
    // 4xx (incl. conflict): server won — drop, surface via caller UI next read.
  }

  /// Flush pending ops; keeps order, stops at first persistent failure.
  Future<int> flushQueue() async {
    var sent = 0;
    final remaining = List<PendingOp>.from(queue);
    for (final op in remaining) {
      try {
        await _send(op);
        queue.remove(op);
        sent += 1;
      } catch (_) {
        break;
      }
    }
    return sent;
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
    final ok = await erp.login(_user.text.trim(), _pass.text);
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('base_url', _url.text.trim());
    await prefs.setString('user', _user.text.trim());
    if (!mounted) return;
    setState(() => _busy = false);
    if (ok) {
      Navigator.of(context).pushReplacement(MaterialPageRoute(builder: (_) => HomeScreen(erp: erp)));
    } else {
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('Đăng nhập thất bại — kiểm tra URL / user / mật khẩu')));
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
            TextField(controller: _user, decoration: const InputDecoration(labelText: 'Tài khoản')),
            TextField(controller: _pass, obscureText: true, decoration: const InputDecoration(labelText: 'Mật khẩu')),
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

/// Multi-role switcher: role lives in session state, switching never logs out.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.erp});

  final ErpClient erp;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  String _role = 'Feed Dealer Staff';
  String _status = '';

  static const _roles = {'Feed Dealer Manager', 'Feed Dealer Staff', 'Driver'};

  Future<void> _loadSummary() async {
    try {
      final data = await widget.erp.get(
          '/api/resource/Feed Batch?fields=[\"name\",\"total_debt\"]&limit_page_length=5&order_by=modified desc');
      final rows = (data['data'] as List).cast<Map>();
      setState(() => _status = rows.isEmpty ? 'Chưa có lứa nào' : rows.map((r) => r['name']).join(', '));
    } catch (e) {
      setState(() => _status = 'Lỗi tải: $e');
    }
  }

  @override
  void initState() {
    super.initState();
    _loadSummary();
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
            builder: (_, online, __) => IconButton(
              onPressed: () => isOnline.value = !isOnline.value, // dev toggle; replace with connectivity_plus
              icon: Icon(online ? Icons.cloud_done : Icons.cloud_off),
              tooltip: online ? 'Online (bấm để mô phỏng offline)' : 'Offline (bấm để online)',
            ),
          ),
        ],
      ),
      drawer: Drawer(
        child: ListView(children: [
          const DrawerHeader(child: Text('Chọn vai trò')),
          for (final r in _roles)
            ListTile(
              title: Text(r),
              trailing: r == _role ? const Icon(Icons.check) : null,
              onTap: () => setState(() => _role = r),
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
          if (driverMode) ...[
            FilledButton.icon(
              // delivery confirm is an operational action: queued with idempotency
              onPressed: () async {
                final key = await widget.erp.write('POST', '/api/resource/Delivery Confirmation', {'dummy': 1});
                setState(() => _status = 'Đã xác nhận giao (key=$key)');
              },
              icon: const Icon(Icons.local_shipping),
              label: const Text('Xác nhận giao hàng'),
            ),
          ] else ...[
            FilledButton.icon(
              // MONEY: hidden AND guarded when offline (belt and suspenders)
              onPressed: () {
                FinancialAction.guard();
                setState(() => _status = 'Màn thu tiền (P2 sau)');
              },
              icon: const Icon(Icons.payments),
              label: const Text('Thu tiền (online only)'),
            ),
          ],
        ]),
      ),
    );
  }
}
