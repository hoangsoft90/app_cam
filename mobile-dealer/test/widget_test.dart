import 'dart:convert';

import 'package:mobile_dealer/main.dart' as app;
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

http.Response _json(Map<String, dynamic> body, {int status = 200, Map<String, String> headers = const {}}) =>
    http.Response(jsonEncode(body), status, headers: headers);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    app.isOnline.value = true;
  });

  group('FinancialAction guard (hard rule #1)', () {
    test('throws while offline', () {
      app.isOnline.value = false;
      expect(() => app.FinancialAction.guard(), throwsStateError);
    });

    test('is a no-op while online', () {
      expect(() => app.FinancialAction.guard(), returnsNormally);
    });
  });

  group('idempotency keys (hard rule #3)', () {
    test('are unique across 100 calls', () {
      final keys = {for (var i = 0; i < 100; i++) app.newIdempotencyKey()};
      expect(keys.length, 100);
    });
  });

  group('ErpClient.login (Mốc 1)', () {
    test('password login succeeds and captures sid', () async {
      final client = MockClient((req) async {
        expect(req.url.path, '/api/method/login');
        expect(req.bodyFields['usr'], 'p2-test-owner@example.com');
        return _json({'message': 'Logged In', 'full_name': 'P2 Owner'},
            headers: {'set-cookie': 'sid=abc123; Path=/; HttpOnly'});
      });
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: client);
      final r = await erp.login('p2-test-owner@example.com', 'pw');
      expect(r.ok, isTrue);
      expect(r.fullName, 'P2 Owner');
      expect(erp.sid, 'abc123');
    });

    test('wrong password falls back to token auth then fails with friendly error', () async {
      final client = MockClient((req) async {
        if (req.url.path == '/api/method/login') {
          return http.Response(
              '{"exc": ["Traceback ... currentsite.txt ..."]}', 401);
        }
        // token fallback probe also rejected
        return _json({'message': 'Guest'}, status: 403);
      });
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: client);
      final r = await erp.login('u', 'bad');
      expect(r.ok, isFalse);
      expect(r.error, 'Sai tài khoản hoặc mật khẩu');
      expect(erp.tokenPair, isNull);
    });

    test('token pair fallback succeeds when password auth is not applicable', () async {
      final client = MockClient((req) async {
        if (req.url.path == '/api/method/login') {
          return http.Response('{"exc": ["... currentsite.txt ..."]}', 401);
        }
        expect(req.headers['Authorization'], 'token K:S');
        return _json({'message': 'p2-test-staff@example.com'});
      });
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: client);
      final r = await erp.login('K', 'S');
      expect(r.ok, isTrue);
      expect(erp.tokenPair, 'K:S');
    });

    test('network failure maps to a friendly offline message', () async {
      final client = MockClient((req) async => throw http.ClientException('down'));
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: client);
      final r = await erp.login('u', 'p');
      expect(r.ok, isFalse);
      expect(r.error, 'Không kết nối được máy chủ');
    });
  });

  group('fetchUserRoles (Mốc 1)', () {
    test('parses list shape', () async {
      final client = MockClient((req) async {
        if (req.url.path == '/api/method/frappe.auth.get_logged_user') {
          return _json({'message': 'p2-test-staff@example.com'});
        }
        expect(req.url.queryParameters['doctype'], 'Has Role');
        return _json({'message': [
          {'role': 'Feed Dealer Staff'},
          {'role': 'Driver'},
        ]});
      });
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: client);
      expect(await erp.fetchUserRoles(), ['Feed Dealer Staff', 'Driver']);
    });

    test('parses map shape', () async {
      final client = MockClient((req) async {
        if (req.url.path == '/api/method/frappe.auth.get_logged_user') {
          return _json({'message': 'owner@example.com'});
        }
        return _json({'message': {
          'roles': {'Feed Dealer Manager': {}, 'Driver': {}},
        }});
      });
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: client);
      expect(await erp.fetchUserRoles(), ['Feed Dealer Manager', 'Driver']);
    });
  });

  group('session restore (Mốc 1)', () {
    test('saved role is respected only if the server grants it', () async {
      SharedPreferences.setMockInitialValues({'role': 'Driver'});
      final prefs = await SharedPreferences.getInstance();
      final serverRoles = ['Feed Dealer Manager'];
      var role = prefs.getString('role') ?? 'Feed Dealer Staff';
      if (serverRoles.isNotEmpty && !serverRoles.contains(role)) {
        role = serverRoles.first; // trust the server over the saved pref
      }
      expect(role, 'Feed Dealer Manager');
    });
  });
}
