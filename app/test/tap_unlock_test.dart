import 'package:flutter_test/flutter_test.dart';

import 'package:argos_app/screens/camera_test_screen.dart';

void main() {
  final t0 = DateTime(2026, 11, 23, 10);
  DateTime at(int ms) => t0.add(Duration(milliseconds: ms));

  test('5 touches rapides ouvrent l\'écran caché', () {
    final u = TapUnlock();
    expect([for (var i = 0; i < 4; i++) u.tap(at(i * 300))], everyElement(isFalse));
    expect(u.tap(at(1200)), isTrue);
    expect(u.tap(at(1300)), isFalse, reason: 'la série repart de zéro');
  });

  test('des touches trop espacées ne suffisent pas', () {
    final u = TapUnlock();
    for (var i = 0; i < 10; i++) {
      expect(u.tap(at(i * 1000)), isFalse);
    }
  });

  test('une touche normale sur le titre ne fait rien', () {
    expect(TapUnlock().tap(t0), isFalse);
  });

  test('résumé du boîtier lisible par l\'enseignant', () {
    final lines = describeStatus({
      'persons': 2,
      'phones': 1,
      'teacher': false,
      'fps': 0.8,
      'people': [
        {'id': 1, 'head_visible': true, 'head_down': false, 'phone_near': true,
         'phone_progress': [4, 10], 'head_down_progress': [0, 40]},
        {'id': 2, 'head_visible': true, 'head_down': true, 'phone_near': false,
         'phone_progress': [0, 10], 'head_down_progress': [12, 40]},
      ],
    });
    expect(lines[0], 'Le boîtier voit : 2 personnes · 1 téléphone');
    expect(lines[1], contains('0.8 image/s'));
    expect(lines[2], contains('téléphone en main'));
    expect(lines[2], contains('règle téléphone 4/10'));
    expect(lines[3], contains('tête baissée 12/40'));
  });

  test('rien encore analysé', () {
    expect(describeStatus(null).single, contains('rien analysé'));
  });

  test('cadre rouge si alerte, vert sinon', () {
    expect(boxColor({'alert': true}), detectionRed);
    expect(boxColor({'alert': false}), detectionGreen);
    expect(boxColor({}), detectionGreen);
  });

  test('caméra du Pi prioritaire sur le téléphone', () {
    expect(piCameraActive({'source': 'picamera'}), isTrue);
    expect(piCameraActive({'source': 'phone'}), isFalse);
    expect(piCameraActive(null), isFalse);
  });
}
