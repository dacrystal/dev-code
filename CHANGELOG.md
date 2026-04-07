## [0.2.0] - 2026-04-07

### Added

- Migrate CLI from argparse to click with dynamic shell completion ([#1](https://github.com/dacrystal/dev-code/issues/1))
- Migrate demo pipeline to scriptcast ([#2](https://github.com/dacrystal/dev-code/issues/2))

## [0.1.9] - 2026-03-26

### Added

- Rewrite devcode list --long as flat table with NAME/DESC/PATH
- Remove devcode init command, simplify devcode edit
- Add asciinema demo generator with PS1 timing, comment step, and validation

### Fixed

- Remove skip_install from tox so devcode CLI is available in tests
- Replace subprocess devcode call with direct main() invocation in test

## [0.1.8] - 2026-03-25

### Added

- Add --version flag, shown in --help
- Guard against projectpath being a subdirectory of a git repo

### Changed

- Extract _expand_source_path to eliminate path resolution duplication

### Fixed

- Cross-platform path handling for Windows and Mac
- Normalize both sides of path comparison for Windows 8.3 short names

## [0.1.7] - 2026-03-25

### Added

- Support path input in devcode open
- Template path resolution — DEVCODE_TEMPLATE_PATH, devcontainer.json validation, remove builtins from open/list/edit, cmd_edit opens VS Code directly

### Fixed

- Ps -i open uses config_file directly instead of re-resolving template
- Ps -i open uses config_file directly instead of re-resolving template

## [0.1.6] - 2026-03-24

### Added

- Rename Override → override, add unknown-field warning, and README doc improvements
- Add shell tab-completion (bash and zsh)
- Ps — add # column, sort by CreatedAt, -a flag, -i interactive mode
- Rename CLI command to devcode and source file to devcode.py

## [0.1.1] - 2026-03-23

### Added

- Dev-code CLI — day one release
- Add ASCII art logo banner to CLI and README
- Add coverage reporting, tox, and gated PyPI publish pipeline

### Fixed

- Pad time.time mock side_effect to survive Python 3.12 logging calls
- Make CI pass on all platforms

