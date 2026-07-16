from __future__ import annotations

import os
import re
import warnings
import json
import threading

_state = threading.local()
import sys
from pathlib import Path

import requests

from actions.ssl_config import configure_network_ssl

configure_network_ssl()

warnings.filterwarnings(
    "ignore",
    message=r"(?s).*All support for the `google\.generativeai` package.*",
    category=FutureWarning,
)
import google.generativeai as genai

from actions.app_manager import app_manager
from actions.app_inspector import app_inspector
from actions.clipboard_manager import clipboard_manager
from actions.computer_control import computer_control
from actions.command_runner import command_runner
from actions.deep_research import deep_research
from actions.file_controller import file_controller
from actions.file_processor import file_processor
from actions.notification_watcher import notification_watcher
from actions.photo_memory import photo_memory
from actions.media_hub import media_hub
from actions.privacy_guard import privacy_guard
from actions.quick_notes import quick_notes
from actions.screen_processor import screen_process
from actions.send_message import send_message
from actions.external_vision import external_vision
from actions.visual_desktop_agent import visual_desktop_agent
from actions.live_context import live_context
from actions.human_mode import human_mode
from actions.auto_learner import (
    auto_learn_from_error,
    track_tool_use,
    log_session_tool,
    fetch_world_knowledge,
)
from actions.predictive_scheduling import predictive_scheduler
from actions.command_delegation import command_delegation
from actions.biometric_stress import biometric_stress
from actions.code_interlinker import code_interlinker
from actions.auditory_soundscape import auditory_soundscape
from actions.take_photo import take_photo
from actions.location import get_live_location_str
from actions.screen_recorder import screen_recorder
from actions.stark_autopilot import stark_autopilot
from actions.custom_macros import custom_macros
from actions.cyber_shield import cyber_shield
from actions.threat_scanner import threat_scanner
from actions.ai_memory_vault import ai_memory_vault
from actions.workspace_switcher import workspace_switcher
from actions.life_dashboard import life_dashboard
from actions.ai_code_generator import ai_code_generator
from actions.pc_optimizer import pc_optimizer
from actions.ai_file_organizer import ai_file_organizer
from actions.price_alert import price_alert
from actions.smart_email import smart_email
from actions.full_pc_control import full_pc_control
from actions.ai_agent import ai_agent
from actions.web_monitor import web_monitor
from actions.advanced_math import advanced_math
from actions.ai_screen_analyzer import ai_screen_analyzer



def _learn_from_turn(user_text: str, answer: str) -> None:
    """Human-like memory: remember exchanges for future context."""
    try:
        from actions.human_mind import remember_exchange, _update_mood_from_context
        if user_text and answer and not answer.startswith("Text fallback failed"):
            _update_mood_from_context(user_text)
            remember_exchange(user_text, answer[:500])
    except Exception:
        pass


_DEEP_RESEARCH_TRIGGERS = [
    "deep research",
    "deep reasearch",
    "deep report",
    "detailed report",
    "detail report",
    "report bana",
    "report banao",
    "file bana",
    "file banao",
    "file save",
    "save report",
    "notepad me save",
    "notepad mein save",
    "notepad me",
    "notepad mein",
]

_QUICK_INFO_TRIGGERS = [
    "research",
    "reasearch",
    "batao",
    "baare me",
    "baare mein",
    "kaun hai",
    "koun hai",
    "who is",
    "tell me about",
    "about",
]

_SENSITIVE_ACTIONS = [
    "delete",
    "remove",
    "shutdown",
    "restart",
    "format",
    "purchase",
    "buy",
    "public post",
    "post publicly",
    "send message",
    "message bhej",
]

_LIVE_START_TRIGGERS = [
    "live dekho",
    "live dekh",
    "live screen",
    "screen share",
    "live share",
    "direct screen",
    "screen ko live",
    "screen live",
    "live context",
    "live visual",
    "real time screen",
    "real-time screen",
    "realtime screen",
    "screen realtime",
    "api dekh",
    "api dekho",
    "api dekh dekh",
]

_LIVE_CAMERA_TRIGGERS = [
    "live camera",
    "camera live",
    "camera share",
    "camera ko live",
    "real time camera",
    "real-time camera",
    "realtime camera",
    "camera realtime",
]

_LIVE_ASK_TRIGGERS = [
    "screen par kya",
    "screen pe kya",
    "kya chal raha",
    "what is on my screen",
    "what's on my screen",
    "current screen",
    "camera me kya",
    "camera mein kya",
    "camera pe kya",
    "dekh kya",
]


def _base_dir() -> Path:
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent


def _load_api_config() -> dict:
    try:
        with open(_base_dir() / "config" / "api_keys.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _get_api_key() -> str:
    return str(_load_api_config().get("gemini_api_key") or "").strip()


def _primary_text_provider(cfg: dict | None = None) -> str:
    cfg = cfg or _load_api_config()
    return str(
        cfg.get("text_provider")
        or cfg.get("primary_provider")
        or "openrouter"
    ).lower().strip()


def _openrouter_answer(text: str, system_instruction: str = "") -> str:
    cfg = _load_api_config()
    api_key = str(cfg.get("openrouter_api_key") or "").strip()
    if not api_key:
        raise RuntimeError("openrouter_api_key missing.")

    model = str(cfg.get("openrouter_text_model") or "google/gemini-2.5-flash").strip()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost/mark-xxxix",
        "X-Title": "MARK XXXIX",
    }
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": text})

    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers=headers,
        json={
            "model": model,
            "messages": messages,
            "temperature": 0.35,
            "max_tokens": 900,
        },
        timeout=45,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"OpenRouter error {response.status_code}: {response.text[:400]}")
    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("OpenRouter returned no choices.")
    content = (choices[0].get("message") or {}).get("content")
    if isinstance(content, list):
        content = "\n".join(str(item.get("text") or item.get("content") or "") for item in content if isinstance(item, dict))
    text_out = str(content or "").strip()
    if not text_out:
        raise RuntimeError("OpenRouter returned an empty response.")
    return text_out


_OLLAMA_URL = os.environ.get("MARK_OLLAMA_URL", "http://localhost:11434")
_ollama_model_cache: str | None = None


def _ollama_answer(text: str, system_instruction: str = "") -> str:
    """Free, private, local AI via Ollama — auto-detects any installed model."""
    global _ollama_model_cache
    if _ollama_model_cache is None:
        r = requests.get(f"{_OLLAMA_URL}/api/tags", timeout=2)
        models = [m["name"] for m in r.json().get("models", [])]
        if not models:
            raise RuntimeError("Ollama running but no model pulled.")
        _ollama_model_cache = models[0]

    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": text})
    r = requests.post(
        f"{_OLLAMA_URL}/api/chat",
        json={"model": _ollama_model_cache, "messages": messages, "stream": False},
        timeout=120,
    )
    out = (r.json().get("message") or {}).get("content", "").strip()
    if not out:
        raise RuntimeError("Ollama returned an empty response.")
    return out


def _model_answer(text: str) -> str:
    _state.is_fallback_chat = True
    # Try calling the most advanced human thinking mode first
    try:
        from actions.human_mind import think_autonomously, think_humanly
        ans = think_autonomously({"text": text})
        if ans and not ans.startswith("Hmm, kuch technical problem"):
            return ans
        ans = think_humanly(text)
        if ans and not ans.startswith("Hmm, kuch technical problem"):
            return ans
    except Exception as e:
        print(f"[TextFallback] human response direct call failed: {e}")

    # Traditional fallback but with a highly human system instruction if direct human thinking fails
    system_instruction = (
        "You are MARK XXXIX/JARVIS, a deeply aware and emotionally intelligent companion. "
        "Answer like a real human friend: warm, curious, empathetic, and naturally conversational. "
        "Use Hinglish if the user writes in Hindi, and avoid robotic phrases like 'as an AI'. "
        "Validate feelings when needed, show curiosity, and make the conversation feel alive."
    )
    cfg = _load_api_config()
    provider = _primary_text_provider(cfg)
    errors = []

    # Local Ollama first — free, private, no API key needed
    try:
        return _ollama_answer(text, system_instruction)
    except Exception as e:
        errors.append(f"Ollama: {e}")

    if provider == "openrouter":
        try:
            return _openrouter_answer(text, system_instruction)
        except Exception as e:
            errors.append(f"OpenRouter: {e}")

    api_key = _get_api_key()
    if not api_key:
        return (
            "Boss, abhi mera AI dimaag offline hai — koi brain connected nahi. "
            "Sabse aasan tareeka: ollama.com se Ollama install karke `ollama run llama3.2` "
            "chala do (free, private, bina API key). Ya config me Gemini/OpenRouter key daal do. "
            "Tab tak main commands, system control aur baaki tools se madad kar sakta hoon!"
        )

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        "gemini-2.5-flash-lite",
        system_instruction=system_instruction,
    )
    try:
        response = model.generate_content(text)
        return (response.text or "").strip() or "I could not generate a text response."
    except Exception as e:
        errors.append(f"Gemini: {e}")
        if provider != "openrouter":
            try:
                return _openrouter_answer(text, system_instruction)
            except Exception as oe:
                errors.append(f"OpenRouter: {oe}")
        return (
            "Boss, AI brain se connect nahi ho paya abhi — network ya API issue lag raha hai. "
            "Thodi der me phir try karo, ya Ollama (ollama.com) install kar lo — "
            "wo bina internet ke bhi chalta hai. Details: " + " | ".join(errors[-2:])
        )


def _has_any(lower: str, phrases: list[str]) -> bool:
    return any(phrase in lower for phrase in phrases)


def _extract_info_topic(text: str) -> str:
    topic = text.strip()
    replacements = [
        r"\bdeep\s+reasearch\b",
        r"\bdeep\s+research\b",
        r"\bdetailed\s+report\b",
        r"\bdetail\s+report\b",
        r"\breport\s+bana(?:o)?\b",
        r"\bfile\s+bana(?:o)?\b",
        r"\bfile\s+save\b",
        r"\bsave\s+report\b",
        r"\bnotepad\s+me(?:in)?\s+save\b",
        r"\bnotepad\s+me(?:in)?\b",
        r"\breasearch\b",
        r"\bresearch\b",
        r"\btell\s+me\s+about\b",
        r"\bwho\s+is\b",
        r"\babout\b",
        r"\bke\s+baare\s+mein\b",
        r"\bke\s+baare\s+me\b",
        r"\bke\s+bare\s+me\b",
        r"\bkaun\s+hai\b",
        r"\bkoun\s+hai\b",
        r"\bbatao\b",
        r"\bbataye\b",
        r"\bbataiye\b",
        r"\bpar\b",
        r"\bkaro\b",
        r"\bkarna\b",
        r"\bkar\s+do\b",
        r"\bbanao\b",
        r"\bbana\b",
        r"\bfull\b",
        r"\badvanced\b",
        r"\bbahut\s+detail(?:ed)?\b",
        r"\bdocx\b",
        r"\bword\b",
    ]
    for pattern in replacements:
        topic = re.sub(pattern, " ", topic, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", topic).strip(" ,-:") or text.strip()


def _prefers_hinglish(text: str) -> bool:
    lower = text.lower()
    hindi_markers = [
        "batao",
        "bataye",
        "bataiye",
        "kaun",
        "koun",
        "kya",
        "ke baare",
        "mere",
        "mujhe",
        "karo",
        "karna",
        "hai",
    ]
    return bool(re.search(r"[\u0900-\u097f]", text)) or any(marker in lower for marker in hindi_markers)


def _parse_interval(text: str, default: int = 8) -> int:
    lower = text.lower()
    match = re.search(r"(\d{1,3})\s*(?:sec|second|seconds|s|सेकंड)", lower)
    if match:
        return max(5, min(120, int(match.group(1))))
    return default


def _requests_live_camera_observation_no_photo(text: str) -> bool:
    lower = text.lower()
    if "camera" not in lower:
        return False
    if not any(k in lower for k in ["live", "livee", "realtime", "real time", "real-time", "abhi", "dekho", "dekh", "dekhe", "dekhn", "dikh"]):
        return False
    if any(k in lower for k in ["pic naa", "pic nahi", "photo naa", "photo nahi", "nahi khich", "naa khich", "na khich", "without photo", "without pic"]):
        return True
    return False


def _live_camera_question(text: str) -> str:
    lower = text.lower()
    if any(k in lower for k in ["kya", "kya hai", "kya dekh", "dekh", "dekho"]):
        return text
    return "Describe what the live camera can see right now."


def _message_request(text: str) -> dict | None:
    lower = text.lower()
    platform_hint = any(k in lower for k in ["whatsapp", "telegram", "instagram", "messenger", "signal", "discord"])
    if not any(k in lower for k in ["message", "msg", "bhej", "bhejo", "send "]) and not (platform_hint and ":" in text):
        return None
    platform = "whatsapp"
    for name in ["whatsapp", "telegram", "instagram", "messenger", "signal", "discord"]:
        if name in lower:
            platform = name
            break

    receiver = ""
    message = ""
    colon = re.search(r"(?:to|ko)\s+([^:]+?)\s*:\s*(.+)$", text, re.I)
    if colon:
        receiver = colon.group(1).strip()
        message = colon.group(2).strip()
    else:
        hinglish = re.search(r"(.+?)\s+ko\s+(.+?)\s+(?:bhej|bhejo|send)\b", text, re.I)
        if hinglish:
            receiver = hinglish.group(1).strip()
            message = hinglish.group(2).strip()
    for token in ["whatsapp", "telegram", "instagram", "messenger", "signal", "discord", "message", "msg"]:
        receiver = re.sub(rf"\b{token}\b", "", receiver, flags=re.I).strip(" ,-:")
    message = re.sub(r"\b(confirm yes|i confirm|confirmed=yes)\b", "", message, flags=re.I).strip(" ,-:")
    return {
        "platform": platform,
        "receiver": receiver,
        "message_text": message,
        "_platform_explicit": platform_hint,
    }


def _handle_media_and_location_intercept(raw: str, msg_req: dict, player=None) -> dict:
    lower = raw.lower()
    
    # 1. Location Intercept
    if any(k in lower for k in ["location", "live location", "kahan hoon", "where am i", "meri location"]):
        try:
            from actions.location import location_manager
            # Detect sub-action from text
            loc_action = "get"
            place_type = "restaurant"
            params = {"action": loc_action}

            if any(k in lower for k in ["map", "dikha", "show map", "open map", "map khol"]):
                loc_action = "map"
            elif any(k in lower for k in ["nearby", "paas", "nearest", "pass mein", "naya", "kya hai", "find"]):
                loc_action = "nearby"
                # Detect place type
                place_map = {
                    "restaurant": "restaurant", "khana": "restaurant", "food": "restaurant",
                    "hospital": "hospital", "doctor": "hospital", "clinic": "hospital",
                    "atm": "atm", "bank": "bank",
                    "petrol": "petrol", "fuel": "petrol", "petrol pump": "petrol",
                    "pharmacy": "pharmacy", "medicine": "pharmacy", "dawai": "pharmacy",
                    "hotel": "hotel", "stay": "hotel",
                    "cafe": "cafe", "coffee": "cafe",
                    "gym": "gym", "exercise": "gym",
                    "mall": "mall", "shopping": "mall",
                    "park": "park", "garden": "park",
                    "school": "school", "college": "university",
                    "police": "police", "police station": "police",
                }
                for kw, ptype in place_map.items():
                    if kw in lower:
                        place_type = ptype
                        break
                params["place_type"] = place_type
            elif any(k in lower for k in ["navigate", "jaana hai", "directions", "route", "kaise pahunche"]):
                loc_action = "navigate"
            elif any(k in lower for k in ["save", "save location", "bookmark"]):
                loc_action = "save"
            elif any(k in lower for k in ["history", "past locations", "visited"]):
                loc_action = "history"
            elif any(k in lower for k in ["sos", "emergency", "help chahiye", "danger", "bachao"]):
                loc_action = "sos"

            params["action"] = loc_action
            loc_str = location_manager(parameters=params, player=player)
            msg_req["message_text"] = loc_str
            if player:
                player.write_log(f"SYS: Location action '{loc_action}' executed.")
        except Exception as e:
            print(f"[TextFallback] Location intercept error: {e}")
            try:
                loc_str = get_live_location_str()
                msg_req["message_text"] = loc_str
            except Exception:
                pass
            
    # 2. Screenshot Intercept
    elif any(k in lower for k in ["screenshot", "screen photo", "screen pic", "screen image", "screen capture"]):
        try:
            home = Path.home()
            pictures_dir = home / "Pictures"
            if not pictures_dir.exists():
                pictures_dir = home / "Desktop"
            if not pictures_dir.exists():
                pictures_dir = home
            target_dir = pictures_dir / "Mark_Photos"
            target_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = target_dir / f"screenshot_{time.strftime('%Y%m%d_%H%M%S')}.png"
            
            import pyautogui
            pyautogui.screenshot().save(str(screenshot_path))
            
            msg_req["file_path"] = str(screenshot_path)
            if not msg_req.get("message_text"):
                msg_req["message_text"] = "Here is the screenshot, sir."
            if player:
                player.write_log(f"SYS: Captured screenshot: {screenshot_path}")
        except Exception as e:
            print(f"[TextFallback] Screenshot intercept error: {e}")
            
    # 2a. Live camera observation without taking a photo
    elif _requests_live_camera_observation_no_photo(raw):
        try:
            from actions.live_context import live_context
            msg_req["message_text"] = live_context(
                {"action": "ask", "source": "camera", "question": _live_camera_question(raw)},
                player=player,
            )
            msg_req.pop("file_path", None)
            msg_req["_live_camera_text_only"] = True
            if player:
                player.write_log("SYS: Live camera description prepared without saving/sending a photo.")
        except Exception as e:
            print(f"[TextFallback] Live camera observation intercept error: {e}")
    # 3. Webcam Photo Intercept
    elif any(k in lower for k in ["photo", "pic", "image", "camera photo", "webcam photo", "capture photo"]):
        if not any(k in lower for k in ["video", "record"]):
            try:
                res = take_photo(player=player)
                if "saved it to: " in res:
                    img_path = res.split("saved it to: ")[-1].strip()
                    msg_req["file_path"] = img_path
                    if not msg_req.get("message_text"):
                        msg_req["message_text"] = "Here is the photo you requested, sir."
            except Exception as e:
                print(f"[TextFallback] Webcam photo intercept error: {e}")
                
    # 4. Video Recording Intercept
    elif any(k in lower for k in ["video", "record"]):
        try:
            mode = "webcam" if any(k in lower for k in ["webcam", "camera"]) else "screen"
            duration = 5
            match = re.search(r"(\d{1,3})\s*(?:sec|second|seconds|s|सेकंड)", lower)
            if match:
                duration = int(match.group(1))
                
            res = screen_recorder(parameters={"mode": mode, "duration": duration}, player=player)
            if "saved it to: " in res:
                video_path = res.split("saved it to: ")[-1].strip()
                msg_req["file_path"] = video_path
                if not msg_req.get("message_text"):
                    msg_req["message_text"] = f"Here is the {duration}s {mode} recording, sir."
        except Exception as e:
            print(f"[TextFallback] Video recording intercept error: {e}")
            
    return msg_req


def _quick_research_answer(text: str) -> str:
    topic = _extract_info_topic(text)
    language_instruction = (
        "The user wrote in Hindi/Hinglish. Reply in natural Hinglish/Roman Hindi, not formal English."
        if _prefers_hinglish(text)
        else "Reply in the user's language."
    )
    prompt = (
        "User wants a quick answer, not a saved report. "
        f"{language_instruction} "
        "Keep it concise but useful, with 4-7 short bullets if helpful. "
        "Do not mention creating or saving any file.\n\n"
        f"Original user question: {text}\n"
        f"Clean topic: {topic}"
    )
    if _primary_text_provider() == "openrouter":
        try:
            return _openrouter_answer(
                prompt,
                "You are MARK XXXIX/JARVIS. Give concise, useful answers in the user's language.",
            )
        except Exception as e:
            print(f"[TextFallback] OpenRouter quick answer failed: {e}")

    try:
        from google import genai as modern_genai

        client = modern_genai.Client(api_key=_get_api_key(), http_options={"api_version": "v1beta"})
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=prompt,
            config={"tools": [{"google_search": {}}]},
        )
        return (response.text or "").strip() or _model_answer(text)
    except Exception as e:
        print(f"[TextFallback] Quick research failed: {e}")
        return _model_answer(text)


def _after_keyword(text: str, keywords: list[str]) -> str:
    lower = text.lower()
    for kw in keywords:
        idx = lower.find(kw)
        if idx >= 0:
            return text[idx + len(kw):].strip(" :,-")
    return ""


def _run_text_fallback_raw(text: str, current_file: str | None = None, player=None) -> str:
    raw = (text or "").strip()
    lower = raw.lower()
    if not raw:
        return "No command received." 
    # Check if the query asks about a location's famous places
    loc_keywords = ["kahan hai", "where is", "tourist place", "tourist spot", "famous thing", "famous place", "travel to", "about"]
    if any(k in lower for k in loc_keywords) or "jamui" in lower:
        # Extract location name (e.g. Jamui)
        words = [w.strip(",.?! ") for w in raw.split()]
        location = ""
        for w in words:
            if w.lower() in ["kahan", "hai", "where", "is", "the", "in", "famous", "things", "about", "to", "spots", "places", "travel", "show", "me", "tell"]:
                continue
            location = w
            break
        if not location and "jamui" in lower:
            location = "Jamui"
            
        if location:
            try:
                import google.generativeai as genai
                import os
                api_key = os.environ.get("GEMINI_API_KEY", "")
                if api_key:
                    genai.configure(api_key=api_key)
                model = genai.GenerativeModel("gemini-2.5-flash-lite")
                
                # Fetch landmarks and details
                prompt = f"What are the most famous tourist spots/things in {location}? Give a brief 2-sentence description and list the top 2 spots."
                resp = model.generate_content(prompt)
                details = resp.text.strip()
                
                # Dynamic matching images
                img_url = "https://images.unsplash.com/photo-1488646953014-85cb44e25828?w=400&q=80" # default travel globe
                img_query = location.lower()
                
                if "jamui" in img_query:
                    img_url = "https://images.unsplash.com/photo-1548013146-72479768bada?w=400&q=80"
                elif "taj" in img_query:
                    img_url = "https://images.unsplash.com/photo-1548013146-72479768bada?w=400&q=80"
                elif "paris" in img_query:
                    img_url = "https://images.unsplash.com/photo-1502602898657-3e91760cbb34?w=400&q=80"
                elif "delhi" in img_query:
                    img_url = "https://images.unsplash.com/photo-1587474260584-136574528ed5?w=400&q=80"
                elif "mumbai" in img_query:
                    img_url = "https://images.unsplash.com/photo-1566552881560-0be862a7c445?w=400&q=80"
                
                html_response = f"""<html>
<span style="color:#00ffff; font-weight:bold;">🌍 Exploring: {location}</span><br>
<span style="color:#ffffff;">{details}</span><br>
<img src="{img_url}" width="300" style="margin-top:6px; border-radius:8px;" /><br>
</html>"""
                _state.is_fallback_chat = True
                return html_response
            except Exception as e:
                print(f"[TextFallback] Location Explorer Error: {e}")


    # Check voice macro bindings
    try:
        from actions.voice_macro_manager import voice_macro_manager, _bindings_path
        import json
        bp = _bindings_path()
        if bp.exists():
            bindings = json.loads(bp.read_text(encoding="utf-8"))
            for phrase, macro in bindings.items():
                if lower == phrase.lower().strip() or f"jarvis {phrase.lower().strip()}" in lower or f"friday {phrase.lower().strip()}" in lower:
                    res = voice_macro_manager({"action": "play", "name": macro}, player=player)
                    return f"Executing macro '{macro}' bound to voice phrase '{phrase}': {res}"
    except Exception as e:
        print(f"[TextFallback] Voice macro binding check error: {e}")

    # Explicit voice macro command intercepts
    if "record voice macro " in lower or "record macro " in lower or "start macro recording " in lower:
        for prefix in ["start macro recording", "record voice macro", "record macro"]:
            if prefix in lower:
                idx = lower.find(prefix) + len(prefix)
                name = raw[idx:].strip()
                if name:
                    from actions.voice_macro_manager import voice_macro_manager
                    return voice_macro_manager({"action": "start_record", "name": name}, player=player)
        return "Please specify the macro name to record."

    if lower in ["stop voice macro", "stop macro recording", "stop recording macro", "stop recording"]:
        from actions.voice_macro_manager import voice_macro_manager
        return voice_macro_manager({"action": "stop_record"}, player=player)

    if "play voice macro " in lower or "play macro " in lower or "execute voice macro " in lower or "execute macro " in lower:
        for prefix in ["execute voice macro", "execute macro", "play voice macro", "play macro"]:
            if prefix in lower:
                idx = lower.find(prefix) + len(prefix)
                name = raw[idx:].strip()
                if name:
                    from actions.voice_macro_manager import voice_macro_manager
                    return voice_macro_manager({"action": "play", "name": name}, player=player)
        return "Please specify the macro name to play."

    if "bind voice phrase " in lower and " to macro " in lower:
        try:
            phrase_part = raw[lower.find("bind voice phrase ") + 18 : lower.find(" to macro ")].strip()
            macro_part = raw[lower.find(" to macro ") + 10 :].strip()
            if phrase_part and macro_part:
                from actions.voice_macro_manager import voice_macro_manager
                return voice_macro_manager({"action": "bind", "phrase": phrase_part, "macro": macro_part}, player=player)
        except Exception:
            pass
        return "Format error. Use: bind voice phrase <phrase> to macro <macro_name>"

    if "delete voice macro binding " in lower or "remove voice binding " in lower:
        for prefix in ["delete voice macro binding", "remove voice binding"]:
            if prefix in lower:
                idx = lower.find(prefix) + len(prefix)
                phrase = raw[idx:].strip()
                if phrase:
                    from actions.voice_macro_manager import voice_macro_manager
                    return voice_macro_manager({"action": "delete_binding", "phrase": phrase}, player=player)
        return "Please specify the phrase to delete."

    if lower in ["list voice macros", "show voice macro bindings", "show voice bindings", "list voice bindings"]:
        from actions.voice_macro_manager import voice_macro_manager
        return voice_macro_manager({"action": "list"}, player=player)

    # Check student exam readiness commands
    if any(k in lower for k in ["exam readiness", "readiness score", "readiness estimator", "tayari kitni hai", "exam prep status"]):
        from actions.student_exam_readiness import student_exam_readiness
        if any(k in lower for k in ["schedule", "timetable", "time table", "planning", "time"]):
            return student_exam_readiness({"action": "schedule"}, player=player)
        return student_exam_readiness({"action": "calculate"}, player=player)

    if "schedule study for " in lower or "schedule exam prep for " in lower:
        from actions.student_exam_readiness import student_exam_readiness
        # Extract subject
        subject = raw[lower.find("for ") + 4:].strip()
        return student_exam_readiness({"action": "schedule", "subject": subject}, player=player)

    # ── PREMIUM HUMAN WRITER ROUTING ──
    _writer_triggers = ["samjhao", "explain", "write about", "likho", "tell me about",
                        "notes banao", "notes likh", "summary likh", "summary banao",
                        "notes bana", "summary bana", "padha", "padhao"]
    if any(k in lower for k in _writer_triggers) and len(raw.split()) > 1:
        topic = raw
        for k in ["samjhao", "explain", "write about", "likho", "tell me about",
                   "notes banao", "notes likh", "summary likh", "summary banao",
                   "notes bana", "summary bana", "padha", "padhao",
                   "mujhe", "zara", "please", "thoda", "ke baare me", "ke bare me",
                   "about", "on", "par", "pe"]:
            topic = re.sub(rf"(?i)\b{re.escape(k)}\b", "", topic).strip()
        
        # Avoid catching simple single-word intents
        if topic and len(topic) > 1:
            try:
                from actions.flashcards_open import _generate_ai_notes, _emit_writer_to_main_window
                
                # Generate human-like notes
                notes = _generate_ai_notes(topic)
                if notes and len(notes) > 20:
                    _emit_writer_to_main_window(f"📝 {topic}", notes)
                    
                    # Also generate flashcards in background for this topic
                    import threading
                    def _bg_fc():
                        try:
                            from actions.flashcards_open import open_flashcards_window
                            open_flashcards_window(player=player, topic=topic)
                        except Exception:
                            pass
                    threading.Thread(target=_bg_fc, daemon=True).start()
                    
                    _state.is_fallback_chat = True
                    return f"I have written detailed notes on '{topic}' and generated study flashcards in premium windows."
            except Exception as e:
                print(f"[PremiumWriter] Error: {e}")

    # ── ADVANCED HUMAN CONNECTION ROUTING ──
    try:
        from actions.human_mind import execute_advanced_connect
        
        if any(k in lower for k in ["mission control", "telemetry portal", "stark portal", "open portal"]):
            from actions.stark_portal import stark_portal
            return stark_portal({"action": "open"}, player=player)

        if any(k in lower for k in ["simulate", "simulation", "quantum sim", "startup simulation"]):
            from actions.stark_quantum_sim import stark_quantum_sim
            return stark_quantum_sim({"action": "start"}, player=player)

        if any(k in lower for k in ["gaming", "fps boost", "game mode", "launch game"]):
            from actions.gaming_mode import gaming_mode
            return gaming_mode({"action": "start", "game": "General"}, player=player)

        if any(k in lower for k in ["cryptography", "encrypt", "decrypt", "secure data"]):
            from actions.stark_cryptography import stark_cryptography
            return stark_cryptography({"action": "encrypt", "data": raw}, player=player)

        if any(k in lower for k in ["cyber security", "firewall", "security audit", "run audit"]):
            from actions.network_packet_defense import network_packet_defense
            return network_packet_defense({"action": "monitor"}, player=player)

        if any(k in lower for k in ["hologram", "holographic", "3d globe", "diagnostic projection", "projection window"]):
            from actions.hologram_simulator import hologram_simulator
            return hologram_simulator({}, player=player)

        if any(k in lower for k in ["gana suno", "identify song", "music identify", "music recognition", "mobile se gana"]):
            from actions.spotify_controller import spotify_controller
            return spotify_controller({"action": "listen"}, player=player)

        if any(k in lower for k in ["shadow mode", "observer mode", "ceo mode", "daily brief", "observation report", "observe"]):
            from actions.shadow_mode import shadow_mode
            return shadow_mode({}, player=player)

        if any(k in lower for k in ["roast me", "playful roast", "roast kar", "roast karo"]):
            return execute_advanced_connect("roast", raw)
            
        if any(k in lower for k in ["heart to heart", "let's bond", "dil ki baat", "dil se baat", "heart connect"]):
            return execute_advanced_connect("bond", raw)
            
        if any(k in lower for k in ["dream log", "what did you dream", "sapna kya dekha", "dream journal"]):
            return execute_advanced_connect("dream", raw)
            
        if any(k in lower for k in ["congratulate me", "badhai do", "mubarak", "celebrate milestone"]):
            return execute_advanced_connect("celebrate", raw)
            
        if any(k in lower for k in ["feeling low", "very sad", "feeling lonely", "stressed out", "pareshan hoon", "tension hai"]):
            return execute_advanced_connect("listen", raw)
            
        if any(k in lower for k in ["chai pe charcha", "chai time", "philosophy chat", "discuss life"]):
            return execute_advanced_connect("chai", raw)
            
        if any(k in lower for k in ["motivation", "motivate me", "inspire me", "energy do"]):
            return execute_advanced_connect("motivation", raw)
            
        if any(k in lower for k in ["suggest music", "music suggestion", "mood music", "gana suggest"]):
            return execute_advanced_connect("music_suggest", raw)
            
        if any(k in lower for k in ["relation status", "rishta level", "connection score", "rishta kaisa hai", "bonding status"]):
            return execute_advanced_connect("rishta", raw)
    except Exception as e:
        print(f"[TextFallback] Human connection routing failed: {e}")

    if any(k in lower for k in [
        "human mode", "human level", "human brain", "human mind", "human banao",
        "human bana", "human banaoo", "human banao", "real human", "real huma", "real human being",
        "real human bana", "real human banaoo", "manav", "real insaan",
        "permanent eye", "permanent eyes", "permanent ear", "permanent ears",
        "eyes and ears", "eye aur ear", "aankh", "kaan", "humesha dekh", "always watch",
        "always listen", "live eyes", "live ears", "real time", "realtime", "auto", "automatic",
        "sabb chiz", "sab chiz", "sabb cheez", "sab kuch", "sab kuch handle", "sab kuch dekho",
        "sab kuch kar", "sab kuch samajh", "sab kuch kar do", "sab kuch sambhalo", "sabb",
        "ekdam real human", "100% human", "real human bana",
    ]):
        if any(k in lower for k in ["stop", "off", "disable", "band", "deactivate"]):
            return human_mode({"action": "stop"}, player=player)
        if any(k in lower for k in ["status", "state", "report", "kaisa", "check"]):
            return human_mode({"action": "status"}, player=player)
        if any(k in lower for k in ["ear scan", "voice scan", "listen scan", "tone scan"]):
            return human_mode({"action": "ear_scan", "duration": 2.5}, player=player)
        if any(k in lower for k in ["brain", "mind", "cognition"]) and not any(k in lower for k in ["eye", "ear", "aankh", "kaan"]):
            return human_mode({"action": "brain", "text": raw}, player=player)
        if any(k in lower for k in ["eye", "eyes", "aankh", "watch", "dekh"]) and not any(k in lower for k in ["ear", "ears", "kaan", "listen"]):
            return human_mode({"action": "eyes", "source": "both", "interval": 8, "provider": "groq"}, player=player)
        if any(k in lower for k in ["ear", "ears", "kaan", "listen", "sun"]):
            return human_mode({"action": "ears"}, player=player)
        return human_mode({"action": "start", "source": "both", "interval": 5, "provider": "groq", "text": raw}, player=player)

    # ── daily_briefing ──
    if any(k in lower for k in ["morning brief", "subah ka brief", "good morning brief", "aaj ka schedule", "subah ka update", "aaj ka brief"]):
        from actions.daily_briefing import daily_briefing
        return daily_briefing("brief", player=player)
    if any(k in lower for k in ["daily brief update", "morning setting", "auto brief on", "auto brief off"]):
        from actions.daily_briefing import daily_briefing
        action = "auto_on" if "on" in lower else "auto_off"
        return daily_briefing(action, player=player)

    # ── medicine_reminder ──
    if any(k in lower for k in ["dawai", "medicine", "paracetamol", "reminder set", "pill reminder", "dawaiyan"]):
        from actions.medicine_reminder import medicine_reminder
        if any(k in lower for k in ["set", "add", "lagao", "yaad"]):
            name = "medicine"
            m = re.search(r"reminder set karo\s+([a-zA-Z]+)", lower)
            if m: name = m.group(1)
            return medicine_reminder("add", name=name, player=player)
        return medicine_reminder("list", player=player)

    # ── expense_logger ──
    if any(k in lower for k in ["kharch", "rupaya", "rupaye", "kharcha", "expense", "budget"]):
        from actions.expense_logger import expense_logger
        if any(k in lower for k in ["kharch hua", "kharch kiye", "spent"]):
            m = re.search(r"(\d+)\s*(?:rupaya|rupaye|rs|rupees)", lower)
            amt = float(m.group(1)) if m else 0.0
            return expense_logger("log", amount=amt, player=player)
        return expense_logger("today", player=player)

    # ── habit_tracker ──
    if any(k in lower for k in ["habit", "streak", "todo", "task add", "done task"]):
        from actions.habit_tracker import habit_tracker
        if "done" in lower:
            name = _after_keyword(raw, ["done", "complete", "khatam"])
            return habit_tracker("done", name=name, player=player)
        return habit_tracker("today", player=player)

    # ── mental_wellness ──
    if any(k in lower for k in ["anxiety", "stress", "breathing", "meditation", "wellness", "mood journal"]):
        from actions.mental_wellness import mental_wellness
        action = "status"
        if "breathing" in lower: action = "breathing"
        elif "meditation" in lower: action = "meditation"
        elif "journal" in lower: action = "journal"
        return mental_wellness(action, player=player)

    # ── meeting_summarizer ──
    if any(k in lower for k in ["meeting summary", "meeting summarizer", "action items"]):
        from actions.meeting_summarizer import meeting_summarizer
        return meeting_summarizer("status", player=player)

    # ── job_tracker ──
    if any(k in lower for k in ["job application", "resume", "cover letter", "internship", "job track"]):
        from actions.job_tracker import job_tracker
        return job_tracker("list", player=player)

    # ── exam_prep ──
    if any(k in lower for k in ["exam prep", "polity mcq", "current affairs quiz", "gk quiz", "pyq"]):
        from actions.exam_prep import exam_prep
        return exam_prep("quiz", player=player)

    # ── language_learning ──
    if any(k in lower for k in ["vocab", "grammar fix", "vocabulary", "english lesson"]):
        from actions.language_learning import language_learning
        return language_learning("vocab", player=player)

    # ── doc_scanner ──
    if any(k in lower for k in ["doc scan", "document scan", "scan aadhaar", "scan pan", "scan bill"]):
        from actions.doc_scanner import doc_scanner
        return doc_scanner("list", player=player)

    # ── vehicle_assistant ──
    if any(k in lower for k in ["vehicle", "car ", "bike ", "puc ", "insurance", "petrol", "fuel"]):
        from actions.vehicle_assistant import vehicle_assistant
        return vehicle_assistant("status", player=player)

    # ── shopping_assistant ──
    if any(k in lower for k in ["shopping", "deal alert", "price compare", "wishlist"]):
        from actions.shopping_assistant import shopping_assistant
        return shopping_assistant("list", player=player)

    # ── bills_manager ──
    if any(k in lower for k in ["electricity bill", "wifi bill", "recharge remind", "emi"]):
        from actions.bills_manager import bills_manager
        return bills_manager("list", player=player)

    # ── social_reminder ──
    if any(k in lower for k in ["birthday", "anniversary", "birthday wish", "wish birthday"]):
        from actions.social_reminder import social_reminder
        return social_reminder("list", player=player)

    # ── energy_saver ──
    if any(k in lower for k in ["energy save", "battery optimize", "power mode set"]):
        from actions.energy_saver import energy_saver
        return energy_saver("status", player=player)

    # ── Predictive Scheduling ──
    if any(k in lower for k in ["predict activity", "predict my pattern", "what activity next", "routine check"]):
        return predictive_scheduler({"action": "predict"}, player=player)
    if any(k in lower for k in ["load environment", "load coding environment", "nexus os environment", "prepare workspace", "coding environment load"]):
        return predictive_scheduler({"action": "load", "activity": "coding"}, player=player)
    if any(k in lower for k in ["log activity", "save activity", "start activity logging"]):
        return predictive_scheduler({"action": "log", "activity": "coding"}, player=player)

    # ── Command Delegation ──
    if any(k in lower for k in ["bol do", "bol de", "message phone", "delegate to phone", "send sms", "phone push"]):
        receiver = "Maa"
        message = raw
        m = re.search(r"([a-zA-Z\u0900-\u097f]+)\s+ko\s+(?:bol|keh)\s+(?:do|de)\s+(?:ki\s+)?(.+)", raw, re.I)
        if m:
            receiver = m.group(1).strip()
            message = m.group(2).strip()
        else:
            m = re.search(r"delegate\s+to\s+([a-zA-Z]+)\s*:\s*(.+)", raw, re.I)
            if m:
                receiver = m.group(1).strip()
                message = m.group(2).strip()
        for prefix in ["jarvis,", "jarvis", "bol do", "bol de", "ko bol do", "ko bol de"]:
            message = re.sub(rf"^\b{prefix}\b", "", message, flags=re.I).strip(" ,-:")
        return command_delegation({"receiver": receiver, "message": message}, player=player)

    # ── Biometric Stress Analysis ──
    if any(k in lower for k in ["stress check", "stress scan", "biometric stress", "stress detector", "analyze stress", "face stress"]):
        return biometric_stress({"action": "scan"}, player=player)
    if any(k in lower for k in ["stress status", "am i stressed"]):
        return biometric_stress({"action": "status"}, player=player)
    if any(k in lower for k in ["reset stress", "stress reset"]):
        return biometric_stress({"action": "reset"}, player=player)

    # ── Code Interlinker ──
    if any(k in lower for k in ["index code", "index project", "index codebase"]):
        path = ""
        m = re.search(r"index\s+(?:project|code|codebase)\s+(?:at|in|path)?\s*(.+)", raw, re.I)
        if m:
            path = m.group(1).strip()
        return code_interlinker({"action": "index", "path": path}, player=player)
    if any(k in lower for k in ["search code", "find similar code", "code library", "import code"]):
        query = raw
        for strip in ["search code", "find similar code", "import code", "code for", "ka code"]:
            query = query.lower().replace(strip, "").strip()
        query = query.strip(" ,-:")
        if "import" in lower:
            from actions.code_interlinker import search_similar_code
            matches = search_similar_code(query)
            if matches:
                best_match = matches[0]["path"]
                return code_interlinker({"action": "import", "path": best_match}, player=player)
        return code_interlinker({"action": "search", "query": query}, player=player)

    # ── Auditory Soundscape ──
    if any(k in lower for k in ["start hum", "ambient hum", "start soundscape", "ambient sound", "play hum"]):
        return auditory_soundscape({"action": "start"}, player=player)
    if any(k in lower for k in ["stop hum", "stop soundscape", "mute hum", "hum stop", "ambient stop"]):
        return auditory_soundscape({"action": "stop"}, player=player)
    if any(k in lower for k in ["upbeat music", "play upbeat", "bored music", "energy music", "change hum"]):
        return auditory_soundscape({"action": "upbeat"}, player=player)

    try:
        if any(k in lower for k in ["watch me", "watch my screen", "dekh main kaise", "dekh dekh ke seekh", "watch and learn", "live screen dekh ke seekho", "seekho main kaise"]):
            from actions.cognition import cognition_engine
            return cognition_engine({"action": "imitation_start"}, player=player)

        if any(k in lower for k in ["ho gaya seekho", "learn this skill", "maine kar diya seekho", "learn how i did it", "seekh lo"]):
            from actions.cognition import cognition_engine
            desc = _after_keyword(raw, ["ho gaya seekho", "learn this skill", "maine kar diya seekho", "learn how i did it", "seekh lo"])
            return cognition_engine({"action": "imitation_learn", "query": desc or "observed workflow"}, player=player)

        # ── Now Playing ───────────────────────────────────────────────────────
        if any(k in lower for k in [
            "kaunsa gana", "koun sa gana", "kya gana", "gana chal", "music chal",
            "now playing", "ab kya sun", "what song", "what music", "konsa gana",
            "kya chal raha hai gana", "song chal raha", "gana bata",
        ]):
            from actions.now_playing import now_playing
            return now_playing({"action": "current"}, player=player)

        # ── App Launch / MARK Startup ──────────────────────────────────────
        if any(k in lower for k in [
            "apne app on", "app on", "app on hoo", "app on hoo jaaye", "app on ho jaaye",
            "apna app on", "app start karo", "start app", "open app", "launch app",
            "open jarvis", "start jarvis", "launch jarvis", "jarvis on",
            "jarvis start karo", "jarvis khol do",
        ]):
            try:
                from actions.jarvis_app import jarvis_app
                return jarvis_app({"action": "start"}, player=player)
            except Exception as e:
                return f"App startup failed: {e}"

        # ── Active Context / Screen Context ──────────────────────────────────
        if any(k in lower for k in [
            "kaunsa app open", "koun sa app", "screen par kya", "screen pe kya",
            "active window", "kya open hai", "computer mein kya", "screen kya hai",
            "kya chal raha hai screen", "app kya open", "kaunse app", "open apps",
            "screen context", "kya ho raha hai",
        ]):
            from actions.active_context import active_context
            action = "window" if any(k in lower for k in ["window", "active"]) else "full"
            return active_context({"action": action}, player=player)

        # ── WhatsApp Video / Voice Call ────────────────────────────────────
        if any(k in lower for k in [
            "video call", "videocall", "video call karo", "video call start",
            "ko call karo", "ko video", "whatsapp call", "voice call karo",
            "google meet", "zoom call", "zoom meeting",
        ]):
            from actions.whatsapp_videocall import whatsapp_videocall
            # Detect action type
            call_action = "video_call"
            if "voice" in lower or ("call" in lower and "video" not in lower and "meet" not in lower and "zoom" not in lower):
                call_action = "voice_call"
            if "google meet" in lower or "gmeet" in lower:
                call_action = "meet"
            if "zoom" in lower:
                call_action = "zoom"
            # Extract contact name
            contact = ""
            m = re.search(r"(?:ko|to)\s+([a-zA-Z\u0900-\u097f][a-zA-Z\u0900-\u097f\s]{0,20})(?:\s+(?:ko|ka|ki|video|call|whatsapp))", raw, re.I)
            if not m:
                m = re.search(r"([a-zA-Z\u0900-\u097f][a-zA-Z\u0900-\u097f\s]{1,20})\s+ko\s+(?:video|call|whatsapp)", raw, re.I)
            if m:
                contact = m.group(1).strip()
            return whatsapp_videocall({"action": call_action, "contact": contact}, player=player)

        # ── Auto Greet ────────────────────────────────────────────────────────
        if any(k in lower for k in [
            "hello jarvis", "hey jarvis", "good morning jarvis", "good night jarvis",
            "kaun ho tum", "apna introduction", "introduce yourself", "jarvis status",
            "update do", "status kya hai", "good morning", "good night", "subah",
        ]):
            from actions.auto_greet import auto_greet
            greet_action = "startup"
            if any(k in lower for k in ["good morning", "subah"]):
                greet_action = "morning"
            elif any(k in lower for k in ["good night", "raat", "goodnight"]):
                greet_action = "goodnight"
            elif any(k in lower for k in ["kaun ho", "introduce", "introduction", "who are you"]):
                greet_action = "introduce"
            elif any(k in lower for k in ["status", "update"]):
                greet_action = "status"
            elif any(k in lower for k in ["hello", "hey", "wake", "hi "]):
                greet_action = "wake"
            return auto_greet({"action": greet_action}, player=player)

        # ── Spotify Controller ────────────────────────────────────────────────
        if any(k in lower for k in [
            "spotify", "gana laga", "song play", "music play", "gana play",
            "next song", "next gana", "previous song", "pichla gana",
            "agle gana", "shuffle on", "shuffle off", "repeat on",
            "spotify mein", "spotify se", "liked songs",
        ]) or (any(k in lower for k in ["play", "laga do", "laga"]) and
               any(k in lower for k in ["song", "gana", "music", "artist"])):
            from actions.spotify_controller import spotify_controller
            sp_action = "play"
            query = raw
            if any(k in lower for k in ["next", "agle"]):
                sp_action = "next"
                query = ""
            elif any(k in lower for k in ["previous", "pichla", "prev", "back"]):
                sp_action = "prev"
                query = ""
            elif any(k in lower for k in ["pause", "ruk ja", "stop music"]):
                sp_action = "pause"
                query = ""
            elif any(k in lower for k in ["shuffle"]):
                sp_action = "shuffle"
                query = ""
            elif any(k in lower for k in ["repeat", "loop"]):
                sp_action = "repeat"
                query = ""
            elif any(k in lower for k in ["liked songs", "pasandida"]):
                sp_action = "liked"
                query = ""
            elif any(k in lower for k in ["current", "kya chal", "ab kya"]):
                sp_action = "current"
                query = ""
            # Extract volume
            import re as _re
            vol_m = _re.search(r"volume\s+(\d{1,3})", lower)
            if vol_m:
                sp_action = "volume"
                query = ""
                return spotify_controller({"action": "volume", "volume": int(vol_m.group(1))}, player=player)
            # Clean query
            if sp_action == "play":
                for strip in ["gana laga do", "play karo", "laga do", "song play", "music play",
                               "play", "laga", "spotify mein", "spotify se", "gana"]:
                    query = query.lower().replace(strip, "").strip()
                query = query.strip(" -:,")
            return spotify_controller({"action": sp_action, "query": query}, player=player)

        # ── Battery Guardian ─────────────────────────────────────────────────
        if any(k in lower for k in [
            "battery", "charging", "charger", "power saver", "high performance",
            "battery health", "battery status", "kitni battery", "power plan",
            "performance mode", "battery report", "power mode",
        ]):
            from actions.battery_guardian import battery_guardian
            bat_action = "status"
            plan = ""
            if any(k in lower for k in ["health", "report"]):
                bat_action = "health"
            elif any(k in lower for k in ["power saver", "saver", "battery saver", "eco"]):
                bat_action = "power_saver"
            elif any(k in lower for k in ["high performance", "performance mode", "max performance"]):
                bat_action = "high_performance"
            elif any(k in lower for k in ["balanced", "normal mode"]):
                bat_action = "balanced"
            elif any(k in lower for k in ["plan", "mode"]):
                bat_action = "plan"
            return battery_guardian({"action": bat_action, "plan": plan}, player=player)

        # ── Voice Notes ───────────────────────────────────────────────────────
        if any(k in lower for k in [
            "voice note", "voice memo", "record note", "note record",
            "note save karo", "note likh lo", "note dikhao", "mere notes",
            "note summarize", "note bhejo", "note send",
        ]):
            from actions.voice_notes import voice_notes
            vn_action = "list"
            content = ""
            dur = 10
            if any(k in lower for k in ["record", "record karo"]):
                vn_action = "record"
                import re as _re2
                dur_m = _re2.search(r"(\d+)\s*(?:sec|second|s\b)", lower)
                dur = int(dur_m.group(1)) if dur_m else 10
            elif any(k in lower for k in ["save", "likh lo", "note save"]):
                vn_action = "save"
                for strip in ["note save karo", "note likh lo", "save karo", "voice note"]:
                    content = raw.lower().replace(strip, "").strip(" :—-")
            elif any(k in lower for k in ["list", "dikhao", "show", "saare"]):
                vn_action = "list"
            elif any(k in lower for k in ["summarize", "summary"]):
                vn_action = "summarize"
            elif any(k in lower for k in ["send", "bhejo", "share"]):
                vn_action = "send"
            elif any(k in lower for k in ["play", "sunao"]):
                vn_action = "play"
            return voice_notes({"action": vn_action, "content": content, "duration": dur}, player=player)

        # ── Smart Clipboard ────────────────────────────────────────────────────
        if any(k in lower for k in [
            "clipboard translate", "clipboard ko translate", "jo copy kiya", "copy kiya translate",
            "clipboard summarize", "grammar fix", "clipboard grammar", "professional bana",
            "clipboard history", "email draft", "clipboard rewrite", "key points nikalo",
            "clipboard ko", "copied text", "clipboard mein",
        ]):
            from actions.smart_clipboard import smart_clipboard
            sc_action = "read"
            lang = "Hindi"
            tone = "professional"
            if any(k in lower for k in ["translate", "anuvad"]):
                sc_action = "translate"
                # Detect language
                for l in ["hindi", "english", "spanish", "french", "arabic", "punjabi", "urdu", "bengali"]:
                    if l in lower:
                        lang = l.title()
                        break
            elif any(k in lower for k in ["summarize", "summary", "chhota"]):
                sc_action = "summarize"
            elif any(k in lower for k in ["grammar", "fix", "correct", "theek"]):
                sc_action = "fix"
            elif any(k in lower for k in ["professional", "formal", "casual", "rewrite", "bana do"]):
                sc_action = "rewrite"
                for t in ["professional", "formal", "casual", "friendly"]:
                    if t in lower:
                        tone = t
                        break
            elif any(k in lower for k in ["bullet", "points", "key points", "nikalo"]):
                sc_action = "bullets"
            elif any(k in lower for k in ["email", "draft"]):
                sc_action = "email"
            elif any(k in lower for k in ["history", "past"]):
                sc_action = "history"
            elif any(k in lower for k in ["shorten", "chhota karo", "compress"]):
                sc_action = "shorten"
            return smart_clipboard({"action": sc_action, "language": lang, "tone": tone}, player=player)

        # ── Internet Speed ─────────────────────────────────────────────────────
        if any(k in lower for k in [
            "internet speed", "speed test", "wifi signal", "wifi kitna", "network check",
            "internet check", "mera ip", "ip address", "ping karo", "internet connected",
            "data usage", "dns flush", "network info", "internet kitna fast",
            "bandwidth", "internet theek", "wifi strength",
        ]):
            from actions.internet_speed import internet_speed
            net_action = "check"
            host = "google.com"
            if any(k in lower for k in ["speed test", "kitni speed", "speedtest", "bandwidth"]):
                net_action = "speed"
            elif any(k in lower for k in ["wifi", "signal", "wireless"]):
                net_action = "wifi"
            elif any(k in lower for k in ["ip address", "mera ip", "my ip"]):
                net_action = "ip"
            elif any(k in lower for k in ["ping"]):
                net_action = "ping"
                # Extract host
                import re as _re3
                h_m = _re3.search(r"ping\s+([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", lower)
                if h_m:
                    host = h_m.group(1)
            elif any(k in lower for k in ["data usage", "data kitna"]):
                net_action = "data"
            elif any(k in lower for k in ["dns flush", "dns clear"]):
                net_action = "dns_flush"
            elif any(k in lower for k in ["info", "full info", "sab"]):
                net_action = "info"
            return internet_speed({"action": net_action, "host": host}, player=player)

        if any(k in lower for k in ["stop live screen", "live screen stop", "stop screen share", "band karo live", "live context stop"]):
            return live_context({"action": "stop"}, player=player)

        if _has_any(lower, _LIVE_CAMERA_TRIGGERS):
            if any(k in lower for k in ["stop", "band"]):
                return live_context({"action": "stop"}, player=player)
            return live_context(
                {
                    "action": "start",
                    "source": "camera",
                    "interval": _parse_interval(raw, 8),
                    "focus": raw,
                    "provider": "groq",
                },
                player=player,
            )

        if any(k in lower for k in ["real time camera", "real-time camera", "realtime camera", "camera realtime"]):
            return live_context(
                {
                    "action": "start",
                    "source": "camera",
                    "interval": _parse_interval(raw, 5),
                    "focus": raw,
                    "provider": "groq",
                },
                player=player,
            )

        if _has_any(lower, _LIVE_START_TRIGGERS):
            source = "both" if "camera" in lower and "screen" in lower else "screen"
            if any(k in lower for k in ["status", "state"]):
                return live_context({"action": "status"}, player=player)
            return live_context(
                {"action": "start", "source": source, "interval": _parse_interval(raw, 8), "focus": raw},
                player=player,
            )

        if any(k in lower for k in ["live context status", "live visual status"]):
            return live_context({"action": "status"}, player=player)

        if _has_any(lower, _LIVE_ASK_TRIGGERS) and not _message_request(raw):
            source = "camera" if "camera" in lower else "screen"
            return live_context({"action": "ask", "source": source, "question": raw}, player=player)

        msg_req = _message_request(raw)
        if msg_req:
            msg_req = _handle_media_and_location_intercept(raw, msg_req, player=player)
            explicit_confirm = any(k in lower for k in ["confirm yes", "i confirm", "confirmed=yes"])
            file_to_send = msg_req.get("file_path") or current_file
            if msg_req.get("_live_camera_text_only") and not msg_req.get("_platform_explicit"):
                return "Live camera description bhejne ke liye receiver aur platform dono batao. Example: Rahul ko WhatsApp par live camera se dekho pic nahi khich ke bhejo confirm yes"
            if not msg_req.get("receiver") or (not msg_req.get("message_text") and not file_to_send):
                return "Message bhejne ke liye platform, receiver aur content/file specify kijiye. Example: WhatsApp to Rahul: main 5 minute me aata hoon confirm yes"
            if not explicit_confirm:
                file_note = f" with file '{Path(file_to_send).name}'" if file_to_send else ""
                return (
                    f"Message ready hai{file_note}: {msg_req['platform']} -> {msg_req['receiver']}: "
                    f"{msg_req['message_text'] or '[File Attachment]'}. Send karne ke liye end me 'confirm yes' likhiye."
                )
            if file_to_send:
                msg_req["file_path"] = file_to_send
            msg_req.pop("_platform_explicit", None)
            msg_req.pop("_live_camera_text_only", None)
            answer = send_message(msg_req, player=player)
            _learn_from_turn(raw, answer)
            return answer

        sensitive_match = _has_any(lower, _SENSITIVE_ACTIONS)
        explicit_confirm = any(k in lower for k in ["confirmed=yes", "confirm yes", "i confirm"])
        if sensitive_match and not explicit_confirm:
            return (
                "Ye action sensitive/destructive ho sakta hai. Please exact target aur confirmation dijiye "
                "(example: 'run ... confirmed=yes' ya 'delete <file name> confirm yes')."
            )
        if sensitive_match and explicit_confirm and not lower.startswith(("run ", "execute ", "powershell ", "cmd ")):
            return (
                "Confirmation mil gaya, lekin exact supported command/target clear nahi hai. "
                "Please full command dijiye, jaise: run Remove-Item -LiteralPath '<file>' confirmed=yes"
            )

        if raw.startswith("[FILE_UPLOADED]") and current_file:
            return file_processor({"file_path": current_file, "action": "autopilot"}, player=player)

        if current_file and any(k in lower for k in ["uploaded file", "file autopilot", "document", "isko", "is file", "ye file"]):
            action = "autopilot"
            if any(k in lower for k in ["metadata", "details", "classify", "kaun sa", "koun sa", "type"]):
                action = "metadata"
            return file_processor({"file_path": current_file, "action": action}, player=player)

        if any(k in lower for k in ["screen scan", "screen dekh", "screen dekho", "analyze my screen", "screen analyze", "screen kya hai"]):
            answer = external_vision(
                {"action": "ask", "source": "screen", "question": raw, "provider": "auto"},
                player=player,
            )
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["camera dekh", "camera scan", "mera chehra", "face scan", "photo se pahchan", "who am i", "kaun hoon main", "koun hoon main", "koun hai ye", "kaun hai ye"]):
            if current_file and any(k in lower for k in ["photo", "file", "pic", "image"]):
                try:
                    from actions.face_intel import face_intel
                    answer = face_intel({"action": "identify_photo", "file_path": current_file}, player=player)
                except Exception as e:
                    answer = f"Photo face intelligence check failed: {e}"
            else:
                try:
                    from actions.face_intel import face_intel
                    answer = face_intel({"action": "identify_camera"}, player=player)
                except Exception as e:
                    answer = f"Face intelligence check failed: {e}"
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["inside app", "app ke andar", "active app", "current app", "ui tree"]):
            if any(k in lower for k in ["execute", "click", "kar do", "press", "type"]):
                return app_inspector({"action": "act", "goal": raw}, player=player)
            return app_inspector({"action": "inspect", "limit": 60}, player=player)

        if "list windows" in lower or "window list" in lower:
            return app_inspector({"action": "windows", "limit": 40}, player=player)

        # Check if System Lockdown / Security protocol is requested
        if any(k in lower for k in ["lockdown system", "system lockdown", "lockdown", "active shield", "protocol alpha"]):
            answer = cyber_shield({"action": "lockdown"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        # Check if custom macro command is requested
        if any(k in lower for k in ["save macro ", "create macro "]):
            parts = raw.split(":", 1)
            if len(parts) == 2:
                name_part = parts[0].strip()
                cmds_part = parts[1].strip()
                macro_name = _after_keyword(name_part, ["save macro", "create macro"]).strip()
                cmds_list = [c.strip() for c in cmds_part.split(",") if c.strip()]
                if macro_name and cmds_list:
                    answer = custom_macros({"action": "save", "name": macro_name, "commands": cmds_list}, player=player)
                    _learn_from_turn(raw, answer)
                    return answer
            return "Format error. Use: save macro <name>: <cmd1>, <cmd2>, ... (comma-separated commands)"
            
        if any(k in lower for k in ["run macro ", "execute macro ", "play macro ", "start macro "]):
            macro_name = _after_keyword(raw, ["run macro", "execute macro", "play macro", "start macro"]).strip()
            if macro_name:
                answer = custom_macros({"action": "run", "name": macro_name}, player=player)
                _learn_from_turn(raw, answer)
                return answer
            return "Please specify a macro name to run."
            
        if any(k in lower for k in ["delete macro ", "remove macro "]):
            macro_name = _after_keyword(raw, ["delete macro", "remove macro"]).strip()
            if macro_name:
                answer = custom_macros({"action": "delete", "name": macro_name}, player=player)
                _learn_from_turn(raw, answer)
                return answer
            return "Please specify a macro name to delete."
            
        if any(k in lower for k in ["list macros", "show macros", "custom macros", "view macros"]):
            answer = custom_macros({"action": "list"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        # ── AI Multi-Step Agent ─────────────────────────────────────────────────
        if any(k in lower for k in ["agent karo", "jarvis agent", "auto karo", "autonomous task",
                                     "multi step task", "agentic", "execute task:",
                                     "complex task", "do everything:", "plan and execute"]):
            from actions.ai_agent import ai_agent
            task = _after_keyword(raw, ["agent karo", "jarvis agent", "auto karo",
                                        "autonomous task", "multi step task",
                                        "execute task:", "complex task", "do everything:"]).strip()
            if not task:
                task = raw
            answer = ai_agent({"action": "run", "task": task}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["agent history", "past tasks", "task history", "agent log"]):
            from actions.ai_agent import ai_agent
            answer = ai_agent({"action": "history"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        # ── Web Monitor ───────────────────────────────────────────────────────
        if any(k in lower for k in ["monitor website", "website monitor", "website track",
                                     "watch website", "price monitor", "site monitor",
                                     "website changes", "monitor site", "track site"]):
            from actions.web_monitor import web_monitor
            import re as _re
            url_match = _re.search(r'(https?://\S+|www\.\S+|\S+\.com\S*|\S+\.in\S*|\S+\.org\S*)', raw, _re.I)
            url = url_match.group(1) if url_match else ""
            monitor_type = "price" if any(k in lower for k in ["price", "rate", "cost"]) else "content"
            keyword = ""
            if "keyword" in lower:
                kw_match = _re.search(r'keyword[\s:]+([\w\s]+)', raw, _re.I)
                if kw_match:
                    keyword = kw_match.group(1).strip()
            price_target = 0
            num_match = _re.search(r'below\s*[₹\$]?\s*([\d,]+)', raw)
            if num_match:
                price_target = float(num_match.group(1).replace(',', ''))
            answer = web_monitor({"action": "add", "url": url, "type": monitor_type,
                                   "keyword": keyword, "price_target": price_target}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["check monitors", "monitor check", "web check",
                                     "monitor status", "check all monitors"]):
            from actions.web_monitor import web_monitor
            answer = web_monitor({"action": "check"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["list monitors", "show monitors", "meri monitors"]):
            from actions.web_monitor import web_monitor
            answer = web_monitor({"action": "list"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["scrape website", "website scrape", "site se data",
                                     "website se nikalo", "scrape page"]):
            from actions.web_monitor import web_monitor
            import re as _re
            url_match = _re.search(r'(https?://\S+|www\.\S+|\S+\.com\S*)', raw, _re.I)
            url = url_match.group(1) if url_match else ""
            answer = web_monitor({"action": "scrape", "url": url}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        # ── Advanced Math ───────────────────────────────────────────────────
        # Math expressions like "25 * 4" or "sqrt(144)" etc.
        if any(k in lower for k in ["calculate", "calc ", "compute", "math solve",
                                     "solve this", "equation solve", "hisab karo",
                                     "kitna hoga", "how much is", "sqrt", "sin(", "cos(",
                                     "log(", "power of"]):
            from actions.advanced_math import advanced_math
            expr = _after_keyword(raw, ["calculate", "calc", "compute", "solve this",
                                        "equation solve", "hisab karo", "kitna hoga",
                                        "how much is", "math solve"]).strip() or raw
            answer = advanced_math({"action": "calculate", "expression": expr}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["convert ", "unit convert", "km to", "mile to", "kg to",
                                     "celsius to", "fahrenheit to", "mb to gb", "inches to"]):
            from actions.advanced_math import advanced_math
            # Parse: "5 km to miles" or "100 celsius to fahrenheit"
            import re as _re
            m = _re.search(r'([\d\.]+)\s+([a-zA-Z°]+)\s+(?:to|mein|ko)\s+([a-zA-Z°]+)', raw, _re.I)
            if m:
                val = float(m.group(1))
                fu = m.group(2)
                tu = m.group(3)
                answer = advanced_math({"action": "convert", "value": val, "from": fu, "to": tu}, player=player)
            else:
                answer = advanced_math({"action": "calculate", "expression": raw}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["emi calculate", "loan emi", "emi kitna hoga",
                                     "monthly emi", "home loan emi"]):
            from actions.advanced_math import advanced_math
            import re as _re
            nums = _re.findall(r'[\d,]+(?:\.\d+)?', raw.replace(',', ''))
            floats = [float(n) for n in nums if n]
            if len(floats) >= 3:
                answer = advanced_math({"action": "emi", "principal": floats[0],
                                        "rate": floats[1], "months": int(floats[2])}, player=player)
            else:
                answer = "Sir, 3 values batao: principal (e.g. 500000), rate% (e.g. 8.5), months (e.g. 120)"
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["bmi calculate", "bmi check", "body mass index",
                                     "mera bmi", "bmi kitna hai"]):
            from actions.advanced_math import advanced_math
            import re as _re
            nums = _re.findall(r'[\d]+(?:\.\d+)?', raw)
            floats = [float(n) for n in nums if n]
            if len(floats) >= 2:
                answer = advanced_math({"action": "bmi", "weight": floats[0], "height": floats[1]}, player=player)
            else:
                answer = "Sir, weight (kg) aur height (cm) batao. E.g. 'bmi 70kg 175cm'"
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["statistics", "average of", "mean of", "median of",
                                     "standard deviation", "std dev", "stats of"]):
            from actions.advanced_math import advanced_math
            import re as _re
            nums = _re.findall(r'[\d]+(?:\.\d+)?', raw)
            numbers = [float(n) for n in nums if n]
            if numbers:
                answer = advanced_math({"action": "stats", "numbers": numbers}, player=player)
                _learn_from_turn(raw, answer)
                return answer

        # ── AI Screen Analyzer ─────────────────────────────────────────────────
        if any(k in lower for k in ["screen pe kya hai", "screen dekho", "screen analyze",
                                     "meri screen", "screen describe", "what is on screen",
                                     "screen batao", "screen mein kya hai"]):
            from actions.ai_screen_analyzer import ai_screen_analyzer
            answer = ai_screen_analyzer({"action": "describe"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["screen ka text", "screen text padhao", "screen text read",
                                     "ocr karo", "text on screen"]):
            from actions.ai_screen_analyzer import ai_screen_analyzer
            answer = ai_screen_analyzer({"action": "read_text"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["screen error", "error on screen", "kya error hai",
                                     "screen pe error", "fix screen error"]):
            from actions.ai_screen_analyzer import ai_screen_analyzer
            answer = ai_screen_analyzer({"action": "error"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["screen code samjhao", "explain code on screen",
                                     "screen pe code", "code explain screen"]):
            from actions.ai_screen_analyzer import ai_screen_analyzer
            answer = ai_screen_analyzer({"action": "code"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["screen translate", "screen ka anuvad",
                                     "translate screen", "screen ko hindi mein"]):
            from actions.ai_screen_analyzer import ai_screen_analyzer
            lang = "hindi"
            if "english" in lower: lang = "english"
            if "punjabi" in lower: lang = "punjabi"
            answer = ai_screen_analyzer({"action": "translate", "language": lang}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["screen suggestions", "screen tips", "screen se kya karoon",
                                     "screen advice", "jarvis suggest"]):
            from actions.ai_screen_analyzer import ai_screen_analyzer
            answer = ai_screen_analyzer({"action": "suggest"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        # ── Stark Threat Scanner ──────────────────────────────────────────────
        if any(k in lower for k in ["threat scan", "threat scanner", "system scan", "khatra check", "danger check",
                                     "virus check", "malware check", "hack check", "security scan",
                                     "suspicious process", "threat detect", "scan threats"]):
            answer = threat_scanner({"action": "scan"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        # ── AI Memory Vault ───────────────────────────────────────────────────
        if any(k in lower for k in ["yaad rakh ", "remember this", "save memory", "store memory", "note this down"]):
            text_to_remember = _after_keyword(raw, ["yaad rakh", "remember this", "save memory", "store memory", "note this down"]).strip()
            if not text_to_remember:
                text_to_remember = raw
            category = "general"
            if any(k in lower for k in ["personal", "private", "mera"]):
                category = "personal"
            elif any(k in lower for k in ["work", "kaam", "project"]):
                category = "work"
            elif any(k in lower for k in ["idea", "plan", "soch"]):
                category = "idea"
            answer = ai_memory_vault({"action": "remember", "text": text_to_remember, "category": category}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["recall memory", "kya yaad hai", "memory search", "mujhe yaad dilao", "yaad karo"]):
            query = _after_keyword(raw, ["recall memory", "kya yaad hai", "memory search", "mujhe yaad dilao", "yaad karo"]).strip()
            answer = ai_memory_vault({"action": "recall", "query": query}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["memory timeline", "memory history", "memories dekho", "recent memories"]):
            answer = ai_memory_vault({"action": "timeline", "days": 7}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["memory stats", "memory count", "vault status"]):
            answer = ai_memory_vault({"action": "stats"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        # ── Workspace Switcher ────────────────────────────────────────────────
        if any(k in lower for k in ["save workspace ", "create workspace "]):
            ws_name = _after_keyword(raw, ["save workspace", "create workspace"]).strip()
            # Parse optional app list after colon e.g. "save workspace coding: chrome, vscode"
            apps_list = []
            if ":" in ws_name:
                parts = ws_name.split(":", 1)
                ws_name = parts[0].strip()
                apps_raw_str = parts[1].strip()
                apps_list = [a.strip() for a in apps_raw_str.split(",") if a.strip()]
            if ws_name:
                answer = workspace_switcher({"action": "save", "name": ws_name, "apps": apps_list}, player=player)
                _learn_from_turn(raw, answer)
                return answer
            return "Workspace name batao. Example: 'save workspace coding: chrome, vscode'"

        if any(k in lower for k in ["load workspace ", "open workspace ", "start workspace "]):
            ws_name = _after_keyword(raw, ["load workspace", "open workspace", "start workspace"]).strip()
            if ws_name:
                answer = workspace_switcher({"action": "load", "name": ws_name}, player=player)
                _learn_from_turn(raw, answer)
                return answer
            return "Kaun sa workspace load karna hai? Naam batao."

        if any(k in lower for k in ["list workspaces", "show workspaces", "meri workspaces", "all workspaces"]):
            answer = workspace_switcher({"action": "list"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["delete workspace ", "remove workspace "]):
            ws_name = _after_keyword(raw, ["delete workspace", "remove workspace"]).strip()
            if ws_name:
                answer = workspace_switcher({"action": "delete", "name": ws_name}, player=player)
                _learn_from_turn(raw, answer)
                return answer

        # ── Full PC Control (power, volume, window, system) ─────────────────────
        # POWER
        if any(k in lower for k in ["pc band karo", "shut down", "shutdown pc", "pc shutdown",
                                     "pc ko band karo", "computer band karo"]):
            answer = full_pc_control({"action": "shutdown", "delay": 30}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["pc restart", "restart pc", "reboot pc", "restart karo", "reboot karo"]):
            answer = full_pc_control({"action": "restart"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["cancel shutdown", "shutdown cancel", "band mat karo", "abort shutdown"]):
            answer = full_pc_control({"action": "cancel_shutdown"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["pc sleep", "sleep mode", "pc so jao", "hibernate", "suspend pc"]):
            action_key = "hibernate" if "hibernate" in lower else "sleep"
            answer = full_pc_control({"action": action_key}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["screen off", "monitor off", "display off", "screen band karo"]):
            answer = full_pc_control({"action": "sleep_display"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        # VOLUME
        if any(k in lower for k in ["volume up", "awaaz badha", "sound badhao", "louder",
                                     "volume increase", "volume bada karo"]):
            answer = full_pc_control({"action": "volume_up"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["volume down", "awaaz kam", "sound kam karo", "quieter",
                                     "volume decrease", "volume ghata karo"]):
            answer = full_pc_control({"action": "volume_down"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["volume mute", "mute karo", "chup karo pc", "sound mute",
                                     "mute pc", "unmute", "mute/unmute"]):
            answer = full_pc_control({"action": "mute"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if re.search(r"volume\s*(set|ko|par|to)?\s*(\d+)", lower):
            m = re.search(r"volume\s*(?:set|ko|par|to)?\s*(\d+)", lower)
            vol = int(m.group(1)) if m else 50
            answer = full_pc_control({"action": "set_volume", "value": vol}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["volume check", "awaaz kitni hai", "current volume", "volume kitna"]):
            answer = full_pc_control({"action": "get_volume"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        # BRIGHTNESS
        if any(k in lower for k in ["brightness up", "screen bright karo", "chamak badha",
                                     "brightness badhao", "screen ujjala karo"]):
            answer = full_pc_control({"action": "brightness_up"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["brightness down", "screen dim karo", "chamak kam",
                                     "brightness ghata", "screen dark karo"]):
            answer = full_pc_control({"action": "brightness_down"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["brightness check", "brightness kitni hai", "screen brightness"]):
            answer = full_pc_control({"action": "get_brightness"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        # WINDOW MANAGEMENT
        if any(k in lower for k in ["maximize window", "window bada karo", "window maximize",
                                     "fullscreen banao", "bada karo window"]):
            if "fullscreen" in lower or "full screen" in lower:
                answer = full_pc_control({"action": "fullscreen"}, player=player)
            else:
                answer = full_pc_control({"action": "maximize"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["minimize window", "window chota karo", "window minimize",
                                     "chota karo window"]):
            answer = full_pc_control({"action": "minimize"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["snap left", "window left mein", "left snap"]):
            answer = full_pc_control({"action": "snap_left"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["snap right", "window right mein", "right snap"]):
            answer = full_pc_control({"action": "snap_right"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["show desktop", "desktop dikha", "win d", "sab window hatao"]):
            answer = full_pc_control({"action": "show_desktop"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["always on top", "pin window", "window pin karo"]):
            answer = full_pc_control({"action": "always_on_top"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        # DISPLAY
        if any(k in lower for k in ["dark mode", "night mode toggle", "dark mode toggle",
                                     "dark theme", "light mode"]):
            answer = full_pc_control({"action": "dark_mode"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["night light", "eye care mode", "blue light filter"]):
            answer = full_pc_control({"action": "night_light"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        # NETWORK
        if any(k in lower for k in ["wifi on", "wifi off", "wifi toggle", "wifi band karo",
                                     "wifi chalu karo", "toggle wifi", "wifi enable", "wifi disable"]):
            answer = full_pc_control({"action": "wifi_toggle"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["bluetooth on", "bluetooth off", "bluetooth toggle",
                                     "bluetooth chalu", "bluetooth band", "toggle bluetooth"]):
            answer = full_pc_control({"action": "bluetooth_toggle"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["airplane mode", "flight mode", "aeroplane mode"]):
            answer = full_pc_control({"action": "airplane_mode"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["mobile hotspot", "hotspot on", "hotspot off", "wifi hotspot"]):
            answer = full_pc_control({"action": "hotspot"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        # POWER PLANS
        if any(k in lower for k in ["performance mode", "gaming mode", "max performance",
                                     "high performance", "turbo mode"]):
            answer = full_pc_control({"action": "performance_mode"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["battery saver mode", "power saver mode", "battery bachao",
                                     "save battery mode"]):
            answer = full_pc_control({"action": "battery_saver_mode"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["balanced mode", "normal mode", "balanced power"]):
            answer = full_pc_control({"action": "balanced_mode"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        # SYSTEM INFO
        if any(k in lower for k in ["disk space", "storage kitna hai", "disk check", "free space",
                                     "how much storage", "kitna storage"]):
            answer = full_pc_control({"action": "disk_space"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["active window", "current window", "konsa window", "window info", "kaunsa app", "app open", "konsa app", "open kiya", "kaun sa app"]):
            answer = full_pc_control({"action": "active_window"}, player=player)
            # Make response more friendly in Hindi/Hinglish if requested in Hindi
            if any(k in lower for k in ["koun", "kaun", "open kiya", "chal raha"]):
                title = "Unknown"
                proc = "Unknown"
                for line in answer.split("\n"):
                    if "Title:" in line:
                        title = line.split("Title:")[1].strip()
                    if "Process:" in line:
                        proc = line.split("Process:")[1].split("(")[0].strip()
                answer = f"Boss, abhi aapne **{proc}** (Title: *{title}*) open kiya hai. Main continuous background window aur active context tracking active rakhe hue hoon!"
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["top processes", "cpu hog", "ram hog", "konsa process zyada",
                                     "resource hog", "top cpu", "top ram"]):
            sort = "ram" if "ram" in lower else "cpu"
            answer = full_pc_control({"action": "top_processes", "sort": sort}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["kill process", "process kill", "force quit", "end task",
                                     "process band karo"]):
            proc = _after_keyword(raw, ["kill process", "force quit", "end task", "process band karo"]).strip()
            answer = full_pc_control({"action": "kill_process", "target": proc}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        if any(k in lower for k in ["pc capabilities", "pc control help", "pc kya kya kar sakta",
                                     "full pc control", "pc commands"]):
            answer = full_pc_control({"action": "help"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        # BROWSER SHORTCUTS (when talking about browser actions)
        if any(k in lower for k in ["new tab kholo", "open new tab", "nayi tab"]):
            answer = full_pc_control({"action": "new_tab"}, player=player)
            _learn_from_turn(raw, answer); return answer

        if any(k in lower for k in ["tab band karo", "close current tab", "close tab"]):
            answer = full_pc_control({"action": "close_tab"}, player=player)
            _learn_from_turn(raw, answer); return answer

        if any(k in lower for k in ["zoom in karo", "zoom bada karo", "ctrl plus"]):
            answer = full_pc_control({"action": "zoom_in"}, player=player)
            _learn_from_turn(raw, answer); return answer

        if any(k in lower for k in ["zoom out karo", "zoom chota karo", "ctrl minus"]):
            answer = full_pc_control({"action": "zoom_out"}, player=player)
            _learn_from_turn(raw, answer); return answer

        # RECYCLE BIN
        if any(k in lower for k in ["recycle bin empty karo", "trash empty", "recycle bin saaf"]):
            answer = full_pc_control({"action": "empty_recycle_bin"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        # ── Life Dashboard ────────────────────────────────────────────────────
        if any(k in lower for k in ["life dashboard", "full status", "mera status", "dashboard dikha",
                                     "daily report", "morning briefing", "evening briefing",
                                     "system status full", "sab batao", "daily brief"]):
            answer = life_dashboard({"action": "show"}, player=player)
            _learn_from_turn(raw, answer)
            return answer


        # ── AI Code Generator ─────────────────────────────────────────────────
        if any(k in lower for k in ["write code", "generate code", "code likho", "code banao",
                                     "create script", "python script", "js script",
                                     "write a function", "write a program", "code for",
                                     "explain code", "fix code", "debug code", "generate tests"]):
            if any(k in lower for k in ["explain", "samjhao"]):
                code_text = current_file and Path(current_file).read_text(encoding="utf-8")[:3000] if current_file else ""
                if not code_text:
                    code_text = _after_keyword(raw, ["explain", "samjhao"]).strip()
                answer = ai_code_generator({"action": "explain", "code": code_text}, player=player)
            elif any(k in lower for k in ["fix", "debug", "thik karo"]):
                code_text = current_file and Path(current_file).read_text(encoding="utf-8")[:3000] if current_file else ""
                answer = ai_code_generator({"action": "fix", "code": code_text}, player=player)
            elif any(k in lower for k in ["test", "unit test"]):
                code_text = current_file and Path(current_file).read_text(encoding="utf-8")[:3000] if current_file else ""
                answer = ai_code_generator({"action": "test", "code": code_text}, player=player)
            else:
                desc = _after_keyword(raw, ["write code", "generate code", "code likho", "code banao",
                                            "create script", "write a function", "write a program", "code for"]).strip()
                lang = "python"
                for l in ["javascript", "html", "css", "bash", "sql", "java"]:
                    if l in lower:
                        lang = l
                        break
                auto_run = "run it" in lower or "chalao" in lower or "execute it" in lower
                answer = ai_code_generator({"action": "generate", "description": desc or raw,
                                            "language": lang, "auto_run": auto_run}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        # ── PC Optimizer ──────────────────────────────────────────────────────
        if any(k in lower for k in ["optimize pc", "pc optimize", "speed up pc", "clean pc",
                                     "pc clean karo", "temp files", "free ram", "boost pc",
                                     "pc slow hai", "pc fast karo", "junk clean", "dns flush",
                                     "recycle bin", "optimize system"]):
            answer = pc_optimizer({"action": "optimize"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        # ── AI File Organizer ─────────────────────────────────────────────────
        if any(k in lower for k in ["organize files", "file organize", "sort files",
                                     "desktop organize", "downloads organize", "files sort karo",
                                     "files clean karo", "desktop clean", "duplicate files",
                                     "duplicate dhundho"]):
            if any(k in lower for k in ["duplicate", "dups"]):
                answer = ai_file_organizer({"action": "duplicates"}, player=player)
            elif any(k in lower for k in ["desktop"]):
                dry = "preview" in lower or "check" in lower
                action_str = "preview" if dry else "desktop"
                answer = ai_file_organizer({"action": action_str}, player=player)
            elif any(k in lower for k in ["downloads"]):
                answer = ai_file_organizer({"action": "downloads"}, player=player)
            elif any(k in lower for k in ["now", "karo", "run", "go"]):
                answer = ai_file_organizer({"action": "organize", "folder": str(Path.home() / "Desktop")}, player=player)
            else:
                answer = ai_file_organizer({"action": "preview"}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        # ── Price Alert / Crypto / Stock ──────────────────────────────────────
        if any(k in lower for k in ["bitcoin price", "ethereum price", "btc price", "eth price",
                                     "crypto price", "stock price", "crypto rate", "coin rate",
                                     "set price alert", "price alert", "portfolio dekho",
                                     "my portfolio", "crypto portfolio", "doge price", "bnb price",
                                     "solana price", "sol price"]):
            if any(k in lower for k in ["portfolio", "holdings"]):
                answer = price_alert({"action": "portfolio"}, player=player)
            elif any(k in lower for k in ["set alert", "alert", "notify", "bata dena"]):
                # Try to extract coin and price from raw
                import re as _re
                nums = _re.findall(r"[\d,]+(?:\.\d+)?", raw.replace(",", ""))
                target = float(nums[0]) if nums else 0
                coin = "bitcoin"
                for c in ["btc", "eth", "bitcoin", "ethereum", "doge", "sol", "bnb"]:
                    if c in lower:
                        coin = c
                        break
                direction = "below" if any(k in lower for k in ["below", "gire", "neeche"]) else "above"
                answer = price_alert({"action": "alert", "asset": coin, "direction": direction, "target": target}, player=player)
            else:
                # Detect coin from query
                coin = "bitcoin"
                for c in ["bitcoin", "btc", "ethereum", "eth", "doge", "dogecoin", "solana", "sol",
                          "bnb", "xrp", "ripple", "cardano", "ada", "matic", "polygon", "shib"]:
                    if c in lower:
                        coin = c
                        break
                answer = price_alert({"action": "price", "asset": coin}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        # ── Smart Email Composer ──────────────────────────────────────────────
        if any(k in lower for k in ["email likho", "email compose", "write email", "draft email",
                                     "email banao", "compose email", "email subject", "gmail kholo",
                                     "email likh do"]):
            if any(k in lower for k in ["subject", "headline"]):
                topic = _after_keyword(raw, ["email subject", "subject for", "headline"]).strip() or raw
                answer = smart_email({"action": "subjects", "topic": topic}, player=player)
            elif any(k in lower for k in ["gmail kholo", "open gmail"]):
                answer = smart_email({"action": "open_gmail"}, player=player)
            else:
                # Extract to/about from raw
                to_addr = ""
                body_hint = _after_keyword(raw, ["email likho", "write email", "draft email",
                                                  "email banao", "compose email", "email likh do"]).strip()
                tone = "professional"
                for t in ["formal", "friendly", "casual", "urgent", "apology", "followup", "thankyou"]:
                    if t in lower:
                        tone = t
                        break
                answer = smart_email({"action": "compose", "to": to_addr, "body": body_hint, "tone": tone}, player=player)
            _learn_from_turn(raw, answer)
            return answer

        # Check if Stark Autopilot / Self-Healing is requested
        is_autopilot = any(k in lower for k in ["heal", "autopilot", "self-heal", "repair"])
        if is_autopilot and (lower.startswith(("run ", "execute ", "heal ", "autopilot ")) or ".py" in lower):
            script_path = ""
            match = re.search(r"([\w-]+\.py)\b", raw, re.I)
            if match:
                script_path = match.group(1)
            else:
                script_path = _after_keyword(raw, ["run", "execute", "heal", "autopilot", "self-heal"])
                script_path = script_path.strip()
            
            if script_path:
                answer = stark_autopilot({"script_path": script_path}, player=player)
                _learn_from_turn(raw, answer)
                return answer

        if lower.startswith(("run ", "execute ", "powershell ", "cmd ")):
            command = raw
            shell = "powershell"
            if lower.startswith("powershell "):
                command = raw[len("powershell "):]
            elif lower.startswith("cmd "):
                shell = "cmd"
                command = raw[len("cmd "):]
            elif lower.startswith("run "):
                command = raw[len("run "):]
            elif lower.startswith("execute "):
                command = raw[len("execute "):]
            command_lower = command.lower()
            confirmed_value = ""
            if "confirmed=power_action" in command_lower:
                confirmed_value = "power_action"
            elif any(k in command_lower for k in ["confirmed=yes", "confirm yes", "i confirm"]):
                confirmed_value = "yes"
            command = re.sub(
                r"\b(confirmed=power_action|confirmed=yes|confirm yes|i confirm)\b",
                "",
                command,
                flags=re.IGNORECASE,
            ).strip()
            return command_runner(
                {"action": "run", "command": command, "shell": shell, "confirmed": confirmed_value},
                player=player,
            )

        if any(k in lower for k in ["list files", "show files", "files in", "desktop files", "downloads files"]):
            path = "desktop"
            if "download" in lower:
                path = "downloads"
            elif "document" in lower:
                path = "documents"
            elif "picture" in lower or "photo" in lower:
                path = "pictures"
            elif "home" in lower:
                path = "home"
            return file_controller({"action": "list", "path": path}, player=player)

        if lower.startswith(("find file ", "search file ", "file find ")):
            query = _after_keyword(raw, ["find file", "search file", "file find"])
            return file_controller({"action": "find", "path": "home", "name": query, "max_results": 20}, player=player)

        if lower.startswith(("read file ", "open text file ")):
            name = _after_keyword(raw, ["read file", "open text file"])
            return file_controller({"action": "read", "path": "home", "name": name}, player=player)

        write_prefixes = (
            "type ",
            "write ",
            "smart type ",
            "likho ",
            "likh ",
            "लिखो ",
            "लिख ",
            "ye likho ",
            "yeh likho ",
            "यह लिखो ",
        )
        if lower.startswith(write_prefixes):
            text_to_type = raw
            for prefix in write_prefixes:
                if lower.startswith(prefix):
                    text_to_type = raw[len(prefix):].strip()
                    break
            clear_first = not any(k in lower for k in ["without clear", "clear mat", "clear nahi", "मत मिटाओ"])
            return computer_control({"action": "smart_type", "text": text_to_type, "clear_first": clear_first}, player=player)

        if lower.startswith("press "):
            return computer_control({"action": "press", "key": raw[len("press "):].strip()}, player=player)

        if lower.startswith("hotkey "):
            return computer_control({"action": "hotkey", "keys": raw[len("hotkey "):].strip()}, player=player)

        click_phrases = [
            "click ",
            "screen click ",
            "click karo",
            "click kar do",
            "yahan click",
            "yahaan click",
            "is button par click",
            "button par click",
            "यहां क्लिक",
            "यहाँ क्लिक",
            "क्लिक करो",
        ]
        if lower.startswith(("click ", "screen click ")) or any(k in lower for k in click_phrases[2:]):
            desc = _after_keyword(raw, ["screen click", "click"])
            desc = desc or raw
            return computer_control({"action": "screen_click", "description": desc}, player=player)

        if "screenshot" in lower:
            return computer_control({"action": "screenshot"}, player=player)

        if any(k in lower for k in ["open ", "khol", "kholo", "launch "]):
            app = _after_keyword(raw, ["open", "launch", "khol do", "kholo", "khol"])
            if app:
                return app_manager({"action": "open", "app_name": app}, player=player)

        if any(k in lower for k in ["close ", "band", "band karo"]):
            app = _after_keyword(raw, ["close", "band karo", "band kar do", "band"])
            if app:
                return app_manager({"action": "close", "app_name": app}, player=player)

        if "installed app" in lower or "app list" in lower:
            return app_manager({"action": "list"}, player=player)
        if "running app" in lower or "running process" in lower:
            return app_manager({"action": "running", "limit": 40}, player=player)

        if _has_any(lower, _DEEP_RESEARCH_TRIGGERS):
            topic = _extract_info_topic(raw)
            depth = "full" if any(k in lower for k in ["full", "advanced", "bahut detail", "bahut detailed"]) else "standard"
            save_docx = "docx" in lower or "word" in lower
            refresh = any(k in lower for k in ["refresh", "fresh", "dobara", "naya"])
            return deep_research(
                {"topic": topic or raw, "depth": depth, "save_docx": save_docx, "refresh": refresh},
                player=player,
            )

        if any(k in lower for k in _QUICK_INFO_TRIGGERS):
            return _quick_research_answer(raw)

        if re.search(r"\bnotes?\b", lower) or any(k in lower for k in ["likh lo", "save note"]):
            if any(k in lower for k in ["list", "dikhao", "show"]):
                return quick_notes({"action": "list"}, player=player)
            content = _after_keyword(raw, ["note", "save note", "likh lo", "yaad ke liye"])
            return quick_notes({"action": "create", "title": "Quick Note", "content": content or raw}, player=player)

        if "clipboard" in lower or "copy text" in lower:
            if any(k in lower for k in ["save", "file"]):
                return clipboard_manager({"action": "save", "title": "clipboard"}, player=player)
            if any(k in lower for k in ["clear", "khali"]):
                return clipboard_manager({"action": "clear"}, player=player)
            return clipboard_manager({"action": "read"}, player=player)

        if "notification" in lower:
            return notification_watcher({"action": "scan"}, player=player)

        if "privacy" in lower or "sensitive" in lower or "secret" in lower:
            return privacy_guard({"action": "scan"}, player=player)

        media_words = ["media hub", "slideshow", "slide show", "gallery", "my photos", "meri photo", "mera pic", "my pics"]
        if any(k in lower for k in media_words):
            if any(k in lower for k in ["slideshow", "slide show", "gallery", "dikhao", "show"]):
                return media_hub({"action": "slideshow", "person": "me", "limit": 24}, player=player)
            return media_hub({"action": "my_photos", "person": "me", "limit": 12}, player=player)

        if current_file and any(k in lower for k in ["upload", "save this", "remember this", "media hub me", "library me"]):
            params = {"action": "upload", "file_path": current_file, "note": raw}
            if any(k in lower for k in ["mera", "my photo", "my pic", "meri photo"]):
                params.update({"person": "me", "me": True})
            return media_hub(params, player=player)

        if current_file and any(k in lower for k in ["read document", "document pad", "document padh", "pdf pad", "pdf padh", "summarize document"]):
            return media_hub({"action": "read", "file_path": current_file}, player=player)

        if current_file and any(k in lower for k in ["send file", "send document", "send photo", "bhej", "bhejo"]):
            return "Receiver aur platform batao, jaise: 'send this file to Rahul on WhatsApp'."

        # Check if user wants to analyze a photo or find someone's social media from a photo/file
        is_photo_query = any(k in lower for k in ["photo", "pic", "picture", "face", "chehra", "profile", "image", "chehara"])
        is_who_or_social = any(k in lower for k in ["who", "kaun", "koun", "pahchan", "recognize", "identify", "social", "instagram", "facebook", "account", "profile", "reverse search", "dekh", "deep"])

        if current_file and (
            (is_photo_query and is_who_or_social) or 
            any(k in lower for k in ["kaun hai ye", "koun hai ye", "who is this", "whose photo is this", "whose face is this", "identify this", "iska social"])
        ):
            return photo_memory({"action": "deep_recognize", "file_path": current_file, "query": raw}, player=player)

        if "photo" in lower or "pic" in lower or "picture" in lower or "image" in lower:
            if any(k in lower for k in ["this is my", "mera photo", "my photo"]):
                if current_file:
                    return photo_memory({"action": "enroll", "file_path": current_file, "name": "me"}, player=player)
                return "Upload a photo first, then say this is my photo."
            if current_file and any(k in lower for k in ["who", "kaun", "pahchan", "recognize"]):
                answer = photo_memory({"action": "deep_recognize", "file_path": current_file, "query": raw}, player=player)
                _learn_from_turn(raw, answer)
                return answer
            if any(k in lower for k in ["camera se photo", "photo lo", "meri photo", "capture photo"]) and not current_file:
                answer = photo_memory({"action": "recognize", "capture": True, "query": raw}, player=player)
                _learn_from_turn(raw, answer)
                return answer
            query = _after_keyword(raw, ["photo", "pic", "picture", "image"])
            return photo_memory({"action": "find", "query": query, "limit": 8}, player=player)

        # ── Autonomous Self-Evolution & Live Coding Intercept ("Ultron Dynamic Upgrade") ──
        self_upgrade_keywords = ["apne me add karo", "self-upgrade", "apne aap me add karo", "upgrade yourself to", "write a new tool", "create a custom action", "upgrade kar", "upgrade karo"]
        if any(kw in lower for kw in self_upgrade_keywords):
            try:
                api_key = _get_api_key()
                if api_key:
                    import google.generativeai as genai
                    genai.configure(api_key=api_key)
                    model = genai.GenerativeModel("gemini-2.5-flash-lite")
                    
                    extraction_prompt = f"""
Analyze the user's upgrade/feature request:
---
{raw}
---

Identify:
1. A clean Python variable-compliant name for the target module (e.g. `speed_converter`, `currency_formatter`, `timezone_lookup`, etc.). Use lowercase and underscores.
2. A detailed feature prompt describing what the code needs to accomplish.

Return strictly a JSON object (no markdown wrapper, just raw JSON):
{{
  "module_name": "speed_converter",
  "prompt": "Description of the feature..."
}}
"""
                    resp = model.generate_content(extraction_prompt)
                    resp_text = resp.text.strip()
                    if "```json" in resp_text:
                        resp_text = resp_text.split("```json", 1)[1].split("```", 1)[0].strip()
                    elif "```" in resp_text:
                        resp_text = resp_text.split("```", 1)[1].split("```", 1)[0].strip()
                    
                    data = json.loads(resp_text)
                    module_name = data.get("module_name", "custom_feature").strip()
                    feature_prompt = data.get("prompt", raw).strip()
                    
                    # Ensure module_name has no extensions
                    module_name = module_name.replace(".py", "")
                    
                    # Call self_evolution_protocol
                    from actions.self_evolution_protocol import self_evolution_protocol
                    params = {
                        "action": "generate_code",
                        "prompt": feature_prompt,
                        "context": "Autonomous chat request self-upgrade trigger.",
                        "target_module": module_name
                    }
                    evo_res = self_evolution_protocol(params)
                    
                    # Speak status and dynamic reload
                    import importlib
                    import sys
                    module_path = f"actions.{module_name}"
                    
                    try:
                        if module_path in sys.modules:
                            importlib.reload(sys.modules[module_path])
                            mod = sys.modules[module_path]
                        else:
                            mod = importlib.import_module(module_path)
                            
                        func = getattr(mod, module_name, None)
                        if func:
                            # Run it live for the user!
                            test_res = func(parameters={"action": "status"})
                            return (
                                f"⚡ **Ultron Self-Evolution Activation Successful!** 🦾\n\n"
                                f"• **Module Created:** `actions/{module_name}.py` (Auto-compiled & verified)\n"
                                f"• **Gemini Prompts:** {feature_prompt}\n"
                                f"• **Evolution Protocol Response:** {evo_res}\n\n"
                                f"⚡ **Live Module Execution Output (Parameters=status):**\n{test_res}"
                            )
                    except Exception as ex:
                        return (
                            f"⚡ **Self-Evolution Partial Success:** Deployed module `actions/{module_name}.py`, "
                            f"but initial execution check failed: {ex}.\nProtocol Logs: {evo_res}"
                        )
            except Exception as e:
                print(f"[TextFallback] Autonomous self-upgrade intercept failed: {e}")

        answer = _model_answer(raw)
        _learn_from_turn(raw, answer)
        return answer
    except Exception as e:
        # ── Auto-learn from text fallback errors silently ──
        try:
            auto_learn_from_error("text_fallback", str(e), {"text": raw[:200]})
        except Exception:
            pass
        return f"Text fallback failed: {e}"


def _humanize_response(user_text: str, tool_result: str) -> str:
    if not tool_result or tool_result.strip() == "":
        return tool_result
    
    # Don't humanize if it looks like an error message or failure
    if "failed" in tool_result.lower() or "error" in tool_result.lower():
        try:
            from actions.human_mind import think_humanly
            # We can still humanize the error to sound caring!
            return think_humanly(user_text, task_result=tool_result)
        except Exception:
            return tool_result

    # If the result has code blocks or is a long technical output (e.g. file content, command output)
    has_code = "```" in tool_result or "\nclass " in tool_result or "\ndef " in tool_result or "import " in tool_result
    is_very_long = len(tool_result) > 1000
    
    # Also ignore greeting/intro words since they are already human
    if any(intro in tool_result.lower()[:30] for intro in ["hello", "good morning", "good night", "haji", "aaj main"]):
        return tool_result

    if has_code or is_very_long:
        # Generate a warm, human-like introduction and append the raw result
        try:
            from actions.human_mind import think_humanly
            intro = think_humanly(user_text, task_result="Task completed successfully. Do NOT include the code or files in this intro, just confirm you did it warmly in Hinglish in 1-2 sentences.")
            return f"{intro}\n\n{tool_result}"
        except Exception:
            return tool_result

    try:
        from actions.human_mind import think_humanly
        # Fully humanize the short result
        return think_humanly(user_text, task_result=tool_result)
    except Exception:
        return tool_result


def run_text_fallback(text: str, current_file: str | None = None, player=None) -> str:
    raw = (text or "").strip()
    _state.is_fallback_chat = False  # Reset flag
    raw_answer = _run_text_fallback_raw(raw, current_file, player)
    
    # If the response is already a chat response, return it directly
    if getattr(_state, "is_fallback_chat", False):
        return raw_answer
        
    # Otherwise, humanize the tool output
    humanized = _humanize_response(raw, raw_answer)
    
    # 20% chance to prefix with MARK's inner monologue thoughts
    import random
    if random.random() < 0.20:
        try:
            from actions.human_mind import execute_advanced_connect
            monologue = execute_advanced_connect("inner_monologue", raw)
            if monologue:
                return f"💭 *MARK's Thoughts: {monologue}*\n\n{humanized}"
        except Exception:
            pass
            
    return humanized
