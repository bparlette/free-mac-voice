// Transcribe WAV files with Apple's SpeechAnalyzer / SpeechTranscriber (macOS 26+). Reads file paths on stdin, prints JSON lines.
import Foundation
import Speech
import AVFoundation

@main struct Run {
  static func main() async {
    let locale = Locale(identifier: "en-US")
    let probe = SpeechTranscriber(locale: locale, preset: .transcription)
    do {
      if let req = try await AssetInventory.assetInstallationRequest(supporting: [probe]) { try await req.downloadAndInstall() }
    } catch { print("{\"error\": \"asset install failed: \(error)\"}"); return }
    while let path = readLine() {
      let t0 = Date(); var text = ""; var err = ""
      do {
        let transcriber = SpeechTranscriber(locale: locale, preset: .transcription)
        let analyzer = SpeechAnalyzer(modules: [transcriber])
        let file = try AVAudioFile(forReading: URL(fileURLWithPath: path))
        let collect = Task { () -> String in
          var s = ""
          for try await r in transcriber.results { s += String(r.text.characters) }
          return s
        }
        if let last = try await analyzer.analyzeSequence(from: file) { try await analyzer.finalizeAndFinish(through: last) }
        else { await analyzer.cancelAndFinishNow() }
        text = try await collect.value
      } catch { err = "\(error)" }
      let out: [String: Any] = ["path": path, "text": text, "ms": Date().timeIntervalSince(t0) * 1000, "error": err]
      print(String(data: try! JSONSerialization.data(withJSONObject: out), encoding: .utf8)!); fflush(stdout)
    }
  }
}
