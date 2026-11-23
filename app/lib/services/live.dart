import 'dart:async';
import 'dart:convert';

import 'package:web_socket_channel/web_socket_channel.dart';

import '../models.dart';
import 'api.dart';

class LiveMessage {
  final String type; // new_event | event_updated
  final bool notify;
  final AlertEvent event;
  LiveMessage(this.type, this.notify, this.event);
}

/// WebSocket connection to the backend, with automatic reconnection.
class LiveService {
  final Api api;
  final _messages = StreamController<LiveMessage>.broadcast();
  final _connected = StreamController<bool>.broadcast();
  WebSocketChannel? _ch;
  Timer? _ping;
  Timer? _retry;
  int _attempt = 0;
  bool _closed = false;

  LiveService(this.api);

  Stream<LiveMessage> get messages => _messages.stream;
  Stream<bool> get connected => _connected.stream;

  Future<void> start() async {
    _closed = false;
    await _connect();
  }

  Future<void> _connect() async {
    if (_closed || !api.isLoggedIn) return;
    try {
      final ch = WebSocketChannel.connect(api.wsUri());
      await ch.ready;
      _ch = ch;
      _attempt = 0;
      _connected.add(true);
      _ping?.cancel();
      _ping = Timer.periodic(const Duration(seconds: 25), (_) => _ch?.sink.add('ping'));
      ch.stream.listen(_onData, onDone: _scheduleRetry, onError: (_) => _scheduleRetry());
    } catch (_) {
      _scheduleRetry();
    }
  }

  void _onData(dynamic data) {
    try {
      final j = jsonDecode(data as String) as Map<String, dynamic>;
      _messages.add(LiveMessage(
        j['type'] as String,
        (j['notify'] ?? false) as bool,
        AlertEvent.fromJson(j['event'] as Map<String, dynamic>),
      ));
    } catch (_) {}
  }

  void _scheduleRetry() {
    _ping?.cancel();
    _ch = null;
    if (_closed) return;
    _connected.add(false);
    _retry?.cancel();
    final secs = [1, 2, 5, 10, 20, 30][_attempt.clamp(0, 5)];
    _attempt++;
    _retry = Timer(Duration(seconds: secs), _connect);
  }

  Future<void> stop() async {
    _closed = true;
    _ping?.cancel();
    _retry?.cancel();
    await _ch?.sink.close();
    _ch = null;
  }

  void dispose() {
    stop();
    _messages.close();
    _connected.close();
  }
}
