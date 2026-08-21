# Ato

Ato is a modular, LLM-powered personal AI agent. It is being developed incrementally
around one interface-independent Agent Core rather than as a collection of separate
chatbots.

## Current functionality

The current Phase 8 build provides:

- An interactive terminal conversation
- Incremental streaming responses in the terminal
- Conversation context during the current process
- Persistent recent conversation history across restarts
- Configurable context budgeting with deterministic compaction of older turns
- Persisted summaries with recent messages retained verbatim
- Explicit SQLite long-term facts with bounded relevance retrieval
- Confirmed correction and deletion of individual long-term memories by ID
- Categorized long-term memory for facts, preferences, projects, and decisions
- Memory lifecycle controls for retrieval tracking, archiving, restoration, and expiration
- Deterministic phrase- and category-aware memory ranking with redundant-context filtering
- Local document ingestion and retrieval for text, Markdown, CSV, source code, PDF, and DOCX
- Local SQLite FTS5/BM25 knowledge ranking with automatic index migration and lexical fallback
- Injection-resistant JSON retrieval context with source-label citation instructions
- Confirmed, bounded public HTTPS page fetching with readable-text extraction
- Page-labelled text extraction from bounded public PDF sources
- Injection-resistant external evidence labels and exact-URL citation instructions
- Optional confirmed Tavily or Brave web search with bounded, untrusted result snippets
- Confirmed multi-source web research with URL deduplication, host diversity, and partial failures
- Stable evidence-passage IDs and conservative cross-source numeric disagreement hints
- Bounded, versioned JSON memory with atomic writes
- A `/clear-memory` command for deleting saved conversation context
- An allowlisted tool registry with validated arguments
- Bounded tools for file listing/search, text reads, syntax checks, and Git inspection
- Fixed lint and test runners protected by permission prompts and timeouts
- Confirmed, atomic tools for new text files and exact unique text replacement
- CRITICAL-confirmation file trashing with a reported recovery path
- Read-only local branch inspection and HIGH-confirmation path-scoped Git commits
- Privacy-conscious read-only OS, CPU, RAM, Python, and workspace-disk information
- Workspace path boundaries and a maximum tool-call limit
- Provider-loop tool evidence capped per result and cumulatively with truncation metadata
- LOW/MEDIUM/HIGH/CRITICAL permission levels with fail-closed confirmation
- Redacted append-only JSONL audit logging for every tool execution decision
- A provider-neutral `LLMClient` interface
- A DeepSeek provider using its OpenAI-compatible chat API
- Streaming tool-call reconstruction with the same execution and permission limits
- Provider-neutral structured JSON contracts with independent schema validation
- Environment-based configuration with no hard-coded secrets
- Friendly handling of expected startup and provider errors
- Automated tests that do not make real API requests

Permanent/recursive deletion, arbitrary command execution, browser automation, autonomous
workflows, voice, GUI, and
cybersecurity capabilities are intentionally reserved for later phases.

The repository also retains the earlier experimental prototype under
`legacy/phase0`. It is intentionally isolated from the active package so useful
ideas can be migrated during the appropriate phases without destabilizing Phase 1.

## Project structure

```text
ato/
|-- src/ato/
|   |-- brain/          # Agent Core, messages, prompts, and LLM contract
|   |-- knowledge/      # Local document ingestion, chunking, and retrieval
|   |-- memory/         # Conversation JSON and durable SQLite fact persistence
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
- `pypdf` for bounded PDF text extraction
- `python-docx` for bounded Word document text and table extraction
- `python-dotenv` for local environment configuration
- `pytest`, `reportlab`, and `ruff` for development checks and document fixtures

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

Edit `.env` and replace the DeepSeek placeholder with your key. Search keys are optional;
Tavily is preferred when both are configured:

```dotenv
DEEPSEEK_API_KEY=your_deepseek_api_key_here
TAVILY_API_KEY=your_tavily_api_key_here
BRAVE_SEARCH_API_KEY=your_brave_search_api_key_here
ATO_MODEL=deepseek-v4-flash
ATO_MEMORY_FILE=data/memory.json
ATO_MEMORY_MAX_MESSAGES=40
ATO_CONTEXT_MAX_TOKENS=12000
ATO_CONTEXT_RECENT_MESSAGES=12
ATO_CONTEXT_SUMMARY_MAX_CHARS=6000
ATO_LONG_TERM_MEMORY_FILE=data/long_term_memory.db
ATO_KNOWLEDGE_FILE=data/knowledge.db
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
This does not remove separately approved long-term facts.

## Verify the project

```powershell
python -m pytest
python -m ruff check .
```

The tests use fake or mocked providers and do not consume API credits.

## Phase 5 research safety

The first research tool, `fetch_web_page`, retrieves readable text from one URL only after
`MEDIUM` confirmation. It accepts public HTTPS on port 443, resolves and validates every
address, connects directly to a validated public IP with normal TLS hostname verification,
and refuses localhost, private, link-local, reserved, credential-bearing, and non-HTTPS URLs.
Redirects are not followed because each destination requires separate approval. Responses
must be uncompressed HTML, plain text, or PDF and stay within fixed timeout, byte, page, and
output limits. Public PDFs are capped at 10 MB and 100 pages, retain `[PDF page N]` markers,
and use `pypdf` for text extraction. Encrypted, malformed, oversized, and image-only PDFs are
rejected; OCR and visual/layout interpretation remain future work.
Every permission decision and execution result uses Ato's existing audit pipeline.
Fetched results include an exact `source_url` and an `untrusted_external` trust label. Ato's
system policy treats all webpage text as evidence rather than instructions, ignores action
requests embedded in pages, and requires the exact URL when an answer relies on that evidence.

When `TAVILY_API_KEY` or `BRAVE_SEARCH_API_KEY` is configured, Ato registers `web_search`.
Tavily is selected first when both exist. Each query requires `MEDIUM` confirmation because it
sends the query to an external provider and consumes API allowance. Search is limited to ten
results and Ato's conservative 400-character/50-word query limits. Tavily requests explicitly
use basic search, disable generated answers, raw content, images, and automatic advanced mode,
so a normal request costs one free-plan credit. Returned titles, URLs, and descriptions are
labelled untrusted; Ato is instructed to fetch relevant pages before making detailed claims.
See the [Tavily Search API reference](https://docs.tavily.com/documentation/api-reference/endpoint/search)
or [Brave Search quickstart](https://api-dashboard.search.brave.com/documentation/quickstart).

The same configured provider also enables `research_web`. After one `MEDIUM` confirmation, it
searches up to ten candidates and fetches at most three public HTTPS sources through the same
DNS, address, redirect, content-type, size, and timeout protections as `fetch_web_page`. Search
order is retained while duplicate URLs, non-HTTPS results, and excess results from one hostname
are removed. Each successful source contributes at most 2,500 characters, keeping the combined
evidence below normal tool-context limits. A rejected or unreadable source is reported as a
bounded per-source failure and does not discard evidence already collected from other sources.
All returned titles, snippets, text, URLs, and errors remain untrusted external data. Ato is
instructed to cite exact successful `source_url` values and disclose insufficient source coverage.
For each successful source, Phase 8 extracts at most two query-relevant passages and assigns
stable IDs such as `S1-E1`. Matching is explicitly lexical and never presented as proof that a
passage entails a generated claim. A bounded comparison also identifies differing numeric values
only when passages from separate sources share query terms. These entries are labelled
`potential_numeric_disagreement`; they require source review and are not treated as verified
contradictions. Distinct values are preserved so Ato can explain uncertainty rather than silently
selecting whichever source appeared first.

The default test suite never makes network requests. To exercise the real TLS, DNS, response,
and HTML extraction path from a network-enabled terminal, run the explicitly opted-in check:

```powershell
$env:ATO_RUN_LIVE_WEB_TESTS="1"
python -m pytest tests/test_web_live.py -m live_network -v
Remove-Item Env:ATO_RUN_LIVE_WEB_TESTS
```

With your search key in `.env`, the same opt-in switch can verify Tavily without printing the
secret. The test makes one basic search request and consumes one free-plan credit:

```powershell
$env:ATO_RUN_LIVE_WEB_TESTS="1"
python -m pytest tests/test_web_search_live.py -m live_network -k tavily -v
Remove-Item Env:ATO_RUN_LIVE_WEB_TESTS
```

The environment variable acts as an intentional network-test switch. Without it, the live test
is skipped and normal offline verification remains deterministic.

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

`ContextManager` uses a conservative provider-neutral token estimate. When history
exceeds the configured budget, older turns are converted into a bounded, labelled
summary while recent turns remain verbatim. The summary is stored separately in the
version-2 memory format and injected as context, never as a fabricated assistant reply.
Existing version-1 memory files migrate automatically when next saved.

Long-term memory is separate from conversation history. Use `/remember <fact>` to
save a user-approved fact, `/memories` to list saved facts, `/edit-memory <id> <fact>`
to correct one after confirmation, and `/forget <id>` to delete one after confirmation.
Prefix new or edited content with `preference:`, `project:`, `decision:`, or `fact:` to
categorize it; content without a recognized prefix remains a fact. Existing memory databases
migrate automatically and classify older entries as facts.
Edits preserve the memory ID while replacing its searchable content; duplicate facts and
the same sensitive-content patterns rejected during creation are also rejected during edits.
Relevant facts are retrieved with bounded local
keyword matching and injected as data rather than instructions. Likely passwords,
API keys, tokens, and secrets are rejected. The SQLite database is local plain text,
is excluded from Git, and should be protected using normal operating-system access controls.

Phase 7 lifecycle commands provide reversible control over when memories participate in
retrieval. `/archive-memory <id>` hides a memory without deleting it, while
`/restore-memory <id>` makes an archived, unexpired memory active again.
`/expire-memory <id> <days>` sets an expiration from 1 to 3,650 days and
`/clear-memory-expiration <id>` removes it. `/all-memories` displays active, archived, and
expired records. Lifecycle changes require confirmation. Created, updated, and last-retrieved
timestamps are maintained locally; existing databases migrate automatically. Archived and
expired entries are excluded before relevant context is sent to the model.

Memory retrieval combines bounded word coverage, adjacent phrase matches, and explicit intent
signals for preferences, projects, and decisions. Normalized duplicates that differ only in
case, spacing, or punctuation are collapsed before reaching the model, including duplicates
returned by different memory sources. Distinct or conflicting facts are preserved so Ato does
not hide uncertainty. Ranking remains deterministic, local, and limited to a fixed candidate set.

Local RAG is available through `/ingest <workspace-path>`, `/knowledge`, and confirmed
`/remove-document <id>` commands. Ingestion accepts UTF-8 TXT, Markdown, CSV, JSON,
common source-code and configuration formats up to 500,000 bytes, plus PDF and DOCX files
up to 10 MB. PDFs are limited to 200 pages; DOCX archives and all extracted text have
additional expansion limits. Documents are chunked into ignored local SQLite storage and
retrieved through local SQLite FTS5 with BM25 relevance ranking. Existing knowledge databases
are indexed automatically, and Ato falls back to bounded lexical scoring on Python builds that
do not provide FTS5. PDF page markers and DOCX table markers are retained in extracted text to
improve source context.
Re-ingesting an unchanged document is idempotent; changed documents replace their old
chunks. Environment files, protected directories, symlinks, unsupported formats, paths
outside the workspace, encrypted or malformed documents, and content that appears to contain
secrets are rejected. Image-only/scanned PDF OCR, embeddings, and external vector databases
remain future additions.
Ingestion requires confirmation that relevant excerpts may be sent to the configured
model provider during future questions; cancelling leaves the knowledge base unchanged.
Retrieved excerpts are passed to the model as explicitly untrusted JSON data rather than
instructions. When Ato relies on a knowledge excerpt, it is instructed to include its exact
local label, such as `[knowledge guide.md#0]`, and to avoid unsupported citations.

The terminal streams DeepSeek text fragments as they arrive. Tool-call fragments are
reassembled and validated before execution, and tool results remain subject to the
same permissions and audit controls. A conversation turn is persisted only after the
entire stream completes successfully; partial or failed streams do not enter memory.
Provider-facing tool output is limited to 12,000 characters per result and 30,000 characters
across one response cycle. Oversized results are wrapped in valid JSON with explicit truncation
metadata; once the cumulative budget is exhausted, later evidence is omitted. These limits
apply equally to streaming and non-streaming tool loops and prevent multi-page research from
bypassing normal conversation context controls.

DeepSeek JSON mode is available through `StructuredOutputSpec` and
`DeepSeekProvider.generate_structured`. Ato adds an explicit JSON-schema instruction,
caps output size and tokens, parses the response safely, and validates object fields,
nested arrays/objects, required fields, primitive types, enums, and additional-field
rules. Invalid or empty provider output raises a controlled `StructuredOutputError`.

## Phase 3 tool safety

Inspection tools are deliberately read-only:

- `list_files` lists at most 200 files and ignores Git, virtual environments, and caches.
- `read_text_file` reads UTF-8 files up to 100,000 bytes inside the workspace.
- `git_status` runs only the fixed read-only Git status command.
- `search_files` performs literal search across at most 500 small files and returns at most 100 matches.
- `python_syntax_check` parses one `.py` file without executing it.
- `git_diff` and `git_log` use fixed commands, timeouts, and capped output.
- `lint_project` runs only Ruff and requires `MEDIUM` permission.
- `test_project` runs only pytest and requires `HIGH` permission because tests execute code.

The first editing tools are deliberately narrow:

- `create_text_file` creates a new UTF-8 file and refuses to overwrite existing files.
- `replace_text_in_file` changes one exact text block only when it has a unique match.
- Both require `HIGH` confirmation, use atomic writes, and cap files at 100,000 bytes.
- Environment, credential, private-key, Git metadata, CI workflow, runtime data, symlink,
  and out-of-workspace targets are rejected.
- `trash_text_file` moves one small UTF-8 file into ignored `data/trash/` storage after
  `CRITICAL` confirmation and returns its recovery path. It never deletes directories.
- `git_branches` lists local branches without changing repository state.
- `git_commit_files` creates a local commit for 1-20 explicit, non-protected paths after
  `HIGH` confirmation. It uses fixed `git commit --only` arguments, caps one-line commit
  messages at 200 characters, and preserves unrelated staged changes.
- `system_info` reports non-identifying host capacity information. It omits usernames and
  hostnames and labels network state as `not_probed` rather than making outbound requests.

DeepSeek may request a registered tool, but Ato's Python code validates the tool name
and arguments and performs the operation. The registry rejects unknown or missing fields,
incorrect types (including booleans passed as integers), disallowed enum values, oversized
strings or arrays, duplicate array items, and values outside declared numeric bounds before
permission checks or tool execution. Lint and test execution accepts no command
arguments, runs without a shell, and has strict time and output limits. There is no
general shell, Git push/pull/reset/branch-switching, recursive/permanent deletion,
unrestricted overwrite, or arbitrary Python-execution capability.

Every tool has a permission level:

- `LOW` tools run automatically and are still audited.
- `MEDIUM`, `HIGH`, and `CRITICAL` tools require an explicit Allow/Deny decision.
- If no confirmation handler or audit log is available, protected execution fails closed.

Audit events are appended to `data/audit.jsonl` with the time, sanitized user request,
tool, redacted arguments, permission level, decision, and a result length and SHA-256
digest. Raw tool results and file payloads are not copied into the audit log. Write
confirmations show only bounded, secret-sanitized previews plus content hashes.
