// Added by Travis234 for the bundled Ghost MCP package.

import Foundation
import Testing
@testable import GhostOS

@Suite("Travis234 Ghost setup contract")
struct SetupContractTests {
    @Test("version output uses the real bundled Ghost version")
    func versionOutputUsesRealVersion() {
        #expect(GhostCLIText.versionLine == "Travis234 Ghost MCP 2.2.1")
    }

    @Test("setup writes only below Travis state and never overwrites recipes")
    func setupWritesOnlyBelowTravisState() throws {
        let fileSystem = RecordingSetupFileSystem(existingNames: ["gmail-send.json"])
        let vision = RecordingVisionInstaller()
        let paths = TravisPaths(
            homeDirectory: URL(fileURLWithPath: "/home/test", isDirectory: true),
            executableURL: URL(fileURLWithPath: "/pkg/bin/ghost")
        )

        let report = try TravisSetup(
            paths: paths,
            fileSystem: fileSystem,
            permissions: .allGranted,
            visionInstaller: vision
        ).run(includeVision: false)

        #expect(fileSystem.writtenPaths.allSatisfy {
            $0.path.hasPrefix("/home/test/.travis234/ghost-mcp/")
        })
        #expect(fileSystem.writtenPaths.allSatisfy {
            !$0.path.contains(".claude") && !$0.path.contains("mcp.json")
        })
        #expect(fileSystem.copiedDestinations.map(\.lastPathComponent) == ["slack-send.json"])
        #expect(report.recipesInstalled == 1)
        #expect(vision.installCount == 0)
    }

    @Test("vision installation is explicit")
    func visionInstallationIsExplicit() throws {
        let fileSystem = RecordingSetupFileSystem(existingNames: [])
        let vision = RecordingVisionInstaller()
        let paths = TravisPaths(
            homeDirectory: URL(fileURLWithPath: "/home/test", isDirectory: true),
            executableURL: URL(fileURLWithPath: "/pkg/bin/ghost")
        )

        _ = try TravisSetup(
            paths: paths,
            fileSystem: fileSystem,
            permissions: .allGranted,
            visionInstaller: vision
        ).run(includeVision: true)

        #expect(vision.installCount == 1)
    }

    @Test("vision failures use the dedicated setup error")
    func visionFailuresAreTyped() {
        let fileSystem = RecordingSetupFileSystem(existingNames: [])
        let paths = TravisPaths(
            homeDirectory: URL(fileURLWithPath: "/home/test", isDirectory: true),
            executableURL: URL(fileURLWithPath: "/pkg/bin/ghost")
        )

        #expect(throws: VisionInstallError.self) {
            try TravisSetup(
                paths: paths,
                fileSystem: fileSystem,
                permissions: .allGranted,
                visionInstaller: FailingVisionInstaller()
            ).run(includeVision: true)
        }
    }

    @Test("doctor reports the canonical state root without mutation")
    func doctorIsReadOnly() {
        let fileSystem = RecordingSetupFileSystem(existingNames: [])
        let paths = TravisPaths(
            homeDirectory: URL(fileURLWithPath: "/home/test", isDirectory: true),
            executableURL: URL(fileURLWithPath: "/pkg/bin/ghost")
        )

        let report = TravisDoctor(
            paths: paths,
            fileSystem: fileSystem,
            permissions: .allGranted
        ).run()

        #expect(report.stateRoot == "/home/test/.travis234/ghost-mcp")
        #expect(fileSystem.writtenPaths.isEmpty)
    }
}

private final class RecordingSetupFileSystem: SetupFileSystem {
    let existingNames: Set<String>
    var writtenPaths: [URL] = []
    var copiedDestinations: [URL] = []

    init(existingNames: Set<String>) {
        self.existingNames = existingNames
    }

    func createDirectory(at url: URL) throws {
        writtenPaths.append(url)
    }

    func contentsOfDirectory(at url: URL) throws -> [URL] {
        if url.path.contains("/assets/recipes") {
            return [
                url.appending(path: "gmail-send.json"),
                url.appending(path: "slack-send.json"),
            ]
        }
        return existingNames.map { url.appending(path: $0) }
    }

    func fileExists(at url: URL) -> Bool {
        existingNames.contains(url.lastPathComponent)
    }

    func copyItem(at source: URL, to destination: URL) throws {
        writtenPaths.append(destination)
        copiedDestinations.append(destination)
    }
}

private final class RecordingVisionInstaller: VisionInstalling {
    var installCount = 0

    func install(paths: TravisPaths) throws {
        installCount += 1
    }
}

private final class FailingVisionInstaller: VisionInstalling {
    private struct Failure: Error {}

    func install(paths: TravisPaths) throws {
        throw Failure()
    }
}
