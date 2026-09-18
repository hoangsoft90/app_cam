import 'package:http/http.dart' as http;
import 'package:mobile_dealer/core.dart';
import 'package:mobile_dealer/session.dart';

/// Re-establish a session at app start (auto-login).
///
/// Returns the working client, or null when the user must see LoginScreen:
///   * no stored session (first launch) → null (silent)
///   * stored URL unreachable → null (silent — user may be offline)
///   * token/sid expired AND (no password stored OR auto-login off) → null
/// Anything surprising (server error, bad payload) → surfaced in `error`
/// instead of silently dumping the user at the login screen, so a real
/// infrastructure problem is never disguised as "please log in".
///
/// Probe order: sid probe (cheapest) → token probe → password re-login.
/// The home screen must re-fetch data after restore; roles etc. are NOT cached
/// as truth (server-wins).
Future<(ErpClient?, String?)> restoreSession({http.Client? client}) async {
  final baseUrl = await SessionStore.read('base_url');
  final user = await SessionStore.read('user');
  if (baseUrl == null || baseUrl.isEmpty || user == null || user.isEmpty) {
    return (null, null); // first launch — normal, silent
  }

  final sid = await SessionStore.read('sid');
  final secret = await SessionStore.read('secret');
  final autoLogin = await SessionStore.read('auto_login') == '1';
  final secretIsToken = await SessionStore.read('secret_is_token') == '1';
  // The token pair lives in `secret` — ONE source of truth: SessionStore.save
  // stores api_key:api_secret as `secret` with secret_is_token=1. (Bug caught
  // in review: this file used to read a separate `token_pair` key that NO
  // writer ever created, so token-based auto-login silently never worked.)
  final tokenPair = secretIsToken ? secret : await SessionStore.read('token_pair');

  Future<ErpClient?> probe(ErpClient erp) async {
    try {
      await erp.loggedUser();
      return erp; // a working probe IS the restored session
    } on Exception {
      return null;
    }
  }

  final erp = ErpClient(baseUrl: baseUrl, client: client);
  if (sid != null && sid.isNotEmpty) {
    erp.sid = sid;
    final ok = await probe(erp);
    if (ok != null) return (ok, null);
    erp.sid = null; // dead cookie must not linger on the client
  }
  if (tokenPair != null && tokenPair.isNotEmpty) {
    erp.tokenPair = tokenPair;
    final ok = await probe(erp);
    if (ok != null) return (ok, null);
    erp.tokenPair = null;
  }

  // Both probes dead. Silent password re-login is a CONVENIENCE the owner
  // opted into — honour the toggle, never re-login without it.
  if (autoLogin && secret != null && secret.isNotEmpty && !secretIsToken) {
    final r = await erp.login(user, secret);
    if (r.ok) {
      // refresh the sid for next launch
      if (erp.sid != null) await SessionStore.write('sid', erp.sid!);
      return (erp, null);
    }
    return (null, r.error);
  }
  if (secretIsToken && tokenPair == null) {
    // inconsistent storage; force re-login
    return (null, null);
  }
  return (null, null);
}
