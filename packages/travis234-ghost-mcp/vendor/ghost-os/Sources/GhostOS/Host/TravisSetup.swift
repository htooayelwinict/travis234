// Added by Travis234 for the bundled Ghost MCP package.

import AppKit
import ApplicationServices
import Foundation

public struct PermissionStatus: Sendable {
    public let accessibilityGranted: Bool
    public let screenRecordingGranted: Bool
    public let inputMonitoringGranted: Bool

    public init(
        accessibilityGranted: Bool,
        screenRecordingGranted: Bool,
        inputMonitoringGranted: Bool
    ) {
        self.accessibilityGranted = accessibilityGranted
        self.screenRecordingGranted = screenRecordingGranted
        self.inputMonitoringGranted = inputMonitoringGranted
    }

    public static let allGranted = PermissionStatus(
        accessibilityGranted: true,
        screenRecordingGranted: true,
        inputMonitoringGranted: true
    )

    public static func current() -> PermissionStatus {
        let mask: CGEventMask = 1 << CGEventType.keyDown.rawValue
        let tap = CGEvent.tapCreate(
            tap: .cgSessionEventTap,
            place: .headInsertEventTap,
            options: .listenOnly,
            eventsOfInterest: mask,
            callback: { _, _, event, _ in Unmanaged.passUnretained(event) },
            userInfo: nil
        )
        if let tap {
            CGEvent.tapEnable(tap: tap, enable: false)
            CFMachPortInvalidate(tap)
        }

        return PermissionStatus(
            accessibilityGranted: AXIsProcessTrusted(),
            screenRecordingGranted: ScreenCapture.hasPermission(),
            inputMonitoringGranted: tap != nil
        )
    }
}

public struct SetupReport: Codable, Sendable {
    public let accessibilityGranted: Bool
    public let screenRecordingGranted: Bool
    public let inputMonitoringGranted: Bool
    public let recipesInstalled: Int
    public let visionReady: Bool
    public let restartRequired: Bool
}

public protocol SetupFileSystem: AnyObject {
    func createDirectory(at url: URL) throws
    func contentsOfDirectory(at url: URL) throws -> [URL]
    func fileExists(at url: URL) -> Bool
    func copyItem(at source: URL, to destination: URL) throws
}

public final class LocalSetupFileSystem: SetupFileSystem {
    private let fileManager = FileManager.default

    public init() {}

    public func createDirectory(at url: URL) throws {
        try fileManager.createDirectory(at: url, withIntermediateDirectories: true)
    }

    public func contentsOfDirectory(at url: URL) throws -> [URL] {
        try fileManager.contentsOfDirectory(
            at: url,
            includingPropertiesForKeys: nil,
            options: [.skipsHiddenFiles]
        )
    }

    public func fileExists(at url: URL) -> Bool {
        fileManager.fileExists(atPath: url.path)
    }

    public func copyItem(at source: URL, to destination: URL) throws {
        try fileManager.copyItem(at: source, to: destination)
    }
}

public protocol PermissionPrompting: AnyObject {
    func requestMissing(_ permissions: PermissionStatus)
}

public final class SystemPermissionPrompter: PermissionPrompting {
    public init() {}

    public func requestMissing(_ permissions: PermissionStatus) {
        if !permissions.accessibilityGranted {
            let options = ["AXTrustedCheckOptionPrompt": true]
            _ = AXIsProcessTrustedWithOptions(options as CFDictionary)
            open("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")
        }
        if !permissions.screenRecordingGranted {
            ScreenCapture.requestPermission()
            open("x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")
        }
        if !permissions.inputMonitoringGranted {
            open("x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent")
        }
    }

    private func open(_ value: String) {
        guard let url = URL(string: value) else { return }
        NSWorkspace.shared.open(url)
    }
}

public protocol VisionInstalling: AnyObject {
    func install(paths: TravisPaths) throws
}

public enum VisionInstallError: LocalizedError {
    case pythonUnavailable
    case processTimedOut(String)
    case processFailed(String, Int32)
    case installationFailed(String)

    public var errorDescription: String? {
        switch self {
        case .pythonUnavailable:
            "Python 3.10 or newer is required for optional Ghost vision"
        case let .processTimedOut(label):
            "\(label) exceeded its setup deadline"
        case let .processFailed(label, status):
            "\(label) failed with exit status \(status)"
        case let .installationFailed(message):
            "vision installation failed: \(message)"
        }
    }
}

public final class BoundedVisionInstaller: VisionInstalling {
    private let fileSystem: any SetupFileSystem

    public init(fileSystem: any SetupFileSystem = LocalSetupFileSystem()) {
        self.fileSystem = fileSystem
    }

    public func install(paths: TravisPaths) throws {
        guard let python = findPython() else { throw VisionInstallError.pythonUnavailable }

        try fileSystem.createDirectory(at: paths.stateRoot)
        try run(
            executable: python,
            arguments: ["-m", "venv", paths.visionEnvironment.path],
            timeout: 120,
            label: "vision environment creation"
        )

        let pip = paths.visionEnvironment.appending(path: "bin/pip").path
        try run(
            executable: pip,
            arguments: [
                "install", "--disable-pip-version-check", "--no-input", "--no-deps",
                "mlx-vlm==0.1.15",
            ],
            timeout: 300,
            label: "mlx-vlm installation"
        )
        try run(
            executable: pip,
            arguments: [
                "install", "--disable-pip-version-check", "--no-input",
                "-r", paths.visionSidecarDirectory.appending(path: "requirements.txt").path,
                "huggingface-hub",
            ],
            timeout: 600,
            label: "vision dependency installation"
        )

        try fileSystem.createDirectory(at: paths.visionModelDirectory)
        let downloader = paths.visionEnvironment.appending(path: "bin/huggingface-cli").path
        try run(
            executable: downloader,
            arguments: [
                "download", "mlx-community/ShowUI-2B-bf16-8bit",
                "--local-dir", paths.visionModelDirectory.path,
            ],
            timeout: 1800,
            label: "vision model download"
        )
    }

    private func findPython() -> String? {
        let candidates = [
            "/opt/homebrew/bin/python3",
            "/opt/homebrew/bin/python3.13",
            "/opt/homebrew/bin/python3.12",
            "/opt/homebrew/bin/python3.11",
            "/opt/homebrew/bin/python3.10",
            "/usr/local/bin/python3",
            "/usr/bin/python3",
        ]
        return candidates.first(where: FileManager.default.isExecutableFile(atPath:))
    }

    private func run(
        executable: String,
        arguments: [String],
        timeout: TimeInterval,
        label: String
    ) throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try process.run()

        let deadline = Date().addingTimeInterval(timeout)
        while process.isRunning && Date() < deadline {
            RunLoop.current.run(until: Date().addingTimeInterval(0.1))
        }
        if process.isRunning {
            process.terminate()
            process.waitUntilExit()
            throw VisionInstallError.processTimedOut(label)
        }
        guard process.terminationStatus == 0 else {
            throw VisionInstallError.processFailed(label, process.terminationStatus)
        }
    }
}

public struct TravisSetup {
    private let paths: TravisPaths
    private let fileSystem: any SetupFileSystem
    private let permissions: PermissionStatus
    private let permissionPrompter: any PermissionPrompting
    private let visionInstaller: any VisionInstalling

    public init(
        paths: TravisPaths = TravisPaths(),
        fileSystem: any SetupFileSystem = LocalSetupFileSystem(),
        permissions: PermissionStatus = .current(),
        permissionPrompter: any PermissionPrompting = SystemPermissionPrompter(),
        visionInstaller: (any VisionInstalling)? = nil
    ) {
        self.paths = paths
        self.fileSystem = fileSystem
        self.permissions = permissions
        self.permissionPrompter = permissionPrompter
        self.visionInstaller = visionInstaller ?? BoundedVisionInstaller(fileSystem: fileSystem)
    }

    public func run(includeVision: Bool) throws -> SetupReport {
        permissionPrompter.requestMissing(permissions)
        try fileSystem.createDirectory(at: paths.recipesDirectory)

        let bundledRecipes = paths.packageRoot.appending(path: "assets/recipes", directoryHint: .isDirectory)
        let sources = try fileSystem.contentsOfDirectory(at: bundledRecipes)
            .filter { $0.pathExtension == "json" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
        var installed = 0
        for source in sources {
            let destination = paths.recipesDirectory.appending(path: source.lastPathComponent)
            guard !fileSystem.fileExists(at: destination) else { continue }
            try fileSystem.copyItem(at: source, to: destination)
            installed += 1
        }

        if includeVision {
            do {
                try visionInstaller.install(paths: paths)
            } catch let error as VisionInstallError {
                throw error
            } catch {
                let message = String(error.localizedDescription.prefix(500))
                    .replacingOccurrences(of: "\n", with: " ")
                throw VisionInstallError.installationFailed(message)
            }
        }

        let visionReady = fileSystem.fileExists(
            at: paths.visionModelDirectory.appending(path: "model.safetensors")
        )
        let restartRequired = !permissions.accessibilityGranted
            || !permissions.screenRecordingGranted
            || !permissions.inputMonitoringGranted
        return SetupReport(
            accessibilityGranted: permissions.accessibilityGranted,
            screenRecordingGranted: permissions.screenRecordingGranted,
            inputMonitoringGranted: permissions.inputMonitoringGranted,
            recipesInstalled: installed,
            visionReady: visionReady,
            restartRequired: restartRequired
        )
    }
}
