import 'package:flutter/material.dart';

import 'core.dart';

/// Mốc 2 — Owner dashboard: customers / batches / debts / approve SO.
///
/// ONLINE-ONLY by design (Mốc 4 introduces the offline queue for the subset of
/// actions allowed offline — none of these are in that subset). Approving a
/// Sales Order creates real receivables on the server, so it is a financial
/// action: [FinancialAction.guard] before submit, and a confirmation dialog so
/// a stray tap cannot commit money.
class OwnerDashboardScreen extends StatefulWidget {
  const OwnerDashboardScreen({super.key, required this.erp});

  final ErpClient erp;

  @override
  State<OwnerDashboardScreen> createState() => _OwnerDashboardScreenState();
}

class _OwnerDashboardScreenState extends State<OwnerDashboardScreen> {
  static const _openDebtStatuses = ['Chưa trả', 'Một phần', 'Quá hạn'];

  bool _loading = true;
  bool _approving = false;
  String? _error;
  List<Map<String, dynamic>> _customers = const [];
  List<Map<String, dynamic>> _batches = const [];
  List<Map<String, dynamic>> _debts = const [];
  List<Map<String, dynamic>> _orders = const [];

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
      final customers = await widget.erp.getList('Customer',
          fields: ['name', 'customer_name'], limit: 20, orderBy: 'modified desc');
      final batches = await widget.erp.getList('Feed Batch',
          fields: ['name', 'customer', 'total_debt'], limit: 20, orderBy: 'modified desc');
      final debts = await widget.erp.getList('Batch Debt',
          fields: ['name', 'customer', 'batch', 'allocated_amount', 'paid_amount',
              'returned_amount', 'outstanding_amount', 'status', 'due_date'],
          filters: {'status': ['in', _openDebtStatuses]},
          limit: 20,
          orderBy: 'due_date asc');
      final orders = await widget.erp.getList('Sales Order',
          fields: ['name', 'customer', 'grand_total', 'transaction_date'],
          filters: {'docstatus': 0},
          limit: 20,
          orderBy: 'transaction_date desc');
      if (!mounted) return;
      setState(() {
        _customers = customers;
        _batches = batches;
        _debts = debts;
        _orders = orders;
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

  Future<void> _approve(Map<String, dynamic> order) async {
    final name = order['name'] as String? ?? '';
    final total = (order['grand_total'] as num?) ?? 0;
    final customer = order['customer'] as String? ?? '';
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Duyệt đơn hàng?'),
        content: Text('$name — $customer\nTổng: ${vnd(total)}\n\n'
            'Duyệt sẽ ghi nhận công nợ trên máy chủ (áp hạn mức tín dụng).'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Huỷ')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Duyệt')),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    setState(() => _approving = true);
    try {
      FinancialAction.guard(); // hard rule #1 — this path is online-only anyway
      await widget.erp.submitDoc('Sales Order', name);
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('Đã duyệt $name')));
      await _refresh();
    } on StateError {
      // FinancialAction.guard() throws StateError (an Error, NOT an Exception —
      // it does not match `on Exception`, so without this clause the offline
      // tap would crash the app instead of explaining itself).
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
          content: Text('Đang offline — không thể duyệt đơn (thao tác tài chính)')));
    } on SubmitRejected catch (e) {
      // Server refused (credit limit, permissions...) — show its message; the
      // order stays a draft. Server-wins: no local force-through.
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('Máy chủ từ chối: ${e.message}')));
    } on Exception catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('Không duyệt được: $e')));
    } finally {
      if (mounted) setState(() => _approving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Chủ — Tổng quan')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? _ErrorCard(error: _error!, onRetry: _refresh)
              : RefreshIndicator(onRefresh: _refresh, child: _buildList()),
    );
  }

  Widget _buildList() {
    final outstanding = _debts.fold<num>(0, (s, d) => s + ((d['outstanding_amount'] as num?) ?? 0));
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Row(
          children: [
            _StatChip(label: 'Khách', value: '${_customers.length}'),
            const SizedBox(width: 8),
            _StatChip(label: 'Lứa', value: '${_batches.length}'),
            const SizedBox(width: 8),
            _StatChip(label: 'Phải thu (top 20)', value: vnd(outstanding)),
          ],
        ),
        _Section('Đơn hàng chờ duyệt', _orders.isEmpty
            ? const Text('Không có đơn nháp nào')
            : Column(
                children: [
                  for (final o in _orders)
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      title: Text('${o['name']}'),
                      subtitle: Text('${o['customer'] ?? ''} · ${vnd((o['grand_total'] as num?) ?? 0)}'),
                      trailing: _approving
                          ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
                          : FilledButton(onPressed: () => _approve(o), child: const Text('Duyệt')),
                    ),
                ],
              )),
        _Section('Lứa nuôi', _batches.isEmpty
            ? const Text('Chưa có lứa nào')
            : Column(
                children: [
                  for (final b in _batches)
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      title: Text('${b['name']}'),
                      subtitle: Text('${b['customer'] ?? ''}'),
                      trailing: Text(vnd((b['total_debt'] as num?) ?? 0)),
                    ),
                ],
              )),
        _Section('Nợ cần thu (Chưa trả / Một phần / Quá hạn)', _debts.isEmpty
            ? const Text('Không có nợ mở nào')
            : Column(
                children: [
                  for (final d in _debts)
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      title: Text('${d['customer'] ?? ''}'),
                      subtitle: Text(
                          '${d['batch'] ?? ''} · còn ${vnd((d['outstanding_amount'] as num?) ?? 0)}'
                          ' (đã trả ${vnd((d['paid_amount'] as num?) ?? 0)}) · đến hạn ${d['due_date'] ?? '—'}'),
                      trailing: _DebtStatusChip(status: '${d['status'] ?? ''}'),
                    ),
                ],
              )),
      ],
    );
  }
}

class _ErrorCard extends StatelessWidget {
  const _ErrorCard({required this.error, required this.onRetry});

  final String error;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Card(
          color: Colors.red.shade50,
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text('Lỗi tải dữ liệu: $error'),
                const SizedBox(height: 12),
                FilledButton.icon(
                  onPressed: onRetry,
                  icon: const Icon(Icons.refresh),
                  label: const Text('Thử lại'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _StatChip extends StatelessWidget {
  const _StatChip({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: Card(
        child: Padding(
          padding: const EdgeInsets.all(10),
          child: Column(children: [
            Text(label, style: const TextStyle(fontSize: 11, color: Colors.black54)),
            const SizedBox(height: 4),
            Text(value, style: const TextStyle(fontWeight: FontWeight.bold)),
          ]),
        ),
      ),
    );
  }
}

class _Section extends StatelessWidget {
  const _Section(this.title, this.child);

  final String title;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 4),
          child,
        ],
      ),
    );
  }
}

class _DebtStatusChip extends StatelessWidget {
  const _DebtStatusChip({required this.status});

  final String status;

  @override
  Widget build(BuildContext context) {
    final color = switch (status) {
      'Quá hạn' => Colors.red,
      'Một phần' => Colors.orange,
      _ => Colors.green,
    };
    return Chip(
      label: Text(status, style: TextStyle(fontSize: 11, color: color)),
      visualDensity: VisualDensity.compact,
    );
  }
}
