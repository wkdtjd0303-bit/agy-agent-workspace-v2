#!/usr/bin/env python3
"""
🌐 Android MCP Server (Model Context Protocol Standard)
Implements official JSON-RPC 2.0 protocol specifications over stdio transport:
- 'initialize': protocolVersion "2024-11-05", serverInfo, capabilities
- 'tools/list': publishes formal JSON Schemas for all Android tools
- 'tools/call': executes actions/assertions via AndroidMCPToolLayer and returns MCP Content blocks
Enables external LLM Agents, AGY Agents, or Claude Desktop to discover and execute Android E2E tools directly.
"""

import sys
import json
import logging
from typing import Dict, Any, List, Optional

from android_mcp_tools import AndroidMCPToolLayer, get_android_mcp_tools

logger = logging.getLogger("AndroidMCPServer")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [AndroidMCPServer] %(message)s")


# Formal JSON Schema Catalog for every Android MCP Tool
ANDROID_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "launch_app",
        "description": "Launch an Android application by package name and optional main activity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "package_name": {"type": "string", "description": "Target Android package name (e.g. 'com.pdfflow.app')"},
                "main_activity": {"type": "string", "description": "Optional launchable activity (e.g. '.MainActivity')"}
            },
            "required": ["package_name"]
        }
    },
    {
        "name": "restart_app",
        "description": "Force stop and cold/warm restart the target application, asserting zero startup crashes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "package_name": {"type": "string", "description": "Target Android package name"},
                "main_activity": {"type": "string", "description": "Optional main activity"},
                "cold": {"type": "boolean", "default": True, "description": "Whether to perform a cold restart"}
            },
            "required": ["package_name"]
        }
    },
    {
        "name": "tap_element",
        "description": "Tap an interactive UI element by text, content-description, or resource-id with auto-recovery.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text_or_id": {"type": "string", "description": "Text, content-description, or resource-id to tap"},
                "timeout": {"type": "number", "default": 6.0, "description": "Timeout in seconds"},
                "max_retries": {"type": "integer", "default": 3, "description": "Maximum autonomous recovery attempts"}
            },
            "required": ["text_or_id"]
        }
    },
    {
        "name": "navigate_page",
        "description": "Navigate to a specific document or list page (e.g. Page 1) via semantic pager or gesture.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_page": {"type": "integer", "default": 1, "description": "Page number to navigate to"},
                "total_pages_hint": {"type": "integer", "default": 1, "description": "Total pages hint"},
                "max_retries": {"type": "integer", "default": 3, "description": "Maximum recovery attempts"}
            },
            "required": ["target_page"]
        }
    },
    {
        "name": "type_text",
        "description": "Inject text into the currently focused Android input field.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text string to inject"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "inject_asset",
        "description": "Push a user test asset (e.g. PDF file, Image) into emulator storage.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "host_file_path": {"type": "string", "description": "Absolute path to host file"},
                "target_device_path": {"type": "string", "default": "/sdcard/Download/", "description": "Device destination directory"}
            },
            "required": ["host_file_path"]
        }
    },
    {
        "name": "get_ui_tree",
        "description": "Dump and parse the current active UI hierarchy into semantic accessibility nodes.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "capture_screen",
        "description": "Capture genuine hardware framebuffer screenshot of the emulator display.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "default": "screen", "description": "Descriptive label for the screenshot"}
            }
        }
    },
    {
        "name": "inspect_screen_vision",
        "description": "Run real-time Gemini Vision multimodal analysis on current screen to identify icons or custom canvas.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "default": "화면에 보이는 주요 버튼 및 텍스트를 식별하라", "description": "Vision query prompt"}
            }
        }
    },
    {
        "name": "verify_screen",
        "description": "Assert that expected text or UI element exists in the current active screen.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "expected_text": {"type": "string", "description": "Expected text or content-description string"}
            },
            "required": ["expected_text"]
        }
    },
    {
        "name": "verify_file",
        "description": "Assert that a physical file exists on device storage with minimum byte size, valid PDF header, or hash.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_path": {"type": "string", "description": "Absolute device path (e.g. '/sdcard/Download/output.pdf')"},
                "min_bytes": {"type": "integer", "default": 1, "description": "Minimum required file size in bytes"},
                "check_pdf_header": {"type": "boolean", "default": False, "description": "Assert valid %PDF- binary header"},
                "expected_hash": {"type": "string", "description": "Optional expected MD5 checksum"}
            },
            "required": ["device_path"]
        }
    },
    {
        "name": "verify_app_state",
        "description": "Assert zero FATAL EXCEPTION and zero ANRs in target app logcat diagnostics.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "package_name": {"type": "string", "description": "Target Android package name"}
            },
            "required": ["package_name"]
        }
    }
]


class AndroidMCPServer:
    """
    Official JSON-RPC 2.0 Model Context Protocol Server for Android Automation.
    Supports stdio transport and programmatic request dispatch.
    """

    def __init__(self, tool_layer: Optional[AndroidMCPToolLayer] = None):
        self.tool_layer = tool_layer or get_android_mcp_tools()
        self.protocol_version = "2024-11-05"
        self.server_name = "android-mcp-server"
        self.server_version = "1.5.0"

    def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatches JSON-RPC 2.0 request and returns formatted response."""
        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {})

        # 1. Initialize
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {
                        "tools": {
                            "listChanged": False
                        }
                    },
                    "serverInfo": {
                        "name": self.server_name,
                        "version": self.server_version
                    }
                }
            }

        # 2. Notifications (e.g. notifications/initialized)
        if method.startswith("notifications/"):
            return {}

        # 3. tools/list
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": ANDROID_TOOL_SCHEMAS
                }
            }

        # 4. tools/call
        if method == "tools/call":
            tool_name = params.get("name", "")
            args = params.get("arguments", {})
            return self._dispatch_tool_call(req_id, tool_name, args)

        # Unknown method
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32601,
                "message": f"Method not found: {method}"
            }
        }

    def _dispatch_tool_call(self, req_id: Any, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Maps MCP tool call to AndroidMCPToolLayer method."""
        tool_fn = getattr(self.tool_layer, tool_name, None)
        if not tool_fn or not callable(tool_fn):
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32602,
                    "message": f"Unknown tool: {tool_name}"
                }
            }

        try:
            exec_res = tool_fn(**args)
            res_dict = exec_res.to_dict() if hasattr(exec_res, "to_dict") else {"result": exec_res}
            is_err = not getattr(exec_res, "success", True)

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(res_dict, ensure_ascii=False, indent=2)
                        }
                    ],
                    "isError": is_err
                }
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Tool execution error: {e}"
                        }
                    ],
                    "isError": True
                }
            }

    def run_stdio_server(self):
        """Standard MCP Stdio transport loop."""
        logger.info(f"Starting Android MCP Server ({self.server_name} v{self.server_version}) over stdio...")
        for line in sys.stdin:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                req = json.loads(line_str)
                resp = self.handle_request(req)
                if resp:
                    sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                    sys.stdout.flush()
            except Exception as e:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {e}"}
                }
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()


class AndroidMCPClient:
    """
    Standard MCP Client for Android MCP Server.
    Decouples orchestrator/agent from internal implementation details.
    Communicates strictly via JSON-RPC 2.0 (initialize, tools/list, tools/call).
    """

    def __init__(self, server: Optional[AndroidMCPServer] = None, tool_layer: Optional[AndroidMCPToolLayer] = None):
        self.server = server or AndroidMCPServer(tool_layer=tool_layer)
        self._req_id = 0
        self.server_info: Dict[str, Any] = {}
        self.available_tools: List[Dict[str, Any]] = []

    def initialize(self) -> Dict[str, Any]:
        self._req_id += 1
        req = {
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "AndroidMCPClient", "version": "1.5.0"}
            }
        }
        resp = self.server.handle_request(req)
        self.server_info = resp.get("result", {}).get("serverInfo", {})
        return resp

    def list_tools(self) -> List[Dict[str, Any]]:
        self._req_id += 1
        req = {
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": "tools/list",
            "params": {}
        }
        resp = self.server.handle_request(req)
        self.available_tools = resp.get("result", {}).get("tools", [])
        return self.available_tools

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._req_id += 1
        req = {
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments or {}
            }
        }
        resp = self.server.handle_request(req)
        content = resp.get("result", {}).get("content", [])
        is_error = resp.get("result", {}).get("isError", False)
        parsed_data = None
        if content and isinstance(content, list) and content[0].get("type") == "text":
            try:
                parsed_data = json.loads(content[0]["text"])
            except Exception:
                parsed_data = content[0]["text"]

        return {
            "id": self._req_id,
            "is_error": is_error,
            "data": parsed_data,
            "raw_response": resp
        }


if __name__ == "__main__":
    server = AndroidMCPServer()
    server.run_stdio_server()

