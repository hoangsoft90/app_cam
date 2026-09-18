import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:mobile_dealer/driver_delivery.dart';
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

  group('DriverDeliveryScreen (Mốc 3 — real widget, mocked ERP, fake camera/GPS)', () {
    // 1x1 PNG — enough that the sheet really holds image payload, and small
    // enough that the client-side size guard passes.
    final tinyPng = base64Encode(<int>[
      0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D,
      0x49, 0x48, 0x44, 0x52, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
      0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4, 0x89, 0x00, 0x00, 0x00,
      0x0A, 0x49, 0x44, 0x41, 0x54, 0x78, 0x9C, 0x63, 0x00, 0x01, 0x00, 0x00,
      0x05, 0x00, 0x01, 0x0D, 0x0A, 0x2D, 0xB4, 0x00, 0x00, 0x00, 0x00, 0x49,
      0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82,
    ]);

    final orderRow = <String, dynamic>{
      'name': 'SAL-ORD-2026-00300',
      'customer': 'CUST-1',
      'customer_name': 'Trại Cám Vũ',
      'grand_total': 1500000,
      'delivery_date': '2026-09-19',
      'confirmation': null,
      'confirmation_status': null,
      'pending_owner_approval': 0,
    };

    /// Mocked ERP for the driver door; [sent] records every confirm_delivery
    /// body so tests can prove what actually left the device.
    MockClient driverErp(
      List<Map<String, dynamic>> sent, {
      Map<String, dynamic>? reply,
      int replyStatus = 200,
      String failureBody = '{"_server_messages": "[\\"Đơn hàng chưa được duyệt\\"]"}',
      bool failAlways = false,
      List<Map<String, dynamic>>? rows,
    }) {
      return MockClient((req) async {
        if (req.url.path == '/api/method/feed_dealer.api.driver_deliveries') {
          return _json({'message': rows ?? [orderRow]});
        }
        if (req.url.path == '/api/method/feed_dealer.api.confirm_delivery') {
          sent.add({...req.bodyFields});
          if (failAlways) {
            // utf-8 content-type matters even for failures: without it http
            // decodes the body as latin-1 and the Vietnamese message becomes
            // mojibake (same trap as the Mốc 1 test helper).
            return _json(jsonDecode(failureBody) as Map<String, dynamic>, status: 417);
          }
          final method = (jsonDecode(req.bodyFields['payload']!)
              as Map<String, dynamic>)['confirmation_method'];
          // Behave like the real server: only OTP is final, everything else is
          // provisional until the owner approves.
          final provisional = method != 'OTP';
          return _json({
            'message': reply ??
                {
                  'name': 'DEL-2026-00001',
                  'status': provisional ? 'Giao thành công tạm' : 'Giao thành công',
                  'pending_owner_approval': provisional ? 1 : 0,
                  'delivery_note': provisional ? null : 'MAT-DN-2026-00042',
                  'idempotent': false,
                }
          }, status: replyStatus);
        }
        return http.Response('{"exc": ["nope"]}', 404);
      });
    }

    Map<String, dynamic> payloadOf(Map<String, dynamic> sent) =>
        jsonDecode(sent['payload'] as String) as Map<String, dynamic>;

    Future<void> openSheet(WidgetTester tester, app.ErpClient erp, {DeliveryDeps? deps}) async {
      await tester.pumpWidget(MaterialApp(
        home: app.DriverDeliveryScreen(
          erp: erp,
          deps: deps ?? const DeliveryDeps(pickPhoto: null, readGps: null),
        ),
      ));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Xác nhận giao'));
      await tester.pumpAndSettle();
    }

    Future<void> tapSubmit(WidgetTester tester) async {
      await tester.ensureVisible(find.text('Gửi xác nhận'));
      await tester.tap(find.text('Gửi xác nhận'));
      await tester.pumpAndSettle();
    }

    testWidgets('OTP branch: sends the OTP and reports the Delivery Note the server filed',
        (tester) async {
      final sent = <Map<String, dynamic>>[];
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: driverErp(sent));
      await openSheet(tester, erp);

      // OTP is the default branch; without a code the button must stay disabled.
      final button = tester.widget<FilledButton>(
          find.widgetWithText(FilledButton, 'Gửi xác nhận'));
      expect(button.onPressed, isNull, reason: 'no OTP typed yet');

      await tester.enterText(find.byType(TextField), '123456');
      await tester.pumpAndSettle();
      await tapSubmit(tester);

      expect(sent.length, 1, reason: 'exactly one request left the device');
      final payload = payloadOf(sent.single);
      expect(payload['confirmation_method'], 'OTP');
      expect(payload['otp_code'], '123456');
      expect(payload['sales_order'], 'SAL-ORD-2026-00300');
      expect((payload['idempotency_key'] as String).isNotEmpty, isTrue);

      // The driver is told the stock document exists (OTP is final).
      expect(find.text('Đã giao'), findsOneWidget);
      expect(find.textContaining('MAT-DN-2026-00042'), findsOneWidget);
    });

    testWidgets('Photo Only: blocked without a reason, then provisional with reason+photo+GPS',
        (tester) async {
      final sent = <Map<String, dynamic>>[];
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: driverErp(sent));
      await openSheet(
        tester,
        erp,
        deps: DeliveryDeps(
          pickPhoto: () async => tinyPng,
          readGps: () async => (lat: 10.12345, lng: 106.54321),
        ),
      );

      await tester.tap(find.text('Ảnh (chờ chủ duyệt)'));
      await tester.pumpAndSettle();

      // GPS is fetched on choosing this branch (the server requires a fix here).
      expect(find.textContaining('Toạ độ: 10.12345'), findsOneWidget);

      await tester.enterText(find.byType(TextField).first, 'Khách không nghe máy');
      await tester.pumpAndSettle();
      // Reason alone is not enough: the server also wants a photo.
      final button = tester.widget<FilledButton>(
          find.widgetWithText(FilledButton, 'Gửi xác nhận'));
      expect(button.onPressed, isNull, reason: 'no photo yet');

      await tester.ensureVisible(find.textContaining('Chụp ảnh'));
      await tester.tap(find.textContaining('Chụp ảnh'));
      await tester.pumpAndSettle();
      expect(find.text('Đã có 1 ảnh bằng chứng'), findsOneWidget);
      await tapSubmit(tester);

      final payload = payloadOf(sent.single);
      expect(payload['confirmation_method'], 'Photo Only — Needs Approval');
      expect(payload['no_otp_reason'], 'Khách không nghe máy');
      expect((payload['photos'] as List).length, 1);
      expect(payload['gps_latitude'], 10.12345);

      // Provisional: stock does NOT leave the warehouse yet — said plainly.
      expect(find.text('Đã gửi, chờ duyệt'), findsOneWidget);
    });

    testWidgets('server refusal is shown verbatim and the retry reuses the SAME idempotency key',
        (tester) async {
      final sent = <Map<String, dynamic>>[];
      final erp = app.ErpClient(
          baseUrl: 'https://x.example', client: driverErp(sent, failAlways: true));
      await openSheet(tester, erp);

      await tester.enterText(find.byType(TextField), '999999');
      await tester.pumpAndSettle();
      await tapSubmit(tester);

      expect(find.textContaining('Đơn hàng chưa được duyệt'), findsOneWidget,
          reason: 'the server message is what the driver must see');

      // Owner policy: a dropped link must not file two deliveries — retry keeps
      // the key generated when the sheet opened.
      await tapSubmit(tester);
      expect(sent.length, 2);
      expect(payloadOf(sent[1])['idempotency_key'], payloadOf(sent[0])['idempotency_key']);
    });

    testWidgets('offline WITHOUT a queue wired: refuses honestly, nothing is sent',
        (tester) async {
      // A screen that never got a queue must not silently drop the driver's
      // work — but it must not pretend to queue it either.
      final sent = <Map<String, dynamic>>[];
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: driverErp(sent));
      await openSheet(tester, erp);

      await tester.enterText(find.byType(TextField), '123456');
      await tester.pumpAndSettle();
      app.isOnline.value = false; // a dead link, as the connectivity tracker sees it
      await tapSubmit(tester);

      expect(find.textContaining('chưa bật được hàng đợi ngoại tuyến'), findsOneWidget);
      expect(sent, isEmpty);
      app.isOnline.value = true;
    });

    testWidgets('a rejected delivery appears as re-fileable, a live one as done',
        (tester) async {
      final rows = <Map<String, dynamic>>[
        {
          'name': 'SAL-ORD-2026-00301',
          'customer_name': 'Trại A',
          'grand_total': 500000,
          'delivery_date': '2026-09-19',
          'confirmation': 'DEL-1',
          'confirmation_status': 'Bị từ chối',
          'confirmation_reject_reason': 'ảnh mờ',
          'pending_owner_approval': 0,
        },
        {
          'name': 'SAL-ORD-2026-00302',
          'customer_name': 'Trại B',
          'grand_total': 700000,
          'delivery_date': '2026-09-19',
          'confirmation': 'DEL-2',
          'confirmation_status': 'Giao thành công',
          'confirmation_delivery_note': 'MAT-DN-2026-00077',
          'pending_owner_approval': 0,
        },
        {
          'name': 'SAL-ORD-2026-00303',
          'customer_name': 'Trại C',
          'grand_total': 900000,
          'delivery_date': '2026-09-19',
          'confirmation': 'DEL-3',
          'confirmation_status': 'Giao thành công tạm',
          'pending_owner_approval': 1,
        },
      ];
      final erp = app.ErpClient(
          baseUrl: 'https://x.example', client: driverErp(<Map<String, dynamic>>[], rows: rows));
      await tester.pumpWidget(MaterialApp(home: app.DriverDeliveryScreen(erp: erp)));
      await tester.pumpAndSettle();

      expect(find.text('Xác nhận lại'), findsOneWidget, reason: 'only the rejected one');
      expect(find.textContaining('Lý do từ chối: ảnh mờ'), findsOneWidget);
      expect(find.textContaining('phiếu xuất kho MAT-DN-2026-00077'), findsOneWidget);
      // The provisional one says exactly what is still missing: the owner.
      expect(find.text('Trạng thái: Chờ chủ đại lý duyệt'), findsOneWidget);
      // And it is NOT offered as an action (only a rejected delivery can be re-filed).
      expect(find.text('Xác nhận giao'), findsNothing);
    });

    testWidgets('Mốc 4: offline filing is QUEUED with the sheet key, nothing is sent',
        (tester) async {
      final sent = <Map<String, dynamic>>[];
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: driverErp(sent));
      final queue = app.OfflineQueue(
        prefs: await _emptyPrefs(),
        send: erp.confirmDeliveryRaw, // the real wire path, mocked underneath
      );
      await tester.pumpWidget(MaterialApp(
        home: app.DriverDeliveryScreen(erp: erp, queue: queue),
      ));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Xác nhận giao'));
      await tester.pumpAndSettle();

      await tester.enterText(find.byType(TextField), '123456');
      await tester.pumpAndSettle();
      app.isOnline.value = false;
      // The connectivity flag drives a ValueListenableBuilder, so the subtree
      // needs a frame before its label reflects the new state (measured: the
      // assertion below failed on CI without this pump).
      await tester.pump();
      // The button SAYS what will happen (a queued row, not a sent one).
      expect(find.text('Lưu chờ gửi'), findsOneWidget);
      expect(find.textContaining('xác nhận sẽ được lưu trên máy'), findsOneWidget);
      await tester.tap(find.text('Lưu chờ gửi'));
      await tester.pumpAndSettle();

      expect(find.text('Đã lưu, chờ có mạng'), findsOneWidget);
      expect(sent, isEmpty, reason: 'offline: no request may leave the device');
      expect(queue.pendingRows.single.entityId, 'SAL-ORD-2026-00300');
      expect(queue.pendingRows.single.idempotencyKey, isNotEmpty);

      await tester.tap(find.text('Đóng'));
      await tester.pumpAndSettle();
      // Back on the list the driver sees what is still owed to the server.
      expect(find.textContaining('Chờ gửi: 1'), findsOneWidget);
      app.isOnline.value = true;
    });

    testWidgets('Mốc 4: a link that dies MID-FLIGHT is queued, not lost', (tester) async {
      // isOnline is still true when the driver taps (the last request worked),
      // and the connection drops while the payload is on the wire. The exact
      // case a driver hits at the edge of the farm's wifi.
      final client = MockClient((req) async {
        if (req.url.path == '/api/method/feed_dealer.api.driver_deliveries') {
          return _json({'message': [orderRow]});
        }
        if (req.url.path == '/api/method/feed_dealer.api.confirm_delivery') {
          throw http.ClientException('connection closed before full header was received');
        }
        return http.Response('{"exc": ["nope"]}', 404);
      });
      final erp = app.ErpClient(baseUrl: 'https://x.example', client: client);
      final queue = app.OfflineQueue(prefs: await _emptyPrefs(), send: erp.confirmDeliveryRaw);
      await tester.pumpWidget(MaterialApp(home: app.DriverDeliveryScreen(erp: erp, queue: queue)));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Xác nhận giao'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), '123456');
      await tester.pumpAndSettle();
      await tester.tap(find.text('Gửi xác nhận'));
      await tester.pumpAndSettle();

      expect(find.text('Đã lưu, chờ có mạng'), findsOneWidget);
      expect(queue.pendingRows.length, 1);
    });
  });

  group('Mốc 4 — offline queue rules', () {
    final payload = <String, dynamic>{
      'sales_order': 'SAL-ORD-2026-00300',
      'confirmation_method': 'OTP',
      'idempotency_key': 'k-1',
    };

    test('a money operation can NOT enter the queue (hard rule #2)', () async {
      final queue = app.OfflineQueue(
        prefs: await _emptyPrefs(),
        send: (_) async => throw StateError('the queue must never send this'),
      );
      await expectLater(
        queue.enqueue(app.QueuedMutation(
          id: 'x1',
          operation: 'payment_entry',
          entity: 'Payment Entry',
          entityId: 'PE-0001',
          payload: const {},
          createdAt: DateTime.now(),
          idempotencyKey: 'k-money',
        )),
        throwsA(isA<app.QueueRejected>()),
      );
      expect(queue.length, 0, reason: 'nothing financial is ever stored offline');
    });

    test('the same order is not queued twice, and an oversized payload is refused', () async {
      final queue = app.OfflineQueue(
        prefs: await _emptyPrefs(),
        send: (_) async => throw const app.OfflineFailure(),
      );
      await queue.enqueueDelivery(
        salesOrder: 'SAL-ORD-2026-00300',
        idempotencyKey: 'k-1',
        payload: payload,
      );
      await expectLater(
        queue.enqueueDelivery(
          salesOrder: 'SAL-ORD-2026-00300',
          idempotencyKey: 'k-1b',
          payload: payload,
        ),
        throwsA(isA<app.QueueRejected>()),
      );
      final huge = <String, dynamic>{...payload, 'photos': [List.filled(3000001, 'x').join()]};
      await expectLater(
        queue.enqueueDelivery(
          salesOrder: 'SAL-ORD-2026-00999',
          idempotencyKey: 'k-2',
          payload: huge,
        ),
        throwsA(isA<app.QueueRejected>()),
      );
      expect(queue.length, 1);
    });

    test('flush sends the STORED idempotency key and clears the row', () async {
      final sent = <Map<String, dynamic>>[];
      final queue = app.OfflineQueue(
        prefs: await _emptyPrefs(),
        send: (p) async {
          sent.add(p);
          return const app.DeliveryResult(
            name: 'DEL-1',
            status: 'Giao thành công',
            pendingOwnerApproval: false,
          );
        },
      );
      await queue.enqueueDelivery(
        salesOrder: 'SAL-ORD-2026-00300',
        idempotencyKey: 'k-1',
        payload: payload,
      );
      final report = await queue.flush();

      expect(report.sent, 1);
      expect(sent.single['idempotency_key'], 'k-1');
      expect(queue.length, 0, reason: 'the server has it; the key prevents a double');
    });

    test('a dead link keeps rows pending, bumps retry_count and stops the pass', () async {
      var calls = 0;
      final queue = app.OfflineQueue(
        prefs: await _emptyPrefs(),
        send: (_) async {
          calls += 1;
          throw const app.OfflineFailure();
        },
      );
      await queue.enqueueDelivery(
        salesOrder: 'SAL-ORD-2026-00300',
        idempotencyKey: 'k-1',
        payload: payload,
      );
      await queue.enqueueDelivery(
        salesOrder: 'SAL-ORD-2026-00301',
        idempotencyKey: 'k-2',
        payload: {...payload, 'sales_order': 'SAL-ORD-2026-00301', 'idempotency_key': 'k-2'},
      );
      final report = await queue.flush();

      expect(report.offline, isTrue);
      expect(report.attempted, 1, reason: 'walking the rest of a dead link helps nobody');
      expect(calls, 1);
      expect(queue.pendingRows.length, 2, reason: 'no work is lost');
      expect(queue.pendingRows.first.retryCount, 1);
    });

    test('a server refusal becomes a CONFLICT with its message and is never auto-retried', () async {
      final queue = app.OfflineQueue(
        prefs: await _emptyPrefs(),
        send: (_) async => throw const app.ApiRejected('Đơn hàng đã có xác nhận giao hàng'),
      );
      await queue.enqueueDelivery(
        salesOrder: 'SAL-ORD-2026-00300',
        idempotencyKey: 'k-1',
        payload: payload,
      );
      final first = await queue.flush();
      expect(first.conflicts, 1);
      expect(queue.conflictRows.single.lastError, 'Đơn hàng đã có xác nhận giao hàng');

      final second = await queue.flush();
      expect(second.attempted, 0, reason: 'server-wins: that decision is final');
      expect(queue.pendingRows, isEmpty);
    });

    test('an expired session keeps the row and asks for a fresh login', () async {
      final queue = app.OfflineQueue(
        prefs: await _emptyPrefs(),
        send: (_) async => throw const app.AuthExpired(),
      );
      await queue.enqueueDelivery(
        salesOrder: 'SAL-ORD-2026-00300',
        idempotencyKey: 'k-1',
        payload: payload,
      );
      final report = await queue.flush();

      expect(report.authExpired, isTrue);
      expect(queue.needsLogin, isTrue);
      expect(queue.pendingRows.length, 1, reason: 'a 401 is not a refusal of the work');
    });

    test('rows survive an app restart (reloaded from prefs)', () async {
      final prefs = await _emptyPrefs();
      final first = app.OfflineQueue(prefs: prefs, send: (_) async => throw const app.OfflineFailure());
      await first.enqueueDelivery(
        salesOrder: 'SAL-ORD-2026-00300',
        idempotencyKey: 'k-1',
        payload: payload,
      );

      final second = app.OfflineQueue(prefs: prefs, send: (_) async => throw const app.OfflineFailure());
      await second.load();
      expect(second.pendingRows.single.entityId, 'SAL-ORD-2026-00300');
      expect(second.pendingRows.single.idempotencyKey, 'k-1');
    });

    testWidgets('the queue screen shows the server reason and can discard the row',
        (tester) async {
      final queue = app.OfflineQueue(
        prefs: await _emptyPrefs(),
        send: (_) async => throw const app.ApiRejected('Tồn kho không đủ'),
      );
      await queue.enqueueDelivery(
        salesOrder: 'SAL-ORD-2026-00300',
        idempotencyKey: 'k-1',
        payload: payload,
      );
      await queue.flush();

      await tester.pumpWidget(MaterialApp(home: app.QueueScreen(queue: queue)));
      await tester.pumpAndSettle();
      expect(find.textContaining('Máy chủ từ chối: Tồn kho không đủ'), findsOneWidget);
      expect(find.textContaining('hệ thống không tự gửi lại'), findsOneWidget);

      await tester.tap(find.text('Bỏ'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(FilledButton, 'Bỏ'));
      await tester.pumpAndSettle();
      expect(queue.length, 0);
    });
  });
}

/// A mutable prefs map: `SharedPreferences.setMockInitialValues(const {})` makes
/// every WRITE throw "Cannot modify an unmodifiable map" — measured on CI.
Future<SharedPreferences> _emptyPrefs() async {
  SharedPreferences.setMockInitialValues(<String, Object>{});
  return SharedPreferences.getInstance();
}
