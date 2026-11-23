import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';

import '../services/api.dart';
import '../services/notifier.dart';
import 'alerts_screen.dart';
import 'register_screen.dart';

class LoginScreen extends StatefulWidget {
  final Api api;
  const LoginScreen({super.key, required this.api});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  late final _url = TextEditingController(text: widget.api.baseUrl);
  final _user = TextEditingController();
  final _pass = TextEditingController();
  bool _busy = false;
  String? _error;

  Future<void> _login() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    // requested during the click: browsers require a user action
    initNotifications();
    try {
      await widget.api.login(_url.text, _user.text.trim(), _pass.text);
      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => AlertsScreen(api: widget.api)),
      );
    } catch (e) {
      setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 380),
          child: ListView(
            shrinkWrap: true,
            padding: const EdgeInsets.all(24),
            children: [
              Semantics(
                label: 'Argos',
                image: true,
                child: Image.asset('assets/logo.png', height: 120, fit: BoxFit.contain),
              ),
              const SizedBox(height: 8),
              const Text('Assistance à la surveillance de classe', textAlign: TextAlign.center),
              const SizedBox(height: 24),
              if (!kIsWeb)
                TextField(
                  controller: _url,
                  keyboardType: TextInputType.url,
                  decoration: const InputDecoration(
                    labelText: 'Adresse du serveur',
                    hintText: 'http://192.168.1.50:8000',
                    prefixIcon: Icon(Icons.dns_outlined),
                  ),
                ),
              TextField(
                controller: _user,
                keyboardType: TextInputType.emailAddress,
                autocorrect: false,
                decoration: const InputDecoration(
                  labelText: 'E-mail ENIT ou identifiant',
                  prefixIcon: Icon(Icons.person_outline),
                ),
              ),
              TextField(
                controller: _pass,
                obscureText: true,
                onSubmitted: (_) => _login(),
                decoration: const InputDecoration(labelText: 'Mot de passe', prefixIcon: Icon(Icons.lock_outline)),
              ),
              const SizedBox(height: 16),
              if (_error != null)
                Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
                ),
              FilledButton(
                onPressed: _busy ? null : _login,
                child: _busy
                    ? const SizedBox(height: 18, width: 18, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Text('Se connecter'),
              ),
              const SizedBox(height: 8),
              TextButton(
                onPressed: _busy
                    ? null
                    : () => Navigator.of(context).push(MaterialPageRoute(
                          builder: (_) => RegisterScreen(api: widget.api, serverUrl: _url.text),
                        )),
                child: const Text('Nouvel enseignant ? Créer un compte'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
