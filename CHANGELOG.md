# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
## [0.2.0] - 2023-12-29
### Added
- `hor` commandline shorthand for `hikari_orchestrator`.
- `hikari_orchestrator run` command for spawning a multiprocess bot from an entry function and basic config.

### Changed
- Renamed the `hikari_orchestrator` commandline command to `hikari_orchestrator server`.

## [0.1.2] - 2023-12-29
### Added
- Support for Python 3.12.

## [0.1.1] - 2023-07-30
### Fixed
- The CLI entry point now loads the dotenv file when being called as a project script.
- `--private-key` now correctly pulls from the env variable `"ORCHESTRATOR_PRIVATE_KEY"`.

## [0.1.0] - 2023-07-29
### Added
- Initial implementation.

[Unreleased]: https://github.com/FasterSpeeding/hikari-orchestrator/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/FasterSpeeding/hikari-orchestrator/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/FasterSpeeding/hikari-orchestrator/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/FasterSpeeding/hikari-orchestrator/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/FasterSpeeding/hikari-orchestrator/compare/8c010e29c45b32334644634240e7618d0933c2bf...v0.1.0
