// Modified by Travis234 from Ghost OS revision 991aa4831295aaff6beef04cc809d0f0b53dc024.
// Travis234 bundled Ghost MCP command entry point.

import AppKit
import Foundation
import GhostOS

_ = CGMainDisplayID()

let arguments = Array(CommandLine.arguments.dropFirst())
let command = arguments.first ?? "help"
let remaining = Array(arguments.dropFirst())

switch command {
case "mcp":
    guard remaining.isEmpty else { failUnknownFlags(command: command, flags: remaining) }
    MCPServer().run()

case "setup":
    runSetup(arguments: remaining)

case "doctor":
    runDoctor(arguments: remaining)

case "version", "--version", "-v":
    guard remaining.isEmpty else { failUnknownFlags(command: command, flags: remaining) }
    print(GhostCLIText.versionLine)

case "help", "--help", "-h":
    guard remaining.isEmpty else { failUnknownFlags(command: command, flags: remaining) }
    printUsage()

default:
    fputs("Unknown Ghost MCP command: \(command)\n", stderr)
    printUsage()
    exit(1)
}

func runSetup(arguments: [String]) {
    let allowed = Set(["--vision"])
    guard arguments.allSatisfy(allowed.contains), Set(arguments).count == arguments.count else {
        failUnknownFlags(command: "setup", flags: arguments)
    }

    do {
        let report = try TravisSetup().run(includeVision: arguments.contains("--vision"))
        print("Travis234 Ghost MCP setup")
        print("  State: \(TravisPaths().stateRoot.path)")
        print("  Accessibility: \(word(report.accessibilityGranted))")
        print("  Screen Recording: \(word(report.screenRecordingGranted))")
        print("  Input Monitoring: \(word(report.inputMonitoringGranted))")
        print("  Recipes installed: \(report.recipesInstalled)")
        print("  Vision: \(report.visionReady ? "ready" : "not installed (optional)")")
        if report.restartRequired {
            print("  Restart Travis234 after granting missing permissions.")
            exit(2)
        }
    } catch let error as VisionInstallError {
        fputs("Ghost vision setup failed: \(error.localizedDescription)\n", stderr)
        exit(4)
    } catch {
        fputs("Ghost package setup failed: \(bounded(error.localizedDescription))\n", stderr)
        exit(3)
    }
}

func runDoctor(arguments: [String]) {
    guard arguments.isEmpty || arguments == ["--json"] else {
        failUnknownFlags(command: "doctor", flags: arguments)
    }

    let paths = TravisPaths()
    guard FileManager.default.fileExists(atPath: paths.instructionsFile.path),
          FileManager.default.fileExists(
              atPath: paths.visionSidecarDirectory.appending(path: "server.py").path
          )
    else {
        fputs("Ghost package resources are incomplete. Reinstall travis234-ghost-mcp.\n", stderr)
        exit(3)
    }

    let report = TravisDoctor(paths: paths).run()
    if arguments == ["--json"] {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        guard let data = try? encoder.encode(report), let json = String(data: data, encoding: .utf8) else {
            fputs("Could not encode Ghost doctor report.\n", stderr)
            exit(3)
        }
        print(json)
    } else {
        print("Travis234 Ghost MCP doctor")
        print("  Version: \(report.version)")
        print("  Executable: \(report.executable)")
        print("  Architecture: \(report.architecture)")
        print("  State: \(report.stateRoot)")
        print("  Accessibility: \(word(report.accessibilityGranted))")
        print("  Screen Recording: \(word(report.screenRecordingGranted))")
        print("  Input Monitoring: \(word(report.inputMonitoringGranted))")
        print("  Recipes: \(report.recipeCount)")
        print("  Vision: \(report.visionReady ? "ready" : "not installed (optional)")")
    }

    if !report.accessibilityGranted
        || !report.screenRecordingGranted
        || !report.inputMonitoringGranted
    {
        exit(2)
    }
}

func failUnknownFlags(command: String, flags: [String]) -> Never {
    let rendered = bounded(flags.joined(separator: " "))
    fputs("Unsupported \(command) option: \(rendered)\n", stderr)
    exit(1)
}

func word(_ granted: Bool) -> String {
    granted ? "granted" : "not granted"
}

func bounded(_ value: String) -> String {
    String(value.prefix(500)).replacingOccurrences(of: "\n", with: " ")
}

func printUsage() {
    print("""
    Travis234 Ghost MCP \(GhostOS.version) - macOS computer-use server

    Usage: ghost <command>

    Commands:
      mcp              Start the bundled MCP server over stdio
      setup [--vision] Check permissions and install missing bundled recipes
      doctor [--json]  Inspect the installation without changing it
      version          Print the bundled Ghost version

    Travis234 registers this MCP server automatically; no MCP config file is needed.
    """)
}
