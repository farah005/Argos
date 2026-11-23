// Data models exchanged with the Argos backend.

DateTime? parseUtc(dynamic v) {
  if (v == null) return null;
  var s = v.toString();
  // SQLite returns dates without a time zone: they are UTC dates
  final hasTz = s.endsWith('Z') || RegExp(r'[+-]\d\d:\d\d$').hasMatch(s);
  if (!hasTz) s = '${s}Z';
  return DateTime.tryParse(s)?.toLocal();
}

class AlertEvent {
  final int id;
  final String classroom;
  final String category;
  final double confidence;
  final String tier;
  final int? trackId;
  final DateTime? detectedAt;
  final DateTime? startedAt;
  final String status;
  final bool hasClip;
  final bool teacherFacingBoard;

  AlertEvent({
    required this.id,
    required this.classroom,
    required this.category,
    required this.confidence,
    required this.tier,
    required this.trackId,
    required this.detectedAt,
    required this.startedAt,
    required this.status,
    required this.hasClip,
    required this.teacherFacingBoard,
  });

  factory AlertEvent.fromJson(Map<String, dynamic> j) => AlertEvent(
        id: j['id'] as int,
        classroom: (j['classroom'] ?? '') as String,
        category: j['category'] as String,
        confidence: (j['confidence'] as num).toDouble(),
        tier: j['tier'] as String,
        trackId: j['track_id'] as int?,
        detectedAt: parseUtc(j['detected_at']),
        startedAt: parseUtc(j['started_at']),
        status: j['status'] as String,
        hasClip: (j['has_clip'] ?? false) as bool,
        teacherFacingBoard: (j['teacher_facing_board'] ?? false) as bool,
      );

  /// Neutral wording, as required by the report.
  String get label => categoryLabel(category);
}

String categoryLabel(String c) {
  switch (c) {
    case 'possible_phone_use':
      return 'Utilisation possible du téléphone';
    case 'persistent_head_down':
      return 'Tête baissée persistante';
    case 'possible_conflict':
      return 'Conflit physique possible';
    default:
      return c;
  }
}

String statusLabel(String s) {
  switch (s) {
    case 'pending':
      return 'À revoir';
    case 'confirmed':
      return 'Confirmée';
    case 'dismissed':
      return 'Rejetée';
    default:
      return s;
  }
}

String hhmm(DateTime? d) {
  if (d == null) return '--:--';
  String two(int n) => n.toString().padLeft(2, '0');
  return '${two(d.hour)}:${two(d.minute)}:${two(d.second)}';
}
