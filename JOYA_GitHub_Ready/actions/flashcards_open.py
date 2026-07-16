from __future__ import annotations

import json
import re
import threading
import webbrowser
from typing import List
from datetime import datetime

from actions.ssl_config import configure_network_ssl
configure_network_ssl()

def _get_active_url(path: str) -> str:
    import socket
    is_local_active = False
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(("127.0.0.1", 8000))
        is_local_active = True
        s.close()
    except Exception:
        pass
    if is_local_active:
        return f"http://127.0.0.1:8000/{path}"
    else:
        return f"https://joya-site-1.onrender.com/{path}"



def _generate_ai_flashcards(topic: str) -> list[dict]:
    """Use Gemini AI to generate fresh, unique flashcards on any topic."""
    try:
        import google.generativeai as genai

        prompt = f"""You are an expert teacher and flashcard creator.
Generate exactly 8 high-quality study flashcards about: "{topic}"

Rules:
- Each question must be UNIQUE and cover a DIFFERENT aspect of the topic.
- Questions should range from basics to advanced concepts.
- Answers should be clear, concise (2-4 sentences), and written in a human-friendly style.
- Include real-world examples or analogies where helpful.
- Do NOT repeat the same concept in multiple cards.

Return ONLY valid JSON array, no markdown, no explanation:
[
  {{"id": 1, "question": "...", "answer": "...", "topic": "{topic}"}},
  ...
]"""

        model = genai.GenerativeModel("gemini-2.5-flash")
        resp = model.generate_content(prompt)
        raw = resp.text.strip()
        
        # Clean markdown fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        
        cards = json.loads(raw)
        if isinstance(cards, list) and len(cards) > 0:
            _save_generated_cards(cards)
            return cards
    except Exception as e:
        print(f"[AI Flashcards] Generation error: {e}")
    
    return []


def _save_generated_cards(cards: list[dict]):
    try:
        from actions.study_assistant import STUDY_FILE
        data = {}
        if STUDY_FILE.exists():
            try:
                data = json.loads(STUDY_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        
        save_cards = []
        for idx, c in enumerate(cards):
            save_cards.append({
                "id": int(c.get("id") or idx + 1),
                "question": str(c.get("question") or c.get("q") or ""),
                "answer": str(c.get("answer") or c.get("a") or ""),
                "topic": str(c.get("topic") or ""),
                "created": re.sub(r'\.\d+', '', datetime.now().isoformat()) if 'datetime' in globals() else "2026-07-14T16:20:00"
            })
        data["flashcards"] = save_cards
        STUDY_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[AI Flashcards] Error saving to STUDY_FILE: {e}")



def _generate_ai_notes(topic: str) -> str:
    """Use Gemini AI to write premium human-like notes/summary on a topic."""
    try:
        import google.generativeai as genai

        prompt = f"""You are a student writing detailed, premium, handwritten-style study notes for yourself on the topic: "{topic}".

Rules:
- Write in a highly personal, human way (conversational, using shortcuts, direct, addressing yourself like "Remember this!", "Exam tip:").
- Use bullet points, bold key terms, and short, crisp lists.
- Avoid formal introductions/conclusions. Get straight to the facts.
- Insert realistic human-like annotations (e.g. "[IMP!]", "[CRUCIAL]", "[PYQ 2025]", "[Self-Note: don't forget this part!]").
- Make it extremely detailed, clear, and comprehensive.
- Keep it around 400-600 words.

Write the notes directly, no JSON, no markdown fences."""

        model = genai.GenerativeModel("gemini-2.5-flash")
        resp = model.generate_content(prompt)
        content = resp.text.strip()
        if content:
            _save_generated_notes(topic, content)
        return content
    except Exception as e:
        print(f"[AI Notes] Generation error: {e}")
        return f"Could not generate notes for '{topic}'. Please check your internet connection."


def _save_generated_notes(topic: str, content: str):
    try:
        from actions.study_assistant import STUDY_FILE
        data = {}
        if STUDY_FILE.exists():
            try:
                data = json.loads(STUDY_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        data["notes"] = {
            "topic": topic,
            "content": content,
            "created": datetime.now().isoformat()
        }
        STUDY_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[AI Notes] Error saving notes to STUDY_FILE: {e}")


def _load_static_flashcards(topic: str = "") -> list[dict]:
    """Fallback: Load saved Study Assistant flashcards from local file."""
    try:
        from actions.study_assistant import STUDY_FILE
        data = json.loads(STUDY_FILE.read_text(encoding="utf-8")) if STUDY_FILE.exists() else {}
        raw_cards: List[dict] = data.get("flashcards", [])
        if topic:
            raw_cards = [c for c in raw_cards if topic.lower() in (c.get("topic") or "").lower()]
        cards: list[dict] = []
        for idx, c in enumerate(raw_cards):
            question = str(c.get("question") or "").strip()
            answer = str(c.get("answer") or "").strip()
            if not question or not answer:
                continue
            cards.append({
                "id": int(c.get("id") or idx + 1),
                "question": question,
                "answer": answer,
                "topic": str(c.get("topic") or ""),
            })
        return cards
    except Exception:
        return []


def _emit_to_main_window(player, cards: list[dict], topic: str = "") -> bool:
    """Ask the main UI thread to open the flashcards window when available."""
    try:
        win = getattr(player, "_win", None) or player
        sig = getattr(win, "_open_flashcards_sig", None)
        if sig is None:
            return False
        sig.emit({"cards": cards, "topic": topic})
        return True
    except Exception:
        return False


def _emit_writer_to_main_window(topic: str, content: str) -> bool:
    """Ask the main UI thread to open the Premium Writer window."""
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            for w in app.topLevelWidgets():
                if hasattr(w, "_show_premium_writer_sig"):
                    w._show_premium_writer_sig.emit(topic, content)
                    return True
    except Exception:
        pass
    return False


def open_flashcards_window(player=None, topic: str = "") -> str:
    """Open PyQt window with AI-generated flashcards.
    
    If a topic is provided, AI generates fresh unique flashcards.
    Otherwise falls back to stored flashcards.
    """
    cards = []

    # Try AI generation first if topic is given
    if topic and len(topic.strip()) > 1:
        cards = _generate_ai_flashcards(topic)

    # Fallback to static file
    if not cards:
        cards = _load_static_flashcards(topic)

    # If still empty, generate a default set
    if not cards and topic:
        cards = [
            {"id": 1, "question": f"What is {topic}?", "answer": f"AI could not generate cards for '{topic}'. Please try again.", "topic": topic}
        ]

    try:
        webbrowser.open(_get_active_url("flashcards.html"))
    except Exception as e:
        print(f"[Browser Launch Error] {e}")

    if _emit_to_main_window(player, cards, topic):
        return f"Opening {len(cards)} AI-generated flashcards on '{topic}'."

    # Direct window open (fallback)
    from PyQt6.QtWidgets import QApplication
    from actions.ui_flashcards import Flashcard, FlashcardsWindow

    flashcards = [
        Flashcard(
            id=int(c.get("id") or idx + 1),
            question=str(c.get("question") or ""),
            answer=str(c.get("answer") or ""),
            topic=str(c.get("topic") or ""),
        )
        for idx, c in enumerate(cards)
    ]

    app = QApplication.instance()
    if app is None:
        app = QApplication([])

    win = FlashcardsWindow(flashcards, parent=player if hasattr(player, "window") else None)
    app._joya_flashcards_window = win
    win.show()
    win.raise_()
    win.activateWindow()

    return f"Opened {len(flashcards)} AI-generated flashcards."


def open_notes_window(topic: str = "") -> str:
    """Generate AI notes and show in the Premium Writer window."""
    if not topic or len(topic.strip()) < 2:
        return "Please specify a topic for notes generation."
    
    notes = _generate_ai_notes(topic)
    try:
        webbrowser.open(_get_active_url("notes.html"))
    except Exception as e:
        print(f"[Browser Launch Error] {e}")
        
    if _emit_writer_to_main_window(f"📝 Notes: {topic}", notes):
        return f"AI-generated premium notes on '{topic}' opened in writer window."
    
    return notes


def open_flashcards_and_notes(player=None, topic: str = "") -> str:
    """Generate BOTH flashcards and notes for a topic (used by Student Portal missions)."""
    if not topic or len(topic.strip()) < 2:
        return "Please specify a topic."
    
    results = []
    
    # Generate flashcards (will automatically open flashcards.html browser page)
    fc_result = open_flashcards_window(player=player, topic=topic)
    results.append(fc_result)
    
    # Generate notes in writer window
    def _gen_notes():
        notes = _generate_ai_notes(topic)
        _emit_writer_to_main_window(f"📝 Study Notes: {topic}", notes)
        try:
            webbrowser.open(_get_active_url("notes.html"))
        except Exception as e:
            print(f"[Browser Launch Error] {e}")
    
    # Run notes generation in background to not block UI
    t = threading.Thread(target=_gen_notes, daemon=True)
    t.start()
    
    results.append(f"Generating premium notes on '{topic}' in background...")
    
    return "\n".join(results)
