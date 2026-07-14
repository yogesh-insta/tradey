# Branch protection (`main`)

**Policy:** no direct pushes to `main`. Branch → PR → merge.

## Status (API)

Repo is **private**. Classic branch protection and repository rulesets both returned:

> Upgrade to GitHub Pro or make this repository public to enable this feature. (HTTP 403)

Until Pro (or public), GitHub cannot enforce this server-side for this repo.

## Intended rules (when enabled)

- Require a pull request before merging
- Approving reviews: `0` (solo) if allowed, else `1`
- Enforce for administrators: **on** (owner must use PRs too)
- No force pushes / no branch deletions on `main`
- Conversation resolution: optional

## Enable later

**Settings → Rules → Rulesets** (or Branches → Branch protection), target `main`, add “Require a pull request”, turn on “Do not allow bypassing”.

Or after Pro / public:

```bash
gh api -X POST repos/yogesh-insta/tradey/rulesets --input - <<'EOF'
{
  "name": "Protect main — require PR",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/heads/main"], "exclude": [] } },
  "rules": [
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false
      }
    },
    { "type": "non_fast_forward" }
  ],
  "bypass_actors": []
}
