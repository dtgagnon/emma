# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-02-03

### Added
- NotmuchSource as preferred email backend for fast local search
- Emma background service with monitoring, digests, and action items
- Interactive email selector for delete, move, and analyze commands
- Shell completion support
- Config override support via `config.local.yaml`
- Task-aware body preparation utilities for LLM processing
- Contextual help display on CLI errors

### Changed
- Use email address as config key with user identity for LLM context
- Improved digest filtering, formatting, and summary generation
- Show help text when unknown commands are entered

### Fixed
- Add notmuch to PATH and database write access in service
- Remove duplicate completions directory inclusion in build
- Make home module self-contained with callPackage

## [0.1.0] - 2026-01-15

### Added
- Initial release
- Rule-based email processing
- LLM-powered email analysis with Anthropic Claude and Ollama
- CLI interface with Typer and Rich
- IMAP/SMTP email handling
- SQLite database for audit logging
- Home Manager module for NixOS integration
