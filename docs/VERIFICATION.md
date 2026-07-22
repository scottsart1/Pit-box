# Pit Wall 3.3.1 verification

## Source inventory

The repository contains **71 test functions** after the 3.3.1 reliability additions.

## Completed in the isolated build environment

The build environment did not contain the OpenAI SDK wheel, `f1-packets`, `sounddevice`, or Ruff, and its package mirror could not provide them. With minimal import stubs used only to permit collection of dependency-independent tests, the following completed successfully:

```text
59 automated test cases passed
1 OpenAI-SDK serialization test deselected
15 UDP/parser/reliability test functions excluded because f1-packets was unavailable
Python compileall passed
Dashboard JavaScript syntax passed with Node.js
```

No stub is included in the release ZIP. `install_windows.ps1` installs the declared production and test dependencies and runs the complete test suite on the user's Windows installation.

## New 3.3.1 coverage

The added tests verify:

1. `none` cannot silently become the primary provider, while it remains valid as a fallback choice.
2. A normal-route deadline cancels a slow primary and reaches the fallback promptly.
3. SDK clients are configured for zero hidden retries.
4. DeepSeek thinking requests omit `tool_choice`.
5. Empty tool lists are omitted from DeepSeek requests.
6. DeepSeek truncation retries once with the larger configured budget.
7. Persistent truncation falls back without incrementing the circuit breaker.
8. Tool results are executed once and reused after mid-call failover.
9. Two genuine availability failures open the circuit breaker.
10. Billing/insufficient-quota errors do not open the provider-health circuit.
11. `auto` status reports the concrete resolved provider.
12. A/B comparison is opt-in and its cooldown is enforced.
13. Live shakedown reports basic, non-thinking-tool, and thinking-tool stages.
14. Prompt construction is explicit rather than reconstructed from an incidental list shape.

## Existing behavior retained

The broader suite continues to cover:

- deterministic and Monte Carlo strategy planning;
- dry-compound legality;
- SC/VSC/red-flag distinctions;
- setup foundations and learning;
- SQLite history and unsigned session UID persistence;
- racing-line analysis;
- proactive radio cadence and relevance;
- wake phrase and L3/UDP Action behavior;
- latency routing and dashboard state rail;
- telemetry tool schemas and results;
- provider A/B evidence freezing and setup-mutation protection.

## Live API boundary

No live DeepSeek or OpenAI reasoning request was made during packaging because credentials are not present in the build environment. Mocked tests prove local serialization, routing, validation, memoization, timeout, and fallback behavior; they do not prove account access or provider availability.

The release therefore includes an explicit live shakedown:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/llm/shakedown" -Method Post
```

Before a race it should report each configured provider as ready across:

```text
basic
nonthinking_tool
thinking_tool
```

A failed stage reports its concrete exception and latency so wire-format, key, balance, or service issues are visible before entering the cockpit.

## Reproduction on Windows

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install_windows.ps1
.\.venv\Scripts\python.exe -m pytest -q
.\start_pitwall.bat
```

Then click **Test providers** on the dashboard and run one normal and one deep radio call.
