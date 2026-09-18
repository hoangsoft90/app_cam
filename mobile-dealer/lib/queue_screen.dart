import 'package:flutter/material.dart';

import 'offline_queue.dart';

/// Mốc 4 — the offline queue, shown honestly.
///
/// Two lists, because they need different actions:
///   * **Chờ gửi** — still owed to the server. Nothing is wrong; they drain when
///     the link returns. The only action is "bỏ" (the driver gave up on it).
///   * **Cần xử lý** — the SERVER answered and refused. The message is the
///     server's own text, and retrying the same payload automatically would only
///     reproduce the refusal. So there is deliberately NO "retry" here: either
///     the driver files the delivery again from the delivery screen (with a fresh
///     reason/method) or discards the row.
class QueueScreen extends StatefulWidget {
  const QueueScreen({super.key, required this.queue, this.onRetry});

  final OfflineQueue queue;

  /// Runs one sync pass (owned by the screen that knows about connectivity).
  final Future<void> Function()? onRetry;

  @override
  State<QueueScreen> createState() => _QueueScreenState();
}

class _QueueScreenState extends State<QueueScreen> {
  bool _busy = false;

  Future<void> _retry() async {
    final retry = widget.onRetry;
    if (retry == null) return;
    setState(() => _busy = true);
    await retry();
    if (!mounted) return;
    setState(() => _busy = false);
  }

  Future<void> _discard(QueuedMutation row) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Bỏ mục này?'),
        content: Text('Mục cho đơn ${row.entityId} sẽ bị xoá khỏi máy. '
            'Nếu chưa gửi được lên máy chủ thì việc giao đó coi như chưa ghi nhận.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Không')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Bỏ')),
        ],
      ),
    );
    if (confirmed != true) return;
    await widget.queue.discard(row.id);
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final pending = widget.queue.pendingRows;
    final conflicts = widget.queue.conflictRows;
    return Scaffold(
      appBar: AppBar(title: const Text('Hàng đợi ngoài tuyến')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Text('Chỉ xác nhận giao hàng được phép lưu ngoại tuyến. '
              'Thu tiền, đổi hạn mức và duyệt đơn vẫn bắt buộc có mạng — '
              'hàng đợi này không bao giờ chứa các thao tác đó.'),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: _busy ? null : _retry,
                  icon: const Icon(Icons.sync),
                  label: Text(_busy ? 'Đang gửi…' : 'Gửi lại ngay'),
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          Text('Chờ gửi (${pending.length})', style: Theme.of(context).textTheme.titleMedium),
          if (pending.isEmpty) const Text('Không có mục nào đang chờ.'),
          for (final row in pending) _rowCard(row, conflict: false),
          const SizedBox(height: 16),
          Text('Cần xử lý (${conflicts.length})', style: Theme.of(context).textTheme.titleMedium),
          if (conflicts.isEmpty) const Text('Không có mục nào bị máy chủ từ chối.'),
          for (final row in conflicts) _rowCard(row, conflict: true),
        ],
      ),
    );
  }

  Widget _rowCard(QueuedMutation row, {required bool conflict}) => Card(
        color: conflict ? Theme.of(context).colorScheme.errorContainer : null,
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('${row.entity} ${row.entityId}',
                  style: const TextStyle(fontWeight: FontWeight.bold)),
              Text('${row.operation} · lúc ${_fmt(row.createdAt)} · thử ${row.retryCount} lần'),
              if ((row.lastError ?? '').isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 4),
                  child: Text(conflict ? 'Máy chủ từ chối: ${row.lastError}' : 'Trạng thái: ${row.lastError}',
                      style: TextStyle(color: conflict ? Theme.of(context).colorScheme.error : null)),
                ),
              if (conflict)
                const Padding(
                  padding: EdgeInsets.only(top: 4),
                  child: Text('Máy chủ đã quyết định nên hệ thống không tự gửi lại — '
                      'giao lại từ màn Giao hàng (chọn cách xác nhận khác) hoặc bỏ mục này.'),
                ),
              Align(
                alignment: Alignment.centerRight,
                child: TextButton(
                  onPressed: () => _discard(row),
                  child: const Text('Bỏ'),
                ),
              ),
            ],
          ),
        ),
      );

  /// Local time, minute precision. Hand-formatted on purpose: `intl` would be a
  /// dependency for one label.
  static String _fmt(DateTime utc) {
    final t = utc.toLocal();
    String two(int v) => v.toString().padLeft(2, '0');
    return '${two(t.day)}/${two(t.month)} ${two(t.hour)}:${two(t.minute)}';
  }
}
