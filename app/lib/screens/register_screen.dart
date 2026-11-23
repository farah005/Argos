import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';

import '../services/api.dart';
import '../services/notifier.dart';
import 'alerts_screen.dart';

/// Same rule as the backend: firstname.lastname@enit.utm.tn (student addresses are rejected).
final teacherEmail = RegExp(r'^[a-z]+(-[a-z]+)*\.[a-z]+(-[a-z]+)*\d*@enit\.utm\.tn$');

String? checkTeacherEmail(String v) {
  final e = v.trim().toLowerCase();
  if (e.isEmpty) return 'Adresse obligatoire';
  if (e.endsWith('@etudiant-enit.utm.tn')) return 'Les comptes sont réservés aux enseignants';
  if (!teacherEmail.hasMatch(e)) return 'Format attendu : prenom.nom@enit.utm.tn';
  return null;
}

class RegisterScreen extends StatefulWidget {
  final Api api;
  final String serverUrl;
  const RegisterScreen({super.key, required this.api, required this.serverUrl});

  @override
  State<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends State<RegisterScreen> {
  final _form = GlobalKey<FormState>();
  late final _url = TextEditingController(text: widget.serverUrl);
  final _email = TextEditingController();
  final _pass = TextEditingController();
  final _pass2 = TextEditingController();
  bool _busy = false;
  String? _error;

  Future<void> _submit() async {
    if (!_form.currentState!.validate()) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    initNotifications();
    try {
      await widget.api.register(_url.text, _email.text.trim().toLowerCase(), _pass.text);
      if (!mounted) return;
      Navigator.of(context).pushAndRemoveUntil(
        MaterialPageRoute(builder: (_) => AlertsScreen(api: widget.api)),
        (_) => false,
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
      appBar: AppBar(title: const Text('Créer un compte enseignant')),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 380),
          child: Form(
            key: _form,
            child: ListView(
              shrinkWrap: true,
              padding: const EdgeInsets.all(24),
              children: [
                const Text(
                  'Utilisez votre adresse ENIT (prenom.nom@enit.utm.tn). '
                  'Vous verrez les alertes dès que l\'administrateur vous aura attribué vos classes.',
                ),
                const SizedBox(height: 16),
                if (!kIsWeb)
                  TextFormField(
                    controller: _url,
                    keyboardType: TextInputType.url,
                    decoration: const InputDecoration(
                      labelText: 'Adresse du serveur',
                      prefixIcon: Icon(Icons.dns_outlined),
                    ),
                  ),
                TextFormField(
                  controller: _email,
                  keyboardType: TextInputType.emailAddress,
                  autocorrect: false,
                  validator: (v) => checkTeacherEmail(v ?? ''),
                  decoration: const InputDecoration(
                    labelText: 'Adresse e-mail ENIT',
                    hintText: 'prenom.nom@enit.utm.tn',
                    prefixIcon: Icon(Icons.alternate_email),
                  ),
                ),
                TextFormField(
                  controller: _pass,
                  obscureText: true,
                  validator: (v) => (v ?? '').length < 8 ? '8 caractères minimum' : null,
                  decoration: const InputDecoration(labelText: 'Mot de passe', prefixIcon: Icon(Icons.lock_outline)),
                ),
                TextFormField(
                  controller: _pass2,
                  obscureText: true,
                  validator: (v) => v != _pass.text ? 'Les mots de passe ne correspondent pas' : null,
                  onFieldSubmitted: (_) => _submit(),
                  decoration: const InputDecoration(
                    labelText: 'Confirmer le mot de passe',
                    prefixIcon: Icon(Icons.lock_outline),
                  ),
                ),
                const SizedBox(height: 16),
                if (_error != null)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
                  ),
                FilledButton(
                  onPressed: _busy ? null : _submit,
                  child: _busy
                      ? const SizedBox(height: 18, width: 18, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Text('Créer mon compte'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
