import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Durable session storage — ANDROID KEYSTORE (encrypted), never plaintext
/// prefs. What is stored and WHY it is acceptable here:
///  * base_url  — not a secret
///  * user      — not a secret
///  * role      — UI preference; the server re-checks every request
///  * token pair (api_key:api_secret) — long-lived secret, Keystore-encrypted
///  * password  — ONLY when auto-login is on, so an expired session can be
///    silently re-established. Owner-requested convenience; protected by
///    device unlock (Keystore requires it for key use) + go-live review.
///  * sid       — deliberately NOT persisted: short-lived cookie, re-probed.
/// Turning the auto-login toggle OFF wipes token+password (with it, a stolen
/// device unlocks the ERP account). Logout wipes everything.
class SessionStore {
  static const _storage = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );

  static Future<void> save({
    required String baseUrl,
    required String user,
    required String secret,
    required bool secretIsToken,
    required bool autoLogin,
  }) async {
    await _storage.write(key: 'base_url', value: baseUrl);
    await _storage.write(key: 'user', value: user);
    await _storage.write(key: 'auto_login', value: autoLogin ? '1' : '0');
    await _storage.write(key: 'secret_is_token', value: secretIsToken ? '1' : '0');
    await _storage.write(key: 'secret', value: secret);
  }

  static Future<void> write(String key, String value) => _storage.write(key: key, value: value);

  static Future<String?> read(String key) => _storage.read(key: key);

  /// Logout: wipe credentials; keep URL/user so the login screen comes back
  /// prefilled (typing the server URL on a phone is the annoying part).
  static Future<void> clearCredentials() async {
    await _storage.delete(key: 'secret');
    await _storage.delete(key: 'secret_is_token');
    await _storage.delete(key: 'auto_login');
    await _storage.delete(key: 'sid');
  }

  static Future<void> clearAll() => _storage.deleteAll();
}
