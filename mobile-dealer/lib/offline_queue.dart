import 'dart:convert';

import 'package:flutter/foundation.dart' show immutable;
import 'package:shared_preferences/shared_preferences.dart';

import 'core.dart';

/// Mốc 4 — the offline mutation queue.
///
/// Hard rules from `.plan/phases/phase_02_internal_mobile.md` §19/§20, enforced
/// here rather than described in a comment:
///
/// * **Financial mutations are NEVER queued.** Only operations in
///   [kQueueableOperations] can enter this queue; payment, credit-limit changes
///   and SI submits are not in it and the guard for them is
///   [FinancialAction.guard] (they throw while offline).
/// * **Every queued row carries an idempotency key**, generated once when the
///   row is created and replayed verbatim — that is what makes a retry
///   harmless ("offline confirm twice → one server record").
/// * **Server wins.** A row the server REFUSED is never retried automatically;
///   it lands in the conflict list with the server's own message, and only the
///   user can act on it (fix and re-file, or discard).
///
/// Storage is SharedPreferences JSON: one row = one JSON object, readable in a
/// prefs dump. A real database (sqflite/drift) is deliberately NOT added here —
/// one queueable operation does not justify a migration story, and the payload
/// cap below keeps the prefs file small.
const String kQueuePrefKey = 'offline_queue_v1';

/// Row is still owed to the server (retryable, nothing was decided).
const String kQueuedPending = 'pending';

/// The server answered and said no. Terminal until a human acts.
const String kQueuedConflict = 'conflict';

/// The only operation a driver may file while offline (§18). Adding a second
/// one means adding it here AND to the flush dispatcher — on purpose, so a new
/// operation cannot silently become queueable.
const String kOpDeliveryConfirm = 'delivery_confirm';
const Set<String> kQueueableOperations = {kOpDeliveryConfirm};

/// Rows kept on the device. Far above a day's deliveries; a cap exists so a
/// device left offline for weeks cannot grow without bound.
const int kMaxQueuedRows = 50;

/// Refuse to store more than this much JSON per row. Photo evidence is base64
/// (≈1.33× the bytes); beyond ~3 MB the device storage, not the network, is the
/// problem. The user gets a clear message instead of a stalled sync.
const int kMaxQueuedPayloadChars = 3_000_000;

int _queueSeq = 0;

String _newQueueId() {
  _queueSeq += 1;
  return 'q-${DateTime.now().toUtc().microsecondsSinceEpoch}-$_queueSeq';
}

/// The queue refused the row LOCALLY (its own limits / duplicates). Distinct
/// from [ApiRejected]: nothing has been sent to the server yet.
class QueueRejected implements Exception {
  const QueueRejected(this.message);
  final String message;

  @override
  String toString() => message;
}

/// One queued mutation. Field names match phase_02_internal_mobile.md §17
/// exactly, so a row read from the prefs dump is readable by a human.
@immutable
class QueuedMutation {
  const QueuedMutation({
    required this.id,
    required this.operation,
    required this.entity,
    required this.entityId,
    required this.payload,
    required this.createdAt,
    required this.idempotencyKey,
    this.retryCount = 0,
    this.status = kQueuedPending,
    this.lastError,
  });

  final String id;
  final String operation;
  final String entity;
  final String entityId;
  final Map<String, dynamic> payload;
  final DateTime createdAt;
  final int retryCount;
  final String status;
  final String idempotencyKey;
  final String? lastError;

  bool get isPending => status == kQueuedPending;
  bool get isConflict => status == kQueuedConflict;

  QueuedMutation copyWith({int? retryCount, String? status, String? lastError}) => QueuedMutation(
        id: id,
        operation: operation,
        entity: entity,
        entityId: entityId,
        payload: payload,
        createdAt: createdAt,
        idempotencyKey: idempotencyKey,
        retryCount: retryCount ?? this.retryCount,
        status: status ?? this.status,
        lastError: lastError ?? this.lastError,
      );

  Map<String, dynamic> toJson() => {
        'id': id,
        'operation': operation,
        'entity': entity,
        'entity_id': entityId,
        'payload': payload,
        'created_at': createdAt.toUtc().toIso8601String(),
        'retry_count': retryCount,
        'status': status,
        'idempotency_key': idempotencyKey,
        'last_error': lastError,
      };

  factory QueuedMutation.fromJson(Map<String, dynamic> json) => QueuedMutation(
        id: json['id'] as String,
        operation: json['operation'] as String,
        entity: json['entity'] as String? ?? '',
        entityId: json['entity_id'] as String? ?? '',
        payload: (json['payload'] as Map).cast<String, dynamic>(),
        createdAt: DateTime.parse(json['created_at'] as String),
        retryCount: (json['retry_count'] as num? ?? 0).toInt(),
        status: json['status'] as String? ?? kQueuedPending,
        idempotencyKey: json['idempotency_key'] as String,
        lastError: json['last_error'] as String?,
      );
}

/// What one [OfflineQueue.flush] pass did. Returned (not just logged) so the UI
/// can report it honestly instead of saying "đã đồng bộ" on a failed pass.
@immutable
class FlushReport {
  const FlushReport({
    this.sent = 0,
    this.conflicts = 0,
    this.offline = false,
    this.authExpired = false,
    this.attempted = 0,
  });

  final int sent;
  final int conflicts;

  /// The pass stopped because a request never reached the server.
  final bool offline;

  /// The pass stopped because the session is gone — the user must log in again.
  final bool authExpired;

  final int attempted;

  bool get nothingToSend => attempted == 0;
}

/// How the queue hands a stored payload to the server. Injected so tests drive
/// the whole queue with a MockClient and no network.
typedef SendDeliveryPayload = Future<DeliveryResult> Function(Map<String, dynamic> payload);

class OfflineQueue {
  OfflineQueue({required SharedPreferences prefs, required SendDeliveryPayload send})
      : _prefs = prefs,
        _send = send;

  final SharedPreferences _prefs;
  final SendDeliveryPayload _send;
  final List<QueuedMutation> _rows = [];
  bool _loaded = false;

  /// Set when a pass stopped on an expired session: the UI must send the user
  /// back to the login screen instead of retrying forever.
  bool needsLogin = false;

  List<QueuedMutation> get rows => List.unmodifiable(_rows);
  List<QueuedMutation> get pendingRows => _rows.where((r) => r.isPending).toList();
  List<QueuedMutation> get conflictRows => _rows.where((r) => r.isConflict).toList();
  int get length => _rows.length;

  /// Reads the queue from disk. Safe to call twice.
  Future<void> load() async {
    if (_loaded) return;
    _loaded = true;
    final raw = _prefs.getString(kQueuePrefKey);
    if (raw == null || raw.isEmpty) return;
    try {
      final decoded = jsonDecode(raw);
      if (decoded is! List) return;
      _rows
        ..clear()
        ..addAll(decoded.whereType<Map>().map((row) => QueuedMutation.fromJson(row.cast<String, dynamic>())));
    } on FormatException {
      // Corrupt queue: drop it rather than crash the app on launch. Rows are
      // re-filable by hand; a crash loop on startup is not.
      _rows.clear();
    }
  }

  /// Queues one delivery confirmation. The idempotency key comes from the SHEET
  /// (it was created when the sheet opened), so the queued row and the live
  /// attempt share it.
  Future<QueuedMutation> enqueueDelivery({
    required String salesOrder,
    required String idempotencyKey,
    required Map<String, dynamic> payload,
  }) async {
    await load();
    return enqueue(
      QueuedMutation(
        id: _newQueueId(),
        operation: kOpDeliveryConfirm,
        entity: 'Sales Order',
        entityId: salesOrder,
        payload: payload,
        createdAt: DateTime.now().toUtc(),
        idempotencyKey: idempotencyKey,
      ),
    );
  }

  /// The one gate every queued row passes. Rejects anything that is not a
  /// queueable operation, anything already queued for the same entity, and
  /// anything too large for the device to hold.
  Future<QueuedMutation> enqueue(QueuedMutation row) async {
    await load();
    if (!kQueueableOperations.contains(row.operation)) {
      throw QueueRejected(
        'Thao tác ${row.operation} không được phép lưu ngoại tuyến '
        '(chỉ giao hàng mới xếp hàng đợi).',
      );
    }
    final duplicate = _rows.any((r) =>
        r.operation == row.operation && r.entityId == row.entityId && r.status == kQueuedPending);
    if (duplicate) {
      throw const QueueRejected('Đơn này đã nằm trong hàng đợi chờ gửi.');
    }
    if (row.status == kQueuedPending && _rows.where((r) => r.isPending).length >= kMaxQueuedRows) {
      throw const QueueRejected('Hàng đợi đã đầy — cần có mạng để gửi bớt trước khi thêm.');
    }
    if (jsonEncode(row.payload).length > kMaxQueuedPayloadChars) {
      throw const QueueRejected(
        'Bằng chứng quá lớn để lưu ngoại tuyến — chụp lại ảnh nhỏ hơn khi có mạng.',
      );
    }
    _rows.add(row);
    await _save();
    return row;
  }

  /// Drops a row (conflict the user gave up on, or a stuck pending one).
  Future<void> discard(String id) async {
    _rows.removeWhere((r) => r.id == id);
    await _save();
  }

  /// Sends every pending row, oldest first.
  ///
  /// Outcome per row:
  ///   * delivered        → row removed (the server has it; the key makes a
  ///                        duplicate impossible)
  ///   * [OfflineFailure] → row stays pending, retry_count++, pass STOPS (no
  ///                        point walking the rest of a dead link)
  ///   * [AuthExpired]    → row stays pending, [needsLogin] set, pass stops
  ///   * [ApiRejected]    → row becomes a conflict with the server's message
  ///                        and is never retried on its own (server wins)
  Future<FlushReport> flush() async {
    await load();
    needsLogin = false;
    var sent = 0;
    var conflicts = 0;
    var attempted = 0;

    final ordered = [..._rows.where((r) => r.isPending)]
      ..sort((a, b) => a.createdAt.compareTo(b.createdAt));

    for (final row in ordered) {
      attempted += 1;
      try {
        await _send(row.payload);
        _rows.removeWhere((r) => r.id == row.id);
        sent += 1;
        await _save();
      } on OfflineFailure {
        _replace(row.copyWith(
          retryCount: row.retryCount + 1,
          lastError: 'Chưa gửi được — mất kết nối',
        ));
        await _save();
        return FlushReport(sent: sent, conflicts: conflicts, offline: true, attempted: attempted);
      } on AuthExpired catch (error) {
        needsLogin = true;
        _replace(row.copyWith(retryCount: row.retryCount + 1, lastError: '$error'));
        await _save();
        return FlushReport(
          sent: sent,
          conflicts: conflicts,
          authExpired: true,
          attempted: attempted,
        );
      } on ApiRejected catch (error) {
        // Terminal: the server made a decision about this payload. Keeping the
        // row (instead of deleting it) is the whole point of the conflict log.
        _replace(row.copyWith(
          retryCount: row.retryCount + 1,
          status: kQueuedConflict,
          lastError: '$error',
        ));
        conflicts += 1;
        await _save();
      } on Exception catch (error) {
        // Unknown failure (a bug, a malformed row). Do NOT silently retry
        // forever: surface it as a conflict the user can see and discard.
        _replace(row.copyWith(
          retryCount: row.retryCount + 1,
          status: kQueuedConflict,
          lastError: '$error',
        ));
        conflicts += 1;
        await _save();
      }
    }
    return FlushReport(sent: sent, conflicts: conflicts, attempted: attempted);
  }

  void _replace(QueuedMutation row) {
    final i = _rows.indexWhere((r) => r.id == row.id);
    if (i >= 0) _rows[i] = row;
  }

  Future<void> _save() async {
    await _prefs.setString(
      kQueuePrefKey,
      jsonEncode(_rows.map((r) => r.toJson()).toList()),
    );
  }
}
