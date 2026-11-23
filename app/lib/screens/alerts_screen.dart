import 'dart:async';

import 'package:flutter/material.dart';

import '../models.dart';
import '../services/api.dart';
import '../services/live.dart';
import '../services/notifier.dart';
import 'alert_detail_screen.dart';
import 'camera_test_screen.dart';
import 'login_screen.dart';

class AlertsScreen extends StatefulWidget {
  final Api api;
  const AlertsScreen({super.key, required this.api});

  @override
  State<AlertsScreen> createState() => _AlertsScreenState();
}

class _AlertsScreenState extends State<AlertsScreen> {
  late final LiveService _live = LiveService(widget.api);
  final _unlock = TapUnlock();
  StreamSubscription? _sub, _connSub;
  List<AlertEvent> _events = [];
  List<Map<String, dynamic>> _summary = [];
  bool _connected = false;
  bool _loading = true;
  bool _noClassroom = false;
  String? _error;

  bool get _isAdmin => widget.api.user?['role'] == 'admin';

  @override
  void initState() {
    super.initState();
    initNotifications();
    _connSub = _live.connected.listen((c) => setState(() => _connected = c));
    _sub = _live.messages.listen(_onLive);
    _live.start();
    _refresh();
  }

  @override
  void dispose() {
    _sub?.cancel();
    _connSub?.cancel();
    _live.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    try {
      final ev = await widget.api.events();
      final s = await widget.api.summary();
      final rooms = _isAdmin ? const <String>['*'] : await widget.api.myClassrooms();
      setState(() {
        _events = ev;
        _summary = s;
        _noClassroom = rooms.isEmpty;
        _error = null;
      });
    } on ApiException catch (e) {
      if (e.message.contains('Session expirée')) return _logout();
      setState(() => _error = e.message);
    } catch (e) {
      setState(() => _error = 'Serveur injoignable');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  void _onLive(LiveMessage m) {
    setState(() {
      final i = _events.indexWhere((e) => e.id == m.event.id);
      if (i >= 0) {
        _events[i] = m.event;
      } else {
        _events.insert(0, m.event);
      }
    });
    if (m.type == 'new_event') {
      _reloadSummary();
    }
    if (m.type == 'new_event' && m.notify) {
      final e = m.event;
      final body = '${e.classroom} · confiance ${(e.confidence * 100).round()} % · élève #${e.trackId ?? '?'}';
      showAlert('Argos : ${e.label}', body);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text('${e.label} — $body'),
        duration: const Duration(seconds: 8),
        action: SnackBarAction(label: 'Voir', onPressed: () => _open(e)),
      ));
    }
  }

  Future<void> _reloadSummary() async {
    try {
      final s = await widget.api.summary();
      if (mounted) setState(() => _summary = s);
    } catch (_) {}
  }

  Future<void> _open(AlertEvent e) async {
    final updated = await Navigator.of(context).push<AlertEvent>(
      MaterialPageRoute(builder: (_) => AlertDetailScreen(api: widget.api, event: e)),
    );
    if (updated != null) {
      setState(() {
        final i = _events.indexWhere((x) => x.id == updated.id);
        if (i >= 0) _events[i] = updated;
      });
    }
  }

  Future<void> _logout() async {
    await _live.stop();
    await widget.api.logout();
    if (!mounted) return;
    Navigator.of(context).pushReplacement(MaterialPageRoute(builder: (_) => LoginScreen(api: widget.api)));
  }

  int _count(String status) => _events.where((e) => e.status == status).length;

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 3,
      child: Scaffold(
        appBar: AppBar(
          // 5 quick taps on the title: hidden "Camera test" screen (nothing hints at it)
          title: GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTap: () {
              if (_unlock.tap(DateTime.now())) {
                Navigator.of(context).push(
                    MaterialPageRoute(builder: (_) => CameraTestScreen(api: widget.api)));
              }
            },
            child: Text('Alertes — ${widget.api.displayName}'),
          ),
          actions: [
            Tooltip(
              message: _connected ? 'Temps réel connecté' : 'Reconnexion…',
              child: Icon(_connected ? Icons.wifi : Icons.wifi_off,
                  color: _connected ? Colors.green : Theme.of(context).colorScheme.error),
            ),
            IconButton(onPressed: _refresh, icon: const Icon(Icons.refresh), tooltip: 'Actualiser'),
            IconButton(onPressed: _logout, icon: const Icon(Icons.logout), tooltip: 'Se déconnecter'),
          ],
          bottom: TabBar(tabs: [
            Tab(text: 'À revoir (${_count('pending')})'),
            Tab(text: 'Confirmées (${_count('confirmed')})'),
            Tab(text: 'Rejetées (${_count('dismissed')})'),
          ]),
        ),
        body: _loading
            ? const Center(child: CircularProgressIndicator())
            : Column(children: [
                if (_error != null)
                  MaterialBanner(content: Text(_error!), actions: [
                    TextButton(onPressed: _refresh, child: const Text('Réessayer')),
                  ]),
                if (_noClassroom && _error == null)
                  MaterialBanner(
                    leading: const Icon(Icons.hourglass_top),
                    content: const Text(
                      'Votre compte est créé. Vous verrez les alertes dès que l\'administrateur '
                      'vous aura attribué vos classes.',
                    ),
                    actions: [TextButton(onPressed: _refresh, child: const Text('Vérifier'))],
                  ),
                _SummaryBar(summary: _summary),
                Expanded(
                  child: TabBarView(children: [
                    _list('pending'),
                    _list('confirmed'),
                    _list('dismissed'),
                  ]),
                ),
              ]),
      ),
    );
  }

  Widget _list(String status) {
    final items = _events.where((e) => e.status == status).toList();
    return RefreshIndicator(
      onRefresh: _refresh,
      child: items.isEmpty
          ? ListView(children: const [
              SizedBox(height: 120),
              Center(child: Text('Aucune alerte')),
            ])
          : ListView.separated(
              itemCount: items.length,
              separatorBuilder: (_, __) => const Divider(height: 1),
              itemBuilder: (_, i) => _tile(items[i]),
            ),
    );
  }

  static IconData _icon(String category) {
    switch (category) {
      case 'possible_phone_use':
        return Icons.smartphone;
      case 'possible_conflict':
        return Icons.groups_outlined;
      default:
        return Icons.airline_seat_flat_angled;
    }
  }

  Widget _tile(AlertEvent e) {
    final high = e.tier == 'high';
    return ListTile(
      leading: CircleAvatar(
        backgroundColor: high ? Colors.orange.shade100 : Colors.blueGrey.shade50,
        child: Icon(_icon(e.category), color: high ? Colors.orange.shade800 : Colors.blueGrey),
      ),
      title: Text(e.label),
      subtitle: Text('${e.classroom} · ${hhmm(e.detectedAt)} · élève #${e.trackId ?? '?'}'
          '${high ? '' : ' · résumé'}'),
      trailing: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Text('${(e.confidence * 100).round()} %', style: const TextStyle(fontWeight: FontWeight.bold)),
          if (e.hasClip) const Icon(Icons.movie_outlined, size: 16),
        ],
      ),
      onTap: () => _open(e),
    );
  }
}

class _SummaryBar extends StatelessWidget {
  final List<Map<String, dynamic>> summary;
  const _SummaryBar({required this.summary});

  @override
  Widget build(BuildContext context) {
    final byCat = <String, int>{};
    for (final s in summary) {
      byCat[s['category'] as String] = (byCat[s['category']] ?? 0) + (s['count'] as int);
    }
    if (byCat.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
      child: Wrap(
        spacing: 8,
        children: [
          const Chip(label: Text('24 h')),
          for (final e in byCat.entries) Chip(label: Text('${categoryLabel(e.key)} : ${e.value}')),
        ],
      ),
    );
  }
}
