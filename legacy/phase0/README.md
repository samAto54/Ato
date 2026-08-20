# Phase 0 legacy prototype

This directory preserves the experimental Ato code recovered from the earlier
project archive. It is retained for reference and future, deliberate migration.

It is **not** imported by the active Phase 1 application. The prototype includes:

- A DeepSeek chat-completions experiment
- JSON-based persistent memory experiments
- Local name and greeting logic
- Speech-recognition and text-to-speech experiments
- A terminal banner

These features are outside the current Phase 1 boundary in the master project
specification. They should be migrated individually, with tests and architecture
review, during their appropriate project phases.

The original hard-coded DeepSeek credential was removed during import. If this
prototype is ever run, supply `DEEPSEEK_API_KEY` through the environment.
