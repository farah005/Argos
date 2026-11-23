// System notifications: different implementations on mobile and on the web.
export 'notifier_stub.dart'
    if (dart.library.io) 'notifier_io.dart'
    if (dart.library.js_interop) 'notifier_web.dart';
