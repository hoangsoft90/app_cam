import 'dart:async';

import 'package:flutter/material.dart';
import 'package:mobile_dealer/core.dart';
import 'package:mobile_dealer/driver_delivery.dart';
import 'package:mobile_dealer/offline_queue.dart';
import 'package:mobile_dealer/owner_dashboard.dart';
import 'package:mobile_dealer/queue_screen.dart';
import 'package:mobile_dealer/restore_session.dart';
import 'package:mobile_dealer/session.dart';
import 'package:mobile_dealer/settings_screen.dart';
import 'package:shared_preferences/shared_preferences.dart';

// Tests drive ErpClient/FinancialAction/etc. through this file's namespace.
export 'package:mobile_dealer/core.dart'
    show
        ApiRejected,
        AuthExpired,
        AuthResult,
        DeliveryResult,
        ErpClient,
        FinancialAction,
        OfflineFailure,
        SubmitRejected,
        buildDeliveryPayload,
        isOnline,
        looksLikeTokenPair,
        newIdempotencyKey,
        validateBaseUrl,
        vnd;
export 'package:mobile_dealer/driver_delivery.dart'
    show
        ConfirmDeliverySheet,
        DeliveryDeps,
        DriverDeliveryScreen,
        SignaturePad,
        kMethodOtp,
        kMethodPhotoOnly,
        kMethodSignature;
export 'package:mobile_dealer/offline_queue.dart'
    show
        FlushReport,
        OfflineQueue,
        QueueRejected,
        QueuedMutation,
        kOpDeliveryConfirm,
        kQueuePrefKey,
        kQueuedConflict,
        kQueuedPending;
export 'package:mobile_dealer/queue_screen.dart' show QueueScreen;

void main() => runApp(const CamVietApp());

class CamVietApp extends StatelessWidget {
  const CamVietApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Cám Việt',
      theme: ThemeData(colorSchemeSeed: const Color(0xFF2E9E57), useMaterial3: true),
      home: const SplashScreen(),
    );
  }
}

/// Mốc 1.5 — the password field doubles as a token field
/// (`api_key:api_secret`), sessions persist in the device Keystore and the
/// app auto-logins on launch (owner-requested), with a visible toggle in
/// Settings to switch that off.

/// Splash: runs auto-login once, then routes.
class SplashScreen extends StatefulWidget {
  const SplashScreen({super.key});

  @override
  State<SplashScreen> createState() => _SplashScreenState();
}

class _SplashScreenState extends State<SplashScreen> {
  @override
  void initState() {
    super.initState();
    unawaited(_bootstrap());
  }

  Future<void> _bootstrap() async {
    final (erp, error) = await restoreSession();
    if (!mounted) return;
    if (erp != null) {
      Navigator.of(context).pushReplacement(MaterialPageRoute(builder: (_) => HomeScreen(erp: erp)));
      return;
    }
    Navigator.of(context).pushReplacement(MaterialPageRoute(
      builder: (_) => LoginScreen(autoLoginError: error),
    ));
  }

  @override
  Widget build(BuildContext context) {
    return const Scaffold(
      body: Center(child: CircularProgressIndicator()),
    );
  }
}

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key, this.autoLoginError});

  /// Non-null when auto-login TRIED but failed for a reason the user can act
  /// on (e.g. server rejected the stored password). Silent skips (no stored
  /// session, offline) pass null and show no scary banner.
  final String? autoLoginError;

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
      // Fast-fill the two non-secret fields from (unencrypted) prefs; secrets
      // come from SessionStore only when the user asks for them in Settings.
      _url.text = p.getString('base_url') ?? '';
      _user.text = p.getString('user') ?? '';
    });
  }

  Future<void> _login() async {
    final urlError = validateBaseUrl(_url.text);
    if (urlError != null) {
      _snack(urlError);
      return;
    }
    setState(() => _busy = true);
    final baseUrl = _url.text.trim().replaceAll(RegExp(r'/+$'), '');
    final erp = ErpClient(baseUrl: baseUrl);
    final secret = _pass.text.trim();
    final result = looksLikeTokenPair(secret)
        ? await erp.loginWithToken(user: _user.text.trim(), pair: secret)
        : await erp.login(_user.text.trim(), secret);

    if (result.ok) {
      final isToken = looksLikeTokenPair(secret);
      await SessionStore.save(
        baseUrl: baseUrl,
        user: _user.text.trim(),
        secret: secret,
        secretIsToken: isToken,
        autoLogin: true, // default ON per owner request; toggle in Settings
      );
      if (erp.sid != null) await SessionStore.write('sid', erp.sid!);
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('base_url', baseUrl);
      await prefs.setString('user', _user.text.trim());
    }
    if (!mounted) return;
    setState(() => _busy = false);
    if (result.ok) {
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => HomeScreen(erp: erp, fullName: result.fullName)),
      );
    } else {
      _snack(result.error ?? 'Đăng nhập thất bại');
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Cám Việt — Đăng nhập'),
        actions: [
          IconButton(
            icon: const Icon(Icons.settings),
            tooltip: 'Cài đặt',
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute(builder: (_) => SettingsScreen(onSession: (erp) {
                Navigator.of(context).pushReplacement(
                    MaterialPageRoute(builder: (_) => HomeScreen(erp: erp)));
              }, onLogout: () {})),
            ),
          ),
        ],
      ),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          children: [
            if (widget.autoLoginError != null)
              Card(
                color: Colors.amber.shade100,
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Text('Đăng nhập tự động thất bại: ${widget.autoLoginError}\nVui lòng đăng nhập lại.',
                      style: const TextStyle(color: Colors.black87)),
                ),
              ),
            TextField(controller: _url, decoration: const InputDecoration(labelText: 'URL máy chủ ERPNext')),
            const SizedBox(height: 8),
            TextField(controller: _user, decoration: const InputDecoration(labelText: 'Tài khoản')),
            const SizedBox(height: 8),
            TextField(
              controller: _pass,
              obscureText: true,
              onSubmitted: (_) => _busy ? null : _login(),
              decoration: const InputDecoration(
                labelText: 'Mật khẩu / API token',
                helperText: 'Mật khẩu tài khoản, hoặc api_key:api_secret',
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
  const HomeScreen({
    super.key,
    required this.erp,
    this.fullName,
    this.deliveryDeps = const DeliveryDeps(),
    this.queue,
  });

  final ErpClient erp;
  final String? fullName;

  /// Camera/GPS seams for the driver screen; tests inject fakes here so the
  /// widget tree never touches a platform channel.
  final DeliveryDeps deliveryDeps;

  /// Mốc 4 — tests inject a preloaded queue; the app builds its own from prefs.
  final OfflineQueue? queue;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  static const _appRoles = {'Feed Dealer Manager', 'Feed Dealer Staff', 'Driver'};

  /// How often a device that is OFFLINE knocks on the door again. The driver
  /// should not have to open a screen for the queue to drain; 30 s is frequent
  /// enough to feel automatic while connected to a farm link, and the timer only
  /// runs while the app is offline.
  static const _offlineRetry = Duration(seconds: 30);

  List<String> _serverRoles = const [];
  String _role = 'Feed Dealer Staff';
  String _status = 'Đang tải…';
  bool _loadingRoles = true;

  OfflineQueue? _queue;
  Timer? _offlineTimer;
  FlushReport? _lastFlush;

  @override
  void initState() {
    super.initState();
    unawaited(_bootstrap());
    unawaited(_openQueue());
    // Reacts to the truth, not to a guess: whenever a request proves the link is
    // back, the queue drains immediately instead of on the next timer tick.
    isOnline.addListener(_onConnectivityChanged);
  }

  @override
  void dispose() {
    isOnline.removeListener(_onConnectivityChanged);
    _offlineTimer?.cancel();
    super.dispose();
  }

  /// Builds the queue (prefs-backed) and drains anything left over from a
  /// previous run — the app may have been killed while rows were waiting.
  Future<void> _openQueue() async {
    final queue = widget.queue ??
        OfflineQueue(
          prefs: await SharedPreferences.getInstance(),
          send: widget.erp.confirmDeliveryRaw,
        );
    await queue.load();
    if (!mounted) return;
    setState(() => _queue = queue);
    if (queue.length > 0) await _flushQueue();
  }

  void _onConnectivityChanged() {
    if (!mounted) return;
    setState(() {});
    if (isOnline.value) {
      _offlineTimer?.cancel();
      _offlineTimer = null;
      unawaited(_flushQueue());
      return;
    }
    // Nothing queued = nothing to sync: a timer would only keep the device
    // awake and make widget tests end with a live timer.
    if ((_queue?.length ?? 0) == 0) return;
    _offlineTimer ??= Timer.periodic(_offlineRetry, (_) => unawaited(_flushQueue()));
  }

  /// One sync pass. Reports honestly: a pass that sent nothing because the link
  /// was still down must NOT be shown as "đã đồng bộ".
  Future<void> _flushQueue() async {
    final queue = _queue;
    if (queue == null || queue.pendingRows.isEmpty) return;
    final report = await queue.flush();
    if (!mounted) return;
    setState(() => _lastFlush = report);
    if (report.authExpired) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Phiên đăng nhập đã hết hạn — đăng nhập lại để gửi hàng đợi.')),
      );
    }
  }

  /// Tap on the connectivity icon: probe, then report what ACTUALLY happened
  /// (the app must not claim a sync that did not take place).
  Future<void> _retryFromUi() async {
    final queue = _queue;
    if (queue == null || queue.length == 0) {
      setState(() => _status = 'Đang kiểm tra kết nối…');
      await _loadSummary();
      return;
    }
    if (queue.pendingRows.isEmpty) {
      // Only conflicts left: retrying them is pointless (the server decided).
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
        content: Text('Không còn mục nào chờ gửi — mục bị máy chủ từ chối cần bạn xử lý trong Hàng đợi.'),
      ));
      return;
    }
    await _flushQueue();
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(_flushSummary())));
  }

  /// A pass that sent nothing because the link was still down must never read as
  /// "đã đồng bộ".
  String _flushSummary() {
    final report = _lastFlush;
    if (report == null) return 'Không có gì để gửi.';
    if (report.authExpired) return 'Phiên đăng nhập đã hết hạn — đăng nhập lại rồi gửi lại.';
    if (report.offline) {
      return 'Vẫn chưa có mạng — còn ${_queue?.pendingRows.length ?? 0} mục chờ gửi.';
    }
    final parts = <String>[];
    if (report.sent > 0) parts.add('đã gửi ${report.sent}');
    if (report.conflicts > 0) parts.add('${report.conflicts} bị máy chủ từ chối (xem Hàng đợi)');
    return parts.isEmpty ? 'Không có gì để gửi.' : 'Kết quả: ${parts.join(', ')}.';
  }

  void _openQueueScreen() {
    final queue = _queue;
    if (queue == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Hàng đợi đang khởi tạo — thử lại sau một giây.')),
      );
      return;
    }
    Navigator.of(context)
        .push(MaterialPageRoute(
          builder: (_) => QueueScreen(queue: queue, onRetry: _flushQueue),
        ))
        .then((_) {
      if (mounted) setState(() {});
    });
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
          // Mốc 4: the offline state is DERIVED from real traffic, so the manual
          // dev toggle is gone. Tapping retries the queue — the one action that
          // can actually prove the link is back.
          ValueListenableBuilder<bool>(
            valueListenable: isOnline,
            builder: (_, online, _) {
              final waiting = _queue?.length ?? 0;
              return IconButton(
                onPressed: _retryFromUi,
                icon: Badge(
                  isLabelVisible: waiting > 0,
                  label: Text('$waiting'),
                  child: Icon(online ? Icons.cloud_done : Icons.cloud_off),
                ),
                tooltip: online
                    ? 'Đang kết nối${waiting > 0 ? ' — $waiting mục chờ gửi' : ''}'
                    : 'Mất kết nối${waiting > 0 ? ' — $waiting mục chờ gửi' : ''}',
              );
            },
          ),
          IconButton(
            icon: const Icon(Icons.settings),
            tooltip: 'Cài đặt',
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute(builder: (_) => SettingsScreen(
                onSession: (erp) => Navigator.of(context).pushReplacement(
                    MaterialPageRoute(builder: (_) => HomeScreen(erp: erp))),
                onLogout: () => Navigator.of(context).pushAndRemoveUntil(
                    MaterialPageRoute(builder: (_) => const LoginScreen()),
                    (route) => false),
              )),
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
          const Divider(),
          ListTile(
            leading: const Icon(Icons.cloud_queue),
            title: const Text('Hàng đợi ngoài tuyến'),
            subtitle: Text('${_queue?.pendingRows.length ?? 0} chờ gửi · '
                '${_queue?.conflictRows.length ?? 0} cần xử lý'),
            onTap: _openQueueScreen,
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
          if (_role == 'Feed Dealer Manager')
            FilledButton.icon(
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => OwnerDashboardScreen(erp: widget.erp)),
              ),
              icon: const Icon(Icons.dashboard),
              label: const Text('Dashboard Chủ (Mốc 2)'),
            )
          else if (driverMode)
            FilledButton.icon(
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute(
                  builder: (_) => DriverDeliveryScreen(
                    erp: widget.erp,
                    deps: widget.deliveryDeps,
                    queue: _queue,
                    onOpenQueue: _openQueueScreen,
                  ),
                ),
              ).then((_) {
                // Coming back may have QUEUED a row: refresh the counter and arm
                // the retry loop for it (the connectivity signal did not change).
                if (!mounted) return;
                setState(() {});
                _onConnectivityChanged();
              }),
              icon: const Icon(Icons.local_shipping),
              label: const Text('Giao hàng'),
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
