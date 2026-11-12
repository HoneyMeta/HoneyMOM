## What this changes

<!-- Short description and the reason for the change. Link the issue: Fixes #123 -->

## How it was tested

<!-- Commands you ran, e.g. python tests/foundation_check.py, python tests/i18n_check.py, npm run build -->

## Checklist

- [ ] Schema changes come with a migration (`honeymom/migrations/vNNNN_*.py`) and `python -m honeymom db check` passes
- [ ] New user-visible texts are translated (`python tests/i18n_check.py`, `python tests/i18n_web_check.py`)
- [ ] No secrets, tokens, real customer names or personal data in code, fixtures, screenshots or recordings
- [ ] I have signed the [CLA](https://github.com/HoneyMeta/HoneyMOM/blob/main/CLA.md) (the CLA bot asks on your first pull request)
