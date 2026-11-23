import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:argos_app/main.dart';
import 'package:argos_app/models.dart';
import 'package:argos_app/screens/register_screen.dart';
import 'package:argos_app/services/api.dart';

void main() {
  group('dates', () {
    test('une date avec +00:00 est convertie en heure locale', () {
      final d = parseUtc('2026-11-17T21:16:01.143776+00:00')!;
      expect(d.isUtc, isFalse);
      expect(d.toUtc(), DateTime.utc(2026, 11, 17, 21, 16, 1, 143, 776));
    });

    test('une date sans fuseau (ancien backend) est lue comme UTC', () {
      expect(parseUtc('2026-11-17T21:16:01')!.toUtc(), DateTime.utc(2026, 11, 17, 21, 16, 1));
    });

    test('null reste null et hhmm affiche --:--', () {
      expect(parseUtc(null), isNull);
      expect(hhmm(null), '--:--');
    });
  });

  test('événement JSON du backend', () {
    final e = AlertEvent.fromJson({
      'id': 6, 'classroom': 'Salle Test', 'category': 'possible_phone_use', 'confidence': 0.496,
      'tier': 'medium', 'track_id': 1, 'started_at': null, 'detected_at': '2026-11-17T21:16:01+00:00',
      'status': 'pending', 'has_clip': false, 'teacher_facing_board': false,
    });
    expect(e.id, 6);
    expect(e.hasClip, isFalse);
    // neutral wording: never "cheating"
    expect(e.label, 'Utilisation possible du téléphone');
    expect(categoryLabel('persistent_head_down'), 'Tête baissée persistante');
    expect(statusLabel('pending'), 'À revoir');
  });

  testWidgets('non connecté : écran de connexion', (tester) async {
    await tester.pumpWidget(ArgosApp(api: Api(baseUrl: 'http://127.0.0.1:8000')));
    expect(find.bySemanticsLabel('Argos'), findsOneWidget); // logo
    expect(find.text('Adresse du serveur'), findsOneWidget); // field visible outside the web
    expect(find.widgetWithText(FilledButton, 'Se connecter'), findsOneWidget);
    expect(find.text('Nouvel enseignant ? Créer un compte'), findsOneWidget);
  });

  test('libellé neutre du conflit', () {
    expect(categoryLabel('possible_conflict'), 'Conflit physique possible');
  });

  test('adresse e-mail enseignant ENIT', () {
    expect(checkTeacherEmail('sarra.ben-ali@enit.utm.tn'), isNull);
    expect(checkTeacherEmail(' Sarra.Ben-Ali@ENIT.utm.tn '), isNull);
    expect(checkTeacherEmail('amine.trabelsi@etudiant-enit.utm.tn'), 'Les comptes sont réservés aux enseignants');
    expect(checkTeacherEmail('amine@enit.utm.tn'), isNotNull);
    expect(checkTeacherEmail('amine.trabelsi@gmail.com'), isNotNull);
    expect(checkTeacherEmail(''), isNotNull);
  });

  testWidgets('inscription : adresse étudiante refusée avant envoi', (tester) async {
    await tester.pumpWidget(MaterialApp(
      home: RegisterScreen(api: Api(baseUrl: 'http://127.0.0.1:8000'), serverUrl: 'http://127.0.0.1:8000'),
    ));
    await tester.enterText(find.widgetWithText(TextFormField, 'Adresse e-mail ENIT'), 'ali.ben@etudiant-enit.utm.tn');
    await tester.enterText(find.widgetWithText(TextFormField, 'Mot de passe'), 'motdepasse1');
    await tester.enterText(find.widgetWithText(TextFormField, 'Confirmer le mot de passe'), 'autre');
    await tester.tap(find.text('Créer mon compte'));
    await tester.pump();
    expect(find.text('Les comptes sont réservés aux enseignants'), findsOneWidget);
    expect(find.text('Les mots de passe ne correspondent pas'), findsOneWidget);
  });
}
