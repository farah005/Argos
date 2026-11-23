import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import '../models.dart';

class ApiException implements Exception {
  final String message;
  ApiException(this.message);
  @override
  String toString() => message;
}

/// REST client for the Argos backend (FastAPI).
class Api {
  String baseUrl;
  String? token;
  Map<String, dynamic>? user;

  Api({required this.baseUrl, this.token, this.user});

  bool get isLoggedIn => token != null;
  String get displayName => (user?['full_name'] as String?)?.isNotEmpty == true
      ? user!['full_name'] as String
      : (user?['username'] as String? ?? '');

  static Future<Api> restore(String defaultUrl) async {
    final p = await SharedPreferences.getInstance();
    final u = p.getString('user');
    return Api(
      baseUrl: p.getString('baseUrl') ?? defaultUrl,
      token: p.getString('token'),
      user: u != null ? jsonDecode(u) as Map<String, dynamic> : null,
    );
  }

  Map<String, String> get _auth => {'Authorization': 'Bearer $token'};

  Uri _u(String path, [Map<String, String>? q]) =>
      Uri.parse('$baseUrl$path').replace(queryParameters: q);

  Future<void> login(String url, String username, String password) async {
    baseUrl = url.trim().replaceAll(RegExp(r'/+$'), '');
    final http.Response r;
    try {
      r = await http
          .post(_u('/api/auth/login'), body: {'username': username, 'password': password})
          .timeout(const Duration(seconds: 10));
    } catch (_) {
      throw ApiException('Serveur injoignable : vérifiez l\'adresse et le réseau Wi-Fi de l\'école');
    }
    if (r.statusCode != 200) throw ApiException(_detail(r));
    final j = jsonDecode(utf8.decode(r.bodyBytes)) as Map<String, dynamic>;
    token = j['access_token'] as String;
    user = j['user'] as Map<String, dynamic>;
    final p = await SharedPreferences.getInstance();
    await p.setString('baseUrl', baseUrl);
    await p.setString('token', token!);
    await p.setString('user', jsonEncode(user));
  }

  /// Registers a teacher (firstname.lastname@enit.utm.tn), then logs in automatically.
  Future<void> register(String url, String email, String password) async {
    final base = url.trim().replaceAll(RegExp(r'/+$'), '');
    final http.Response r;
    try {
      r = await http
          .post(Uri.parse('$base/api/auth/register'),
              headers: {'Content-Type': 'application/json'},
              body: jsonEncode({'email': email.trim(), 'password': password}))
          .timeout(const Duration(seconds: 10));
    } catch (_) {
      throw ApiException('Serveur injoignable : vérifiez l\'adresse et le réseau Wi-Fi de l\'école');
    }
    if (r.statusCode != 201) throw ApiException(_detail(r));
    await login(base, email.trim(), password);
  }

  /// Classrooms assigned to the logged-in user (empty until the admin assigns one).
  Future<List<String>> myClassrooms() async {
    final j = await _get('/api/me') as Map<String, dynamic>;
    return [for (final c in (j['classrooms'] as List? ?? [])) (c as Map<String, dynamic>)['name'] as String];
  }

  Future<void> logout() async {
    token = null;
    user = null;
    final p = await SharedPreferences.getInstance();
    await p.remove('token');
    await p.remove('user');
  }

  Future<dynamic> _get(String path, [Map<String, String>? q]) async {
    final r = await http.get(_u(path, q), headers: _auth).timeout(const Duration(seconds: 15));
    if (r.statusCode == 401) throw ApiException('Session expirée, reconnectez-vous');
    if (r.statusCode != 200) throw ApiException(_detail(r));
    return jsonDecode(utf8.decode(r.bodyBytes));
  }

  Future<List<AlertEvent>> events({String? status}) async {
    final j = await _get('/api/events', {if (status != null) 'status': status, 'limit': '200'});
    return (j as List).map((e) => AlertEvent.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<List<Map<String, dynamic>>> summary({double hours = 24}) async {
    final j = await _get('/api/summary', {'hours': '$hours'});
    return (j as List).cast<Map<String, dynamic>>();
  }

  Future<AlertEvent> review(int id, String decision) async {
    final r = await http
        .post(_u('/api/events/$id/review'),
            headers: {..._auth, 'Content-Type': 'application/json'},
            body: jsonEncode({'decision': decision}))
        .timeout(const Duration(seconds: 15));
    if (r.statusCode != 200) throw ApiException(_detail(r));
    return AlertEvent.fromJson(jsonDecode(utf8.decode(r.bodyBytes)) as Map<String, dynamic>);
  }

  /// Test mode: sends a JPEG photo to the device of the teacher's classroom.
  Future<int> sendFrame(List<int> jpeg) async {
    final r = await http
        .post(_u('/api/camera/frame'), headers: {..._auth, 'Content-Type': 'image/jpeg'}, body: jpeg)
        .timeout(const Duration(seconds: 5));
    if (r.statusCode == 401) throw ApiException('Session expirée, reconnectez-vous');
    if (r.statusCode != 200) throw ApiException(_detail(r));
    return (jsonDecode(utf8.decode(r.bodyBytes)) as Map<String, dynamic>)['seq'] as int;
  }

  /// Test mode: what the device sees right now (null if nothing recent).
  Future<Map<String, dynamic>?> liveStatus() async {
    final r = await http.get(_u('/api/camera/status'), headers: _auth).timeout(const Duration(seconds: 5));
    if (r.statusCode == 204) return null;
    if (r.statusCode != 200) throw ApiException(_detail(r));
    return jsonDecode(utf8.decode(r.bodyBytes)) as Map<String, dynamic>;
  }

  /// The web video player cannot send headers: the token goes in the URL.
  String clipUrl(int id) => '$baseUrl/api/events/$id/clip?t=$token';

  Uri wsUri() {
    final b = Uri.parse(baseUrl);
    return b.replace(
      scheme: b.scheme == 'https' ? 'wss' : 'ws',
      path: '/ws',
      queryParameters: {'token': token!},
    );
  }

  static String _detail(http.Response r) {
    try {
      final d = jsonDecode(utf8.decode(r.bodyBytes))['detail'];
      if (d is String) return d;
    } catch (_) {}
    return 'Erreur ${r.statusCode}';
  }
}
