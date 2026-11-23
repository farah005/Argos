import 'package:flutter_local_notifications/flutter_local_notifications.dart';

final _plugin = FlutterLocalNotificationsPlugin();
bool _ready = false;
int _id = 0;

/// Android / iOS: local notification triggered by the WebSocket message.
Future<void> initNotifications() async {
  if (_ready) return;
  try {
    const android = AndroidInitializationSettings('@mipmap/ic_launcher');
    const darwin = DarwinInitializationSettings();
    await _plugin.initialize(const InitializationSettings(android: android, iOS: darwin));
    await _plugin
        .resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>()
        ?.requestNotificationsPermission();
    _ready = true;
  } catch (_) {
    // unsupported platform (desktop): the alert stays visible in the app
  }
}

Future<void> showAlert(String title, String body) async {
  if (!_ready) return;
  const details = NotificationDetails(
    android: AndroidNotificationDetails(
      'argos_alerts',
      'Alertes Argos',
      channelDescription: 'Alertes de comportement à revoir',
      importance: Importance.high,
      priority: Priority.high,
    ),
    iOS: DarwinNotificationDetails(),
  );
  await _plugin.show(_id++, title, body, details);
}
