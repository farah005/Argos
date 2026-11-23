import 'dart:async';

import 'package:camera/camera.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../services/api.dart';
import '../services/temp_file.dart';

/// Unlocks the hidden screen: [needed] taps within [window].
class TapUnlock {
  final int needed;
  final Duration window;
  final List<DateTime> _taps = [];
  TapUnlock({this.needed = 5, this.window = const Duration(seconds: 3)});

  /// True when the last tap completes the series (the series then restarts from zero).
  bool tap(DateTime now) {
    _taps.add(now);
    _taps.removeWhere((t) => now.difference(t) > window);
    if (_taps.length >= needed) {
      _taps.clear();
      return true;
    }
    return false;
  }
}

/// Lines shown under the preview: what the device detects (no image comes back).
List<String> describeStatus(Map<String, dynamic>? s) {
  if (s == null) return ["Le boîtier n'a encore rien analysé (patientez quelques secondes)."];
  final persons = s['persons'] as int? ?? 0;
  final phones = s['phones'] as int? ?? 0;
  final fps = (s['fps'] as num?)?.toDouble() ?? 0;
  final lines = <String>[
    'Le boîtier voit : $persons personne${persons > 1 ? 's' : ''} · '
        '$phones téléphone${phones > 1 ? 's' : ''}${s['teacher'] == true ? ' · enseignant repéré' : ''}',
    "Vitesse d'analyse : ${fps.toStringAsFixed(1)} image/s",
  ];
  for (final raw in (s['people'] as List? ?? const [])) {
    final p = raw as Map<String, dynamic>;
    final ph = (p['phone_progress'] as List?)?.cast<int>() ?? const [0, 0];
    final hd = (p['head_down_progress'] as List?)?.cast<int>() ?? const [0, 0];
    final head = p['head_visible'] == false
        ? 'tête non visible'
        : (p['head_down'] == true ? 'tête baissée' : 'tête levée');
    final phone = p['phone_near'] == true ? 'téléphone en main' : 'pas de téléphone en main';
    lines.add('Élève #${p['id']} : $head, $phone — règle téléphone ${ph[0]}/${ph[1]}, '
        'tête baissée ${hd[0]}/${hd[1]}');
  }
  return lines;
}

const detectionRed = Color(0xFFE53935);
const detectionGreen = Color(0xFF43A047);
const detectionPhone = Color(0xFFFF9800);

/// Red: head down, phone in hand or recent alert on this student. Green otherwise.
Color boxColor(Map<String, dynamic> person) => person['alert'] == true ? detectionRed : detectionGreen;

/// True if the device uses its own camera: the phone must then send nothing.
bool piCameraActive(Map<String, dynamic>? status) => status?['source'] == 'picamera';

/// Boxes returned by the device (0..1 coordinates), drawn on the camera preview.
class DetectionPainter extends CustomPainter {
  final Map<String, dynamic>? status;
  DetectionPainter(this.status);

  Rect _rect(List box, Size size) => Rect.fromLTRB(
        (box[0] as num) * size.width,
        (box[1] as num) * size.height,
        (box[2] as num) * size.width,
        (box[3] as num) * size.height,
      );

  @override
  void paint(Canvas canvas, Size size) {
    final s = status;
    if (s == null || piCameraActive(s)) return;
    for (final raw in (s['people'] as List? ?? const [])) {
      final p = raw as Map<String, dynamic>;
      final box = p['box'] as List?;
      if (box == null || box.length != 4) continue;
      final color = boxColor(p);
      final r = _rect(box, size);
      canvas.drawRect(r, Paint()
        ..color = color
        ..style = PaintingStyle.stroke
        ..strokeWidth = 3);
      final label = TextPainter(
        text: TextSpan(
          text: ' #${p['id']}${p['phone_near'] == true ? ' tél.' : ''}${p['head_down'] == true ? ' tête baissée' : ''} ',
          style: const TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.bold),
        ),
        textDirection: TextDirection.ltr,
      )..layout(maxWidth: size.width);
      final top = (r.top - label.height).clamp(0.0, size.height - label.height);
      canvas.drawRect(Rect.fromLTWH(r.left, top, label.width, label.height), Paint()..color = color);
      label.paint(canvas, Offset(r.left, top));
    }
    for (final raw in (s['phone_boxes'] as List? ?? const [])) {
      final box = raw as List;
      if (box.length != 4) continue;
      canvas.drawRect(_rect(box, size), Paint()
        ..color = detectionPhone
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2);
    }
  }

  @override
  bool shouldRepaint(DetectionPainter old) => old.status != status;
}

/// Hidden test mode: the phone acts as the classroom camera while the device camera is
/// unavailable. Each photo is sent to the device and then erased from the phone;
/// the device keeps only the latest frame, in memory.
class CameraTestScreen extends StatefulWidget {
  final Api api;
  const CameraTestScreen({super.key, required this.api});

  @override
  State<CameraTestScreen> createState() => _CameraTestScreenState();
}

class _CameraTestScreenState extends State<CameraTestScreen> with WidgetsBindingObserver {
  static const _period = Duration(milliseconds: 500);   // ~2 frames/s, above what the Pi 4 processes

  CameraController? _cam;
  bool _running = false;
  bool _busy = false;
  int _sent = 0;
  double _rate = 0;
  String? _error;
  DateTime? _startedAt;
  Map<String, dynamic>? _status;
  Timer? _statusTimer;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _init();
  }

  Future<void> _init() async {
    try {
      final cams = await availableCameras();
      if (cams.isEmpty) throw CameraException('none', 'Aucune caméra sur cet appareil');
      final back = cams.firstWhere((c) => c.lensDirection == CameraLensDirection.back, orElse: () => cams.first);
      final c = CameraController(back, ResolutionPreset.medium,
          enableAudio: false, imageFormatGroup: ImageFormatGroup.jpeg);
      await c.initialize();
      if (!mounted) return c.dispose();
      setState(() => _cam = c);
      // automatic start, unless the device already has its own camera
      await _pollStatus();
      if (mounted && !_piCamera && !_running) _toggle();
      _statusTimer ??= Timer.periodic(const Duration(milliseconds: 700), (_) => _pollStatus());
    } on CameraException catch (e) {
      setState(() => _error = e.code == 'CameraAccessDenied'
          ? 'Accès à la caméra refusé : autorisez-le dans les réglages du téléphone'
          : 'Caméra indisponible : ${e.description ?? e.code}');
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state != AppLifecycleState.resumed && _running) {
      setState(() => _running = false);   // no sending in the background
    }
  }

  @override
  void dispose() {
    _running = false;
    _statusTimer?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    _cam?.dispose();
    super.dispose();
  }

  bool get _piCamera => piCameraActive(_status);

  Future<void> _toggle() async {
    if (_running) {
      setState(() => _running = false);
      return;
    }
    setState(() {
      _running = true;
      _sent = 0;
      _rate = 0;
      _error = null;
      _startedAt = DateTime.now();
    });
    _loop();
  }

  Future<void> _pollStatus() async {
    try {
      final st = await widget.api.liveStatus();
      if (!mounted) return;
      setState(() {
        _status = st;
        if (piCameraActive(st)) _running = false;   // the Pi camera has priority
      });
    } catch (_) {}
  }

  Future<void> _loop() async {
    if (_busy) return;
    _busy = true;
    try {
      while (_running && mounted && _cam != null) {
        final t0 = DateTime.now();
        try {
          final shot = await _cam!.takePicture();
          final bytes = await shot.readAsBytes();
          await deleteTempFile(shot.path);   // nothing stays on the phone
          await widget.api.sendFrame(bytes);
          final el = DateTime.now().difference(_startedAt!).inMilliseconds / 1000;
          if (mounted) {
            setState(() {
              _sent++;
              _rate = el > 0 ? _sent / el : 0;
              _error = null;
            });
          }
        } catch (e) {
          if (mounted) setState(() => _error = e.toString());
          if (e is ApiException && e.message.contains('caméra du boîtier fonctionne')) {
            await _pollStatus();   // 409: stop, the screen explains why
            if (mounted) setState(() => _running = false);
          }
          await Future.delayed(const Duration(seconds: 1));
        }
        final wait = _period - DateTime.now().difference(t0);
        if (wait > Duration.zero) await Future.delayed(wait);
      }
    } finally {
      _busy = false;
    }
  }

  @override
  Widget build(BuildContext context) {
    final cam = _cam;
    return Scaffold(
      appBar: AppBar(title: const Text('Caméra test')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Text(
            'Mode test : ce téléphone remplace la caméra du boîtier. Les images sont analysées '
            'en direct par le boîtier puis oubliées ; aucune n\'est enregistrée. Posez le téléphone '
            'fixe, face à la classe, écran allumé.',
          ),
          const SizedBox(height: 12),
          if (cam != null && cam.value.isInitialized)
            ClipRRect(
              borderRadius: BorderRadius.circular(8),
              child: AspectRatio(
                aspectRatio: 1 / cam.value.aspectRatio,
                child: Stack(fit: StackFit.expand, children: [
                  CameraPreview(cam),
                  if (_running) CustomPaint(painter: DetectionPainter(_status)),
                ]),
              ),
            )
          else if (_error == null)
            const Padding(padding: EdgeInsets.all(32), child: Center(child: CircularProgressIndicator())),
          const SizedBox(height: 12),
          if (_piCamera)
            Card(
              color: Theme.of(context).colorScheme.secondaryContainer,
              child: const Padding(
                padding: EdgeInsets.all(12),
                child: Text('La caméra du boîtier fonctionne : seule elle est utilisée, '
                    'le téléphone n\'envoie rien.'),
              ),
            ),
          FilledButton.icon(
            onPressed: cam == null || _piCamera ? null : _toggle,
            icon: Icon(_running ? Icons.stop : Icons.videocam),
            label: Text(_running ? 'Arrêter l\'envoi' : 'Envoyer au boîtier'),
          ),
          const SizedBox(height: 12),
          if (_running || _sent > 0)
            Text('Images envoyées : $_sent  ·  ${_rate.toStringAsFixed(1)} image/s',
                semanticsLabel: 'Images envoyées : $_sent'),
          if (_running) ...[
            const SizedBox(height: 8),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [for (final l in describeStatus(_status)) Text(l)],
                ),
              ),
            ),
            const Text(
              "Une alerte n'apparaît que si la règle atteint son total (ex. 10/10 images avec un "
              "téléphone près de la main). Le téléphone détecté doit être dans l'image, tenu par "
              "quelqu'un face à la caméra.",
              style: TextStyle(fontSize: 12),
            ),
          ],
          if (_error != null) ...[
            const SizedBox(height: 8),
            Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
          ],
          if (kIsWeb) ...[
            const SizedBox(height: 8),
            const Text('Navigateur : la caméra n\'est accessible qu\'en HTTPS ou sur localhost.'),
          ],
        ],
      ),
    );
  }
}
