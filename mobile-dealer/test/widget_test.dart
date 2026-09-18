import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:mobile_dealer/main.dart' as app;
import 'package:mobile_dealer/owner_dashboard.dart';
import 'package:mobile_dealer/restore_session.dart';
import 'package:mobile_dealer/session.dart';
import 'package:shared_preferences/shared_preferences.dart';

http.Response _json(Map<String, dynamic> body, {int status = 200, Map<String, String> headers = const {}}) =>
    // bytes + utf-8: a plain http.Response(string) defaults to latin-1 and
    // THROWS on Vietnamese text ("Vũ") — measured on CI, not guessed.
    http.Response.bytes(utf8.encode(jsonEncode(body)), status, headers: {
      'content-type': 'application/json; charset=utf-8',
      ...headers,
    });

/// Standard mocked ERP: login + roles (Manager + Driver) + one Feed Batch row.
MockClient mockErp() {
  return MockClient((req) async {
    if (req.url.path == '/api/method/login') {
      return _json({'message': 'Logged In', 'full_name': 'P2 Owner'},
          headers: {'set-cookie': 'sid=abc123; Path=/; HttpOnly'});
    }
    if (req.url.path == '/api/method/frappe.auth.get_logged_user') {
      return _json({'message': 'owner@example.com'});
    }
    if (req.url.path == '/api/method/frappe.client.get_list') {
      return _json({'message': [
        {'role': 'Feed Dealer Manager'},
        {'role': 'Driver'},
      ]});
    }
    // Uri normalises the space in "Feed Batch" to %20 in req.url.path.
    if (req.url.path.endsWith('/api/resource/Feed%20Batch')) {
      return _json({'data': [
        {'name': 'LOT-2026-00001', 'total_debt': 1500000},
      ]});
    }
    return http.Response('{"exc": ["nope"]}', 404);
  });
}

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

  group('validateBaseUrl (HTTPS-only policy — VIỆC 1)', () {
    test('https passes', () {
      expect(app.validateBaseUrl('https://erp.example.com'), isNull);
    });

    test('http is rejected with the exact owner-required warning', () {
      expect(app.validateBaseUrl('http://erp.example.com'),
          'Chỉ hỗ trợ kết nối HTTPS để bảo vệ mật khẩu');
    });

    test('debug-only loopback exception: localhost / 127.0.0.1 / 10.0.2.2 pass', () {
      // kDebugMode is true under `flutter test`, so the DEBUG exception is live
      // here. In release builds kDebugMode is tree-shaken false and these same
      // inputs are rejected — that branch cannot be exercised from unit tests,
      // it is asserted by reading the code (single `kDebugMode &&` gate).
      expect(app.validateBaseUrl('http://localhost:8000'), isNull);
      expect(app.validateBaseUrl('http://127.0.0.1:8000'), isNull);
      expect(app.validateBaseUrl('http://10.0.2.2:8000'), isNull);
    });

    test('ftp / garbage rejected as invalid', () {
      expect(app.validateBaseUrl('ftp://x'), isNotNull);
      expect(app.validateBaseUrl('not a url'), isNotNull);
    });
  });

  group('looksLikeTokenPair (api_key:api_secret in password field)', () {
    test('accepts exactly one colon with both sides non-empty', () {
      expect(app.looksLikeTokenPair('abc123:def456'), isTrue);
    });

    test('rejects empty sides, multiple colons, no colon', () {
      expect(app.looksLikeTokenPair(':def456'), isFalse);
      expect(app.looksLikeTokenPair('abc123:'), isFalse);
      expect(app.looksLikeTokenPair('a:b:c'), isFalse);
      expect(app.looksLikeTokenPair('my password'), isFalse);
    });
  });

  group('ErpClient.login (Mốc 1 + hardened)', () {
    test('password login succeeds and captures sid', () async {
      final client = MockClient((req) async =>
          _json({'message': 'Logged In', 'full_name': 'P2 Owner'},
              headers: {'set-cookie': 'sid=abc123; Path=/; HttpOnly'}));
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: client);
      final r = await erp.login('p2-test-owner@example.com', 'pw');
      expect(r.ok, isTrue);
      expect(r.fullName, 'P2 Owner');
      expect(erp.sid, 'abc123');
    });

    test('wrong password falls back to token auth then fails with friendly error', () async {
      final client = MockClient((req) async {
        if (req.url.path == '/api/method/login') {
          return http.Response('{"exc": ["Traceback ... currentsite.txt ..."]}', 401);
        }
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

    test('password that IS a token pair goes straight to token auth', () async {
      String? authHeader;
      final client = MockClient((req) async {
        authHeader = req.headers['Authorization'];
        return _json({'message': 'owner@example.com'});
      });
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: client);
      final r = await erp.login('ignored-user', 'K:S');
      expect(r.ok, isTrue);
      expect(authHeader, 'token K:S');
      expect(erp.tokenPair, 'K:S');
    });

    test('non-JSON 200 body (proxy page / null) does not crash the app', () async {
      // Regression for review H2: jsonDecode on a non-map JSON raises a
      // TypeError (an Error, not an Exception) which `on Exception` missed.
      final client = MockClient((req) async => http.Response('null', 200));
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: client);
      final r = await erp.login('u', 'p');
      expect(r.ok, isFalse);
      expect(r.error, 'Phản hồi không hợp lệ từ máy chủ');
      expect(erp.sid, isNull);
    });

    test('200 body that is a JSON list is handled too', () async {
      final client = MockClient((req) async =>
          http.Response('[1,2,3]', 200, headers: {'set-cookie': 'sid=zzz; Path=/'}));
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: client);
      final r = await erp.login('u', 'p');
      expect(r.ok, isFalse);
      expect(r.error, 'Phản hồi không hợp lệ từ máy chủ');
      expect(erp.sid, isNull); // no half-set state on a bad body
    });

    test('network failure maps to a friendly offline message', () async {
      final client = MockClient((req) async => throw http.ClientException('down'));
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: client);
      final r = await erp.login('u', 'p');
      expect(r.ok, isFalse);
      expect(r.error, 'Không kết nối được máy chủ');
    });

    test('plain-http server URL is BLOCKED at login (owner HTTPS policy)', () async {
      var asked = false;
      final client = MockClient((req) async {
        asked = true;
        return _json({'message': 'Logged In'});
      });
      final erp = app.ErpClient(baseUrl: 'http://erp.example.com', client: client);
      final r = await erp.login('u', 'p');
      expect(r.ok, isFalse);
      expect(r.error, 'Chỉ hỗ trợ kết nối HTTPS để bảo vệ mật khẩu');
      expect(asked, isFalse, reason: 'no request may leave the device to a non-HTTPS server');
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

    test('email with + is percent-encoded in role filters (no silent space)', () async {
      // Regression for review H3: raw `+` in the query decodes to a SPACE
      // server-side, so `user+tag@x` matched nothing and locked the user out.
      final client = MockClient((req) async {
        if (req.url.path == '/api/method/frappe.auth.get_logged_user') {
          return _json({'message': 'user+tag@example.com'});
        }
        expect(req.url.query.contains('user%2Btag%40example.com'), isTrue,
            reason: 'raw query must carry the percent-encoded email, got: ${req.url.query}');
        return _json({'message': [
          {'role': 'Driver'},
        ]});
      });
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: client);
      expect(await erp.fetchUserRoles(), ['Driver']);
    });
  });

  group('ErpClient.getList + submitDoc (Mốc 2)', () {
    test('getList encodes params and casts rows', () async {
      final calls = <Uri>[];
      final client = MockClient((req) async {
        calls.add(req.url);
        if (req.url.path == '/api/method/frappe.client.get_list') {
          return _json({'message': [
            {'name': 'CUST-001', 'customer_name': 'Vũ'},
          ]});
        }
        return http.Response('{}', 404);
      });
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: client);
      final rows = await erp.getList('Batch Debt',
          fields: ['name', 'outstanding_amount'],
          filters: {'status': ['in', ['Chưa trả']]},
          limit: 5);
      expect(rows.single['name'], 'CUST-001');
      expect(calls.single.queryParameters['doctype'], 'Batch Debt');
      expect(calls.single.queryParameters['limit_page_length'], '5');
      // Uri encodes JSON densely: `{"status":["in",...]}` → %7B%22status%22%3A%5B%22in%22
      expect(calls.single.query, contains('filters=%7B%22status%22%3A%5B%22in%22'));
    });

    test('getList rejects a non-list payload', () async {
      final client = MockClient((req) async => _json({'message': {'oops': 1}}));
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: client);
      await expectLater(erp.getList('Customer', fields: ['name']), throwsFormatException);
    });

    test('submitDoc PUTs docstatus 1 and succeeds on 200', () async {
      String? body;
      final client = MockClient((req) async {
        expect(req.method, 'PUT');
        body = req.body;
        return _json({'data': {'name': 'SO-001', 'docstatus': 1}});
      });
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: client);
      await erp.submitDoc('Sales Order', 'SO-001');
      expect(jsonDecode(body!), {'docstatus': 1});
    });

    test('submitDoc surfaces the server validation message (server-wins)', () async {
      final client = MockClient((req) async => _json({
            'success': false,
            '_server_messages': jsonEncode([
              'Hạn mức tín dụng không đủ cho đơn này',
            ]),
          }, status: 417));
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: client);
      await expectLater(
        erp.submitDoc('Sales Order', 'SO-001'),
        throwsA(isA<app.SubmitRejected>().having((e) => e.message, 'message',
            'Hạn mức tín dụng không đủ cho đơn này')),
      );
    });
  });

  group('OwnerDashboardScreen (Mốc 2 — real widget, mocked ERP)', () {
    Future<http.Response> Function(http.Request) erpBackend() => (req) async {
          // approve flow PUTs the submit — respond OK so the happy path lands.
          if (req.method == 'PUT' && req.url.path.endsWith('/SO-001')) {
            return _json({'data': {'name': 'SO-001', 'docstatus': 1}});
          }
          if (req.url.path == '/api/method/frappe.client.get_list') {
            final doctype = req.url.queryParameters['doctype'];
            if (doctype == 'Customer') {
              return _json({'message': [
                {'name': 'CUST-001', 'customer_name': 'Vũ nông trại'},
              ]});
            }
            if (doctype == 'Feed Batch') {
              return _json({'message': [
                {'name': 'LOT-2026-00001', 'customer': 'Vũ nông trại', 'total_debt': 1500000},
              ]});
            }
            if (doctype == 'Batch Debt') {
              return _json({'message': [
                {'name': 'BD-001', 'customer': 'Vũ nông trại', 'batch': 'LOT-2026-00001',
                 'allocated_amount': 1000000, 'paid_amount': 400000, 'returned_amount': 0,
                 'outstanding_amount': 600000, 'status': 'Một phần', 'due_date': '2026-10-01'},
              ]});
            }
            if (doctype == 'Sales Order') {
              return _json({'message': [
                {'name': 'SO-001', 'customer': 'Vũ nông trại', 'grand_total': 2000000,
                 'transaction_date': '2026-09-18'},
              ]});
            }
          }
          return http.Response('{}', 404);
        };

    testWidgets('renders stats + debts + draft SO', (tester) async {
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: MockClient(erpBackend()));
      await tester.pumpWidget(MaterialApp(home: OwnerDashboardScreen(erp: erp)));
      await tester.pumpAndSettle();

      expect(find.text('Khách'), findsOneWidget);
      expect(find.text('Lứa'), findsOneWidget);
      // 600,000đ appears in the receivables stat chip; the debt row renders it
      // inside a longer subtitle string (matched by textContaining below).
      expect(find.text('600,000đ'), findsOneWidget);
      expect(find.textContaining('còn 600,000đ (đã trả 400,000đ)'), findsOneWidget);
      expect(find.text('SO-001'), findsOneWidget);
      expect(find.text('Một phần'), findsOneWidget);
    });

    testWidgets('approve flow: guard, confirm dialog, PUT, then list refresh', (tester) async {
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: MockClient(erpBackend()));
      await tester.pumpWidget(MaterialApp(home: OwnerDashboardScreen(erp: erp)));
      await tester.pumpAndSettle();

      await tester.tap(find.widgetWithText(FilledButton, 'Duyệt').first);
      await tester.pumpAndSettle();
      expect(find.text('Duyệt đơn hàng?'), findsOneWidget); // confirmation shown
      await tester.tap(find.widgetWithText(FilledButton, 'Duyệt').last);
      await tester.pumpAndSettle();

      expect(find.text('Đã duyệt SO-001'), findsOneWidget); // success snackbar
    });

    testWidgets('OFFLINE approve is refused with a message (guard throws StateError, '
        'an Error — not an Exception — so this path needs its own clause)', (tester) async {
      var putSeen = false;
      final client = MockClient((req) async {
        if (req.method == 'PUT') {
          putSeen = true;
          return _json({'data': {'name': 'SO-001', 'docstatus': 1}});
        }
        return erpBackend()(req);
      });
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: client);
      await tester.pumpWidget(MaterialApp(home: OwnerDashboardScreen(erp: erp)));
      await tester.pumpAndSettle();

      app.isOnline.value = false; // simulate the connectivity toggle
      await tester.tap(find.widgetWithText(FilledButton, 'Duyệt').first);
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(FilledButton, 'Duyệt').last);
      await tester.pumpAndSettle();

      expect(find.textContaining('Đang offline — không thể duyệt'), findsOneWidget);
      expect(putSeen, isFalse, reason: 'no financial request may leave the device offline');
    });

    testWidgets('server rejection keeps the draft and shows the message', (tester) async {
      var submitted = false;
      final client = MockClient((req) async {
        if (req.method == 'PUT') {
          submitted = true;
          return _json({
            'success': false,
            '_server_messages': jsonEncode(['Hạn mức tín dụng không đủ cho đơn này']),
          }, status: 417);
        }
        return erpBackend()(req);
      });
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: client);
      await tester.pumpWidget(MaterialApp(home: OwnerDashboardScreen(erp: erp)));
      await tester.pumpAndSettle();

      await tester.tap(find.widgetWithText(FilledButton, 'Duyệt').first);
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(FilledButton, 'Duyệt').last);
      await tester.pumpAndSettle();

      expect(submitted, isTrue, reason: 'PUT actually left the app');
      expect(find.textContaining('Máy chủ từ chối'), findsOneWidget);
      expect(find.textContaining('Hạn mức tín dụng'), findsOneWidget);
    });
  });

  group('restoreSession (auto-login — Mốc 1.5)', () {
    // MUTABLE map, deliberately: the plugin's test platform writes into the very
    // map it is handed, so a `const {}` literal makes every write throw
    // "Cannot modify unmodifiable map" (measured on CI).
    setUp(() => FlutterSecureStorage.setMockInitialValues(<String, String>{}));

    test('first launch: no stored session → silent null', () async {
      final (erp, error) = await restoreSession(client: MockClient((req) async => _json({})));
      expect(erp, isNull);
      expect(error, isNull);
    });

    test('token session (api_key:api_secret) is restored via probe', () async {
      // Regression for the `token_pair`/`secret` key mismatch caught in review:
      // the reader used to look up a key no writer ever created.
      FlutterSecureStorage.setMockInitialValues(<String, String>{
        'base_url': 'https://x.example',
        'user': 'owner@example.com',
        'secret': 'K:S',
        'secret_is_token': '1',
        'auto_login': '1',
      });
      final client = MockClient((req) async {
        expect(req.url.path, '/api/method/frappe.auth.get_logged_user');
        expect(req.headers['Authorization'], 'token K:S');
        return _json({'message': 'owner@example.com'});
      });
      final (erp, error) = await restoreSession(client: client);
      expect(error, isNull);
      expect(erp, isNotNull);
      expect(erp!.tokenPair, 'K:S');
    });

    test('expired sid + stored password re-logins when auto_login is on', () async {
      FlutterSecureStorage.setMockInitialValues(<String, String>{
        'base_url': 'https://x.example',
        'user': 'owner@example.com',
        'secret': 'pw',
        'secret_is_token': '0',
        'auto_login': '1',
        'sid': 'dead-cookie',
      });
      final client = MockClient((req) async {
        if (req.url.path == '/api/method/frappe.auth.get_logged_user') {
          return http.Response('{}', 403); // sid + token probes dead
        }
        if (req.url.path == '/api/method/login') {
          return _json({'message': 'Logged In', 'full_name': 'P2 Owner'},
              headers: {'set-cookie': 'sid=fresh; Path=/'});
        }
        return http.Response('{}', 404);
      });
      final (erp, error) = await restoreSession(client: client);
      expect(error, isNull);
      expect(erp, isNotNull);
      expect(erp!.sid, 'fresh');
      // The refreshed sid was persisted for the next launch (this is the write
      // that used to blow up with an unmodifiable map in the test).
      expect(await SessionStore.read('sid'), 'fresh');
      expect(await SessionStore.read('sid'), isNot('dead-cookie'));
    });

    test('auto_login OFF: dead session → silent null (no secret re-login)', () async {
      FlutterSecureStorage.setMockInitialValues(<String, String>{
        'base_url': 'https://x.example',
        'user': 'owner@example.com',
        'secret': 'pw',
        'secret_is_token': '0',
        'auto_login': '0',
      });
      final client = MockClient((req) async => http.Response('{}', 403));
      final (erp, error) = await restoreSession(client: client);
      expect(erp, isNull);
      expect(error, isNull); // silent — the toggle was off
    });
  });

  group('HomeScreen bootstrap (REAL widget + mocked ERP — VIỆC 2)', () {
    testWidgets('drawer: server-granted roles enabled, others disabled; '
        'saved role stays when the server still grants it', (tester) async {
      SharedPreferences.setMockInitialValues({'role': 'Driver'});

      final erp = app.ErpClient(baseUrl: 'https://x.example', client: mockErp());
      await tester.pumpWidget(MaterialApp(home: app.HomeScreen(erp: erp)));
      await tester.pumpAndSettle();

      // The saved pref 'Driver' is respected because the mocked server grants it.
      expect(find.text('Vai trò: Driver'), findsOneWidget);
      expect(find.text('Vai trò: Feed Dealer Manager'), findsNothing);

      await tester.tap(find.byTooltip('Open navigation menu'));
      await tester.pumpAndSettle();

      final managerTile =
          tester.widget<ListTile>(find.widgetWithText(ListTile, 'Feed Dealer Manager'));
      final driverTile = tester.widget<ListTile>(find.widgetWithText(ListTile, 'Driver'));
      final staffTile =
          tester.widget<ListTile>(find.widgetWithText(ListTile, 'Feed Dealer Staff'));
      expect(managerTile.enabled, isTrue, reason: 'server granted Manager');
      expect(driverTile.enabled, isTrue, reason: 'server granted Driver');
      expect(staffTile.enabled, isFalse, reason: 'server did NOT grant Staff');
    });

    testWidgets('saved role NOT granted by server is dropped (server-wins)',
        (tester) async {
      SharedPreferences.setMockInitialValues({'role': 'Driver'});

      // Grants ONLY Manager — the saved Driver pref must be dropped.
      final client = MockClient((req) async {
        if (req.url.path == '/api/method/frappe.auth.get_logged_user') {
          return _json({'message': 'owner@example.com'});
        }
        if (req.url.path == '/api/method/frappe.client.get_list') {
          return _json({'message': [
            {'role': 'Feed Dealer Manager'},
          ]});
        }
        if (req.url.path.endsWith('/api/resource/Feed%20Batch')) {
          return _json({'data': []});
        }
        return http.Response('{"exc": ["nope"]}', 404);
      });
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: client);
      await tester.pumpWidget(MaterialApp(home: app.HomeScreen(erp: erp)));
      await tester.pumpAndSettle();

      expect(find.text('Vai trò: Feed Dealer Manager'), findsOneWidget);
      expect(find.text('Vai trò: Driver'), findsNothing);
    });

    testWidgets('bootstrap failure surfaces as a status message, not a crash',
        (tester) async {
      SharedPreferences.setMockInitialValues(const {});
      final client = MockClient((req) async =>
          http.Response('{"exc": ["boom"]}', 500)); // every call fails
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: client);
      await tester.pumpWidget(MaterialApp(home: app.HomeScreen(erp: erp)));
      await tester.pumpAndSettle();

      expect(find.textContaining('Lỗi tải vai trò'), findsOneWidget);
      // And the app-level default role is still the read-only Staff default.
      expect(find.text('Vai trò: Feed Dealer Staff'), findsOneWidget);
    });
  });
}
