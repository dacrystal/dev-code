.PHONY: lint typecheck test all \
        changelog changelog-preview changelog-bump release

# ── CI / quality ────────────────────────────────────────────────────────────

lint:
	ruff check .

typecheck:
	mypy src/

test:
	pytest --cov=src --cov-report=term-missing

all: lint typecheck test

# ── Changelog / release ──────────────────────────────────────────────────────

# Preview what the next changelog entry will look like (dry-run, no files changed)
changelog-preview:
	git cliff --unreleased --bump

# Show what the next version number will be
changelog-bump:
	git cliff --bumped-version

# Prepend new entries to CHANGELOG.md for the next release
changelog:
	$(eval VERSION := $(shell git cliff --bumped-version 2>/dev/null))
	touch CHANGELOG.md
	git cliff --unreleased --prepend CHANGELOG.md --bump
	@echo "CHANGELOG.md updated to $(VERSION). Review, then run: make release"

# Full release: update changelog, commit, tag, push
release:
	$(eval VERSION := $(shell git cliff --bumped-version 2>/dev/null))
	git add CHANGELOG.md
	@git commit -m "chore: release $(VERSION)" || { echo "Error: CHANGELOG.md unchanged. Run 'make changelog' first."; exit 1; }
	git tag $(VERSION)
	git push origin main
	git push origin $(VERSION)
	@echo "Released $(VERSION)"