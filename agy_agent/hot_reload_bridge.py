import os
import sys
import ast
import logging

logger = logging.getLogger("HotReloadBridge")

_in_sync = False

def sync_in_memory_state():
    """
    Synchronizes in-memory bot state without restarting the process:
    1. Updates all existing ChannelSession instances in session_manager._sessions to use the latest ChannelSession class.
    2. Recompiles and re-executes updated functions from bot.py (mode_command, execute_channel_prompt, pair_command, reload_helper_modules) into sys.modules['__main__'].
    3. Rebinds bot command callbacks in bot.all_commands so Discord commands immediately use the new code.
    """
    global _in_sync
    if _in_sync:
        return True
    _in_sync = True
    try:
        main = sys.modules.get("__main__")
        if not main or (not hasattr(main, "bot") and not hasattr(main, "session_manager") and not getattr(main, "__file__", "").endswith("bot.py")):
            main = sys.modules.get("bot")
        if not main or (not hasattr(main, "bot") and not hasattr(main, "session_manager") and not getattr(main, "__file__", "").endswith("bot.py")):
            return False

        # 1.0 Invalidate import caches to reflect fresh code files immediately
        try:
            import importlib
            importlib.invalidate_caches()
        except Exception as _inv_err:
            logger.debug(f"[HotReloadBridge] invalidate_caches notice: {_inv_err}")

        # 1. Update existing ChannelSession instances and SessionManager class
        try:
            import session_manager
            ChannelSessionClass = session_manager.ChannelSession
            SessionManagerClass = getattr(session_manager, "SessionManager", None)
            if hasattr(main, "session_manager"):
                if SessionManagerClass and main.session_manager.__class__ is not SessionManagerClass:
                    main.session_manager.__class__ = SessionManagerClass
                if hasattr(main.session_manager, "_sessions"):
                    for sess in main.session_manager._sessions.values():
                        if sess.__class__ is not ChannelSessionClass:
                            sess.__class__ = ChannelSessionClass
                        if not hasattr(sess, "_extended_qa_conversation_ids") or not isinstance(getattr(sess, "_extended_qa_conversation_ids", None), dict):
                            sess._extended_qa_conversation_ids = {}
                        if not hasattr(sess, "qa2_conversation_id"):
                            sess.qa2_conversation_id = None
        except Exception as e:
            logger.debug(f"[HotReloadBridge] Error updating sessions class: {e}")

        # 1.1 Ensure extended_qa_bots registry exists in main
        if not isinstance(getattr(main, "extended_qa_bots", None), dict):
            qa2_client = getattr(main, "qa2_bot", None)
            main.extended_qa_bots = {2: qa2_client} if qa2_client else {}
            logger.info("[HotReloadBridge] Initialized extended_qa_bots registry in main.")

        # 1.2 Explicitly ensure EvidenceValidator is bridged into main with fresh reload
        try:
            import importlib
            import evidence_validator
            importlib.reload(evidence_validator)
            main.EvidenceValidator = evidence_validator.EvidenceValidator
        except Exception as _ev_err:
            logger.debug(f"[HotReloadBridge] Error bridging EvidenceValidator: {_ev_err}")

        # 1.3 Ensure critical helper modules are reloaded prior to recompiling bot.py
        for _crit_mod in ("discord_utils", "model_manager", "auto_scheduler"):
            if _crit_mod in sys.modules:
                try:
                    import importlib
                    importlib.reload(sys.modules[_crit_mod])
                except Exception as _mod_err:
                    logger.debug(f"[HotReloadBridge] Pre-reload {_crit_mod} notice: {_mod_err}")

        # 2. Extract and compile ALL top-level imports, classes, and functions from bot.py
        bot_py_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py")
        if os.path.exists(bot_py_path):
            try:
                with open(bot_py_path, "r", encoding="utf-8") as f:
                    source = f.read()

                tree = ast.parse(source, filename="bot.py")
                extracted = []
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            node.decorator_list = []  # strip decorators so we can rebind manually
                        extracted.append(node)

                if extracted:
                    new_module = ast.Module(body=extracted, type_ignores=[])
                    ast.fix_missing_locations(new_module)
                    compiled = compile(new_module, "bot.py", "exec")
                    targets = [main]
                    bot_module = sys.modules.get("bot")
                    if bot_module and bot_module is not main:
                        targets.append(bot_module)
                    for tgt in targets:
                        exec(compiled, tgt.__dict__)

                    # 2.1 Dynamically upgrade classes of active UI view instances
                    try:
                        mm_mod = sys.modules.get("model_manager")
                        if mm_mod and hasattr(mm_mod, "ModelDashboardView") and hasattr(mm_mod.ModelDashboardView, "_active_dashboards"):
                            for _dash in list(mm_mod.ModelDashboardView._active_dashboards):
                                if _dash.__class__ is not mm_mod.ModelDashboardView:
                                    _dash.__class__ = mm_mod.ModelDashboardView
                        auto_dash_cls = main.__dict__.get("AutoDashboardView")
                        if auto_dash_cls and hasattr(auto_dash_cls, "_active_dashboards"):
                            for _adash in list(auto_dash_cls._active_dashboards):
                                if _adash.__class__ is not auto_dash_cls:
                                    _adash.__class__ = auto_dash_cls
                    except Exception as _up_err:
                        logger.debug(f"[HotReloadBridge] View instances upgrade notice: {_up_err}")

                    # Also record _raw_<name> for each extracted function to prevent command wrapper pollution
                    for node in extracted:
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            fn_obj = main.__dict__.get(node.name)
                            if callable(fn_obj) and not hasattr(fn_obj, "invoke"):
                                main.__dict__[f"_raw_{node.name}"] = fn_obj

                    # Synchronize critical function aliases after recompilation
                    if "execute_all_in_one_hot_reset" in main.__dict__:
                        main.perform_master_reset = main.__dict__["execute_all_in_one_hot_reset"]
                    if bot_module and hasattr(bot_module, "execute_all_in_one_hot_reset"):
                        bot_module.perform_master_reset = getattr(bot_module, "execute_all_in_one_hot_reset")

                # [🔒 Hot-Reload Fix] discord 이벤트 핸들러를 모든 봇 클라이언트에 대해
                # 현재 소스 기준으로 재바인딩한다.
                # - 기존에는 on_message/on_ready/on_guild_channel_update 3개만 하드코딩되어
                #   on_command_error/on_error 변경이 !reset 후에도 반영되지 않았다.
                # - 또한 commander_bot.on_ready 를 main.on_ready(=메인 봇 핸들러)로 덮어써
                #   on_commander_ready 가 유실되는 결함이 있었다.
                # 각 클라이언트의 이벤트 속성이 가리키는 함수의 __name__ 을 기준으로
                # main 에 재컴파일된 최신 함수로 교체한다. (커맨드 콜백 치유와 동일 원리)
                _clients = []
                for _cname in ("bot", "commander_bot", "qa_bot", "qa2_bot", "auditor_bot",
                               "server_manager_bot", "image_bot"):
                    _client = getattr(main, _cname, None)
                    if _client is not None:
                        _clients.append(_client)
                _ext_clients = getattr(main, "extended_qa_bots", None)
                if isinstance(_ext_clients, dict):
                    _clients.extend([c for c in _ext_clients.values() if c is not None])
                for _client in _clients:
                    for _evt, _handler in list(vars(_client).items()):
                        if not _evt.startswith("on_"):
                            continue
                        _fn = getattr(_handler, "__func__", _handler)
                        _fn_name = getattr(_fn, "__name__", None)
                        _latest = main.__dict__.get(_fn_name) if _fn_name else None
                        if callable(_latest) and _latest is not _fn:
                            try:
                                setattr(_client, _evt, _latest)
                            except Exception as _evt_err:
                                logger.debug(f"[HotReloadBridge] Failed rebinding event {_evt} ({_fn_name}): {_evt_err}")

                if callable(getattr(main, "init_extended_qa_bots", None)):
                    try:
                        main.init_extended_qa_bots()
                    except Exception as _init_err:
                        logger.debug(f"[HotReloadBridge] init_extended_qa_bots notice: {_init_err}")

                if callable(getattr(main, "ensure_image_bot_started", None)):
                    try:
                        main.ensure_image_bot_started()
                    except Exception as _c_err:
                        logger.debug(f"[HotReloadBridge] ensure_image_bot_started notice: {_c_err}")

                if callable(getattr(main, "ensure_qa2_bot_started", None)):
                    try:
                        main.ensure_qa2_bot_started()
                    except Exception as _qa2_err:
                        logger.debug(f"[HotReloadBridge] ensure_qa2_bot_started notice: {_qa2_err}")

                if callable(getattr(main, "ensure_auditor_bot_started", None)):
                    try:
                        main.ensure_auditor_bot_started()
                    except Exception as _aud_err:
                        logger.debug(f"[HotReloadBridge] ensure_auditor_bot_started notice: {_aud_err}")
            except Exception as e:
                logger.error(f"[HotReloadBridge] Error recompiling bot.py functions: {e}")

        # 3. Rebind command callbacks in bot.all_commands ensuring valid Command objects
        if hasattr(main, "bot") and hasattr(main.bot, "all_commands"):
            from discord.ext import commands as ext_commands

            # 3.1 Automatically update callbacks for all registered commands whose function was refreshed
            for alias, cmd_obj in list(main.bot.all_commands.items()):
                if hasattr(cmd_obj, "callback"):
                    # Break circular or Command-as-callback reference on live command object
                    cb = cmd_obj.callback
                    visited = {id(cmd_obj)}
                    while hasattr(cb, "callback"):
                        if id(cb) in visited or getattr(cb, "callback", None) is cb:
                            cb = None
                            break
                        visited.add(id(cb))
                        cb = cb.callback
                    if cb is not None and callable(cb) and not hasattr(cb, "invoke"):
                        cmd_obj.callback = cb

                    if hasattr(cmd_obj.callback, "__name__"):
                        fn_name = cmd_obj.callback.__name__
                        if fn_name in main.__dict__ and callable(main.__dict__[fn_name]):
                            cand = main.__dict__[fn_name]
                            while hasattr(cand, "callback"):
                                if cand is cmd_obj or getattr(cand, "callback", None) is cand:
                                    cand = None
                                    break
                                cand = cand.callback
                            if cand is not None and cand is not cmd_obj and callable(cand) and not hasattr(cand, "invoke"):
                                cmd_obj.callback = cand

            # 3.2 Explicit safety net rebind helper
            def _rebind_target_command(cmd_func_name, aliases):
                func = main.__dict__.get(f"_raw_{cmd_func_name}") or main.__dict__.get(cmd_func_name)
                if not callable(func):
                    return

                # Unwrap if func is a Command object down to the underlying function
                while hasattr(func, "callback"):
                    if func.callback is func:
                        func = None
                        break
                    func = func.callback

                if func is None or not callable(func) or hasattr(func, "invoke"):
                    return

                # Find an existing Command object in all_commands or via get_command
                existing_cmd = None
                for alias in aliases:
                    cand = main.bot.all_commands.get(alias)
                    if cand is not None and hasattr(cand, "invoke") and hasattr(cand, "callback"):
                        existing_cmd = cand
                        break

                if existing_cmd is None and hasattr(main.bot, "get_command"):
                    cand = main.bot.get_command(aliases[0])
                    if cand is not None and hasattr(cand, "invoke"):
                        existing_cmd = cand

                if existing_cmd is not None:
                    if func is not existing_cmd and not hasattr(func, "invoke"):
                        existing_cmd._callback = func
                        try:
                            existing_cmd.callback = func
                        except Exception:
                            pass
                    existing_aliases = set(getattr(existing_cmd, "aliases", []))
                    for a in aliases[1:]:
                        if a not in existing_aliases:
                            existing_cmd.aliases.append(a)
                    for alias in aliases:
                        main.bot.all_commands[alias] = existing_cmd
                    setattr(main, cmd_func_name, existing_cmd)
                else:
                    new_cmd = ext_commands.Command(func, name=aliases[0], aliases=aliases[1:])
                    if hasattr(main.bot, "add_command"):
                        try:
                            main.bot.add_command(new_cmd)
                        except Exception:
                            pass
                    for alias in aliases:
                        main.bot.all_commands[alias] = new_cmd
                    setattr(main, cmd_func_name, new_cmd)

            _rebind_target_command("mode_command", ["mode", "모드", "동작모드"])
            _rebind_target_command("single_command", ["single", "solo", "단일", "싱글", "single+", "single_plus", "1+", "1plus", "1플러스"])
            _rebind_target_command("image_command", ["image", "img", "이미지", "이미지생성", "그려줘", "draw"])
            _rebind_target_command("pair_plus_command", ["pair+", "pair_plus", "2+", "2plus", "2플러스", "pair_quad", "quad_pair"])
            _rebind_target_command("mode3_command", ["mode3", "3", "자율3", "무인3", "3자율", "3차자율", "auto_trio"])
            _rebind_target_command("mode3_plus_command", ["mode3+", "mode3_plus", "3+", "3plus", "3플러스", "자율3+", "무인3+", "auto_multi"])
            _rebind_target_command("single_qa2_command", ["single_qa2", "qa2_solo", "solo_qa2", "qa2"])
            _rebind_target_command("pair_command", ["pair", "trio", "협업", "페어", "3중", "2"])
            _rebind_target_command("qa_command", ["qa", "qastatus", "qa_status", "다중qa", "qa목록"])
            _rebind_target_command("reset_command", ["reset", "reload", "restart", "hotreload", "리셋", "재시작", "초기화", "핫리셋"])
            _rebind_target_command("auto_command", ["auto", "자율", "오토", "autopilot", "autocode"])
            _rebind_target_command("mkdir_command", ["mkdir", "폴더", "폴더생성", "폴더관리", "folder", "rmdir", "trash", "mk", "디렉토리생성", "디렉토리관리"])
            _rebind_target_command("model_command", ["model", "모델", "modelselect"])
            _rebind_target_command("engine_command", ["engine", "ai", "cli", "엔진", "인공지능", "모델엔진", "engines"])
            _rebind_target_command("role_command", ["role", "역할", "botrole", "채널역할", "roles"])
            _rebind_target_command("identity_command", ["identity", "whoami", "나", "본체", "id", "정체성", "agent", "who"])
            _rebind_target_command("e2e_universal_command", ["e2e", "cross_test", "universal_test", "자율테스트", "e2e테스트", "통합테스트", "검증", "test"])
            _rebind_target_command("rounds_command", ["rounds", "라운드", "maxrounds", "round_limit", "roundcap"])
            _rebind_target_command("watchdog_command", ["watchdog", "safety", "guards", "감시견", "안전가드"])

            # Self-healing sanitize sweep: ensure all entries in all_commands have .invoke and valid callbacks
            for alias, cmd_obj in list(main.bot.all_commands.items()):
                if callable(cmd_obj) and not hasattr(cmd_obj, "invoke"):
                    logger.warning(f"[HotReloadBridge] Auto-healing non-Command entry '{alias}' into Command")
                    main.bot.all_commands[alias] = ext_commands.Command(cmd_obj, name=alias)
                elif hasattr(cmd_obj, "callback"):
                    if cmd_obj.callback is cmd_obj or hasattr(cmd_obj.callback, "invoke"):
                        logger.warning(f"[HotReloadBridge] Detected circular/invalid callback in '{alias}'. Healing...")
                        cand = (
                            main.__dict__.get(f"_raw_{alias}_command")
                            or main.__dict__.get(f"_raw_{alias}")
                            or main.__dict__.get(f"{alias}_command")
                            or main.__dict__.get(alias)
                        )
                        while hasattr(cand, "callback"):
                            if cand is cmd_obj or getattr(cand, "callback", None) is cand:
                                cand = None
                                break
                            cand = getattr(cand, "callback", None)
                        if callable(cand) and not hasattr(cand, "invoke"):
                            cmd_obj._callback = cand
                            try:
                                cmd_obj.callback = cand
                            except Exception:
                                pass
                        elif alias in ["mkdir", "폴더", "폴더생성", "폴더관리", "folder", "rmdir", "trash", "mk", "디렉토리생성", "디렉토리관리"]:
                            raw_mkdir = main.__dict__.get("_raw_mkdir_command") or main.__dict__.get("mkdir_command")
                            while hasattr(raw_mkdir, "callback"):
                                if raw_mkdir is cmd_obj or getattr(raw_mkdir, "callback", None) is raw_mkdir:
                                    raw_mkdir = None
                                    break
                                raw_mkdir = getattr(raw_mkdir, "callback", None)
                            if callable(raw_mkdir) and not hasattr(raw_mkdir, "invoke"):
                                cmd_obj._callback = raw_mkdir
                                try:
                                    cmd_obj.callback = raw_mkdir
                                except Exception:
                                    pass


        # 4. Synchronize live bot client presences to ensure active model names are reflected immediately
        try:
            bot_inst = getattr(main, "bot", None)
            if bot_inst is not None and hasattr(bot_inst, "is_ready") and callable(bot_inst.is_ready) and bot_inst.is_ready():
                import asyncio
                import config
                import discord
                coder_model = getattr(config, "CODER_MODEL", "gemini-3.8-flash-high")
                cmd_prefix = getattr(config, "COMMAND_PREFIX", "!")
                activity = discord.Activity(
                    type=discord.ActivityType.listening,
                    name=f"{cmd_prefix}help | Coder (초고속 개발) | {coder_model}"
                )
                try:
                    loop = asyncio.get_running_loop()
                    if loop and loop.is_running():
                        loop.create_task(bot_inst.change_presence(activity=activity))
                except RuntimeError:
                    pass
        except Exception as _pres_err:
            logger.debug(f"[HotReloadBridge] Failed syncing bot presence in hot reload: {_pres_err}")

        return True
    except Exception as e:
        logger.error(f"[HotReloadBridge] sync_in_memory_state error: {e}")
        return False
    finally:
        _in_sync = False
