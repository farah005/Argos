import 'dart:io';

Future<void> deleteTempFile(String path) async {
  try {
    await File(path).delete();
  } catch (_) {}
}
