// Added by Travis234 for the bundled Ghost MCP package.

import Foundation

public enum GhostCLIText {
    public static var versionLine: String { "Travis234 Ghost MCP \(GhostOS.version)" }
}

public struct DoctorReport: Codable, Sendable {
    public let version: String
    public let executable: String
    public let architecture: String
    public let accessibilityGranted: Bool
    public let screenRecordingGranted: Bool
    public let inputMonitoringGranted: Bool
    public let recipeCount: Int
    public let visionReady: Bool
    public let stateRoot: String
}

public struct TravisDoctor {
    private let paths: TravisPaths
    private let fileSystem: any SetupFileSystem
    private let permissions: PermissionStatus

    public init(
        paths: TravisPaths = TravisPaths(),
        fileSystem: any SetupFileSystem = LocalSetupFileSystem(),
        permissions: PermissionStatus = .current()
    ) {
        self.paths = paths
        self.fileSystem = fileSystem
        self.permissions = permissions
    }

    public func run() -> DoctorReport {
        let recipes = (try? fileSystem.contentsOfDirectory(at: paths.recipesDirectory))?
            .filter { $0.pathExtension == "json" }.count ?? 0
        let visionReady = fileSystem.fileExists(
            at: paths.visionModelDirectory.appending(path: "model.safetensors")
        )

        return DoctorReport(
            version: GhostOS.version,
            executable: paths.executableURL.path,
            architecture: architecture,
            accessibilityGranted: permissions.accessibilityGranted,
            screenRecordingGranted: permissions.screenRecordingGranted,
            inputMonitoringGranted: permissions.inputMonitoringGranted,
            recipeCount: recipes,
            visionReady: visionReady,
            stateRoot: paths.stateRoot.path
        )
    }

    private var architecture: String {
        #if arch(arm64)
        "arm64"
        #elseif arch(x86_64)
        "x86_64"
        #else
        "unknown"
        #endif
    }
}
