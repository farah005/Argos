import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';

import 'screens/alerts_screen.dart';
import 'screens/login_screen.dart';
import 'services/api.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // On the web, the app is served by the backend itself: same address.
  final defaultUrl = kIsWeb ? Uri.base.origin : 'http://192.168.1.50:8000';
  final api = await Api.restore(defaultUrl);
  runApp(ArgosApp(api: api));
}

/// Argos logo colours: navy blue and gold (open book).
const argosNavy = Color(0xFF072349);
const argosGold = Color(0xFFC49558);

final argosTheme = ThemeData(
  useMaterial3: true,
  colorScheme: ColorScheme.fromSeed(seedColor: argosNavy, primary: argosNavy, secondary: argosGold),
  appBarTheme: const AppBarTheme(backgroundColor: argosNavy, foregroundColor: Colors.white),
  tabBarTheme: const TabBarThemeData(
    labelColor: Colors.white,
    unselectedLabelColor: Colors.white70,
    indicatorColor: argosGold,
  ),
);

class ArgosApp extends StatelessWidget {
  final Api api;
  const ArgosApp({super.key, required this.api});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Argos',
      debugShowCheckedModeBanner: false,
      theme: argosTheme,
      home: api.isLoggedIn ? AlertsScreen(api: api) : LoginScreen(api: api),
    );
  }
}
