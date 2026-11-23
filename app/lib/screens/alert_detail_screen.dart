import 'package:flutter/material.dart';
import 'package:video_player/video_player.dart';

import '../models.dart';
import '../services/api.dart';

class AlertDetailScreen extends StatefulWidget {
  final Api api;
  final AlertEvent event;
  const AlertDetailScreen({super.key, required this.api, required this.event});

  @override
  State<AlertDetailScreen> createState() => _AlertDetailScreenState();
}

class _AlertDetailScreenState extends State<AlertDetailScreen> {
  VideoPlayerController? _video;
  String? _videoError;
  late final AlertEvent _event = widget.event;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    if (_event.hasClip) _initVideo();
  }

  Future<void> _initVideo() async {
    final c = VideoPlayerController.networkUrl(Uri.parse(widget.api.clipUrl(_event.id)));
    try {
      await c.initialize();
      await c.setLooping(true);
      await c.play();
      if (mounted) setState(() => _video = c);
    } catch (e) {
      if (mounted) setState(() => _videoError = 'Clip illisible ou supprimé');
      c.dispose();
    }
  }

  @override
  void dispose() {
    _video?.dispose();
    super.dispose();
  }

  Future<void> _review(String decision) async {
    setState(() => _busy = true);
    try {
      final updated = await widget.api.review(_event.id, decision);
      if (!mounted) return;
      Navigator.of(context).pop(updated);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.toString())));
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final e = _event;
    return Scaffold(
      appBar: AppBar(title: Text(e.label)),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          AspectRatio(
            aspectRatio: _video?.value.aspectRatio ?? 16 / 9,
            child: Container(
              color: Colors.black,
              alignment: Alignment.center,
              child: _video != null
                  ? GestureDetector(
                      onTap: () {
                        _video!.value.isPlaying ? _video!.pause() : _video!.play();
                        setState(() {});
                      },
                      child: VideoPlayer(_video!),
                    )
                  : Text(
                      _videoError ?? (e.hasClip ? 'Chargement du clip…' : 'Pas de clip pour cette alerte'),
                      style: const TextStyle(color: Colors.white70),
                    ),
            ),
          ),
          if (_video != null) VideoProgressIndicator(_video!, allowScrubbing: true),
          const SizedBox(height: 16),
          _row('Classe', e.classroom),
          _row('Élève (piste de la séance)', '#${e.trackId ?? '?'} — encadré en orange dans le clip'),
          _row('Début', hhmm(e.startedAt)),
          _row('Détection', hhmm(e.detectedAt)),
          _row('Confiance', '${(e.confidence * 100).round()} % (score de tri, pas une probabilité)'),
          _row('Statut', statusLabel(e.status)),
          if (e.teacherFacingBoard) _row('Contexte', 'Enseignant face au tableau'),
          const SizedBox(height: 8),
          const Text(
            'Cette alerte signale un comportement possible. Vérifiez le clip avant de décider.',
            style: TextStyle(fontStyle: FontStyle.italic),
          ),
          const SizedBox(height: 16),
          if (e.status == 'pending')
            Row(children: [
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: _busy ? null : () => _review('dismissed'),
                  icon: const Icon(Icons.close),
                  label: const Text('Rejeter'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: FilledButton.icon(
                  onPressed: _busy ? null : () => _review('confirmed'),
                  icon: const Icon(Icons.check),
                  label: const Text('Confirmer'),
                ),
              ),
            ]),
        ],
      ),
    );
  }

  Widget _row(String k, String v) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          SizedBox(width: 170, child: Text(k, style: const TextStyle(fontWeight: FontWeight.w600))),
          Expanded(child: Text(v)),
        ]),
      );
}
