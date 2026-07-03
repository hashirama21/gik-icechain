# Contributing

Thanks for contributing to GIK-IceChain. This guide keeps the history and the
codebase clean and reviewable.

## Branch flow

- `develop` is the single source of truth. Do your work there (or on a feature
  branch off `develop`).
- `main` is release-only. It is updated by merging `develop` with `--no-ff`.
- Never commit directly to `main`.

## Commit messages

We follow [Conventional Commits](https://www.conventionalcommits.org):

```
<type>(<scope>): <imperative summary, <= 72 chars>

<body: what and why, wrapped at ~80 cols. Optional.>
```

- **type**: `feat`, `fix`, `docs`, `refactor`, `perf`, `test`, `chore`, `ci`.
- **scope**: the touched area, e.g. `c1`, `c2`, `risk`, `config`, `repo`.
- Summary in the imperative ("add", not "added"), lowercase, no trailing period.
- Do **not** add `Co-Authored-By` trailers.

Examples:

```
feat(risk): add riverine hazard node fed by upstream-basin pooling
fix(c2): convert IFS tp from metres to mm before exceedance
docs(readme): document the admin-1 satellite validation
```

## Before you commit

Run, and make sure they pass:

```bash
ruff format .
ruff check .
mypy src/gik_icechain/ --ignore-missing-imports
pytest tests/unit/ -q
```

Or install the hooks once: `pre-commit install`.

## Style

- **No em-dashes or en-dashes** (`-`, `–`) in code, comments, docs, or the
  README. Use a plain hyphen `-`.
- **Comments are precise and concise.** Explain *why*, not *what* the code
  already says. Delete a comment rather than let it drift out of date.
- Keep functions small and typed; no hardcoded constants (read them from config).
- **No generated artifacts in git**: logs (`*.log`, `*.progress`), run outputs
  (`results/`), and the knowledge graph (`graphify-out/`) are gitignored - keep
  them out of commits.

## Data and secrets

- Never commit credentials. Local secrets live in `.env` (gitignored).
- Large source data (portal exports, GRIB) stays out of the repo; commit the
  small derived artifact instead.
