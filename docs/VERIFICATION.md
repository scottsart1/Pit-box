# Pit Wall 3.3.0 verification

## Completed in the build environment

```text
49 automated tests passed
Ruff static checks passed
Python compileall passed
Dashboard JavaScript syntax passed with Node.js
```

The 49 executed tests cover:

- strategy and Monte Carlo planning;
- FIA compound-rule logic;
- SC/VSC/red-flag strategy distinctions;
- setup foundations and learning;
- SQLite history and unsigned session UID persistence;
- racing-line analysis;
- proactive radio cadence and relevance;
- wake phrase and PTT behavior;
- latency routing and dashboard state rail;
- telemetry tools;
- DeepSeek and OpenAI provider adapters;
- provider fallback, circuit behavior and A/B safety.

## Provider-specific tests

1. Normal DeepSeek requests select `deepseek-v4-flash` and explicitly disable thinking.
2. Deep requests select `deepseek-v4-pro`, enable thinking and send `reasoning_effort=high`.
3. Thinking-mode tool loops preserve `reasoning_content` on the following request.
4. Invalid or hallucinated tool arguments do not execute.
5. A DeepSeek failure falls back to OpenAI.
6. Tool results are reused across provider failover.
7. A/B comparison shares matching evidence and blocks setup-mutating tools.

## Tests requiring the parser package

Twelve tests import packet structures from `f1-packets==2026.1.1`. That distribution was not available through the isolated build environment's package mirror, so those tests could not be executed here. They are unchanged from the working 3.2 UDP/parser code path.

The Windows installer installs `f1-packets` from PyPI and runs the complete suite. The repository contains 61 test functions in total.

## No live API claims

No DeepSeek or OpenAI reasoning API call was made during packaging because user credentials are not available in the build environment. API behavior is tested with response-shaped asynchronous fakes, including tool loops and fallback.

A live shakedown should verify:

```text
/api/health -> both credentials configured
normal question -> deepseek-v4-flash
strategy question -> deepseek-v4-pro
invalid DeepSeek key -> OpenAI fallback
```
