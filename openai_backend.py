# Callable cloud adapter: keep get_llm_reply(messages)->JSON text interchangeable.
import math
import json
from openai import APIError

import config
from backend_errors import BackendError
from conversation import BILINGUAL_SCHEMA, parse_bilingual_reply
from web_search import (SEARCH_INSTRUCTIONS, clean_reply_text, field, force_search,
                        latest_user_text, response_sources, search_requests)


def estimate_text_cost(model, input_tokens, cached_tokens, output_tokens):
    rates = config.OPENAI_PRICING_USD_PER_MILLION.get(model)
    if rates is None or any(value is None for value in (input_tokens, cached_tokens, output_tokens)):
        return None
    if not {"input", "cached_input", "output"}.issubset(rates):
        raise ValueError("OpenAI pricing needs input, cached_input, and output rates.")
    if any(not isinstance(value, (int, float)) or isinstance(value, bool)
           or not math.isfinite(value) or value < 0 for value in rates.values()):
        raise ValueError("OpenAI price rates must be non-negative numbers.")
    cached_tokens = min(cached_tokens, input_tokens)
    return ((input_tokens - cached_tokens) * rates["input"]
            + cached_tokens * rates["cached_input"] + output_tokens * rates["output"]) / 1_000_000


def token_count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class OpenAIBackend:
    supports_web_search = True

    def __init__(self, client, model=None):
        self.client = client
        self.model = model or config.OPENAI_MODEL
        self.last_usage = {}
        self.last_sources = []
        self.estimated_session_usd = 0.0
        self.unaccounted_usage = False

    def __call__(self, messages):
        self.last_usage = {"provider": "openai", "model": self.model}
        self.last_sources = []  # Failed/no-search turns must never reuse earlier citations.
        try:
            estimate_text_cost(self.model, 0, 0, 0)  # Validate rates before a paid request.
        except ValueError as error:
            raise BackendError(str(error)) from error
        budget = config.OPENAI_SESSION_BUDGET_USD
        if budget is not None:
            if not isinstance(budget, (int, float)) or not math.isfinite(budget) or budget < 0:
                raise BackendError("OPENAI_SESSION_BUDGET_USD must be non-negative or None.")
            if self.model not in config.OPENAI_PRICING_USD_PER_MILLION:
                raise BackendError("Add pricing for OPENAI_MODEL or explicitly disable the estimated session budget.")
            if self.unaccounted_usage:
                raise BackendError("A previous request omitted usage; the estimated session budget cannot be checked.")
            if self.estimated_session_usd >= budget:
                raise BackendError("The estimated OpenAI session budget has been reached.")
        enabled = config.OPENAI_WEB_SEARCH_ENABLED
        forced = enabled and force_search(messages)
        if not enabled and latest_user_text(messages).strip().lower().startswith("/search"):
            raise BackendError("Web search is disabled in config.py.")
        parameters = {
            "model": self.model, "input": messages, "store": False,
            "max_output_tokens": config.OPENAI_MAX_OUTPUT_TOKENS,
            "text": {"format": {"type": "json_schema", "name": "akane_reply",
                                "schema": BILINGUAL_SCHEMA, "strict": True}},
        }
        if config.OPENAI_REASONING_EFFORT is not None:
            parameters["reasoning"] = {"effort": config.OPENAI_REASONING_EFFORT}
        if enabled:
            maximum, price = config.OPENAI_WEB_SEARCH_MAX_CALLS, config.OPENAI_WEB_SEARCH_USD_PER_CALL
            if not isinstance(maximum, int) or isinstance(maximum, bool) or not 1 <= maximum <= 10:
                raise BackendError("OPENAI_WEB_SEARCH_MAX_CALLS must be an integer from 1 to 10.")
            if not isinstance(price, (int, float)) or isinstance(price, bool) or not math.isfinite(price) or price < 0:
                raise BackendError("OPENAI_WEB_SEARCH_USD_PER_CALL must be non-negative.")
            if budget is not None and price > 0:
                # Reserve room for available tool calls; text generation can still
                # overshoot this soft cap, as it could before search was enabled.
                affordable = math.floor((budget - self.estimated_session_usd + 1e-12) / price)
                maximum = min(maximum, affordable)
                if maximum < 1:
                    raise BackendError("Not enough estimated session budget remains for a web lookup.")
            parameters.update(
                tools=[{"type": "web_search", "search_context_size": "low"}],
                tool_choice={"type": "web_search"} if forced else "auto",
                max_tool_calls=maximum,
                include=["web_search_call.action.sources"],
                instructions=SEARCH_INSTRUCTIONS,
            )
        try:
            response = self.client.responses.create(**parameters)
            # Usage is recorded even when output is refused/incomplete, since such
            # requests may still consume tokens and the caller may cancel afterward.
            usage = getattr(response, "usage", None)
            input_tokens = token_count(getattr(usage, "input_tokens", None))
            output_tokens = token_count(getattr(usage, "output_tokens", None))
            cached = token_count(getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", None))
            reasoning = token_count(getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", None))
            text_cost = estimate_text_cost(self.model, input_tokens, cached, output_tokens)
            calls, count_source = search_requests(response, enabled)
            # Search-content tokens are part of model usage; don't charge them a
            # second time. Add only the separate search request fee here.
            search_cost = (None if calls is None else 0.0 if calls == 0
                           else calls * config.OPENAI_WEB_SEARCH_USD_PER_CALL)
            cost = None if text_cost is None or search_cost is None else text_cost + search_cost
            self.last_usage.update({
                "model": getattr(response, "model", None) or self.model,
                "input_tokens": input_tokens, "cached_input_tokens": cached,
                "output_tokens": output_tokens, "reasoning_tokens": reasoning,
                "web_search_calls": calls, "search_usage_source": count_source,
                "estimated_search_cost_usd": search_cost,
                "estimated_text_cost_usd": text_cost,
                "estimated_api_cost_usd": cost,
            })
            if cost is not None:
                self.estimated_session_usd += cost
            else:
                self.unaccounted_usage = True
            if getattr(response, "status", None) != "completed":
                raise BackendError("OpenAI did not complete the reply; check the output-token limit or provider status.")
            text = response.output_text
            if not isinstance(text, str) or not text.strip():
                raise BackendError("OpenAI returned no reply text (possibly a refusal).")
            searched = bool(calls) or any(field(item, "type") == "web_search_call"
                                        for item in (field(response, "output", []) or []))
            sources = response_sources(response)
            if forced and not searched:
                raise BackendError("The required web lookup was not performed; the answer was withheld.")
            if searched and not sources:
                raise BackendError("Web lookup returned no usable source links; the answer was withheld.")
            if enabled:
                ja, en = parse_bilingual_reply(text)
                ja, en = clean_reply_text(ja, sources), clean_reply_text(en, sources)
                if not ja or not en:
                    raise BackendError("OpenAI returned empty text after removing citation markup.")
                # Return the same ja/en JSON contract; sources live in metadata.
                text = json.dumps({"ja": ja, "en": en}, ensure_ascii=False)
            self.last_sources = sources
            return text
        except APIError as error:
            raise BackendError(f"OpenAI request failed: {error}") from error
        except (AttributeError, TypeError, ValueError, KeyError) as error:
            raise BackendError(f"OpenAI returned invalid response/usage data: {type(error).__name__}") from error

    def close(self):
        self.client.close()
