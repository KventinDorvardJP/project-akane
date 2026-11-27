# Luna web lookup

OpenAI conversation now offers hosted web search. The cloud model is configured
as `gpt-6-luna`, with its rates added to the cost estimator. Local Ollama remains
the default provider; it has no web tool. STT and VOICEVOX are unchanged.

## Use it

Save configuration and restart Akane, explicitly selecting OpenAI conversation:

```powershell
.\run.ps1 -Hotkeys -LlmBackend openai
```

Omit `-Hotkeys` to use the normal terminal loop. This uses your existing API key
and makes paid conversation requests. No browser, extension, or extra Python
package is needed. The program never opens or operates another app.

- `Who is Rean Schwarzerr?` forces a web-tool lookup, even if Luna feels confident.
- Equivalent English `who's/who was/who are/who were`, Russian `кто`, and Japanese
  `誰/だれ` identity questions also force lookup. This is a small text rule, not a
  complete multilingual intent detector.
- `/search Who is Rean Schwarzer?` explicitly forces a lookup. It works with typed
  or transcribed text beginning with `/search`. Local Ollama rejects this command
  rather than claiming it searched.
- Other questions use automatic tool selection. Instructions ask Luna to search
  for unfamiliar names, doubtful factual claims, possible misspellings, and current
  information, using the conversation's game/language context to disambiguate.
- Ordinary banter can skip search. `/search` does not change the stored user message.

Personal questions such as “What games do you play?”, “Any games you've been
playing recently?” and “How are you doing?” should be answered conversationally
as Akane, without researching ChatGPT/OpenAI's capabilities or memory. The words
“recently” and “games” alone are not a reason to search. Luna is instructed to
identify a needed external fact before searching, keep the query on that fact,
and clarify uncertainty when results are unrelated. Recent releases, factual game
questions and recommendations can still need search. `/search` and forced identity
verification retain their existing behavior. This is model guidance, not a
keyword-based classifier or a guarantee of correct tool choice; no extra model
call is made to classify the question.

Regression conversation for manual acceptance after restarting Akane:

| Question | Expected search behavior |
| --- | --- |
| “What games do you play?” | No web search; natural personal conversation |
| “Any games you have been playing recently?” | No web search; no invented real play history |
| “What games released recently?” | External factual lookup when needed |
| “What recent games would you recommend?” | Search can support recommendations |
| “Who is Rean Schwarzer?” / `/search ...` | Required lookup remains enforced |

With session transcripts enabled, `web_search` events now save provider-returned
queries by model-request number. Both `query` and `queries` API forms are supported;
missing query text is recorded as null/absent actions, never reconstructed from
your question or the sources. Metadata is bounded to ten search actions, eight
queries per action and 1,024 characters per query, with a truncation flag. Full
retrieved pages are not archived. Existing reply-source links, counts and fees are
unchanged. Old logs cannot recover queries they did not record.

The cloud instructions prioritize factual accuracy over character performance,
prefer primary sources, and ask for clarification if identity/typos cannot be
resolved. No character answer is hardcoded. These rules and search do not guarantee
correctness or perfect detection of uncertainty. If required search does not run,
or a searched answer has no usable source URLs, the program withholds that answer
with a recoverable error instead of presenting it as verified.

## Configuration

```python
OPENAI_MODEL = "gpt-6-luna"
OPENAI_WEB_SEARCH_ENABLED = True
OPENAI_WEB_SEARCH_MAX_CALLS = 2
OPENAI_WEB_SEARCH_USD_PER_CALL = 0.01
```

The maximum covers total hosted tool operations within one reply, including page
open/find operations; it is not the number of sources. Automatic searches can add
latency. Keep reasoning at `low` initially. The existing 1,024 output-token limit
also covers reasoning; an incomplete reply remains an error. Increase that limit
only if actual runs show truncation and you accept the extra potential token use.

Setting search to `False` omits the tool and `/search` reports that it is disabled.
It does not grant Ollama access. Restart after changing settings.

## Sources and speech

Each reply segment pairs `ja` with either `en` or `ru`. Japanese goes to VOICEVOX
and the selected English or Russian translation goes to the existing subtitle file.
Source URLs are separate metadata.
Known citation controls, Markdown link wrappers, and returned source URLs are
removed from the speech text. Console output lists sources after the answer.

- `runtime/sources.html` contains the latest translated answer with prominent clickable
  source references. Open it yourself and refresh after a later answer; Akane does
  not launch a browser or a server. HTML/URLs are escaped/validated and it loads no
  external scripts, images, or styles.
- `runtime/sources.json` contains that translation under `en` or `ru` and the source list, including
  title, URL, and whether it was cited or merely retrieved. These generated files
  store the latest answer, not a complete conversation log, and remain Git-ignored.
- Native API citation annotations are labelled **cited**. Search-result pages without
  those annotations are labelled **retrieved**, not falsely presented as individually
  cited evidence. Duplicate and non-HTTP(S) links are discarded.
- Sources remain after normal playback for inspection. A new turn, reset, stop,
  pause, or exit clears them. Publication uses the same cancellation lock as captions,
  so a stopped worker cannot restore old sources.

OBS's existing plain-text subtitle source stays as it is; plain text cannot offer
clickable citations. The generated source page is the citation-capable view for
inspection. Any additional OBS/browser-source or audience-link setup remains a
manual user action; no other app was configured.

## Fees and budget

Current search price is $0.01 per search request, in addition to model usage. Search
content tokens are billed at model rates and included through reported token usage;
the estimator does not add that token charge twice. Page open/find output items are
not separately counted as new searches. Prefer `tool_usage.web_search.num_requests`
when the API reports it; otherwise count search actions in response output. The
count source is recorded. Missing/unrecognizable accounting leaves the estimate
unknown and blocks later budgeted requests rather than inventing a total.
Output-action fallback counts are estimates; reported billable requests can differ
from tool-operation counts. This is another reason the budget is soft.

The soft session budget now covers estimated conversation tokens **plus search
fees**, excluding STT and other unmodelled charges. The available tool-operation
limit is reduced to fit the remaining search-fee allowance. If less than one fee
remains, web-enabled requests are refused; search is not silently dropped. Text
generation can still overshoot the soft cap, and stopping a sent request does not
guarantee avoiding its charge. Incomplete/withheld/cancelled responses still retain
reported usage and fees. Restarting the app resets the local session estimate.

New CSV columns: `web_search_calls`, `search_usage_source`,
`estimated_search_cost_usd`, and `estimated_text_cost_usd`.
`estimated_api_cost_usd` is their estimated combined charge. Both earlier timing
CSV formats migrate on the next write with a byte-for-byte original backup.
Unknown headers are preserved and reported. CSV records counts/costs, not query,
transcript, answer text, or source URLs.

Official implementation references, checked 2026-11-09:

- [Luna tool support](https://developers.openai.com/api/docs/models/gpt-6-luna)
- [Hosted search, sources and citations](https://developers.openai.com/api/docs/guides/tools-web-search)
- [Responses tool limits and usage](https://developers.openai.com/api/reference/python/resources/responses/methods/create)
- [Search pricing](https://developers.openai.com/api/docs/pricing)

## Verification and live acceptance

Tests exercise the installed SDK through an in-memory transport: optional/forced
search, JA/EN schema, misspelling inputs, missing tools/sources, reported/fallback
fees, budget limits, truncation, stale-source prevention, URL/HTML handling,
cancellation, speech separation, and CSV migration. These are implementation
checks, not evidence that a live model identified a character correctly.

Executed verification: **89 regression tests passed** (66 existing checks plus 23
search-related cases). Dependency validation reported no broken requirements.

No live API calls, microphone, playback, browser, or app control were used for this
addition. When free, make an explicit paid test with `Who is Rean Schwarzerr?`,
inspect the answer and source links, and compare `/search Who is Rean Schwarzer?`.
Confirm ordinary banter can skip search and examine the search count/cost in the CSV.
Live search quality, latency, and API-account acceptance remain untested here.
