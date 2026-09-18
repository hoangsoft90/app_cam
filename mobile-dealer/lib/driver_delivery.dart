import 'dart:convert';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:image_picker/image_picker.dart';

import 'core.dart';
import 'offline_queue.dart';

/// Mốc 3/4 — Driver flow: the orders to deliver, and the proof-of-delivery form.
///
/// Business rules the UI must show honestly (owner decision 2026-09-18):
///   * OTP          -> FINAL. The server submits the Delivery Note immediately,
///                     so the stock has already left the warehouse.
///   * Signature    -> PROVISIONAL ("giao thành công tạm"), same group as Photo
///                     Only: the owner approves before stock moves.
///   * Photo Only   -> PROVISIONAL, and it REQUIRES a reason, at least one photo
///                     and a GPS fix (addendum B9: the driver is never blocked
///                     for a missing OTP, but the delivery is not final).
///
/// Hard rules kept from the rest of the app:
///   * idempotency key generated ONCE per delivery attempt and reused on retry
///     (never per tap), so a dropped link cannot file two deliveries;
///   * server errors are shown verbatim and nothing is forced locally;
///   * Mốc 4: a delivery confirmation MAY be filed offline. It is not a
///     financial mutation (no money, no credit limit) and the server still
///     decides the outcome, so the payload goes into the offline queue with the
///     SAME idempotency key and is replayed when the link returns. A payload the
///     server REFUSES is never queued — that decision is already made.
const String kMethodOtp = 'OTP';
const String kMethodSignature = 'Signature';
/// Exact server string (em dash included) — a mismatch is rejected by the
/// controller, so it lives here as a constant instead of being typed inline.
const String kMethodPhotoOnly = 'Photo Only — Needs Approval';

const String kStatusRejected = 'Bị từ chối';
const String kStatusProvisional = 'Giao thành công tạm';
const String kStatusFinal = 'Giao thành công';

/// The platform pieces (camera, GPS) behind injectable functions: widget tests
/// exercise the whole flow with fakes and never touch a platform channel.
class DeliveryDeps {
  const DeliveryDeps({this.pickPhoto, this.readGps});

  /// Returns base64 (already compressed) or null when the user cancels.
  final Future<String?> Function()? pickPhoto;

  /// Returns the current fix, or null when unavailable/permission denied.
  final Future<({double lat, double lng})?> Function()? readGps;

  Future<String?> photo() async => (pickPhoto ?? _pickCompressedPhoto)();
  Future<({double lat, double lng})?> gps() async => (readGps ?? _readGpsFix)();
}

/// Client-side compression: the server refuses anything over 1.5 MB decoded, and
/// a farm link should not carry an original 12 MP photo.
Future<String?> _pickCompressedPhoto() async {
  final file = await ImagePicker().pickImage(
    source: ImageSource.camera,
    maxWidth: 1280,
    maxHeight: 1280,
    imageQuality: 70,
  );
  if (file == null) return null;
  return base64Encode(await file.readAsBytes());
}

Future<({double lat, double lng})?> _readGpsFix() async {
  if (!await Geolocator.isLocationServiceEnabled()) return null;
  var permission = await Geolocator.checkPermission();
  if (permission == LocationPermission.denied) {
    permission = await Geolocator.requestPermission();
  }
  if (permission == LocationPermission.denied ||
      permission == LocationPermission.deniedForever) {
    return null;
  }
  final position = await Geolocator.getCurrentPosition();
  return (lat: position.latitude, lng: position.longitude);
}

String _statusLabel(String? status) {
  switch (status) {
    case kStatusFinal:
      return 'Đã giao';
    case kStatusProvisional:
      return 'Chờ chủ đại lý duyệt';
    case kStatusRejected:
      return 'Bị từ chối';
    default:
      return 'Chưa xác nhận';
  }
}

/// Orders to deliver (server decides the list and the state).
class DriverDeliveryScreen extends StatefulWidget {
  const DriverDeliveryScreen({
    super.key,
    required this.erp,
    this.deps = const DeliveryDeps(),
    this.queue,
    this.onOpenQueue,
  });

  final ErpClient erp;
  final DeliveryDeps deps;

  /// Mốc 4: rows filed while offline wait here. Null in tests that do not care
  /// about queuing — then a dead link just shows an error, as before.
  final OfflineQueue? queue;

  /// Opens the queue/conflict screen. Injected so this file does not have to
  /// know how the app navigates.
  final VoidCallback? onOpenQueue;

  @override
  State<DriverDeliveryScreen> createState() => _DriverDeliveryScreenState();
}

class _DriverDeliveryScreenState extends State<DriverDeliveryScreen> {
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _rows = const [];

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final rows = await widget.erp.driverDeliveries();
      if (!mounted) return;
      setState(() {
        _rows = rows;
        _loading = false;
      });
    } on Exception catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _loading = false;
      });
    }
  }

  bool _needsAction(Map<String, dynamic> row) {
    final status = row['confirmation_status'] as String?;
    return status == null || status == kStatusRejected;
  }

  Future<void> _open(Map<String, dynamic> row) async {
    final changed = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (_) => ConfirmDeliverySheet(
        erp: widget.erp,
        order: row,
        deps: widget.deps,
        queue: widget.queue,
      ),
    );
    if (changed == true) {
      if (mounted) setState(() {}); // the queue banner may have changed
      await _refresh();
    }
  }

  /// Mốc 4 — what is still owed to the server, always visible to the driver.
  /// Blank when there is nothing waiting, so the screen never carries a
  /// permanent "0 items" bar.
  Widget? _queueBanner() {
    final queue = widget.queue;
    if (queue == null || queue.length == 0) return null;
    final pending = queue.pendingRows.length;
    final conflicts = queue.conflictRows.length;
    final color = conflicts > 0
        ? Theme.of(context).colorScheme.errorContainer
        : Theme.of(context).colorScheme.secondaryContainer;
    return Card(
      color: color,
      child: ListTile(
        leading: Icon(conflicts > 0 ? Icons.report_problem : Icons.cloud_upload),
        title: Text('Chờ gửi: $pending · Cần xử lý: $conflicts'),
        subtitle: Text(conflicts > 0
            ? 'Máy chủ từ chối $conflicts mục — xem lý do và làm lại.'
            : 'Sẽ tự gửi khi có mạng trở lại.'),
        onTap: widget.onOpenQueue,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final todo = _rows.where(_needsAction).toList();
    final done = _rows.where((r) => !_needsAction(r)).toList();
    final banner = _queueBanner();
    return Scaffold(
      appBar: AppBar(title: const Text('Giao hàng')),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  if (_error != null) _ErrorCard(message: _error!),
                  if (banner != null) ...[
                    banner,
                    const SizedBox(height: 12),
                  ],
                  const Text('Ghi chú: OTP là xác nhận cuối cùng và xuất kho ngay. '
                      'Chữ ký / ảnh chỉ là "giao thành công tạm" — chủ đại lý duyệt mới xuất kho.'),
                  const SizedBox(height: 16),
                  Text('Cần giao (${todo.length})', style: Theme.of(context).textTheme.titleMedium),
                  if (todo.isEmpty) const Text('Không có đơn nào cần giao.'),
                  for (final row in todo) _orderCard(row, actionable: true),
                  const SizedBox(height: 16),
                  Text('Đã xác nhận (${done.length})', style: Theme.of(context).textTheme.titleMedium),
                  if (done.isEmpty) const Text('Chưa có đơn nào được xác nhận.'),
                  for (final row in done) _orderCard(row, actionable: false),
                ],
              ),
      ),
    );
  }

  Widget _orderCard(Map<String, dynamic> row, {required bool actionable}) {
    final name = row['name'] as String? ?? '';
    final customer = (row['customer_name'] ?? row['customer'] ?? '') as String;
    final total = (row['grand_total'] as num?) ?? 0;
    final status = row['confirmation_status'] as String?;
    final note = row['confirmation_delivery_note'] as String?;
    return Card(
      child: ListTile(
        title: Text('$name — $customer'),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('${vnd(total)} · giao ${row['delivery_date'] ?? ''}'),
            Text(
              'Trạng thái: ${_statusLabel(status)}'
              '${note != null && note.isNotEmpty ? ' — phiếu xuất kho $note' : ''}',
              style: TextStyle(color: status == kStatusRejected ? Colors.red : null),
            ),
            if ((row['confirmation_reject_reason'] as String?)?.isNotEmpty == true)
              Text('Lý do từ chối: ${row['confirmation_reject_reason']}',
                  style: const TextStyle(color: Colors.red)),
          ],
        ),
        isThreeLine: true,
        trailing: actionable
            ? FilledButton(
                onPressed: () => _open(row),
                child: Text(status == kStatusRejected ? 'Xác nhận lại' : 'Xác nhận giao'),
              )
            : const Icon(Icons.check_circle, color: Colors.green),
      ),
    );
  }
}

class _ErrorCard extends StatelessWidget {
  const _ErrorCard({required this.message});
  final String message;

  @override
  Widget build(BuildContext context) => Card(
        color: Theme.of(context).colorScheme.errorContainer,
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Text(message),
        ),
      );
}

/// The proof-of-delivery form. One [newIdempotencyKey] per open sheet, reused by
/// every retry inside it (that is the whole point of the key).
class ConfirmDeliverySheet extends StatefulWidget {
  const ConfirmDeliverySheet({
    super.key,
    required this.erp,
    required this.order,
    this.deps = const DeliveryDeps(),
    this.queue,
  });

  final ErpClient erp;
  final Map<String, dynamic> order;
  final DeliveryDeps deps;

  /// Mốc 4 — where a delivery filed without a link goes to wait.
  final OfflineQueue? queue;

  @override
  State<ConfirmDeliverySheet> createState() => _ConfirmDeliverySheetState();
}

class _ConfirmDeliverySheetState extends State<ConfirmDeliverySheet> {
  final _otp = TextEditingController();
  final _reason = TextEditingController();
  final _signatureKey = GlobalKey<SignaturePadState>();
  final String _idempotencyKey = newIdempotencyKey();

  String _method = kMethodOtp;
  String? _signaturePng;
  final List<String> _photos = [];
  ({double lat, double lng})? _gps;
  bool _locating = false;
  bool _submitting = false;
  String? _error;

  @override
  void dispose() {
    _otp.dispose();
    _reason.dispose();
    super.dispose();
  }

  Future<void> _pickPhoto() async {
    final photo = await widget.deps.photo();
    if (photo == null) return;
    // Stop BEFORE the wire: the server would reject >1.5 MB and a farm link
    // should not upload a payload that cannot pass.
    if (photo.length > kMaxPhotoBase64) {
      setState(() => _error = 'Ảnh quá lớn (${(photo.length / 1024 / 1024).toStringAsFixed(1)} MB). '
          'Chụp lại gần hơn hoặc giảm chất lượng ảnh.');
      return;
    }
    setState(() {
      _error = null;
      if (_photos.length >= kMaxPhotos) {
        _error = 'Tối đa $kMaxPhotos ảnh mỗi lần giao.';
        return;
      }
      _photos.add(photo);
    });
  }

  Future<void> _locate() async {
    setState(() {
      _locating = true;
      _error = null;
    });
    final fix = await widget.deps.gps();
    if (!mounted) return;
    setState(() {
      _locating = false;
      _gps = fix;
      if (fix == null) {
        _error = 'Chưa lấy được toạ độ GPS — bật định vị rồi thử lại '
            '(xác nhận bằng ảnh bắt buộc có toạ độ).';
      }
    });
  }

  bool get _valid {
    switch (_method) {
      case kMethodOtp:
        return _otp.text.trim().isNotEmpty;
      case kMethodSignature:
        return (_signaturePng ?? '').isNotEmpty;
      default:
        return _reason.text.trim().isNotEmpty && _photos.isNotEmpty && _gps != null;
    }
  }

  /// The payload for this sheet. Built ONCE per submit so the queued copy is
  /// exactly what a live attempt would have sent — same idempotency key.
  Map<String, dynamic> _payload() => buildDeliveryPayload(
        salesOrder: widget.order['name'] as String? ?? '',
        method: _method,
        idempotencyKey: _idempotencyKey,
        otpCode: _method == kMethodOtp ? _otp.text.trim() : null,
        noOtpReason: _method == kMethodPhotoOnly ? _reason.text.trim() : null,
        signaturePng: _method == kMethodSignature ? _signaturePng : null,
        photos: _method == kMethodPhotoOnly ? _photos : const [],
        gpsLatitude: _gps?.lat,
        gpsLongitude: _gps?.lng,
      );

  Future<void> _submit() async {
    final payload = _payload();
    setState(() {
      _submitting = true;
      _error = null;
    });
    // Offline is a KNOWN state here, so the doomed round trip is skipped: the
    // driver gets an answer now instead of after a connect timeout.
    if (!isOnline.value) {
      await _queueInstead(payload);
      return;
    }
    try {
      final result = await widget.erp.confirmDeliveryRaw(payload);
      if (!mounted) return;
      await showDialog<void>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: Text(result.isFinal ? 'Đã giao' : 'Đã gửi, chờ duyệt'),
          content: Text(result.isFinal
              ? 'Hệ thống đã tạo phiếu xuất kho ${result.deliveryNote ?? ''} và ghi nhận công nợ.'
              : 'Tài xế đã ghi bằng chứng. Chủ đại lý duyệt xong mới xuất kho — '
                  'trạng thái hiện tại: ${result.status}.'),
          actions: [
            FilledButton(onPressed: () => Navigator.pop(ctx), child: const Text('Đóng')),
          ],
        ),
      );
      if (!mounted) return;
      Navigator.pop(context, true); // tell the list to refresh
    } on OfflineFailure {
      // The link died mid-flight. Same payload, same key: queue it instead of
      // throwing the driver's work away.
      await _queueInstead(payload);
    } on Exception catch (e) {
      // The SERVER answered and refused — never queued. That decision is final
      // (server wins) and retrying it automatically would only repeat it.
      if (!mounted) return;
      setState(() {
        _submitting = false;
        // Same sheet, same key: the retry is safe by construction.
        _error = '$e';
      });
    }
  }

  /// Mốc 4 — the offline branch. Queued rows are replayed with THIS key, so the
  /// server recognises a replay instead of filing a second delivery.
  Future<void> _queueInstead(Map<String, dynamic> payload) async {
    final queue = widget.queue;
    if (queue == null) {
      // No queue wired on this screen: refuse honestly rather than pretend.
      if (mounted) {
        setState(() {
          _submitting = false;
          _error = 'Đang offline — chưa bật được hàng đợi ngoại tuyến cho màn này.';
        });
      }
      return;
    }
    try {
      await queue.enqueueDelivery(
        salesOrder: payload['sales_order'] as String? ?? '',
        idempotencyKey: _idempotencyKey,
        payload: payload,
      );
      if (!mounted) return;
      await showDialog<void>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('Đã lưu, chờ có mạng'),
          content: const Text('Bằng chứng giao hàng đã được lưu trên máy và sẽ tự gửi khi có '
              'mạng trở lại. Chưa có phiếu xuất kho nào được tạo cho tới lúc đó.'),
          actions: [FilledButton(onPressed: () => Navigator.pop(ctx), child: const Text('Đóng'))],
        ),
      );
      if (!mounted) return;
      Navigator.pop(context, true); // the list refreshes and shows the queue banner
    } on QueueRejected catch (e) {
      if (!mounted) return;
      setState(() {
        _submitting = false;
        _error = '$e';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final order = widget.order;
    return Padding(
      padding: EdgeInsets.only(
        left: 16,
        right: 16,
        top: 16,
        bottom: MediaQuery.of(context).viewInsets.bottom + 16,
      ),
      child: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text('Xác nhận giao hàng', style: Theme.of(context).textTheme.titleLarge),
            Text('${order['name']} — ${order['customer_name'] ?? order['customer'] ?? ''}'),
            const SizedBox(height: 12),
            _methodTile(kMethodOtp, 'OTP (xuất kho ngay)', 'Mã khách đọc — xác nhận cuối cùng'),
            _methodTile(kMethodSignature, 'Chữ ký (chờ chủ duyệt)',
                'Không xuất kho ngay: chủ đại lý duyệt trước'),
            _methodTile(kMethodPhotoOnly, 'Ảnh (chờ chủ duyệt)',
                'Bắt buộc lý do + ít nhất 1 ảnh + GPS'),
            const Divider(),
            if (_method == kMethodOtp) _otpField() else const SizedBox.shrink(),
            if (_method == kMethodSignature) _signatureField() else const SizedBox.shrink(),
            if (_method == kMethodPhotoOnly) ..._photoFields(),
            if (_error != null) ...[
              const SizedBox(height: 8),
              Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
            ],
            const SizedBox(height: 16),
            // The offline state is shown BEFORE the driver taps, and the button
            // says what will actually happen (a queued row, not a sent one) —
            // a driver who thinks he filed a delivery that never left the phone
            // is exactly the failure this milestone exists to prevent.
            ValueListenableBuilder<bool>(
              valueListenable: isOnline,
              builder: (_, online, _) => Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (!online)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 12),
                      child: Text(
                        'Đang offline — xác nhận sẽ được lưu trên máy và tự gửi khi có mạng.',
                        style: TextStyle(color: Theme.of(context).colorScheme.error),
                      ),
                    ),
                  Row(
                    children: [
                      Expanded(
                        child: OutlinedButton(
                          onPressed: _submitting ? null : () => Navigator.pop(context, false),
                          child: const Text('Huỷ'),
                        ),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: FilledButton(
                          onPressed: (_valid && !_submitting) ? _submit : null,
                          child: Text(_submitting
                              ? 'Đang gửi…'
                              : online
                                  ? 'Gửi xác nhận'
                                  : 'Lưu chờ gửi'),
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  /// Hand-rolled radio rows instead of `RadioListTile`: its `groupValue`/
  /// `onChanged` API is deprecated in current Flutter (analyzer warnings fail
  /// this project's CI), and this is 12 lines with no deprecation risk.
  Widget _methodTile(String value, String title, String subtitle) {
    final selected = _method == value;
    return ListTile(
      dense: true,
      leading: Icon(selected ? Icons.radio_button_checked : Icons.radio_button_unchecked),
      title: Text(title),
      subtitle: Text(subtitle),
      onTap: _submitting
          ? null
          : () => setState(() {
                _method = value;
                _error = null;
                // Photo Only is the only branch the server requires a GPS fix for,
                // so the fix is fetched when that branch is chosen.
                if (_method == kMethodPhotoOnly && _gps == null) _locate();
              }),
    );
  }

  Widget _otpField() => TextField(
        controller: _otp,
        keyboardType: TextInputType.number,
        decoration: const InputDecoration(
          labelText: 'Mã OTP khách đọc',
          helperText: 'Chưa có SMS (đang chờ nhà cung cấp): chủ đại lý đọc mã cho khách/tài xế',
        ),
        onChanged: (_) => setState(() {}),
      );

  Widget _signatureField() => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SignaturePad(
            key: _signatureKey,
            onChanged: (hasInk) => setState(() {}),
          ),
          const SizedBox(height: 8),
          // Wrap for the same reason as the photo row: two labelled buttons must not
          // overflow when the sheet is narrow (or the font is scaled up).
          Wrap(
            spacing: 12,
            runSpacing: 8,
            children: [
              OutlinedButton(
                onPressed: () => setState(() {
                  _signatureKey.currentState?.clear();
                  _signaturePng = null;
                }),
                child: const Text('Xoá'),
              ),
              FilledButton.tonal(
                onPressed: () async {
                  final png = await _signatureKey.currentState?.toPngBase64();
                  if (png == null) return;
                  setState(() {
                    _signaturePng = png;
                    _error = null;
                  });
                },
                child: Text((_signaturePng ?? '').isEmpty ? 'Lưu chữ ký' : 'Đã lưu chữ ký ✓'),
              ),
            ],
          ),
        ],
      );

  List<Widget> _photoFields() => [
        TextField(
          controller: _reason,
          decoration: const InputDecoration(
            labelText: 'Lý do không có OTP (bắt buộc)',
            helperText: 'Ví dụ: khách không nghe máy, khách ký giấy riêng',
          ),
          onChanged: (_) => setState(() {}),
        ),
        const SizedBox(height: 8),
        // Wrap, not Row: the button label plus a full coordinate string overflows a
        // 608 px surface already (measured by the widget test: RenderFlex overflowed
        // by 52 px) and real phones are narrower.
        Wrap(
          spacing: 12,
          runSpacing: 8,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            OutlinedButton.icon(
              onPressed: _submitting ? null : _pickPhoto,
              icon: const Icon(Icons.photo_camera),
              label: Text('Chụp ảnh (${_photos.length}/$kMaxPhotos)'),
            ),
            Text(_locating
                ? 'Đang lấy toạ độ…'
                : _gps == null
                    ? 'Chưa có GPS'
                    : 'Toạ độ: ${_gps!.lat.toStringAsFixed(5)}, ${_gps!.lng.toStringAsFixed(5)}'),
          ],
        ),
        if (_photos.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text('Đã có ${_photos.length} ảnh bằng chứng'),
          ),
      ];
}

/// Minimal signature capture: draw with a finger, export a PNG.
///
/// Deliberately dependency-free (no signature package) — it is 60 lines of
/// CustomPainter, and the server only needs a small black-on-white PNG.
class SignaturePad extends StatefulWidget {
  const SignaturePad({super.key, this.onChanged});

  final ValueChanged<bool>? onChanged;

  @override
  State<SignaturePad> createState() => SignaturePadState();
}

class SignaturePadState extends State<SignaturePad> {
  final List<List<Offset>> _strokes = [];
  static const int _width = 480;
  static const int _height = 200;

  bool get hasInk => _strokes.any((stroke) => stroke.length > 1);

  void clear() {
    setState(_strokes.clear);
    widget.onChanged?.call(false);
  }

  /// PNG (base64) at [_width]x[_height]; null when nothing was drawn.
  Future<String?> toPngBase64() async {
    if (!hasInk) return null;
    final recorder = ui.PictureRecorder();
    final canvas = Canvas(recorder);
    canvas.drawRect(
      Rect.fromLTWH(0, 0, _width.toDouble(), _height.toDouble()),
      Paint()..color = Colors.white,
    );
    final paint = Paint()
      ..color = Colors.black
      ..strokeWidth = 3
      ..strokeCap = StrokeCap.round;
    for (final stroke in _strokes) {
      for (var i = 1; i < stroke.length; i++) {
        canvas.drawLine(stroke[i - 1], stroke[i], paint);
      }
    }
    final image = await recorder.endRecording().toImage(_width, _height);
    final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
    if (bytes == null) return null;
    return base64Encode(bytes.buffer.asUint8List());
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text('Khách ký vào khung dưới'),
        const SizedBox(height: 4),
        Container(
          height: _height / 2,
          width: double.infinity,
          decoration: BoxDecoration(
            color: Colors.white,
            border: Border.all(color: Colors.black26),
          ),
          child: GestureDetector(
            onPanStart: (details) => setState(() => _strokes.add([details.localPosition])),
            onPanUpdate: (details) => setState(() => _strokes.last.add(details.localPosition)),
            onPanEnd: (_) => widget.onChanged?.call(hasInk),
            child: CustomPaint(painter: _StrokePainter(_strokes)),
          ),
        ),
      ],
    );
  }
}

class _StrokePainter extends CustomPainter {
  _StrokePainter(this.strokes);
  final List<List<Offset>> strokes;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = Colors.black
      ..strokeWidth = 2
      ..strokeCap = StrokeCap.round;
    for (final stroke in strokes) {
      for (var i = 1; i < stroke.length; i++) {
        canvas.drawLine(stroke[i - 1], stroke[i], paint);
      }
    }
  }

  @override
  bool shouldRepaint(covariant _StrokePainter old) => true;
}
