// Added by Travis234 for the bundled Ghost MCP package.

import Testing
@testable import GhostOS

@Suite("Bundled Ghost MCP catalog")
struct MCPToolsTests {
    @Test("catalog contains the pinned 29 tools with complete schemas")
    func catalogIsPinned() {
        let tools = MCPTools.definitions()
        #expect(tools.count == 29)

        let names = Set(tools.compactMap { $0["name"] as? String })
        for expected in [
            "ghost_context",
            "ghost_screenshot",
            "ghost_click",
            "ghost_type",
            "ghost_recipes",
            "ghost_learn_start",
        ] {
            #expect(names.contains(expected))
        }

        for tool in tools {
            let description = tool["description"] as? String
            let schema = tool["inputSchema"] as? [String: Any]
            #expect(description?.isEmpty == false)
            #expect(schema?["type"] as? String == "object")
            #expect(schema?["properties"] is [String: Any])
        }
    }
}
