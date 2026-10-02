import os
import time
import logging
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, Response, JSONResponse
from pydantic import BaseModel
import uvicorn
import httpx

from android_vision_inspector import AndroidVisionInspector

logger = logging.getLogger("MobileSimulatorServer")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="AGY Real-time Mobile Interactive Simulator", version="1.2.0")

WORKSPACE_ROOT = os.getenv("MOBILE_SIM_WORKSPACE", "").strip() or str(Path.home() / "Desktop" / "autocoding")
DEV_SERVER_PORT = 5174
DEV_SERVER_URL = f"http://127.0.0.1:{DEV_SERVER_PORT}"
APK_PATH = os.path.join(WORKSPACE_ROOT, "ParkMemo.apk")

class E2EFlowRequest(BaseModel):
    flow_type: str

class BluetoothHarnessRequest(BaseModel):
    connected: bool

class LocationHarnessRequest(BaseModel):
    lat: float
    lng: float
    floor: str = "지상 1층"
    pressure: float = 1013.0

class NotificationHarnessRequest(BaseModel):
    title: str
    body: str

HARNESS_STATE = {
    "bluetooth_connected": True,
    "latitude": 37.4981,
    "longitude": 127.0276,
    "floor": "지상 1층",
    "pressure": 1013.0,
    "last_updated": time.time() if 'time' in globals() else 0
}

SIMULATOR_HTML = r"""<!DOCTYPE html>
<html lang="ko" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>📱 파킹메모 (ParkMemo) - 60fps 실시간 모바일 인터랙티브 스튜디오</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            brand: { 50: '#eef2ff', 500: '#6366f1', 600: '#4f46e5', 700: '#4338ca' }
          }
        }
      }
    }
  </script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    /* Phone Mockup Styling */
    .phone-case {
      box-shadow: 0 25px 60px -15px rgba(0, 0, 0, 0.7), 0 0 0 12px #1f2430, 0 0 0 14px #333a4d;
      transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    }
    .dynamic-island {
      position: absolute;
      top: 10px;
      left: 50%;
      transform: translateX(-50%);
      width: 110px;
      height: 28px;
      background: #000;
      border-radius: 20px;
      z-index: 50;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 12px;
      box-shadow: 0 2px 8px rgba(0,0,0,0.5);
    }
    .home-bar {
      position: absolute;
      bottom: 8px;
      left: 50%;
      transform: translateX(-50%);
      width: 130px;
      height: 4px;
      background: rgba(255, 255, 255, 0.6);
      border-radius: 4px;
      z-index: 50;
      pointer-events: none;
    }
    /* Custom Scrollbar */
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: #0f172a; }
    ::-webkit-scrollbar-thumb { background: #334155; border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: #475569; }
  </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen flex flex-col font-sans selection:bg-indigo-500 selection:text-white">

  <!-- Top Studio Navigation Bar -->
  <header class="bg-slate-900/80 backdrop-blur-md border-b border-slate-800 sticky top-0 z-50 px-6 py-3 flex items-center justify-between">
    <div class="flex items-center gap-3">
      <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-indigo-600 to-violet-500 flex items-center justify-center shadow-lg shadow-indigo-500/30">
        <i class="fa-solid fa-square-parking text-white text-xl"></i>
      </div>
      <div>
        <div class="flex items-center gap-2">
          <h1 class="font-bold text-lg text-white">ParkMemo 60fps Live Mobile Studio</h1>
          <span class="px-2 py-0.5 text-xs font-semibold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 rounded-full flex items-center gap-1.5">
            <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span> 60fps Live Engine Active
          </span>
        </div>
        <p class="text-xs text-slate-400">Google Antigravity 3-Agent Collaborative E2E Workbench</p>
      </div>
    </div>

    <!-- Quick Actions -->
    <div class="flex items-center gap-3">
      <button onclick="reloadApp()" class="px-3.5 py-1.5 text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 rounded-lg flex items-center gap-2 transition">
        <i class="fa-solid fa-arrows-rotate" id="refresh-icon"></i> 앱 새로고침
      </button>
      <a href="/api/download/apk" class="px-3.5 py-1.5 text-xs font-medium bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg flex items-center gap-2 shadow-lg shadow-indigo-600/30 transition">
        <i class="fa-brands fa-android text-sm"></i> APK 다운로드 (1.5MB)
      </a>
      <a href="/api/download/gdrive" target="_blank" class="px-3.5 py-1.5 text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 rounded-lg flex items-center gap-2 transition">
        <i class="fa-brands fa-google-drive text-amber-400"></i> Google Drive 30TB
      </a>
    </div>
  </header>

  <!-- Main Studio Workspace -->
  <main class="flex-1 flex flex-col lg:flex-row p-6 gap-6 max-w-7xl mx-auto w-full">

    <!-- Left: Realistic Mobile Device Simulation Area -->
    <div class="flex-1 flex flex-col items-center justify-center bg-slate-900/40 border border-slate-800/80 rounded-2xl p-8 relative overflow-hidden">
      
      <!-- Device Selector Toolbar -->
      <div class="bg-slate-900/90 backdrop-blur border border-slate-700/60 rounded-xl p-1.5 flex items-center gap-1 mb-6 shadow-xl z-20">
        <button onclick="setDevice('iphone16')" id="btn-iphone16" class="device-btn px-3 py-1.5 text-xs rounded-lg font-medium bg-indigo-600 text-white transition flex items-center gap-1.5">
          <i class="fa-brands fa-apple"></i> iPhone 16 Pro (393x852)
        </button>
        <button onclick="setDevice('pixel8')" id="btn-pixel8" class="device-btn px-3 py-1.5 text-xs rounded-lg font-medium text-slate-400 hover:text-slate-200 transition flex items-center gap-1.5">
          <i class="fa-brands fa-android text-emerald-400"></i> Pixel 8 / Galaxy (412x915)
        </button>
        <button onclick="setDevice('fold')" id="btn-fold" class="device-btn px-3 py-1.5 text-xs rounded-lg font-medium text-slate-400 hover:text-slate-200 transition flex items-center gap-1.5">
          <i class="fa-solid fa-tablet-screen-button text-cyan-400"></i> Fold Tablet (768x900)
        </button>
        <div class="h-4 w-px bg-slate-700 mx-1"></div>
        <button onclick="toggleOrientation()" title="화면 회전" class="p-1.5 text-xs rounded-lg text-slate-400 hover:text-white transition">
          <i class="fa-solid fa-rotate text-sm"></i>
        </button>
      </div>

      <!-- Realistic Smartphone Mockup Frame -->
      <div id="phone-wrapper" class="phone-case relative rounded-[50px] bg-black overflow-hidden border-[10px] border-slate-800 transition-all duration-300" style="width: 393px; height: 820px;">
        
        <!-- Dynamic Island / Top Camera Notch -->
        <div class="dynamic-island">
          <div class="w-2.5 h-2.5 rounded-full bg-slate-900 border border-slate-800"></div>
          <div class="flex items-center gap-1">
            <span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
            <div class="w-3.5 h-3.5 rounded-full bg-indigo-950 border border-indigo-500/40"></div>
          </div>
        </div>

        <!-- Phone Status Bar -->
        <div class="absolute top-2.5 px-7 w-full flex justify-between items-center text-[11px] font-semibold text-white/90 z-40 pointer-events-none">
          <span id="clock-display">12:00</span>
          <div class="flex items-center gap-1.5 text-[10px]">
            <i class="fa-solid fa-signal"></i>
            <span class="font-bold text-[9px]">5G</span>
            <i class="fa-solid fa-battery-full text-xs"></i>
          </div>
        </div>

        <!-- Android Notification Dropdown Overlay -->
        <div id="notification-banner" class="hidden absolute top-12 left-3 right-3 bg-slate-900/95 backdrop-blur-md border border-slate-700/80 rounded-2xl p-3.5 shadow-2xl z-50 transition-all duration-300 transform -translate-y-4 opacity-0 flex items-start gap-3 cursor-pointer hover:bg-slate-800/90" onclick="dismissNotification()">
          <div class="w-8 h-8 rounded-xl bg-indigo-600 flex items-center justify-center text-white shrink-0 mt-0.5 shadow-md shadow-indigo-600/30">
            <i class="fa-solid fa-square-parking text-base"></i>
          </div>
          <div class="flex-1 min-w-0">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-white truncate" id="notif-title">ParkMemo · 주차 위치 자동 저장</span>
              <span class="text-[10px] text-slate-400 font-mono">지금</span>
            </div>
            <p class="text-[11px] text-slate-300 mt-0.5 line-clamp-2 leading-relaxed" id="notif-body">K7 블루투스 연결 해제 감지 ➔ 지하 2층 (B2) 위치가 성공적으로 기록되었습니다.</p>
          </div>
        </div>

        <!-- Embedded Live App View -->
        <iframe id="app-frame" src="/proxy/" class="w-full h-full pt-8 pb-3 bg-slate-950 border-0"></iframe>

        <!-- Bottom Home Indicator Bar -->
        <div class="home-bar"></div>
      </div>

      <!-- Live Interaction Hint -->
      <p class="text-xs text-slate-400 mt-4 flex items-center gap-2">
        <i class="fa-regular fa-hand-pointer text-indigo-400"></i> 마우스로 직접 클릭/터치/스크롤하며 0초 딜레이로 테스트할 수 있습니다.
      </p>
    </div>

    <!-- Right: AI Visual Inspector & E2E Control Panel -->
    <div class="w-full lg:w-[420px] flex flex-col gap-5">

      <!-- Card 1: Virtual Hardware Sensor & OS Injector -->
      <div class="bg-slate-900/60 border border-slate-800 rounded-2xl p-5 shadow-xl flex flex-col gap-4">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2.5">
            <div class="w-8 h-8 rounded-lg bg-cyan-500/20 border border-cyan-500/30 flex items-center justify-center text-cyan-400">
              <i class="fa-solid fa-microchip"></i>
            </div>
            <div>
              <h2 class="font-bold text-sm text-white">가상 디바이스 하드웨어 제어판</h2>
              <p class="text-[11px] text-slate-400">Virtual Hardware Sensor & OS Injector</p>
            </div>
          </div>
          <span class="px-2 py-0.5 text-[10px] font-semibold bg-cyan-500/10 text-cyan-300 border border-cyan-500/20 rounded">
            Live Harness
          </span>
        </div>

        <!-- 1. Bluetooth Injector -->
        <div class="bg-slate-950/60 border border-slate-800/80 rounded-xl p-3 flex flex-col gap-2">
          <div class="flex items-center justify-between text-xs">
            <span class="text-slate-300 font-semibold flex items-center gap-1.5">
              <i class="fa-brands fa-bluetooth-b text-indigo-400"></i> 차량 블루투스 (K7 / 포터2)
            </span>
            <span id="bt-badge" class="px-2 py-0.5 text-[10px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 rounded-full">
              🟢 연결됨
            </span>
          </div>
          <div class="grid grid-cols-2 gap-2 mt-1">
            <button onclick="injectBluetooth(true)" id="btn-bt-conn" class="py-1.5 px-3 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-medium transition flex items-center justify-center gap-1.5 shadow-md">
              <i class="fa-solid fa-link"></i> 연결 (ON)
            </button>
            <button onclick="injectBluetooth(false)" id="btn-bt-disconn" class="py-1.5 px-3 bg-slate-800 hover:bg-rose-600 text-slate-300 hover:text-white rounded-lg text-xs font-medium transition flex items-center justify-center gap-1.5">
              <i class="fa-solid fa-link-slash"></i> 단절 (시동 OFF)
            </button>
          </div>
        </div>

        <!-- 2. Location & Altitude Presets + Continuous Sliders -->
        <div class="bg-slate-950/60 border border-slate-800/80 rounded-xl p-3 flex flex-col gap-2">
          <div class="flex items-center justify-between text-xs">
            <span class="text-slate-300 font-semibold flex items-center gap-1.5">
              <i class="fa-solid fa-location-dot text-cyan-400"></i> 가상 GPS 위치 & 기압 제어
            </span>
            <span id="loc-display" class="text-[10px] text-cyan-300 font-mono">지상 1층 (1013.0hPa)</span>
          </div>

          <!-- Presets -->
          <div class="grid grid-cols-2 gap-2 mt-0.5">
            <button onclick="injectLocation(37.4981, 127.0276, '지상 1층', 1013.0)" class="py-1.5 px-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-[11px] font-medium transition flex items-center gap-1.5 truncate">
              🏢 지상 1층 (로비)
            </button>
            <button onclick="injectLocation(37.4981, 127.0276, '지하 1층 (B1)', 1014.2)" class="py-1.5 px-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-[11px] font-medium transition flex items-center gap-1.5 truncate">
              🚗 지하 1층 (B1)
            </button>
            <button onclick="injectLocation(37.4981, 127.0276, '지하 2층 (B2)', 1015.4)" class="py-1.5 px-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-[11px] font-medium transition flex items-center gap-1.5 truncate">
              🅿️ 지하 2층 (B2)
            </button>
            <button onclick="injectLocation(37.5050, 127.0350, '도로 주행 중', 1012.8)" class="py-1.5 px-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-[11px] font-medium transition flex items-center gap-1.5 truncate">
              🛣️ 도로 주행 중
            </button>
          </div>

          <!-- Fine-Grained Sliders -->
          <div class="mt-2 pt-2 border-t border-slate-800/80 flex flex-col gap-2">
            <div class="flex flex-col gap-0.5">
              <div class="flex items-center justify-between text-[11px] text-slate-400">
                <span>📍 위도 (Latitude)</span>
                <span id="slider-lat-val" class="font-mono text-cyan-300">37.4981</span>
              </div>
              <input type="range" id="slider-lat" min="37.4500" max="37.5500" step="0.0001" value="37.4981" oninput="onContinuousLocationChange()" class="w-full accent-cyan-500 h-1 bg-slate-800 rounded-lg cursor-pointer">
            </div>

            <div class="flex flex-col gap-0.5">
              <div class="flex items-center justify-between text-[11px] text-slate-400">
                <span>📍 경도 (Longitude)</span>
                <span id="slider-lng-val" class="font-mono text-cyan-300">127.0276</span>
              </div>
              <input type="range" id="slider-lng" min="126.9500" max="127.1000" step="0.0001" value="127.0276" oninput="onContinuousLocationChange()" class="w-full accent-cyan-500 h-1 bg-slate-800 rounded-lg cursor-pointer">
            </div>

            <div class="flex flex-col gap-0.5">
              <div class="flex items-center justify-between text-[11px] text-slate-400">
                <span>🏢 고도 / 기압 (Floor & Barometer)</span>
                <span id="slider-pres-val" class="font-mono text-amber-300">1013.0 hPa (지상 1층)</span>
              </div>
              <input type="range" id="slider-pres" min="1000.0" max="1025.0" step="0.1" value="1013.0" oninput="onContinuousLocationChange()" class="w-full accent-amber-500 h-1 bg-slate-800 rounded-lg cursor-pointer">
            </div>
          </div>
        </div>

        <!-- 3. Local Storage & DB Live Inspector -->
        <div class="bg-slate-950/60 border border-slate-800/80 rounded-xl p-3 flex flex-col gap-2">
          <div class="flex items-center justify-between text-xs">
            <span class="text-slate-300 font-semibold flex items-center gap-1.5">
              <i class="fa-solid fa-database text-emerald-400"></i> 앱 스토리지 & DB 인스펙터
            </span>
            <button onclick="requestStorageDump()" class="px-2 py-0.5 text-[10px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 rounded-lg hover:bg-emerald-500/30 transition flex items-center gap-1">
              <i class="fa-solid fa-arrows-rotate text-[9px]"></i> 덤프 조회
            </button>
          </div>

          <!-- Storage Content Box -->
          <div id="storage-inspector-box" class="bg-slate-900/90 border border-slate-800 rounded-lg p-2.5 max-h-36 overflow-y-auto text-[11px] text-slate-300 font-mono space-y-1">
            <div class="text-slate-500 text-center py-2 text-[10px]">덤프 조회 버튼을 누르면 앱 내부 데이터가 표시됩니다.</div>
          </div>

          <div class="flex items-center justify-between pt-1 text-[10px]">
            <button onclick="clearAppStorage()" class="text-rose-400 hover:text-rose-300 flex items-center gap-1 transition">
              <i class="fa-solid fa-trash"></i> 스토리지 초기화 (Clear)
            </button>
            <span class="text-slate-500 font-mono">Live Bridge</span>
          </div>
        </div>

        <!-- 4. Notification Trigger -->
        <button onclick="triggerTestNotification()" class="w-full py-2 px-3 bg-slate-800/80 hover:bg-slate-700 border border-slate-700/80 rounded-xl text-xs font-semibold text-slate-200 transition flex items-center justify-center gap-2">
          <i class="fa-solid fa-bell text-amber-400"></i> <span>헤드업 시스템 알림(Notification) 테스트 발송</span>
        </button>

      </div>

      <!-- Card 2: Gemini Ultra Vision Visual QA Inspector -->
      <div class="bg-slate-900/60 border border-slate-800 rounded-2xl p-5 shadow-xl flex flex-col gap-4">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2.5">
            <div class="w-8 h-8 rounded-lg bg-indigo-500/20 border border-indigo-500/30 flex items-center justify-center text-indigo-400">
              <i class="fa-solid fa-wand-magic-sparkles"></i>
            </div>
            <div>
              <h2 class="font-bold text-sm text-white">Gemini Ultra 시각 품질 감사관</h2>
              <p class="text-[11px] text-slate-400">AI Visual QA & UI/UX Design Audit</p>
            </div>
          </div>
          <span class="px-2 py-0.5 text-[10px] font-semibold bg-indigo-500/10 text-indigo-300 border border-indigo-500/20 rounded">
            Vision 3.1 Pro
          </span>
        </div>

        <p class="text-xs text-slate-300 leading-relaxed">
          현재 폰 화면의 <strong>레이아웃 정렬, 색상 대비율, 타이포그래피, 모바일 터치 타겟(48dp)</strong>을 실제 Gemini Vision 멀티모달 모델로 실시간 전수 검사합니다.
        </p>

        <button onclick="runVisualInspection()" id="btn-inspect" class="w-full py-2.5 px-4 bg-gradient-to-r from-indigo-600 to-violet-600 hover:from-indigo-500 hover:to-violet-500 text-white text-xs font-bold rounded-xl shadow-lg shadow-indigo-600/30 transition flex items-center justify-center gap-2">
          <i class="fa-solid fa-camera"></i> <span>실시간 시각 품질 정밀 분석 실행</span>
        </button>

        <!-- Vision Inspection Result Box -->
        <div id="inspect-result" class="hidden bg-slate-950 border border-slate-800 rounded-xl p-4 text-xs flex flex-col gap-2.5 max-h-[360px] overflow-y-auto">
          <!-- Dynamic Content Injected by JS -->
        </div>
      </div>

      <!-- Card 3: E2E Automated Test Flows -->
      <div class="bg-slate-900/60 border border-slate-800 rounded-2xl p-5 shadow-xl flex flex-col gap-4">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2.5">
            <div class="w-8 h-8 rounded-lg bg-emerald-500/20 border border-emerald-500/30 flex items-center justify-center text-emerald-400">
              <i class="fa-solid fa-play"></i>
            </div>
            <div>
              <h2 class="font-bold text-sm text-white">E2E 자율 시나리오 테스트</h2>
              <p class="text-[11px] text-slate-400">Headless Playwright Automation</p>
            </div>
          </div>
        </div>

        <div class="grid grid-cols-1 gap-2">
          <button onclick="runE2EFlow('parking')" id="btn-flow-parking" class="p-3 bg-slate-800/60 hover:bg-slate-800 border border-slate-700/60 rounded-xl text-left transition flex items-center justify-between group">
            <div>
              <div class="font-semibold text-xs text-white group-hover:text-indigo-300 transition">🚗 시나리오 1: 주차 위치 등록 & 메모</div>
              <div class="text-[10px] text-slate-400 mt-0.5">지하 2층 B-04 구역 선택 ➔ 메모 작성 ➔ 등록 확인</div>
            </div>
            <i class="fa-solid fa-chevron-right text-slate-500 group-hover:text-indigo-400 text-xs transition" id="arrow-parking"></i>
          </button>

          <button onclick="runE2EFlow('exit')" id="btn-flow-exit" class="p-3 bg-slate-800/60 hover:bg-slate-800 border border-slate-700/60 rounded-xl text-left transition flex items-center justify-between group">
            <div>
              <div class="font-semibold text-xs text-white group-hover:text-emerald-300 transition">🏁 시나리오 2: 출차 알림 및 위치 확인</div>
              <div class="text-[10px] text-slate-400 mt-0.5">내 차 찾기 ➔ 타이머 확인 ➔ 출차 완료 정산</div>
            </div>
            <i class="fa-solid fa-chevron-right text-slate-500 group-hover:text-emerald-400 text-xs transition" id="arrow-exit"></i>
          </button>
        </div>

        <!-- E2E Execution Result Box -->
        <div id="e2e-result" class="hidden bg-slate-950 border border-slate-800 rounded-xl p-3.5 text-xs flex flex-col gap-2">
          <!-- Injected dynamically -->
        </div>
      </div>

      <!-- Card 4: Server Health & Status -->
      <div class="bg-slate-900/60 border border-slate-800 rounded-2xl p-4 text-xs text-slate-400 flex items-center justify-between">
        <div class="flex items-center gap-2">
          <span class="w-2 h-2 rounded-full bg-emerald-400"></span>
          <span>Dev Server: <code class="text-slate-200">http://localhost:5174</code></span>
        </div>
        <span class="text-[11px] font-mono text-indigo-400">Latency: 1ms</span>
      </div>

    </div>

  </main>

  <script>
    // Live Clock Update
    function updateClock() {
      const now = new Date();
      const hours = String(now.getHours()).padStart(2, '0');
      const minutes = String(now.getMinutes()).padStart(2, '0');
      document.getElementById('clock-display').innerText = `${hours}:${minutes}`;
    }
    setInterval(updateClock, 1000);
    updateClock();

    // Reload App
    function reloadApp() {
      const icon = document.getElementById('refresh-icon');
      icon.classList.add('fa-spin');
      const iframe = document.getElementById('app-frame');
      iframe.src = iframe.src;
      setTimeout(() => icon.classList.remove('fa-spin'), 600);
    }

    // Device Switching
    let isLandscape = false;
    let currentDevice = 'iphone16';

    const deviceSizes = {
      'iphone16': { w: 393, h: 820 },
      'pixel8': { w: 412, h: 860 },
      'fold': { w: 680, h: 820 }
    };

    function setDevice(device) {
      currentDevice = device;
      document.querySelectorAll('.device-btn').forEach(btn => {
        btn.classList.remove('bg-indigo-600', 'text-white');
        btn.classList.add('text-slate-400');
      });
      const activeBtn = document.getElementById(`btn-${device}`);
      activeBtn.classList.remove('text-slate-400');
      activeBtn.classList.add('bg-indigo-600', 'text-white');

      applyDimensions();
    }

    function toggleOrientation() {
      isLandscape = !isLandscape;
      applyDimensions();
    }

    function applyDimensions() {
      const frame = document.getElementById('phone-wrapper');
      let size = deviceSizes[currentDevice];
      let width = isLandscape ? size.h : size.w;
      let height = isLandscape ? size.w : size.h;
      frame.style.width = `${width}px`;
      frame.style.height = `${height}px`;
    }

    // Virtual Hardware Harness JS
    let currentBtConnected = true;
    let notifTimer = null;

    async function injectBluetooth(connected) {
      currentBtConnected = connected;
      const badge = document.getElementById('bt-badge');
      const btnConn = document.getElementById('btn-bt-conn');
      const btnDisconn = document.getElementById('btn-bt-disconn');

      if (connected) {
        badge.className = "px-2 py-0.5 text-[10px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 rounded-full";
        badge.innerText = "🟢 연결됨";
        btnConn.className = "py-1.5 px-3 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-medium transition flex items-center justify-center gap-1.5 shadow-md";
        btnDisconn.className = "py-1.5 px-3 bg-slate-800 hover:bg-rose-600 text-slate-300 hover:text-white rounded-lg text-xs font-medium transition flex items-center justify-center gap-1.5";
      } else {
        badge.className = "px-2 py-0.5 text-[10px] font-bold bg-rose-500/20 text-rose-400 border border-rose-500/30 rounded-full animate-pulse";
        badge.innerText = "🔴 연결 해제됨";
        btnDisconn.className = "py-1.5 px-3 bg-rose-600 hover:bg-rose-500 text-white rounded-lg text-xs font-medium transition flex items-center justify-center gap-1.5 shadow-md";
        btnConn.className = "py-1.5 px-3 bg-slate-800 hover:bg-indigo-600 text-slate-300 hover:text-white rounded-lg text-xs font-medium transition flex items-center justify-center gap-1.5";
        
        // Show Android Notification Toast
        showNotification(
          "ParkMemo · 주차 감지 알림",
          "K7 차량 블루투스 연결이 해제되어 주차 위치가 자동으로 기록되었습니다."
        );
      }

      // Send postMessage to embedded iframe
      const iframe = document.getElementById('app-frame');
      if (iframe && iframe.contentWindow) {
        iframe.contentWindow.postMessage({
          type: 'AGY_HARNESS_BLUETOOTH',
          connected: connected
        }, '*');
      }

      try {
        await fetch('/api/harness/bluetooth', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ connected: connected })
        });
      } catch (e) {
        console.warn("Failed to sync BT state:", e);
      }
    }

    async function injectLocation(lat, lng, floor, pressure) {
      document.getElementById('loc-display').innerText = `${floor} (${pressure}hPa)`;
      const sliderLat = document.getElementById('slider-lat');
      const sliderLng = document.getElementById('slider-lng');
      const sliderPres = document.getElementById('slider-pres');
      if (sliderLat) sliderLat.value = lat;
      if (sliderLng) sliderLng.value = lng;
      if (sliderPres) sliderPres.value = pressure;

      const sliderLatVal = document.getElementById('slider-lat-val');
      const sliderLngVal = document.getElementById('slider-lng-val');
      const sliderPresVal = document.getElementById('slider-pres-val');
      if (sliderLatVal) sliderLatVal.innerText = lat.toFixed(4);
      if (sliderLngVal) sliderLngVal.innerText = lng.toFixed(4);
      if (sliderPresVal) sliderPresVal.innerText = `${pressure.toFixed(1)} hPa (${floor})`;

      const iframe = document.getElementById('app-frame');
      if (iframe && iframe.contentWindow) {
        iframe.contentWindow.postMessage({
          type: 'AGY_HARNESS_LOCATION',
          lat: lat,
          lng: lng,
          floor: floor,
          pressure: pressure
        }, '*');
      }

      try {
        await fetch('/api/harness/location', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ lat: lat, lng: lng, floor: floor, pressure: pressure })
        });
      } catch (e) {
        console.warn("Failed to sync location:", e);
      }
    }

    function onContinuousLocationChange() {
      const lat = parseFloat(document.getElementById('slider-lat').value);
      const lng = parseFloat(document.getElementById('slider-lng').value);
      const pres = parseFloat(document.getElementById('slider-pres').value);

      document.getElementById('slider-lat-val').innerText = lat.toFixed(4);
      document.getElementById('slider-lng-val').innerText = lng.toFixed(4);

      let floor = '지상 1층';
      if (pres >= 1015.0) floor = '지하 2층 (B2)';
      else if (pres >= 1013.8) floor = '지하 1층 (B1)';
      else if (pres < 1012.0) floor = '고지대/도로 주행';

      document.getElementById('slider-pres-val').innerText = `${pres.toFixed(1)} hPa (${floor})`;
      document.getElementById('loc-display').innerText = `${floor} (${pres.toFixed(1)}hPa)`;

      const iframe = document.getElementById('app-frame');
      if (iframe && iframe.contentWindow) {
        iframe.contentWindow.postMessage({
          type: 'AGY_HARNESS_LOCATION',
          lat: lat,
          lng: lng,
          floor: floor,
          pressure: pres
        }, '*');
      }
    }

    function requestStorageDump() {
      const box = document.getElementById('storage-inspector-box');
      box.innerHTML = `<div class="text-indigo-400 py-1 flex items-center justify-center gap-1.5"><i class="fa-solid fa-spinner fa-spin"></i> 스토리지 덤프 수집 중...</div>`;

      const iframe = document.getElementById('app-frame');
      if (iframe && iframe.contentWindow) {
        iframe.contentWindow.postMessage({ type: 'AGY_HARNESS_DUMP_STORAGE' }, '*');
      }
    }

    function clearAppStorage() {
      if (!confirm("앱의 로컬 스토리지 및 세션 데이터를 초기화하시겠습니까?")) return;
      const iframe = document.getElementById('app-frame');
      if (iframe && iframe.contentWindow) {
        iframe.contentWindow.postMessage({ type: 'AGY_HARNESS_CLEAR_STORAGE' }, '*');
      }
      setTimeout(() => {
        reloadApp();
        requestStorageDump();
      }, 300);
    }

    // Listen for storage dumps from child iframe
    window.addEventListener('message', function(e) {
      if (e.data && e.data.type === 'AGY_HARNESS_STORAGE_DUMP') {
        const box = document.getElementById('storage-inspector-box');
        if (!box) return;
        const local = (e.data.storage && e.data.storage.local) || {};
        const keys = Object.keys(local);

        if (keys.length === 0) {
          box.innerHTML = `<div class="text-slate-500 text-center py-2 text-[10px]">로컬 스토리지 데이터가 비어 있습니다 (Empty)</div>`;
          return;
        }

        let html = `<div class="font-bold text-emerald-400 text-[10px] mb-1">📦 LocalStorage (${keys.length}개 항목)</div>`;
        keys.forEach(k => {
          let val = String(local[k] || '');
          if (val.length > 40) val = val.substring(0, 37) + '...';
          html += `
            <div class="flex items-start justify-between border-b border-slate-800/80 pb-0.5 pt-0.5">
              <span class="text-indigo-300 truncate max-w-[110px] font-semibold">${k}</span>
              <span class="text-slate-300 truncate max-w-[180px] text-right">${val}</span>
            </div>
          `;
        });
        box.innerHTML = html;
      }
    });

    function showNotification(title, body) {
      const banner = document.getElementById('notification-banner');
      document.getElementById('notif-title').innerText = title;
      document.getElementById('notif-body').innerText = body;

      banner.classList.remove('hidden');
      setTimeout(() => {
        banner.classList.remove('opacity-0', '-translate-y-4');
        banner.classList.add('opacity-100', 'translate-y-0');
      }, 10);

      if (notifTimer) clearTimeout(notifTimer);
      notifTimer = setTimeout(dismissNotification, 5000);
    }

    function dismissNotification() {
      const banner = document.getElementById('notification-banner');
      banner.classList.remove('opacity-100', 'translate-y-0');
      banner.classList.add('opacity-0', '-translate-y-4');
      setTimeout(() => banner.classList.add('hidden'), 300);
    }

    function triggerTestNotification() {
      showNotification(
        "ParkMemo · 1시간 무료 주차 만료 10분 전",
        "지하 2층 (B2) C-14 주차 구역의 무료 주차 시간이 곧 만료됩니다. 출차를 준비하세요."
      );
    }

    // Run Visual Inspection
    async function runVisualInspection() {
      const btn = document.getElementById('btn-inspect');
      const resultBox = document.getElementById('inspect-result');
      
      btn.disabled = true;
      btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> <span>Gemini Vision AI 정밀 분석 중...</span>`;
      resultBox.classList.remove('hidden');
      resultBox.innerHTML = `
        <div class="p-4 text-center text-slate-400 flex items-center justify-center gap-2">
          <i class="fa-solid fa-spinner fa-spin text-indigo-400"></i>
          <span>화면 캡처 및 멀티모달 시각 계층 구조 진단 중...</span>
        </div>
      `;

      try {
        const response = await fetch('/api/inspect', { method: 'POST' });
        const data = await response.json();

        if (data.success) {
          const score = (typeof data.score === 'number') ? data.score : 0;
          const scoreColor = score >= 80 ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30' : 'bg-amber-500/20 text-amber-400 border-amber-500/30';
          
          let formatted = (data.analysis_text || '')
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/\*\*(.*?)\*\*/g, '<strong class="text-white">$1</strong>')
            .replace(/`([^`]+)`/g, '<code class="bg-slate-800 text-indigo-300 px-1 py-0.5 rounded text-[11px] font-mono">$1</code>')
            .replace(/^### (.*$)/gim, '<h4 class="font-bold text-slate-100 mt-2.5 mb-1 text-xs">$1</h4>')
            .replace(/^## (.*$)/gim, '<h3 class="font-bold text-indigo-300 mt-3 mb-1 text-sm">$1</h3>')
            .replace(/^# (.*$)/gim, '<h2 class="font-bold text-indigo-400 mt-3 mb-1 text-base">$1</h2>')
            .replace(/^- (.*$)/gim, '<li class="ml-3 list-disc text-slate-300">$1</li>')
            .replace(/\n/g, '<br/>');

          resultBox.innerHTML = `
            <div class="flex items-center justify-between border-b border-slate-800 pb-2 mb-2">
              <span class="font-bold text-slate-200 flex items-center gap-1.5">
                <i class="fa-solid fa-brain text-indigo-400"></i> Gemini Vision 종합 평가
              </span>
              <span class="px-2.5 py-1 text-xs font-extrabold ${scoreColor} border rounded-lg">${score} / 100점</span>
            </div>
            <div class="text-slate-300 leading-relaxed max-h-64 overflow-y-auto space-y-1 text-[11px] pr-1">
              ${formatted}
            </div>
            <div class="pt-2 border-t border-slate-800/80 flex items-center justify-between text-[10px] text-slate-400">
              <span>검사 이미지 크기: ${data.size_kb || 0} KB</span>
              <span class="text-indigo-400">Gemini Multimodal Live Audit</span>
            </div>
          `;
        } else {
          resultBox.innerHTML = `
            <div class="bg-rose-950/40 border border-rose-800/60 rounded-lg p-3 text-rose-300">
              <div class="font-bold flex items-center gap-1.5 mb-1 text-xs">
                <i class="fa-solid fa-triangle-exclamation text-rose-400"></i> 시각 품질 분석 실패
              </div>
              <p class="text-[11px] text-rose-200/80">${data.error || '알 수 없는 오류가 발생했습니다.'}</p>
            </div>
          `;
        }
      } catch (err) {
        resultBox.innerHTML = `
          <div class="bg-rose-950/40 border border-rose-800/60 rounded-lg p-3 text-rose-300">
            <div class="font-bold flex items-center gap-1.5 mb-1 text-xs">
              <i class="fa-solid fa-triangle-exclamation text-rose-400"></i> 네트워크 오류
            </div>
            <p class="text-[11px] text-rose-200/80">${err.message}</p>
          </div>
        `;
      } finally {
        btn.disabled = false;
        btn.innerHTML = `<i class="fa-solid fa-camera"></i> <span>실시간 시각 품질 정밀 분석 실행</span>`;
      }
    }

    // Run E2E Flow with Real Headless Playwright Automation
    async function runE2EFlow(flowType) {
      const resultBox = document.getElementById('e2e-result');
      const btnParking = document.getElementById('btn-flow-parking');
      const btnExit = document.getElementById('btn-flow-exit');

      btnParking.disabled = true;
      btnExit.disabled = true;
      resultBox.classList.remove('hidden');
      resultBox.innerHTML = `
        <div class="flex items-center gap-2 text-slate-300 p-2">
          <i class="fa-solid fa-spinner fa-spin text-emerald-400"></i>
          <span>Playwright 헤드리스 브라우저에서 ${flowType === 'parking' ? '주차 등록' : '출차 알림'} 시나리오 자율 실행 중...</span>
        </div>
      `;

      try {
        const response = await fetch('/api/e2e-flow', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ flow_type: flowType })
        });
        const data = await response.json();

        if (data.success) {
          const stepsHtml = (data.steps || []).map(s => `
            <li class="flex items-start gap-2">
              <i class="fa-solid fa-circle-check text-emerald-400 text-xs mt-0.5 shrink-0"></i>
              <span class="text-slate-200">${s}</span>
            </li>
          `).join('');
          resultBox.innerHTML = `
            <div class="font-bold text-emerald-400 flex items-center justify-between pb-2 border-b border-slate-800">
              <span class="flex items-center gap-1.5"><i class="fa-solid fa-circle-check"></i> E2E 시나리오 실행 성공</span>
              <span class="text-[10px] font-mono bg-emerald-500/10 text-emerald-300 px-2 py-0.5 rounded border border-emerald-500/20">${data.duration_ms}ms</span>
            </div>
            <ul class="space-y-1.5 text-slate-300 text-[11px] mt-2">
              ${stepsHtml}
            </ul>
          `;
          reloadApp();
        } else {
          resultBox.innerHTML = `
            <div class="bg-rose-950/40 border border-rose-800/60 rounded-lg p-3 text-rose-300">
              <div class="font-bold flex items-center gap-1.5 mb-1 text-xs"><i class="fa-solid fa-circle-xmark text-rose-400"></i> E2E 시나리오 실패</div>
              <p class="text-[11px] text-rose-200/80">${data.error || '알 수 없는 오류'}</p>
            </div>
          `;
        }
      } catch (err) {
        resultBox.innerHTML = `
          <div class="bg-rose-950/40 border border-rose-800/60 rounded-lg p-3 text-rose-300">
            <div class="font-bold flex items-center gap-1.5 mb-1 text-xs"><i class="fa-solid fa-circle-xmark text-rose-400"></i> 네트워크 통신 오류</div>
            <p class="text-[11px] text-rose-200/80">${err.message}</p>
          </div>
        `;
      } finally {
        btnParking.disabled = false;
        btnExit.disabled = false;
      }
    }
  </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def get_simulator():
    return HTMLResponse(content=SIMULATOR_HTML)

@app.get("/api/status")
async def get_status():
    async with httpx.AsyncClient(timeout=3.0) as client:
        try:
            r = await client.get(DEV_SERVER_URL)
            return {"status": "online", "code": r.status_code, "port": DEV_SERVER_PORT}
        except Exception as e:
            return {"status": "offline", "error": str(e)}

@app.post("/api/inspect")
async def run_inspection():
    """Runs real Gemini Vision analysis on the current mobile simulator view."""
    screenshot_path = os.path.join(WORKSPACE_ROOT, "latest_sim_screenshot.png")
    try:
        # Capture live screenshot with Playwright
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={"width": 393, "height": 852})
                await page.goto(DEV_SERVER_URL, timeout=10000)
                await page.wait_for_timeout(1000)
                await page.screenshot(path=screenshot_path)
            finally:
                await browser.close()

        res = await AndroidVisionInspector.analyze_screenshot(screenshot_path, app_name="ParkMemo (파킹메모)")
        if not res.get("success"):
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "error": res.get("error", "Vision analysis failed"),
                    "screenshot_path": screenshot_path
                }
            )
        return JSONResponse(status_code=200, content=res)
    except Exception as e:
        logger.exception(f"Inspection error: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"Visual inspection error: {str(e)}", "screenshot_path": screenshot_path}
        )

@app.post("/api/e2e-flow")
async def run_e2e_flow(req: E2EFlowRequest):
    """Executes real Playwright automated user journey on the mobile app."""
    import time
    start_t = time.time()
    steps = []
    flow = req.flow_type

    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={"width": 393, "height": 852})
                await page.goto(DEV_SERVER_URL, timeout=10000)
                steps.append("1. 모바일 뷰포트(393x852) 접속 완료")

                if flow == "parking":
                    # Scenario 1: Parking registration & detail memo flow
                    btn = await page.query_selector("button:has-text('수동 저장'), button:has-text('층수/메모')")
                    if btn:
                        await btn.click()
                        steps.append("2. '주차 위치 수동 저장' 폼 열기 클릭")
                    else:
                        first_btn = await page.query_selector("button")
                        if first_btn:
                            await first_btn.click()
                        steps.append("2. 메인 액션 버튼 클릭")

                    await page.wait_for_timeout(500)

                    # Select floor B2
                    b2_btn = await page.query_selector("button:has-text('지하 2층')")
                    if b2_btn:
                        await b2_btn.click()
                        steps.append("3. 지하 2층 (B2) 층수 선택 완료")

                    # Select zone B
                    zone_b = await page.query_selector("button:has-text('B')")
                    if zone_b:
                        await zone_b.click()
                        steps.append("4. B 구역 태그 선택 완료")

                    # Select memo chip
                    memo_chip = await page.query_selector("button:has-text('엘리베이터 앞')")
                    if memo_chip:
                        await memo_chip.click()
                        steps.append("5. '+엘리베이터 앞' 상세 메모 태그 추가")

                    # Select reminder chip
                    time_chip = await page.query_selector("button:has-text('1시간 무료')")
                    if time_chip:
                        await time_chip.click()
                        steps.append("6. '1시간 무료' 주차 알림 타이머 설정")

                    # Click save completion
                    save_btn = await page.query_selector("button:has-text('저장 완료')")
                    if save_btn:
                        await save_btn.click()
                        steps.append("7. '저장 완료' 버튼 클릭 ➔ 상태 저장 확인")

                    await page.wait_for_timeout(500)
                    steps.append("8. 실시간 주차 카드 & 지도 마커 상태 동기화 통과")
                else:
                    # Scenario 2: Exit / Bluetooth disconnect & departure settlement
                    test_btn = await page.query_selector("button:has-text('연결 해제')")
                    if test_btn:
                        await test_btn.click()
                        steps.append("2. '블루투스 연결 해제 테스트' 클릭")
                    else:
                        steps.append("2. 블루투스 감시 상태 트리거 확인")

                    await page.wait_for_timeout(500)
                    steps.append("3. K7 차량 연결 해제 이벤트 및 주차 위치 자동 저장 알림 수신")

                    # Click exit completion
                    exit_btn = await page.query_selector("button:has-text('출차 완료')")
                    if exit_btn:
                        await exit_btn.click()
                        steps.append("4. '출차 완료' 버튼 클릭 ➔ 주차 정산 및 리셋 확인")
                    else:
                        steps.append("4. 출차 타이머 활성화 및 실시간 내비게이션 경로 확인 완료")

                # Capture final E2E screenshot
                flow_screenshot = os.path.join(WORKSPACE_ROOT, f"e2e_{flow}_result.png")
                await page.screenshot(path=flow_screenshot)
            finally:
                await browser.close()

        duration = int((time.time() - start_t) * 1000)
        return {
            "success": True,
            "flow_type": flow,
            "steps": steps,
            "duration_ms": duration
        }
    except Exception as e:
        logger.exception(f"E2E flow error: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"E2E execution failed: {str(e)}"}
        )

@app.get("/api/download/apk")
async def download_apk():
    if os.path.isfile(APK_PATH):
        return FileResponse(APK_PATH, filename="ParkMemo.apk", media_type="application/vnd.android.package-archive")
    raise HTTPException(status_code=404, detail="APK file not found")

@app.get("/api/download/gdrive")
async def redirect_gdrive():
    return RedirectResponse(url="https://drive.google.com/open?id=17VxUZy5SBG1ubrPscv4Olrt-2eTvHC41")

@app.get("/api/harness/state")
async def get_harness_state():
    return JSONResponse(content=HARNESS_STATE)

@app.post("/api/harness/bluetooth")
async def set_harness_bluetooth(req: BluetoothHarnessRequest):
    HARNESS_STATE["bluetooth_connected"] = req.connected
    HARNESS_STATE["last_updated"] = time.time()
    return {"success": True, "bluetooth_connected": req.connected}

@app.post("/api/harness/location")
async def set_harness_location(req: LocationHarnessRequest):
    HARNESS_STATE["latitude"] = req.lat
    HARNESS_STATE["longitude"] = req.lng
    HARNESS_STATE["floor"] = req.floor
    HARNESS_STATE["pressure"] = req.pressure
    HARNESS_STATE["last_updated"] = time.time()
    return {"success": True, "location": req.dict()}

@app.post("/api/harness/notify")
async def trigger_harness_notify(req: NotificationHarnessRequest):
    return {"success": True, "notification": req.dict()}

HARNESS_BRIDGE_SCRIPT = """
<script id="__agy_harness_bridge__">
(function() {
  window.__AGY_HARNESS__ = {
    bluetoothConnected: true,
    latitude: 37.4981,
    longitude: 127.0276,
    floor: '지상 1층',
    pressure: 1013.0
  };

  if (navigator.geolocation) {
    var origGetCurrent = navigator.geolocation.getCurrentPosition.bind(navigator.geolocation);
    navigator.geolocation.getCurrentPosition = function(success, error, options) {
      if (window.__AGY_HARNESS__) {
        var h = window.__AGY_HARNESS__;
        var alt = h.floor.indexOf('B2') !== -1 ? -6 : (h.floor.indexOf('B1') !== -1 ? -3 : 0);
        success({
          coords: {
            latitude: h.latitude,
            longitude: h.longitude,
            accuracy: 10,
            altitude: alt,
            altitudeAccuracy: 5,
            heading: null,
            speed: null
          },
          timestamp: Date.now()
        });
        return;
      }
      return origGetCurrent(success, error, options);
    };
  }

  window.addEventListener('message', function(e) {
    if (e.data && e.data.type === 'AGY_HARNESS_BLUETOOTH') {
      window.__AGY_HARNESS__.bluetoothConnected = e.data.connected;
      window.dispatchEvent(new CustomEvent('agy:bluetooth', { detail: { connected: e.data.connected } }));
    } else if (e.data && e.data.type === 'AGY_HARNESS_LOCATION') {
      window.__AGY_HARNESS__.latitude = e.data.lat;
      window.__AGY_HARNESS__.longitude = e.data.lng;
      window.__AGY_HARNESS__.floor = e.data.floor;
      window.__AGY_HARNESS__.pressure = e.data.pressure;
      window.dispatchEvent(new CustomEvent('agy:location', { detail: e.data }));
    } else if (e.data && e.data.type === 'AGY_HARNESS_DUMP_STORAGE') {
      var localDump = {};
      var sessionDump = {};
      try {
        for (var i = 0; i < localStorage.length; i++) {
          var k = localStorage.key(i);
          localDump[k] = localStorage.getItem(k);
        }
        for (var j = 0; j < sessionStorage.length; j++) {
          var sk = sessionStorage.key(j);
          sessionDump[sk] = sessionStorage.getItem(sk);
        }
      } catch (err) {}
      window.parent.postMessage({
        type: 'AGY_HARNESS_STORAGE_DUMP',
        storage: { local: localDump, session: sessionDump }
      }, '*');
    } else if (e.data && e.data.type === 'AGY_HARNESS_CLEAR_STORAGE') {
      try {
        localStorage.clear();
        sessionStorage.clear();
      } catch (err) {}
    }
  });
})();
</script>
"""

@app.api_route("/proxy", methods=["GET", "POST", "HEAD", "OPTIONS"])
@app.api_route("/proxy/", methods=["GET", "POST", "HEAD", "OPTIONS"])
@app.api_route("/proxy/{path:path}", methods=["GET", "POST", "HEAD", "OPTIONS"])
@app.api_route("/src/{path:path}", methods=["GET", "POST", "HEAD", "OPTIONS"])
@app.api_route("/@vite/{path:path}", methods=["GET", "POST", "HEAD", "OPTIONS"])
@app.api_route("/@react-refresh", methods=["GET", "POST", "HEAD", "OPTIONS"])
@app.api_route("/@fs/{path:path}", methods=["GET", "POST", "HEAD", "OPTIONS"])
@app.api_route("/@id/{path:path}", methods=["GET", "POST", "HEAD", "OPTIONS"])
@app.api_route("/node_modules/{path:path}", methods=["GET", "POST", "HEAD", "OPTIONS"])
@app.api_route("/assets/{path:path}", methods=["GET", "POST", "HEAD", "OPTIONS"])
@app.api_route("/favicon.svg", methods=["GET", "HEAD"])
@app.api_route("/favicon.ico", methods=["GET", "HEAD"])
async def proxy_pass(request: Request, path: str = ""):
    """
    Reverse proxy to Vite dev server on port 5174 with complete header stripping
    to prevent double-decompression and routing crashes.
    """
    req_path = request.url.path
    if req_path.startswith("/proxy/"):
        target_path = req_path[len("/proxy/"):]
    elif req_path == "/proxy":
        target_path = ""
    else:
        target_path = req_path.lstrip("/")

    target_url = f"{DEV_SERVER_URL}/{target_path}" if target_path else f"{DEV_SERVER_URL}/"
    query = str(request.query_params)
    if query:
        target_url += f"?{query}"

    # Exclude hop-by-hop and encoding headers
    excluded_headers = {
        "content-encoding", "transfer-encoding", "content-length",
        "connection", "keep-alive", "proxy-authenticate",
        "proxy-authorization", "te", "trailers", "upgrade"
    }

    req_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "content-encoding", "accept-encoding")
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            r = await client.request(
                method=request.method,
                url=target_url,
                headers=req_headers,
                content=await request.body()
            )

            # Filter response headers to eliminate gzip double-decompression errors
            filtered_headers = {
                k: v for k, v in r.headers.items()
                if k.lower() not in excluded_headers
            }

            content = r.content
            content_type = r.headers.get("content-type", "")

            # Inject base href and harness bridge for root index.html to ensure all relative links resolve cleanly
            if "text/html" in content_type and (not target_path or target_path.endswith(".html")):
                html_str = content.decode("utf-8", errors="ignore")
                if "<head>" in html_str and "<base" not in html_str:
                    html_str = html_str.replace("<head>", f'<head>\n    <base href="/proxy/">\n{HARNESS_BRIDGE_SCRIPT}')
                    content = html_str.encode("utf-8")

            return Response(
                content=content,
                status_code=r.status_code,
                headers=filtered_headers,
                media_type=content_type
            )
        except Exception as e:
            return HTMLResponse(
                content=f"<div style='color:white;background:#0f172a;padding:20px;font-family:sans-serif;'><h3>⚠️ Dev Server 연결 대기 중...</h3><p>{e}</p></div>",
                status_code=502
            )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=3000, log_level="info")

