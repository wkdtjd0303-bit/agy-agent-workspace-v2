import os
import re

files_to_check = [
    "bot.py",
    "auto_pilot.py",
    "trio_agent_runner.py",
    "engine_manager.py",
    "model_manager.py",
    "telemetry_status_manager.py",
    "session_manager.py",
    "commander.py",
]

patterns = [
    r'@tasks\.loop',
    r'asyncio\.create_task',
    r'threading\.Thread',
    r'subprocess\.run',
    r'subprocess\.Popen',
    r'os\.system',
    r'asyncio\.create_subprocess',
    r'schedule',
    r'cron',
    r'periodic',
    r'interval',
    r'heartbeat',
    r'keepalive',
    r'antigravity',
    r'agy',
    r'api_key',
    r'gemini',
    r'claude',
]

for filename in files_to_check:
    filepath = os.path.join("/home/jangsung/Desktop/autocoding/!05_agy_agent/agy_agent", filename)
    if not os.path.exists(filepath):
        print(f"NOT FOUND: {filename}")
        continue
    
    print(f"--- {filename} ---")
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    for i, line in enumerate(lines):
        line = line.strip()
        for p in patterns:
            if re.search(p, line, re.IGNORECASE):
                print(f"L{i+1}: {line}")
                break
