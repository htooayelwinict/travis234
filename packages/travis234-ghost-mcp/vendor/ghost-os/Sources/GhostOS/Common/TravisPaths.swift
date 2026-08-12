// Added by Travis234 for the bundled Ghost MCP package.

import Foundation

/// The sole owner of mutable state and installed-resource locations.
public struct TravisPaths: Sendable {
    public let homeDirectory: URL
    public let executableURL: URL

    public init(
        homeDirectory: URL = TravisPaths.resolveHomeDirectory(),
        executableURL: URL = URL(fileURLWithPath: CommandLine.arguments[0])
    ) {
        self.homeDirectory = homeDirectory.standardizedFileURL
        self.executableURL = executableURL.standardizedFileURL
    }

    public static func resolveHomeDirectory(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        fallback: URL = FileManager.default.homeDirectoryForCurrentUser
    ) -> URL {
        if let value = environment["HOME"],
           !value.isEmpty,
           NSString(string: value).isAbsolutePath
        {
            return URL(fileURLWithPath: value, isDirectory: true).standardizedFileURL
        }
        return fallback.standardizedFileURL
    }

    public var stateRoot: URL {
        homeDirectory.appending(path: ".travis234/ghost-mcp", directoryHint: .isDirectory)
    }

    public var recipesDirectory: URL {
        stateRoot.appending(path: "recipes", directoryHint: .isDirectory)
    }

    public var logsDirectory: URL {
        stateRoot.appending(path: "logs", directoryHint: .isDirectory)
    }

    public var visionEnvironment: URL {
        stateRoot.appending(path: "vision-venv", directoryHint: .isDirectory)
    }

    public var visionModelDirectory: URL {
        stateRoot.appending(path: "models/ShowUI-2B", directoryHint: .isDirectory)
    }

    public var packageRoot: URL {
        executableURL.deletingLastPathComponent().deletingLastPathComponent()
    }

    public var instructionsFile: URL {
        packageRoot.appending(path: "assets/GHOST-MCP.md")
    }

    public var visionSidecarDirectory: URL {
        packageRoot.appending(path: "assets/vision-sidecar", directoryHint: .isDirectory)
    }
}
