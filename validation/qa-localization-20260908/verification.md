# QA localization fix verification

Scope: numbered CHAPTER heading protection and explicit English/Chinese decade numerical equivalence. Source registries, translations and audit records were not changed. No commits or pushes.

- tests.xml: 18 tests, 0 failures, 0 errors.
- regression.xml: 15 tests, 0 failures, 0 errors.
- Ruff --no-cache on quality.py and test_qa_localization.py: passed.
- Mypy on quality.py from plugin directory (project configuration): passed.
- git diff --check: passed.
- Two-file full workflow regression was interrupted at 34%, with no failures observed; replaced by the completed focused selection `-k 'qa or scaled_number or dimension'`. Full check.ps1 was not run.
- Initial root-directory mypy omitted plugin configuration and reported third-party stub errors; corrected configured invocation passed. Default plugin Ruff cache was inaccessible; --no-cache passed.

QA fingerprint changed from v6.2 to v6.3 so prior QA evidence must be regenerated. Chinese heading equivalence requires kind=heading, CHAPTER + Arabic number, and the same explicit 第 n 章. All other protected tokens retain existing checks. Explicit decades preserve numeric identity; wrong centuries/decades and actual second/millisecond/kg quantities are covered by negative tests. Ambiguous historical century-versus-decade usages remain an independent semantic review responsibility.

changes.patch includes tracked code/changelog differences and the new test file. tests.xml and regression.xml contain machine-readable results.
