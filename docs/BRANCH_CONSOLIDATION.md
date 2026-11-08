# Branch consolidation — 2026-11-08

The user requested one current `main`, with useful changes preserved before removing old branches.
This was a repository consolidation. It did not deploy services, execute live collectors/inference,
change private environment values, install schedules or start Phase 3.

## Inputs preserved

| Branch | Tip before consolidation | Disposition |
|---|---|---|
| `main` | `cc672ffd0b56558b9f158a4e96f4e11f8c36ce3f` | Common base; runtime contract retained |
| `chatgpt/phase2-completion-fixes` | `38d239ac22cd13bc5b3b12459d503eb49db58852` | Already in the common base |
| `codex/phase2-runtime-validation` | `5ee8a433f060bf044e16e3cf4a1f42a62eed3966` | Completed validation and current report retained |
| `paven/phase2-changes` | `b521564c121bb933a31d3c52d606bbc7d4c3725a` | Identical runtime code; `.vs/` ignore rule retained; interim report archived |

The two development tips have identical application, migration, configuration, Apps Script, Worker,
test and deployment trees. Their only differences were `.gitignore` and the Phase-2 report.
Both histories are joined by a normal merge; no force-push or history replacement is needed.
Branch deletion therefore removes redundant names, not the underlying merged commits.

The later-created `paven` commit still contained the October 9 unfinished investigation report.
The other branch's report contains October 11 completed-gate evidence. The completed report remains
[current](PHASE_2_TEST_REPORT.md); the detailed [interim report](PHASE_2_TEST_REPORT_INTERIM_2026-10-09.md)
is preserved separately and clearly marked historical. Existing dated evidence was not reinterpreted
as a new live test.

## Alignment fixes

- README and setup now reference the current report and require explicit approval for Phase 3.
- Architecture/configuration document migrations through `0005`, 15 application tables,
  persistent Groq Free-tier rate control and bounded semantic drain.
- Operations describe Cloudflare scheduling, final Sheet shards, verified ACK deletion and backups
  from final Sheets, replacing obsolete PostgreSQL archive/sample-dashboard instructions.
- Setup no longer makes an unconditional historical claim that the Cloudflare token is missing.
- Both environment examples match and contain empty placeholders. Defaults remain in application
  configuration/documentation; the ignored `.env` was preserved.
- The training CLI test retains all training/prediction/hash assertions, with a bounded 180-second
  timeout on Windows (60 seconds elsewhere). The first regression passed 94 tests and timed out
  during cold SciPy/scikit-learn startup on this memory-constrained Windows session; a standalone
  import trace confirmed library loading before training. Runtime training code was not changed.

## Local verification

Verification used disposable loopback PostgreSQL databases only. Application/runtime code is unchanged
from both development tips. The final regression used single-threaded numerical-library settings to
limit local memory pressure; no tests were skipped and no assertions were relaxed.

| Check | Result |
|---|---|
| Complete Python/PostgreSQL suite | 95 passed, 0 failed, 0 skipped in 33.41 seconds |
| Dependency warnings | 2 existing FastAPI/Starlette/AnyIO deprecation warnings |
| Focused training CLI/prediction/hash verification | 1 passed in 26.73 seconds |
| Ruff | Passed |
| Git whitespace checks | Passed |
| Apps Script delivery/backup harness | 15 passed, offline mocks only |
| Apps Script bounded delivery controls | 4 passed, zero external calls |
| Cloudflare Worker tests / syntax check | 21 passed / passed |
| Alembic zero-to-head, downgrade-to-base, reapply | Passed on a fresh local disposable database |
| Alembic drift check | No new upgrade operations detected |
| Migration/schema | `0005_semantic_drain_deadline`; 15 application tables, 52 indexes including Alembic's index |
| Local Markdown file links | No missing targets |
| Environment templates | Identical; empty placeholders only |
| Tracked credential-pattern scan | No matches; private environment/credential files remain ignored |

The initial 94-pass/1-timeout run and isolated startup retries are not presented as application
regressions fixed by this merge: the trace showed cold library startup, and the final complete run
passed with the unchanged classifier. The bounded Windows test timeout change is recorded above.

Remote `main` and all three source branch tips were fetched and rechecked before publication.
Only branch names whose tips are ancestors of the tested consolidated commit are eligible for removal.

The pre-consolidation refs are also preserved in a verified local Git bundle at
`.test-runtime/git-backups/pre-consolidation-20261108.bundle` (ignored, not pushed).
Merged branch tips remain accessible from `main` history after branch cleanup.
