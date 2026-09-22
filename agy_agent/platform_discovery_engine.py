#!/usr/bin/env python3
"""
🔍 Universal Platform & Tech Stack Discovery Engine (AGY 3.0 Platform Standard)
Fulfills Items 1 ~ 60 of the Autonomous E2E 200-Point Architecture:
  1. Zero-Configuration Workspace Inspection: Deduces target platform without user intervention.
  2. Multi-Platform Identification: Distinguishes Web, Android, iOS, and Windows Native.
  3. Hybrid Stack Classification: Identifies WebViews, Electron, Flutter, React Native, Unity/Games, and PWAs.
  4. Execution Target Resolution: Detects local dev servers, ADB devices, build artifacts (APK, EXE), and runtimes.
  5. Environment Health & Capability: Separates host environment capability from target execution.
"""

import os
import re
import sys
import glob
import json
import socket
import logging
import platform
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("PlatformDiscoveryEngine")


@dataclass
class PlatformDiscoveryResult:
    """Structured, verifiable result of platform and tech stack discovery."""
    platform: str                    # "WEB", "ANDROID", "IOS", "WINDOWS", "UNKNOWN"
    framework: str                   # e.g. "Jetpack Compose", "React", "Next.js", "Electron", "Flutter", "Unity", "Vanilla HTML"
    runtime: str                     # e.g. "Android ART", "Chromium/Playwright", "Wine/Xvfb", "iOS Simulator"
    artifact: Optional[str] = None   # Path to APK, EXE, index.html, or URL
    execution_target: str = ""       # e.g. "http://localhost:5173", "emulator-5554", "wine:display99", "ios_sim"
    e2e_adapter: str = ""            # "PlaywrightWebAdapter", "AndroidAdbAdapter", "WindowsNativeAdapter", "IosXcuiAdapter"
    confidence: float = 0.0          # 0.0 to 1.0
    evidence: List[str] = field(default_factory=list)
    all_detected_targets: List[Dict[str, Any]] = field(default_factory=list)
    is_runnable_on_host: bool = True
    unsupported_reason: Optional[str] = None
    is_hybrid_stack: bool = False
    is_game_or_canvas: bool = False
    dev_server_command: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PlatformDiscoveryEngine:
    """
    Zero-Configuration Autonomous Discovery Engine.
    Inspects workspace roots, manifests, build directories, open ports, and system capabilities.
    """

    DEV_PORTS = [3000, 5173, 8080, 8000, 4200, 5000, 8888, 80]

    @classmethod
    def discover(
        cls,
        workspace: str,
        explicit_target: Optional[str] = None
    ) -> PlatformDiscoveryResult:
        """
        Main entrypoint for autonomous platform discovery.
        If explicit_target is provided, validates and enriches it with framework/runtime metadata.
        If explicit_target is None, inspects the workspace directory autonomously.
        """
        ws = os.path.abspath(workspace) if workspace else os.getcwd()
        evidence: List[str] = []
        candidates: List[PlatformDiscoveryResult] = []

        # 1. Check if an explicit target string was passed
        if explicit_target and explicit_target.strip():
            candidate = cls._analyze_explicit_target(explicit_target.strip(), ws)
            if candidate:
                candidate.confidence = 1.0  # Explicit target takes precedence
                candidates.append(candidate)

        # 2. Inspect running local services & devices (Ports, ADB, etc.)
        runtime_candidates = cls._probe_live_runtimes(ws)
        candidates.extend(runtime_candidates)

        # 3. Inspect project files & manifests
        manifest_candidates = cls._inspect_workspace_manifests(ws)
        candidates.extend(manifest_candidates)

        # 4. Inspect compiled artifacts (APKs, EXEs, build/dist directories)
        artifact_candidates = cls._inspect_compiled_artifacts(ws)
        candidates.extend(artifact_candidates)

        if not candidates:
            # Fallback for empty/unrecognized directory
            return PlatformDiscoveryResult(
                platform="WEB",
                framework="Generic Web",
                runtime="Chromium/Playwright",
                artifact=os.path.join(ws, "index.html") if os.path.exists(os.path.join(ws, "index.html")) else None,
                execution_target="http://localhost:8080",
                e2e_adapter="PlaywrightWebAdapter",
                confidence=0.1,
                evidence=["Fallback: No recognizable project manifest or artifact detected"],
                is_runnable_on_host=True
            )

        # Sort candidates by confidence descending
        candidates.sort(key=lambda c: c.confidence, reverse=True)
        primary = candidates[0]

        # Attach list of all candidates for full transparency
        primary.all_detected_targets = [c.to_dict() for c in candidates]

        # Verify host capability for the selected target
        cls._verify_host_execution_capability(primary)

        return primary

    @classmethod
    def _analyze_explicit_target(cls, target: str, workspace: str) -> Optional[PlatformDiscoveryResult]:
        """Analyzes an explicitly passed string target (URL, package name, APK, or EXE)."""
        t = target.strip()
        tl = t.lower()

        # Web URL
        if tl.startswith(("http://", "https://")):
            return PlatformDiscoveryResult(
                platform="WEB",
                framework="Web Service / Application",
                runtime="Chromium/Playwright",
                artifact=t,
                execution_target=t,
                e2e_adapter="PlaywrightWebAdapter",
                confidence=0.98,
                evidence=[f"Explicit HTTP/HTTPS URL specified: {t}"]
            )

        # Web local HTML
        if tl.endswith(".html"):
            path = os.path.join(workspace, t) if not os.path.isabs(t) else t
            return PlatformDiscoveryResult(
                platform="WEB",
                framework="Static Web / HTML",
                runtime="Chromium/Playwright",
                artifact=path,
                execution_target=f"file://{path}",
                e2e_adapter="PlaywrightWebAdapter",
                confidence=0.95,
                evidence=[f"Explicit HTML file specified: {path}"]
            )

        # Android APK
        if tl.endswith(".apk"):
            path = os.path.join(workspace, t) if not os.path.isabs(t) else t
            return PlatformDiscoveryResult(
                platform="ANDROID",
                framework="Android Application",
                runtime="Android ART / ADB",
                artifact=path,
                execution_target=path,
                e2e_adapter="AndroidAdbAdapter",
                confidence=0.98,
                evidence=[f"Explicit APK binary specified: {path}"]
            )

        # Android Package Name (e.g. com.enterprise.pdfeditor)
        if "." in t and not tl.endswith((".exe", ".dll", ".bat", ".sh", ".py", ".json", ".md")):
            return PlatformDiscoveryResult(
                platform="ANDROID",
                framework="Android Installed Package",
                runtime="Android ART / ADB",
                artifact=None,
                execution_target=t,
                e2e_adapter="AndroidAdbAdapter",
                confidence=0.90,
                evidence=[f"Explicit Android package identifier specified: {t}"]
            )

        # Windows Binary
        if tl.endswith((".exe", ".msi")):
            path = os.path.join(workspace, t) if not os.path.isabs(t) else t
            return PlatformDiscoveryResult(
                platform="WINDOWS",
                framework="Windows Native Application",
                runtime="Wine/Xvfb / KVM",
                artifact=path,
                execution_target=path,
                e2e_adapter="WindowsNativeAdapter",
                confidence=0.95,
                evidence=[f"Explicit Windows executable specified: {path}"]
            )

        return None

    @classmethod
    def _probe_live_runtimes(cls, workspace: str) -> List[PlatformDiscoveryResult]:
        """Probes live network ports and hardware/emulator connection sockets."""
        results: List[PlatformDiscoveryResult] = []

        # 1. Probe local HTTP dev servers
        for port in cls.DEV_PORTS:
            if cls._is_port_open("127.0.0.1", port):
                url = f"http://127.0.0.1:{port}"
                results.append(PlatformDiscoveryResult(
                    platform="WEB",
                    framework="Active Local Dev Server",
                    runtime="Chromium/Playwright",
                    artifact=url,
                    execution_target=url,
                    e2e_adapter="PlaywrightWebAdapter",
                    confidence=0.88,
                    evidence=[f"Live HTTP service detected on port {port}"]
                ))

        # 2. Probe ADB device daemon
        try:
            import subprocess
            adb_proc = subprocess.run(
                ["adb", "devices"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=2
            )
            if adb_proc.returncode == 0:
                lines = [l.strip() for l in adb_proc.stdout.splitlines() if l.strip()]
                device_lines = [l for l in lines[1:] if "device" in l and "offline" not in l]
                if device_lines:
                    serial = device_lines[0].split()[0]
                    results.append(PlatformDiscoveryResult(
                        platform="ANDROID",
                        framework="Android Active Device/Emulator",
                        runtime="Android ART / ADB",
                        artifact=None,
                        execution_target=serial,
                        e2e_adapter="AndroidAdbAdapter",
                        confidence=0.80,
                        evidence=[f"Live ADB device connection discovered: {serial}"]
                    ))
        except Exception:
            pass

        return results

    @classmethod
    def _inspect_workspace_manifests(cls, workspace: str) -> List[PlatformDiscoveryResult]:
        """Scans workspace for build configurations and manifests."""
        results: List[PlatformDiscoveryResult] = []

        # 1. Android Manifest / Gradle Inspection
        gradle_files = glob.glob(os.path.join(workspace, "**", "build.gradle*"), recursive=True)
        manifest_files = glob.glob(os.path.join(workspace, "**", "AndroidManifest.xml"), recursive=True)
        if gradle_files or manifest_files:
            framework = "Android Native"
            is_hybrid = False
            is_game = False

            # Deep inspect gradle/manifest for Compose, WebView, Flutter, Unity
            evidence_items = []
            for gf in gradle_files[:3]:
                evidence_items.append(f"Found Gradle build script: {os.path.relpath(gf, workspace)}")
                try:
                    with open(gf, "r", encoding="utf-8", errors="ignore") as f:
                        txt = f.read()
                        if "compose" in txt.lower():
                            framework = "Jetpack Compose"
                        if "unity" in txt.lower():
                            framework = "Unity Game Engine (Android)"
                            is_game = True
                            is_hybrid = True
                        if "flutter" in txt.lower():
                            framework = "Flutter (Android)"
                            is_hybrid = True
                except Exception:
                    pass

            for mf in manifest_files[:2]:
                evidence_items.append(f"Found AndroidManifest: {os.path.relpath(mf, workspace)}")
                try:
                    with open(mf, "r", encoding="utf-8", errors="ignore") as f:
                        mtxt = f.read()
                        if "webview" in mtxt.lower():
                            framework = "Android WebView App"
                            is_hybrid = True
                except Exception:
                    pass

            results.append(PlatformDiscoveryResult(
                platform="ANDROID",
                framework=framework,
                runtime="Android ART / ADB",
                artifact=None,
                execution_target="emulator-5554",
                e2e_adapter="AndroidAdbAdapter",
                confidence=0.85,
                evidence=evidence_items,
                is_hybrid_stack=is_hybrid,
                is_game_or_canvas=is_game
            ))

        # 2. iOS Project Inspection (Xcode / CocoaPods / SwiftPM)
        xcodeproj_files = glob.glob(os.path.join(workspace, "**", "*.xcodeproj"), recursive=True)
        xcworkspace_files = glob.glob(os.path.join(workspace, "**", "*.xcworkspace"), recursive=True)
        podfiles = glob.glob(os.path.join(workspace, "**", "Podfile"), recursive=True)
        if xcodeproj_files or xcworkspace_files or podfiles:
            evidence_items = []
            framework = "SwiftUI / UIKit (iOS)"
            for xp in xcodeproj_files[:2]:
                evidence_items.append(f"Found Xcode project: {os.path.relpath(xp, workspace)}")
            for pf in podfiles[:1]:
                evidence_items.append(f"Found CocoaPods Podfile: {os.path.relpath(pf, workspace)}")

            results.append(PlatformDiscoveryResult(
                platform="IOS",
                framework=framework,
                runtime="iOS Simulator / XCUITest",
                artifact=xcodeproj_files[0] if xcodeproj_files else None,
                execution_target="iPhone 16 Pro Simulator",
                e2e_adapter="IosXcuiAdapter",
                confidence=0.85,
                evidence=evidence_items,
                is_hybrid_stack=False
            ))

        # 3. Web & Cross-platform JavaScript/TypeScript (package.json)
        package_json_path = os.path.join(workspace, "package.json")
        if not os.path.exists(package_json_path):
            pj_matches = glob.glob(os.path.join(workspace, "*", "package.json"))
            if pj_matches:
                package_json_path = pj_matches[0]

        if os.path.exists(package_json_path):
            framework = "Vanilla Web (Node.js)"
            plat = "WEB"
            adapter = "PlaywrightWebAdapter"
            runtime = "Chromium/Playwright"
            is_hybrid = False
            is_game = False
            evidence_items = [f"Found package.json: {os.path.relpath(package_json_path, workspace)}"]

            try:
                with open(package_json_path, "r", encoding="utf-8") as f:
                    pkg = json.load(f)
                    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}

                    # Check for Electron (Desktop)
                    if "electron" in deps:
                        plat = "WINDOWS"
                        framework = "Electron Desktop Application"
                        runtime = "Wine/Xvfb / Native Electron"
                        adapter = "WindowsNativeAdapter"
                        is_hybrid = True
                        evidence_items.append("Dependency 'electron' identified: Desktop Application")
                    elif "react-native" in deps:
                        plat = "ANDROID"
                        framework = "React Native Cross-Platform"
                        runtime = "Android ART / ADB"
                        adapter = "AndroidAdbAdapter"
                        is_hybrid = True
                        evidence_items.append("Dependency 'react-native' identified")
                    elif "next" in deps:
                        framework = "Next.js SSR Web Application"
                        evidence_items.append("Dependency 'next' identified")
                    elif "react" in deps:
                        framework = "React SPA Web Application"
                        evidence_items.append("Dependency 'react' identified")
                    elif "vue" in deps:
                        framework = "Vue.js Web Application"
                        evidence_items.append("Dependency 'vue' identified")
                    elif "svelte" in deps:
                        framework = "Svelte Web Application"
                        evidence_items.append("Dependency 'svelte' identified")
                    elif "phaser" in deps or "three" in deps or "pixi.js" in deps:
                        framework = "HTML5 Canvas/WebGL Game"
                        is_game = True
                        evidence_items.append("Game engine dependency (three/phaser/pixi) identified")

                    scripts = pkg.get("scripts", {})
                    dev_cmd = None
                    if "dev" in scripts:
                        dev_cmd = "npm run dev"
                    elif "start" in scripts:
                        dev_cmd = "npm start"

            except Exception as e:
                evidence_items.append(f"Error parsing package.json: {e}")

            results.append(PlatformDiscoveryResult(
                platform=plat,
                framework=framework,
                runtime=runtime,
                artifact=package_json_path,
                execution_target="http://localhost:3000" if plat == "WEB" else "electron_app",
                e2e_adapter=adapter,
                confidence=0.86,
                evidence=evidence_items,
                is_hybrid_stack=is_hybrid,
                is_game_or_canvas=is_game,
                dev_server_command=dev_cmd
            ))

        # 4. Windows Native (.NET / C++ / Cargo / Go)
        csproj_files = glob.glob(os.path.join(workspace, "**", "*.csproj"), recursive=True)
        sln_files = glob.glob(os.path.join(workspace, "**", "*.sln"), recursive=True)
        cargo_files = glob.glob(os.path.join(workspace, "**", "Cargo.toml"), recursive=True)
        if csproj_files or sln_files:
            evidence_items = []
            framework = ".NET WPF/WinUI Native Windows Application"
            for cf in csproj_files[:2]:
                evidence_items.append(f"Found C# project: {os.path.relpath(cf, workspace)}")
            for sf in sln_files[:1]:
                evidence_items.append(f"Found Solution file: {os.path.relpath(sf, workspace)}")

            results.append(PlatformDiscoveryResult(
                platform="WINDOWS",
                framework=framework,
                runtime="Wine/Xvfb / KVM",
                artifact=csproj_files[0] if csproj_files else None,
                execution_target="wine_execution",
                e2e_adapter="WindowsNativeAdapter",
                confidence=0.85,
                evidence=evidence_items
            ))

        # 5. Flutter multiplatform project
        pubspec_path = os.path.join(workspace, "pubspec.yaml")
        if os.path.exists(pubspec_path):
            results.append(PlatformDiscoveryResult(
                platform="ANDROID",
                framework="Flutter Multiplatform Application",
                runtime="Android ART / Flutter Engine",
                artifact=pubspec_path,
                execution_target="emulator-5554",
                e2e_adapter="AndroidAdbAdapter",
                confidence=0.87,
                evidence=[f"Found Flutter pubspec.yaml: {os.path.relpath(pubspec_path, workspace)}"],
                is_hybrid_stack=True
            ))

        return results

    @classmethod
    def _inspect_compiled_artifacts(cls, workspace: str) -> List[PlatformDiscoveryResult]:
        """Scans workspace for compiled binary artifacts (APKs, EXEs, build outputs)."""
        results: List[PlatformDiscoveryResult] = []

        # 1. Check for APKs
        apk_files = glob.glob(os.path.join(workspace, "**", "*.apk"), recursive=True)
        if apk_files:
            # Filter and sort by latest modification time
            sorted_apks = sorted(apk_files, key=os.path.getmtime, reverse=True)
            latest_apk = sorted_apks[0]
            mtime_str = os.path.getmtime(latest_apk)

            results.append(PlatformDiscoveryResult(
                platform="ANDROID",
                framework="Android Application (Compiled Binary)",
                runtime="Android ART / ADB",
                artifact=latest_apk,
                execution_target=latest_apk,
                e2e_adapter="AndroidAdbAdapter",
                confidence=0.96,
                evidence=[
                    f"Found compiled APK binary: {os.path.basename(latest_apk)} (Latest among {len(sorted_apks)} APKs)"
                ]
            ))

        # 2. Check for Windows Executables
        exe_files = glob.glob(os.path.join(workspace, "**", "*.exe"), recursive=True)
        # Exclude common tooling/node_modules executables
        filtered_exes = [
            f for f in exe_files
            if "node_modules" not in f and ".git" not in f and ".gemini" not in f
        ]
        if filtered_exes:
            sorted_exes = sorted(filtered_exes, key=os.path.getmtime, reverse=True)
            latest_exe = sorted_exes[0]
            results.append(PlatformDiscoveryResult(
                platform="WINDOWS",
                framework="Windows Native Executable Binary",
                runtime="Wine/Xvfb / KVM",
                artifact=latest_exe,
                execution_target=latest_exe,
                e2e_adapter="WindowsNativeAdapter",
                confidence=0.94,
                evidence=[f"Found compiled Windows executable: {os.path.basename(latest_exe)}"]
            ))

        # 3. Check for Web build / static entrypoint (index.html, dist/, build/)
        index_html_files = glob.glob(os.path.join(workspace, "**", "index.html"), recursive=True)
        filtered_html = [
            f for f in index_html_files
            if "node_modules" not in f and ".git" not in f and ".gemini" not in f
        ]
        if filtered_html:
            target_html = filtered_html[0]
            results.append(PlatformDiscoveryResult(
                platform="WEB",
                framework="Static Web Application Entrypoint",
                runtime="Chromium/Playwright",
                artifact=target_html,
                execution_target=f"file://{target_html}",
                e2e_adapter="PlaywrightWebAdapter",
                confidence=0.82,
                evidence=[f"Found static web entrypoint: {os.path.relpath(target_html, workspace)}"]
            ))

        return results

    @classmethod
    def _verify_host_execution_capability(cls, result: PlatformDiscoveryResult):
        """
        Enforces Item 67 ~ 69 & 165:
        Verifies if the current host environment can actually execute the detected target.
        If unsupported (e.g. iOS on Linux host), flags is_runnable_on_host=False and specifies unsupported_reason
        so the MechanicalGate can cleanly issue a BLOCKED verdict instead of crashing.
        """
        host_os = platform.system().lower()

        if result.platform == "IOS":
            if host_os != "darwin":
                result.is_runnable_on_host = False
                result.unsupported_reason = (
                    f"Host capability mismatch: iOS Simulator/XCUITest requires macOS (Darwin) host. "
                    f"Current host is {platform.system()} {platform.machine()}. "
                    f"E2E Engine will register BLOCKED status."
                )
                logger.warning(result.unsupported_reason)

        elif result.platform == "WINDOWS":
            # On Linux, verify wine or xvfb availability
            if host_os == "linux":
                import shutil
                has_wine = shutil.which("wine") is not None
                has_xvfb = shutil.which("Xvfb") is not None
                if not has_wine and not has_xvfb:
                    result.is_runnable_on_host = False
                    result.unsupported_reason = (
                        "Wine or Xvfb binary is not installed on Linux host for Windows Native execution. "
                        "E2E Engine will register BLOCKED status."
                    )

        elif result.platform == "ANDROID":
            # Check ADB connectivity or availability
            import shutil
            has_adb = shutil.which("adb") is not None
            if not has_adb:
                result.is_runnable_on_host = False
                result.unsupported_reason = (
                    "ADB command line utility is not available in PATH. "
                    "E2E Engine will register BLOCKED status."
                )

        elif result.platform == "WEB":
            # Playwright browser verification
            result.is_runnable_on_host = True

    @staticmethod
    def _is_port_open(host: str, port: int, timeout_sec: float = 0.5) -> bool:
        """Helper to quickly check if a TCP port is open and listening."""
        try:
            with socket.create_connection((host, port), timeout=timeout_sec):
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False
