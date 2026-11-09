# Shared turn processing for terminal and hotkey modes. One caller owns history.
from time import perf_counter, sleep
import re

import config
from backend_errors import BackendError
from controls import TurnControl, TurnCancelled
from conversation import parse_bilingual_reply, with_japanese_reference
from metrics import append_timing
from source_output import write_sources
from text_to_speech import SpeechSynthesisError


class TurnProcessor:
    def __init__(self, root, system_prompt, reply_function, *, tts_enabled,
                 synthesize, play, write_caption, sleep_function=sleep, event_callback=None):
        self.root = root
        self.reply_function = reply_function
        self.tts_enabled = tts_enabled
        self.synthesize, self.play = synthesize, play
        self.write_caption, self.sleep = write_caption, sleep_function
        self.messages = [{"role": "system", "content": system_prompt}]
        self.event_callback = event_callback

    def emit(self, kind, **payload):
        # GUI observers only enqueue data. Worker threads never touch Tk widgets.
        if self.event_callback:
            try:
                self.event_callback(kind, **payload)
            except Exception as error:
                print(f"Display event unavailable: {error}")

    def notice(self, message, level="info"):
        print(message)
        self.emit("notice", message=message, level=level)

    def update_sources(self, payload=None):
        try:
            write_sources(self.root, payload)
            self.emit("sources", **(payload or {"en": "", "sources": []}))
            return True
        except OSError as error:
            self.notice(f"Source output unavailable: {error}", "warning")
            return False

    def update_subtitle(self, text, *, clear_sources=True):
        if not text and clear_sources:
            self.update_sources()
        try:
            self.write_caption(self.root / "runtime" / "subtitle_en.txt", text)
            return True
        except OSError as error:
            self.notice(f"Subtitle output unavailable: {error}", "warning")
            return False

    def reset(self):
        self.messages[:] = self.messages[:1]
        self.update_subtitle("")
        self.emit("reset")
        self.notice("Conversation history reset.")

    def respond(self, text, *, input_mode="typed", stt_seconds=0.0,
                response_started=None, control=None, japanese_context=None):
        controlled = control is not None
        control = control or TurnControl()
        response_started = perf_counter() if response_started is None else response_started
        row = {
            "input_mode": input_mode,
            "stt_backend": config.STT_BACKEND if input_mode != "typed" else "none",
            "reply_backend": getattr(self.reply_function, "__module__", type(self.reply_function).__name__),
            "status": "reply_unavailable", "stt_seconds": stt_seconds,
            "llm_seconds": 0.0, "tts_seconds": 0.0, "ready_seconds": 0.0,
            "playback_seconds": 0.0, "ja_characters": 0, "en_characters": 0,
        }
        pending_user, committed, requested = False, False, False
        try:
            control.phase("thinking")
            self.update_subtitle("")
            if re.match(r"^/search\b", text.strip(), re.I) and not getattr(self.reply_function, "supports_web_search", False):
                raise BackendError("/search needs the OpenAI conversation backend; local Ollama has no search tool.")
            self.messages.append({"role": "user", "content": with_japanese_reference(text, japanese_context)})
            pending_user = True
            self.emit("user", text=text, mode=input_mode, japanese_context=japanese_context)
            llm_started = perf_counter()
            try:
                requested = True
                reply = self.reply_function(self.messages)
            finally:
                row["llm_seconds"] = perf_counter() - llm_started
            control.check()  # A stopped/paused HTTP result must never become speech.
            ja_reply, en_reply = parse_bilingual_reply(reply)
            self.messages.append({"role": "assistant", "content": reply})
            committed = True
            while len(self.messages) > 1 + config.MAX_HISTORY_TURNS * 2:
                del self.messages[1:3]
            row.update(ja_characters=len(ja_reply), en_characters=len(en_reply))
            print(f"LLM time: {row['llm_seconds']:.2f} seconds")
            print(f"Akane (JA): {ja_reply}")
            print(f"Akane (EN): {en_reply}")
            sources = getattr(self.reply_function, "last_sources", [])
            sources = sources if isinstance(sources, list) else []
            control.show_caption(lambda payload: self.emit("reply", **payload),
                                 {"ja": ja_reply, "en": en_reply, "sources": sources})
            if isinstance(sources, list) and sources:
                # The same lock used for captions protects source publication
                # against pause/stop clearing. Cancelled replies cannot revive it.
                control.show_caption(self.update_sources, {"en": en_reply, "sources": sources})
                for number, source in enumerate(sources, 1):
                    print(f"Source {number} ({source['kind']}): {source['title']} — {source['url']}")
                print(f"Clickable sources: {self.root / 'runtime' / 'sources.html'}")

            if self.tts_enabled:
                control.phase("synthesizing")
                tts_started = perf_counter()
                try:
                    try:
                        audio_data = self.synthesize(ja_reply)
                    finally:
                        row["tts_seconds"] = perf_counter() - tts_started
                    control.phase("speaking")
                    row["ready_seconds"] = perf_counter() - response_started
                    print(f"TTS generation time: {row['tts_seconds']:.2f} seconds")
                    print(f"Response ready time (including STT): {row['ready_seconds']:.2f} seconds")
                    shown = control.show_caption(self.update_subtitle, en_reply)
                    playback_started = perf_counter()
                    try:
                        completed = self.play(audio_data, control)
                    finally:
                        row["playback_seconds"] = perf_counter() - playback_started
                    if completed is False:
                        raise TurnCancelled()
                    control.check()
                    if shown:
                        remaining = max(0.0, config.SUBTITLE_MIN_SECONDS - row["playback_seconds"])
                        if controlled:
                            control.cancel.wait(remaining)
                            control.check()
                        else:
                            self.sleep(remaining)
                    row["status"] = "ok"
                    self.update_subtitle("", clear_sources=False)
                except (SpeechSynthesisError, RuntimeError) as error:
                    control.check()
                    row["status"] = "speech_unavailable"
                    row["ready_seconds"] = perf_counter() - response_started
                    control.show_caption(self.update_subtitle, en_reply)
                    self.notice(f"Speech unavailable: {error}", "warning")
            else:
                row["status"] = "tts_disabled"
                row["ready_seconds"] = perf_counter() - response_started
                control.show_caption(self.update_subtitle, en_reply)
                print(f"Response ready time (including STT): {row['ready_seconds']:.2f} seconds")
            return True
        except TurnCancelled:
            row["status"] = "cancelled"
            self.update_subtitle("")
            self.notice("Turn stopped.")
            return False
        except (BackendError, ValueError, TypeError) as error:
            self.notice(f"Reply unavailable: {error}", "error")
            return False
        finally:
            # Retain a valid visible reply if audio fails/stops after it was committed.
            # A cancelled/failed request before commitment rolls back its user entry.
            if pending_user and not committed:
                self.messages.pop()
            # A turn cancelled before the call must not copy the prior turn's charge.
            usage = getattr(self.reply_function, "last_usage", {}) if requested else {}
            if isinstance(usage, dict):
                row.update({
                    "llm_provider": usage.get("provider", ""),
                    "llm_model": usage.get("model", ""),
                    **{key: usage.get(key) for key in (
                        "input_tokens", "cached_input_tokens", "output_tokens",
                        "reasoning_tokens", "estimated_api_cost_usd", "web_search_calls",
                        "search_usage_source", "estimated_search_cost_usd", "estimated_text_cost_usd")},
                })
                if usage.get("provider") == "openai":
                    print(f"OpenAI tokens: input={usage.get('input_tokens')}, output={usage.get('output_tokens')}")
                    if usage.get("web_search_calls"):
                        print(f"Web search requests: {usage['web_search_calls']} (estimate includes search fees)")
                    if usage.get("estimated_api_cost_usd") is not None:
                        print(f"Estimated conversation API cost: ${usage['estimated_api_cost_usd']:.6f}")
            try:
                append_timing(self.root / "runtime" / "timings.csv", row)
            except (OSError, ValueError) as error:
                self.notice(f"Timing output unavailable: {error}", "warning")
            self.emit("metrics", row=dict(row),
                      session_cost=getattr(self.reply_function, "estimated_session_usd", None),
                      cost_unknown=getattr(self.reply_function, "unaccounted_usage", False))
