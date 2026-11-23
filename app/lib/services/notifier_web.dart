import 'dart:js_interop';

import 'package:web/web.dart' as web;

/// Browser: Notification API. It only exists over HTTPS (or on localhost);
/// otherwise the alert stays displayed in the page.
Future<void> initNotifications() async {
  try {
    if (web.Notification.permission == 'default') {
      await web.Notification.requestPermission().toDart;
    }
  } catch (_) {}
}

Future<void> showAlert(String title, String body) async {
  try {
    if (web.Notification.permission == 'granted') {
      web.Notification(title, web.NotificationOptions(body: body));
    }
  } catch (_) {}
}
