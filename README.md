# Ato

Ato is a modular, LLM-powered personal AI agent. It is being developed incrementally
around one interface-independent Agent Core rather than as a collection of separate
chatbots.

## Current functionality

The current Phase 3 build provides:

- An interactive terminal conversation
- Conversation context during the current process
- Persistent recent conversation history across restarts
- Bounded, versioned JSON memory with atomic writes
- A `/clear-memory` command for deleting saved conversation context
- An allowlisted tool registry with validated arguments
- Bounded tools for file listing/search, text reads, syntax checks, and Git inspection
- Fixed lint and test runners protected by permission prompts and timeouts
- Workspace path boundaries and a maximum tool-call limit
- LOW/MEDIUM/HIGH/CRITICAL permission levels with fail-closed confirmation
- Redacted append-only JSONL audit logging for every tool execution decision
- A provider-neutral `LLMClient` interface
- A DeepSeek provider using its OpenAI-compatible chat API
- Environment-based configuration with no hard-coded secrets
- Friendly handling of expected startup and provider errors
- Automated tests that do not make real API requests

File writes, arbitrary command execution, web research, autonomous workflows, voice, GUI, and
cybersecurity capabilities are intentionally reserved for later phases.

The repository also retains the earlier experimental prototype under
`legacy/phase0`. It is intentionally isolated from the active package so useful
ideas can be migrated during the appropriate phases without destabilizing Phase 1.

## Project structure

```text
ato/
|-- src/ato/
|   |-- brain/          # Agent Core, messages, prompts, and LLM contract
|   |-- memory/         # Validated, atomic JSON persistence
|   |-- providers/      # Provider-specific LLM adapters (DeepSeek initially)
|   |-- security/       # Permission decisions, confirmations, and audit logging
|   |-- tools/          # Allowlisted tools, validation, and workspace boundaries
|   |-- ui/             # Terminal and future user interfaces
|   |-- config.py       # Environment configuration
|   |-- exceptions.py   # Application-specific errors
|   |-- main.py         # Stable application entry point
|   `-- __main__.py     # Supports python -m ato
|-- data/               # Future runtime data; generated contents are ignored
|-- docs/               # Project specifications and design documents
|-- legacy/phase0/      # Preserved inactive code from the original prototype
|-- tests/              # Unit tests
|-- .env.example        # Safe configuration template
|-- .gitignore
|-- requirements.txt    # Runtime dependency list
|-- pyproject.toml      # Package, test, lint, and development configuration
`-- README.md
```

## Dependencies

- Python 3.11 or newer
- `openai` SDK for DeepSeek's OpenAI-compatible API
- `python-dotenv` for local environment configuration
- `pytest` and `ruff` for development checks

Runtime dependencies are listed in `requirements.txt`. Complete package and
development metadata is maintained in `pyproject.toml`.

## Setup

From the project root, create and activate a virtual environment:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

Install Ato with its development tools:

```powershell
python -m pip install -e ".[dev]"
```

Copy the safe environment template:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and replace the placeholder with your own API key:

```dotenv
DEEPSEEK_API_KEY=your_deepseek_api_key_here
ATO_MODEL=deepseek-v4-flash
ATO_MEMORY_FILE=data/memory.json
ATO_MEMORY_MAX_MESSAGES=40
ATO_WORKSPACE_ROOT=.
ATO_AUDIT_FILE=data/audit.jsonl
```

Never commit `.env`; it is excluded by `.gitignore`.

## Run Ato

```powershell
python -m ato
```

You can also run the installed `ato` command. Enter `exit` or `quit` to close.
Successful turns are saved to `data/memory.json` and restored on the next run.
Use `/clear-memory` to remove both the saved history and the current context.

## Verify the project

```powershell
python -m pytest
python -m ruff check .
```

The tests use fake or mocked providers and do not consume API credits.

## Architecture

```text
Terminal UI -> Agent Core -> LLMClient -> DeepSeek Chat API
     |             ^
     `-> JSON Memory Store
```

`Agent` owns conversation state and has no DeepSeek dependency. `DeepSeekProvider`
translates Ato messages to the provider API, allowing future providers or interfaces
to be added without rewriting the Agent Core. `JsonMemoryStore` persists only user
and assistant messages; the system prompt and API credentials are never written to
the memory file. `ToolRegistry` exposes only explicitly registered operations, and
all file paths must remain inside `ATO_WORKSPACE_ROOT`.

## Phase 3 tool safety

The initial tools are deliberately read-only:

- `list_files` lists at most 200 files and ignores Git, virtual environments, and caches.
- `read_text_file` reads UTF-8 files up to 100,000 bytes inside the workspace.
- `git_status` runs only the fixed read-only Git status command.
- `search_files` performs literal search across at most 500 small files and returns at most 100 matches.
- `python_syntax_check` parses one `.py` file without executing it.
- `git_diff` and `git_log` use fixed commands, timeouts, and capped output.
- `lint_project` runs only Ruff and requires `MEDIUM` permission.
- `test_project` runs only pytest and requires `HIGH` permission because tests execute code.

DeepSeek may request a registered tool, but Ato's Python code validates the tool name
and arguments and performs the operation. Lint and test execution accepts no command
arguments, runs without a shell, and has strict time and output limits. There is no
general shell, write, delete, or arbitrary Python-execution capability.

Every tool has a permission level:

- `LOW` tools run automatically and are still audited.
- `MEDIUM`, `HIGH`, and `CRITICAL` tools require an explicit Allow/Deny decision.
- If no confirmation handler or audit log is available, protected execution fails closed.

Audit events are appended to `data/audit.jsonl` with the time, sanitized user request,
tool, redacted arguments, permission level, decision, and a result length and SHA-256
digest. Raw tool results are not copied into the audit log.
