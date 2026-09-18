import 'package:flutter/material.dart';
import 'package:mobile_dealer/core.dart';
import 'package:mobile_dealer/session.dart';

/// Mốc 1.5 — Settings: edit server/user/credentials, toggle auto-login,
/// logout. Saving VALIDATES by actually logging in first: broken credentials
/// are never persisted (the app would otherwise auto-login into nothing).
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({
    super.key,
    required this.onSession,
    required this.onLogout,
  });

  /// Called with a verified client after credentials were saved + login OK.
  final ValueChanged<ErpClient> onSession;

  /// Called after the stored credentials were wiped.
  final VoidCallback onLogout;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final _url = TextEditingController();
  final _user = TextEditingController();
  final _secret = TextEditingController();
  bool _autoLogin = false;
  bool _busy = false;
  bool _showSecret = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final url = await SessionStore.read('base_url');
    final user = await SessionStore.read('user');
    final secret = await SessionStore.read('secret');
    final auto = await SessionStore.read('auto_login');
    if (!mounted) return;
    setState(() {
      _url.text = url ?? '';
      _user.text = user ?? '';
      _secret.text = secret ?? '';
      _autoLogin = auto == '1';
    });
  }

  Future<void> _save() async {
    final urlErr = validateBaseUrl(_url.text);
    if (urlErr != null) {
      _snack(urlErr);
      return;
    }
    final user = _user.text.trim();
    final secret = _secret.text.trim();
    if (user.isEmpty || secret.isEmpty) {
      _snack('Nhập đủ tài khoản và mật khẩu / token');
      return;
    }
    setState(() => _busy = true);
    final baseUrl = _url.text.trim().replaceAll(RegExp(r'/+$'), '');
    final erp = ErpClient(baseUrl: baseUrl);
    final isToken = looksLikeTokenPair(secret);
    final result =
        isToken ? await erp.loginWithToken(user: user, pair: secret) : await erp.login(user, secret);

    if (!mounted) return;
    if (!result.ok) {
      setState(() => _busy = false);
      _snack(result.error ?? 'Không lưu được — đăng nhập thất bại');
      return;
    }
    await SessionStore.save(
      baseUrl: baseUrl,
      user: user,
      secret: secret,
      secretIsToken: isToken,
      autoLogin: _autoLogin,
    );
    if (erp.sid != null) await SessionStore.write('sid', erp.sid!);
    if (!mounted) return;
    setState(() => _busy = false);
    widget.onSession(erp);
  }

  Future<void> _logout() async {
    await SessionStore.clearCredentials();
    widget.onLogout();
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Cài đặt')),
      body: _busy
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                TextField(
                  controller: _url,
                  keyboardType: TextInputType.url,
                  decoration: const InputDecoration(
                    labelText: 'URL máy chủ ERPNext',
                    helperText: 'Chỉ chấp nhận HTTPS (localhost/10.0.2.2 chỉ khi bản debug)',
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _user,
                  decoration: const InputDecoration(labelText: 'Tài khoản (email)'),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _secret,
                  obscureText: !_showSecret,
                  decoration: InputDecoration(
                    labelText: 'Mật khẩu / API token',
                    helperText: 'Dạng api_key:api_secret sẽ đăng nhập bằng token',
                    suffixIcon: IconButton(
                      onPressed: () => setState(() => _showSecret = !_showSecret),
                      icon: Icon(_showSecret ? Icons.visibility_off : Icons.visibility),
                    ),
                  ),
                ),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Tự động đăng nhập khi mở app'),
                  subtitle: const Text('Lưu thông tin trong kho mã hoá của thiết bị (Keystore)'),
                  value: _autoLogin,
                  onChanged: (v) => setState(() => _autoLogin = v),
                ),
                const SizedBox(height: 16),
                FilledButton.icon(
                  onPressed: _save,
                  icon: const Icon(Icons.save),
                  label: const Text('Lưu & kiểm tra đăng nhập'),
                ),
                const SizedBox(height: 8),
                OutlinedButton.icon(
                  onPressed: _logout,
                  style: OutlinedButton.styleFrom(foregroundColor: Colors.red),
                  icon: const Icon(Icons.logout),
                  label: const Text('Đăng xuất (xoá phiên trên máy)'),
                ),
              ],
            ),
    );
  }
}
