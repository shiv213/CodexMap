import AppKit
import SwiftUI

private struct CommandResult { let status: Int32; let output: String }

private func command(_ executable: String, _ arguments: [String]) async throws -> CommandResult {
    try await withCheckedThrowingContinuation { continuation in
        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: executable)
            process.arguments = arguments
            let pipe = Pipe()
            process.standardOutput = pipe
            process.standardError = pipe
            do {
                try process.run()
                DispatchQueue.global().asyncAfter(deadline: .now() + 120) {
                    if process.isRunning { process.terminate() }
                }
                let output = pipe.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                continuation.resume(returning: CommandResult(status: process.terminationStatus, output: String(decoding: output, as: UTF8.self)))
            } catch { continuation.resume(throwing: error) }
        }
    }
}

private enum SetupError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case let .message(message) = self { return message }; return nil }
}

@MainActor final class SetupModel: ObservableObject {
    enum Step { case welcome, python, working, tailscale, complete, failed }
    @Published var step: Step = .welcome
    @Published var privateAccess = false
    @Published var automaticUpdates = true
    @Published var detail = ""
    @Published var error = ""
    @Published var localURL = "http://127.0.0.1:4399/"
    @Published var remoteURL: String?
    @Published var tailscaleInstalled = false
    private var python: String?
    private var installed = false

    var dashboardURL: String { remoteURL ?? localURL }
    var hasInstalled: Bool { installed }
    private var payload: URL { Bundle.main.resourceURL!.appendingPathComponent("payload") }

    private func installerSource() -> URL {
        let current = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/CodexMap/current")
        let installedScript = current.appendingPathComponent("install.py")
        func version(at directory: URL) -> [Int]? {
            guard let text = try? String(contentsOf: directory.appendingPathComponent("VERSION"), encoding: .utf8) else { return nil }
            let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
            guard value.range(of: "^[0-9]+\\.[0-9]+\\.[0-9]+$", options: .regularExpression) != nil else { return nil }
            let components = value.split(separator: ".").compactMap { Int($0) }
            return components.count == 3 ? components : nil
        }
        // An old downloaded installer must not run obsolete service-management code.
        if FileManager.default.fileExists(atPath: installedScript.path),
           let installedVersion = version(at: current), let bundledVersion = version(at: payload),
           bundledVersion.lexicographicallyPrecedes(installedVersion) {
            return current
        }
        return payload
    }

    func install() {
        step = .working; detail = "Checking…"
        Task {
            do {
                python = await findPython()
                guard python != nil else { step = .python; return }
                detail = "Installing…"
                try await installDashboard(tailscale: false)
                installed = true
                if privateAccess { await prepareTailscale() } else { step = .complete }
            } catch { fail(error) }
        }
    }

    func retryPython() { install() }

    func downloadPython() {
        step = .working; detail = "Downloading Python…"
        Task {
            do {
                try await downloadPackage(URL(string: "https://www.python.org/ftp/python/3.13.9/python-3.13.9-macos11.pkg")!, signer: "Developer ID Installer: Python Software Foundation (BMM5U3QVKW)")
                detail = "Finish installing Python, then check again."
                step = .python
            } catch { fail(error) }
        }
    }

    func downloadTailscale() {
        step = .working; detail = "Downloading Tailscale…"
        Task {
            do {
                let (data, response) = try await URLSession.shared.data(from: URL(string: "https://pkgs.tailscale.com/stable/?mode=json")!)
                guard (response as? HTTPURLResponse)?.statusCode == 200,
                      let metadata = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                      let packages = metadata["MacZips"] as? [String: String],
                      let filename = packages["universal-package"],
                      filename.range(of: "^Tailscale-[0-9.]+-macos\\.pkg$", options: .regularExpression) != nil else {
                    throw SetupError.message("Could not find the Tailscale download. Retry.")
                }
                try await downloadPackage(URL(string: "https://pkgs.tailscale.com/stable/\(filename)")!, signer: "Developer ID Installer: Tailscale Inc. (W5364U7YZB)")
                detail = "Finish installing Tailscale, then check again."
                step = .tailscale
            } catch { fail(error) }
        }
    }

    func checkTailscale() {
        step = .working; detail = "Checking Tailscale…"
        Task { await prepareTailscale() }
    }

    func openTailscale() { NSWorkspace.shared.open(URL(fileURLWithPath: "/Applications/Tailscale.app")) }
    func useLocalOnly() { privateAccess = false; remoteURL = nil; step = .complete }
    func openDashboard() { if let url = URL(string: dashboardURL) { NSWorkspace.shared.open(url) } }
    func copyURL() { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(dashboardURL, forType: .string) }
    func retry() { if installed && privateAccess { checkTailscale() } else { install() } }

    private func fail(_ failure: Error) { error = failure.localizedDescription; step = .failed }

    private func findPython() async -> String? {
        var candidates = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3"]
        for version in ["3.14", "3.13", "3.12", "3.11", "3.10", "3.9"] {
            candidates.append("/Library/Frameworks/Python.framework/Versions/\(version)/bin/python3")
        }
        candidates.append("/usr/bin/python3")
        for candidate in candidates where FileManager.default.isExecutableFile(atPath: candidate) {
            // Skip the developer-tools stub.
            if candidate == "/usr/bin/python3" {
                guard let developer = try? await command("/usr/bin/xcode-select", ["-p"]), developer.status == 0 else { continue }
            }
            if let result = try? await command(candidate, ["-c", "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"]), result.status == 0 { return candidate }
        }
        return nil
    }

    private func installDashboard(tailscale: Bool) async throws {
        guard let python else { throw SetupError.message("Install Python 3.9+, then retry.") }
        var arguments = [installerSource().appendingPathComponent("install.py").path, "--json"]
        if tailscale { arguments.append("--tailscale") }
        if !automaticUpdates { arguments.append("--no-auto-update") }
        var result = try await command(python, arguments)
        // The login updater may still hold the install lock during private setup.
        for _ in 0..<30 {
            guard result.status != 0, result.output.contains("Installation or update already running.") else { break }
            detail = "Waiting for the current update…"
            try await Task.sleep(nanoseconds: 1_000_000_000)
            result = try await command(python, arguments)
        }
        guard result.status == 0 else {
            let output = result.output.trimmingCharacters(in: .whitespacesAndNewlines)
            let message = output.components(separatedBy: "Setup failed: ").last ?? output
            throw SetupError.message(message.isEmpty ? "Setup did not finish. Retry, or check ~/Library/Logs/CodexMap." : String(message.prefix(800)))
        }
        guard let line = result.output.split(separator: "\n").last(where: { $0.hasPrefix("{") }),
              let data = String(line).data(using: .utf8),
              let resultJSON = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let local = resultJSON["localUrl"] as? String else {
            throw SetupError.message("No dashboard link returned. Check ~/Library/Logs/CodexMap.")
        }
        localURL = local; remoteURL = resultJSON["remoteUrl"] as? String
    }

    private func tailscaleBinary() -> String? {
        ["/Applications/Tailscale.app/Contents/MacOS/Tailscale", "/opt/homebrew/bin/tailscale", "/usr/local/bin/tailscale"].first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    private func prepareTailscale() async {
        tailscaleInstalled = tailscaleBinary() != nil
        guard let binary = tailscaleBinary() else { detail = "Install Tailscale for access from other devices."; step = .tailscale; return }
        do {
            let result = try await command(binary, ["status", "--json"])
            // Skip CLI warnings before the JSON.
            let jsonStart = result.output.firstIndex(of: "{")
            let jsonEnd = result.output.lastIndex(of: "}")
            let json = jsonStart.flatMap { start in jsonEnd.map { String(result.output[start...$0]) } }
            if result.status == 0, let data = json?.data(using: .utf8),
               let status = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               status["BackendState"] as? String == "Running" {
                detail = "Setting up Tailscale…"
                try await installDashboard(tailscale: true)
                step = .complete
            } else {
                detail = "Open Tailscale, approve VPN access, and sign in."
                step = .tailscale
                if FileManager.default.fileExists(atPath: "/Applications/Tailscale.app") { openTailscale() }
            }
        } catch { fail(error) }
    }

    private func downloadPackage(_ url: URL, signer: String) async throws {
        let (temporary, response) = try await URLSession.shared.download(from: url)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw SetupError.message("Download failed. Retry.") }
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("CodexMap-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let destination = directory.appendingPathComponent(url.lastPathComponent)
        try FileManager.default.moveItem(at: temporary, to: destination)
        let signature = try await command("/usr/sbin/pkgutil", ["--check-signature", destination.path])
        let trusted = signature.output.contains("Status: signed by a developer certificate issued by Apple for distribution") || signature.output.contains("Status: signed by a certificate trusted by")
        guard signature.status == 0, trusted, signature.output.contains(signer), signature.output.contains("Notarization: trusted by the Apple notary service") else {
            try? FileManager.default.removeItem(at: directory)
            throw SetupError.message("Package signature check failed.")
        }
        guard NSWorkspace.shared.open(destination) else { throw SetupError.message("Open the package at \(destination.path).") }
    }
}

struct MapMark: View {
    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 19).fill(Color.primary.opacity(0.05))
            Path { path in path.move(to: CGPoint(x: 41, y: 14)); path.addLine(to: CGPoint(x: 41, y: 68)); path.move(to: CGPoint(x: 14, y: 41)); path.addLine(to: CGPoint(x: 68, y: 41)) }.stroke(Color.primary.opacity(0.15), lineWidth: 1)
            Circle().fill(Color.primary).frame(width: 13, height: 13).offset(x: -17, y: -16)
            Circle().fill(Color.primary.opacity(0.4)).frame(width: 9, height: 9).offset(x: 17, y: 13)
            RoundedRectangle(cornerRadius: 3).fill(Color.primary.opacity(0.65)).frame(width: 11, height: 11).offset(x: 14, y: -17)
        }.frame(width: 82, height: 82)
    }
}

struct MapButtonStyle: ButtonStyle {
    @Environment(\.colorScheme) private var colorScheme
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.system(size: 13, weight: .medium))
            .padding(.horizontal, 18).padding(.vertical, 10)
            .foregroundStyle(colorScheme == .dark ? Color.black : Color.white)
            .background(colorScheme == .dark ? Color.white : Color.black)
            .clipShape(Capsule()).opacity(configuration.isPressed ? 0.75 : 1)
    }
}

struct SetupView: View {
    @StateObject private var model = SetupModel()
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 20) {
                MapMark()
                VStack(alignment: .leading, spacing: 5) {
                    Text("CodexMap").font(.system(size: 29, weight: .semibold))
                    Text("Your Codex chats, mapped.").font(.system(size: 14)).foregroundStyle(.secondary)
                }
            }.padding(.bottom, 30)
            content.frame(maxWidth: .infinity, alignment: .leading)
            Spacer(minLength: 24)
            Divider().padding(.bottom, 15)
            HStack {
                Spacer()
                Button(model.step == .complete ? "Done" : "Close") { NSApplication.shared.terminate(nil) }.keyboardShortcut(.cancelAction)
            }
        }
        .padding(32).frame(width: 550, height: 440)
        .background(Color(nsColor: .windowBackgroundColor))
        .tint(Color.primary)
    }

    @ViewBuilder private var content: some View {
        switch model.step {
        case .welcome:
            Text("Install on this Mac").font(.system(size: 18, weight: .medium)).padding(.bottom, 10)
            Text("Open Codex and start a chat first.").font(.system(size: 13)).foregroundStyle(.secondary).padding(.bottom, 21)
            Toggle("Access from other devices (Tailscale)", isOn: $model.privateAccess).toggleStyle(.checkbox).font(.system(size: 13)).padding(.bottom, 11)
            Toggle("Automatic updates", isOn: $model.automaticUpdates).toggleStyle(.checkbox).font(.system(size: 13))
            Text("From shiv213/CodexMap releases.").font(.system(size: 11)).foregroundStyle(.secondary).padding(.top, 6).padding(.bottom, 20)
            Button("Install", action: model.install).buttonStyle(MapButtonStyle())
        case .working:
            HStack(spacing: 14) { ProgressView().controlSize(.small); Text(model.detail).font(.system(size: 15)) }.padding(.top, 22)
        case .python:
            Text("Install Python").font(.system(size: 18, weight: .medium)).padding(.bottom, 10)
            Text(model.detail.hasPrefix("Finish") ? model.detail : "Python 3.9+ is required. Install it, then check again.").font(.system(size: 13)).foregroundStyle(.secondary).padding(.bottom, 20)
            HStack { Button("Download Python", action: model.downloadPython).buttonStyle(MapButtonStyle()); Button("Check again", action: model.retryPython) }
        case .tailscale:
            Text("Tailscale").font(.system(size: 18, weight: .medium)).padding(.bottom, 10)
            Text(model.detail).font(.system(size: 13)).foregroundStyle(.secondary).padding(.bottom, 20)
            if model.tailscaleInstalled {
                HStack { Button("Open Tailscale", action: model.openTailscale); Button("Finish setup", action: model.checkTailscale).buttonStyle(MapButtonStyle()) }
            } else {
                HStack { Button("Install Tailscale", action: model.downloadTailscale).buttonStyle(MapButtonStyle()); Button("Check again", action: model.checkTailscale) }
            }
            Button("Use this Mac only", action: model.useLocalOnly).buttonStyle(.link).font(.system(size: 12)).padding(.top, 15)
        case .complete:
            Label("Installed", systemImage: "checkmark.circle.fill").font(.system(size: 19, weight: .medium)).padding(.bottom, 12)
            Text(model.dashboardURL).font(.system(size: 13, design: .monospaced)).textSelection(.enabled).padding(12).frame(maxWidth: .infinity, alignment: .leading).background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 8)).padding(.bottom, 12)
            Text(model.remoteURL == nil ? "Starts when you log in." : "Use this link on your Tailscale devices. Keep this Mac awake.").font(.system(size: 12)).foregroundStyle(.secondary).padding(.bottom, 20)
            HStack { Button("Open map", action: model.openDashboard).buttonStyle(MapButtonStyle()); Button("Copy link", action: model.copyURL) }
        case .failed:
            Text("Setup failed").font(.system(size: 18, weight: .medium)).padding(.bottom, 10)
            ScrollView { Text(model.error).font(.system(size: 12)).foregroundStyle(.secondary).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }.frame(maxHeight: 120).padding(.bottom, 15)
            HStack { Button("Try again", action: model.retry).buttonStyle(MapButtonStyle()); if model.privateAccess && model.hasInstalled { Button("Use this Mac only", action: model.useLocalOnly) } }
        }
    }
}

@main struct CodexMapInstaller: App {
    var body: some Scene {
        WindowGroup("CodexMap") { SetupView() }
            .windowResizability(.contentSize)
            .commands { CommandGroup(replacing: .newItem) { }; CommandGroup(replacing: .help) { Button("CodexMap on GitHub") { NSWorkspace.shared.open(URL(string: "https://github.com/shiv213/CodexMap")!) } } }
    }
}
