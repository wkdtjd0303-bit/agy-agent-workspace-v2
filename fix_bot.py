import re

with open('agy_agent/bot.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the duplicate try/except block at 4606
pattern_duplicate = r"""                    try:
                        from model_manager import GlobalModelManager
                        try:
                            from model_manager import GlobalModelManager
                        except:
                            GlobalModelManager = None
                        cmd_model_disp = GlobalModelManager\.get_role_model_display\("commander"\) if GlobalModelManager else getattr\(config, "COMMANDER_MODEL", "gemini-3\.7-flash-middle"\)
                    except Exception:
                        cmd_model_disp = "Gemini 3\.7 Flash" """

replacement_duplicate = """                    try:
                        from model_manager import GlobalModelManager
                    except Exception:
                        GlobalModelManager = None
                    cmd_model_disp = GlobalModelManager.get_role_model_display("commander") if GlobalModelManager else getattr(config, "COMMANDER_MODEL", "gemini-3.7-flash-middle")"""

content = re.sub(pattern_duplicate, replacement_duplicate, content)

with open('agy_agent/bot.py', 'w', encoding='utf-8') as f:
    f.write(content)
