# Callable cloud adapter: keep get_llm_reply(messages)->JSON text interchangeable.
import math
import json
from openai import APIError

import config
from backend_errors import BackendError
from conversation import parse_reply_segments, reply_instructions, reply_schema
from context_tools import (CONTEXT_INSTRUCTIONS, MEMORY_INSTRUCTIONS, MAX_TOOL_CALLS, TurnContextTools,
                           MAX_BATCH_RESULT_CHARS, context_free_messages, refers_to_shared_history)
from web_search import (SEARCH_INSTRUCTIONS, clean_reply_text, field, force_search,
                        latest_user_text, response_sources, search_requests, search_actions)


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
    supports_context_tools = True

    def __init__(self, client, model=None):
        self.client = client
        self.model = model or config.OPENAI_MODEL
        self.last_usage = {}
        self.last_sources = []
        self.estimated_session_usd = 0.0
        self.unaccounted_usage = False

    def __call__(self, messages):
        return self._reply(messages)

    def reply_with_context(self, messages, *, wow_context=None, japanese_context=None,
                           control=None, log=None, memory=None):
        return self._reply(context_free_messages(messages),
                           context=TurnContextTools(wow_context, japanese_context, memory=memory),
                           control=control, log=log)

    def _parameters(self, messages, enabled, forced, remaining):
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
        parameters = {
            "model": self.model, "input": messages, "store": False,
            "max_output_tokens": config.OPENAI_MAX_OUTPUT_TOKENS,
            "text": {"format": {"type": "json_schema", "name": "akane_reply",
                                "schema": reply_schema(), "strict": True}},
            "instructions": ((SEARCH_INSTRUCTIONS + "\n\n") if config.OPENAI_WEB_SEARCH_ENABLED else "") + reply_instructions(),
        }
        if config.OPENAI_REASONING_EFFORT is not None:
            parameters["reasoning"] = {"effort": config.OPENAI_REASONING_EFFORT}
        if enabled:
            maximum, price = remaining, config.OPENAI_WEB_SEARCH_USD_PER_CALL
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
                instructions=SEARCH_INSTRUCTIONS + "\n\n" + reply_instructions(),
            )
        return parameters

    def _record_usage(self, response, enabled):
        # Aggregate every stage, including withheld/cancelled responses. Missing
        # usage in any stage makes the corresponding turn total unknown.
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
        current = {
            "model": getattr(response, "model", None) or self.model,
            "input_tokens": input_tokens, "cached_input_tokens": cached,
            "output_tokens": output_tokens, "reasoning_tokens": reasoning,
            "web_search_calls": calls, "search_usage_source": count_source,
            "estimated_search_cost_usd": search_cost,
            "estimated_text_cost_usd": text_cost,
            "estimated_api_cost_usd": cost,
        }
        numeric = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens",
                   "web_search_calls", "estimated_search_cost_usd", "estimated_text_cost_usd",
                   "estimated_api_cost_usd")
        for key in numeric:
            old = self.last_usage.get(key, 0)
            self.last_usage[key] = None if old is None or current[key] is None else old + current[key]
        self.last_usage["model"] = current["model"]
        old_source = self.last_usage.get("search_usage_source", count_source)
        self.last_usage["search_usage_source"] = count_source if old_source == count_source else "mixed"
        if cost is not None:
            self.estimated_session_usd += cost
        else:
            self.unaccounted_usage = True
        return current

    def _reply(self, messages, *, context=None, control=None, log=None):
        self.last_usage = {"provider": "openai", "model": self.model,
                           "provider_requests": 0, "local_tool_calls": 0}
        self.last_sources = []
        sources, searched = [], False
        remaining = config.OPENAI_WEB_SEARCH_MAX_CALLS
        enabled = config.OPENAI_WEB_SEARCH_ENABLED
        forced = enabled and force_search(messages)
        user_text = latest_user_text(messages).lstrip()
        if (forced and context and context.memory is not None and not user_text.lower().startswith("/search")
                and refers_to_shared_history(user_text)):
            forced = False
        local_tools = context.definitions() if context else []
        # This request-local list is discarded after the turn. Tool output never
        # becomes a permanent snapshot attachment in conversation history.
        inputs = list(messages)
        try:
            if not enabled and user_text.lower().startswith("/search"):
                raise BackendError("Web search is disabled in config.py.")
            for stage in range(2):  # At most one batch of local retrievals.
                if control:
                    control.check()
                use_search = enabled and (stage == 0 or remaining > 0)
                parameters = self._parameters(inputs, use_search, forced and not searched, remaining)
                if context is not None:
                    parameters["instructions"] += "\n\n" + CONTEXT_INSTRUCTIONS
                    if context.memory is not None:
                        parameters["instructions"] += "\n\n" + MEMORY_INSTRUCTIONS
                    if stage == 0 and local_tools:
                        parameters.setdefault("tools", []).extend(local_tools)
                        parameters.setdefault("tool_choice", "auto")
                        # Stateless reasoning continuations must carry encrypted
                        # reasoning along with function calls/results.
                        parameters.setdefault("include", []).append("reasoning.encrypted_content")
                    if stage == 1:
                        parameters["instructions"] += "\nLocal retrieval is finished. Answer from the supplied results."
                self.last_usage["provider_requests"] += 1
                response = self.client.responses.create(**parameters)
                current = self._record_usage(response, use_search)
                if log:
                    log("provider_usage", request=self.last_usage["provider_requests"], **current)
                    actions = search_actions(response)
                    if actions or current["web_search_calls"]:
                        log("web_search", request=self.last_usage["provider_requests"],
                            search_requests=current["web_search_calls"], actions=actions)
                output = field(response, "output", []) or []
                operations = sum(field(item, "type") == "web_search_call" for item in output)
                if operations or current["web_search_calls"]:
                    searched = True
                # The hosted tool-operation limit is shared across both requests;
                # reported searches can exceed visible actions and also reserve it.
                # A smaller affordable limit selected on the first request must
                # also bound the continuation, not reset to the config maximum.
                remaining = parameters.get("max_tool_calls", remaining) - max(operations, current["web_search_calls"] or 0)
                if current["web_search_calls"] is None:
                    remaining = 0  # Unknown search usage cannot authorize more.
                for source in response_sources(response):
                    existing = next((row for row in sources if row["url"] == source["url"]), None)
                    if existing is None:
                        sources.append(source)
                    elif source["kind"] == "cited":
                        existing.update(source)
                if getattr(response, "status", None) != "completed":
                    raise BackendError("OpenAI did not complete the reply; check the output-token limit or provider status.")
                if control:
                    control.check()  # Account late output, but never continue it.
                calls = [item for item in output if field(item, "type") == "function_call"]
                if not calls:
                    break
                if context is None or stage or len(calls) > MAX_TOOL_CALLS:
                    raise BackendError("OpenAI exceeded the local context retrieval limit; no automatic retry was made.")
                ids = [field(item, "call_id") for item in calls]
                if any(not isinstance(identifier, str) or not identifier for identifier in ids) or len(set(ids)) != len(ids):
                    raise BackendError("OpenAI returned invalid context tool call identifiers.")
                inputs = inputs + list(output)  # Preserve reasoning and every call ID.
                budget_error = json.dumps({"status": "too_large", "reason": "The local retrieval result budget was reached."})
                # Reserve room for complete error results even after data fills
                # the batch. Never cut a JSON string to enforce a size limit.
                result_characters = len(calls) * len(budget_error)
                for item in calls:
                    if control:
                        control.check()
                    name = field(item, "name")
                    result, status = context.execute(name, field(item, "arguments"))
                    selection = (json.loads(field(item, "arguments"))
                                 if status != "invalid_request" and name != "search_memory" else None)
                    if result_characters + len(result) > MAX_BATCH_RESULT_CHARS:
                        status = "too_large"
                        result = budget_error
                    else:
                        result_characters += len(result)
                    self.last_usage["local_tool_calls"] += 1
                    if log:
                        log("context_tool", name=name, status=status, selection=selection,
                            result_characters=len(result))
                    inputs.append({"type": "function_call_output", "call_id": field(item, "call_id"),
                                   "output": result})
                # An unaccounted stage cannot authorize another budgeted request.
                # _parameters rechecks the session cap before the continuation.
            text = response.output_text
            if not isinstance(text, str) or not text.strip():
                raise BackendError("OpenAI returned no reply text (possibly a refusal).")
            if forced and not searched:
                raise BackendError("The required web lookup was not performed; the answer was withheld.")
            if searched and not sources:
                raise BackendError("Web lookup returned no usable source links; the answer was withheld.")
            segments = parse_reply_segments(text)
            for part in segments:
                for language in part:
                    part[language] = clean_reply_text(part[language], sources)
                    if not part[language]:
                        raise BackendError("OpenAI returned empty text after removing citation markup.")
            # Preserve paired boundaries through cleanup. Older adapter results
            # remain readable; new requests require the segmented schema.
            data = {"segments": segments} if "segments" in json.loads(text) else segments[0]
            text = json.dumps(data, ensure_ascii=False)
            self.last_sources = sources
            return text
        except APIError as error:
            self.unaccounted_usage = True  # A failed HTTP call may still have billed.
            for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens",
                        "web_search_calls", "estimated_search_cost_usd", "estimated_text_cost_usd",
                        "estimated_api_cost_usd"):
                self.last_usage[key] = None
            raise BackendError(f"OpenAI request failed: {error}") from error
        except (AttributeError, TypeError, ValueError, KeyError) as error:
            raise BackendError(f"OpenAI returned invalid response/usage data: {type(error).__name__}") from error

    def close(self):
        self.client.close()
