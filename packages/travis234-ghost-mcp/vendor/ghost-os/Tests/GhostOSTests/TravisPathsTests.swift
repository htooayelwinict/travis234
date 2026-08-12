// Added by Travis234 for the bundled Ghost MCP package.

import Foundation
import Testing
@testable import GhostOS

@Suite("Travis234 path boundaries")
struct TravisPathsTests {
    private let paths = TravisPaths(
        homeDirectory: URL(fileURLWithPath: "/Users/tester", isDirectory: true),
        executableURL: URL(fileURLWithPath: "/payload/travis234_ghost_mcp/bin/ghost")
    )

    @Test("all mutable paths stay under the Travis234 state root")
    func mutablePathsStayUnderStateRoot() {
        #expect(paths.stateRoot.path == "/Users/tester/.travis234/ghost-mcp")

        for path in [
            paths.recipesDirectory,
            paths.logsDirectory,
            paths.visionEnvironment,
            paths.visionModelDirectory,
        ] {
            #expect(path.path.hasPrefix(paths.stateRoot.path + "/"))
        }
    }

    @Test("resources resolve only from the installed package")
    func resourcesResolveFromPackage() {
        #expect(paths.packageRoot.path == "/payload/travis234_ghost_mcp")
        #expect(paths.instructionsFile.path == "/payload/travis234_ghost_mcp/assets/GHOST-MCP.md")
        #expect(paths.visionSidecarDirectory.path == "/payload/travis234_ghost_mcp/assets/vision-sidecar")
    }

    @Test("process HOME defines the user root for isolated launches")
    func processHomeDefinesUserRoot() {
        let resolved = TravisPaths.resolveHomeDirectory(
            environment: ["HOME": "/isolated/home"],
            fallback: URL(fileURLWithPath: "/Users/real", isDirectory: true)
        )
        let fallback = TravisPaths.resolveHomeDirectory(
            environment: ["HOME": "relative/home"],
            fallback: URL(fileURLWithPath: "/Users/real", isDirectory: true)
        )

        #expect(resolved.path == "/isolated/home")
        #expect(fallback.path == "/Users/real")
    }
}
