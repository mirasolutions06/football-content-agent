# Failure Modes

| Failure | Detection | Mitigation |
|---|---|---|
| Missing provider key | `scripts/smoke.py` fails | Add the key to `.env` or the server env file. |
| API-Football quota exhausted | Live fixture calls fail or return quota errors | Back off polling, use cached state, or switch to the alternate supported key style. |
| RSS feed shape changes | Smoke test reports no `item` or `entry` nodes | Update the parser or remove the feed until it stabilizes. |
| Image search returns wrong subject | Vision/relevance check fails or operator rejects draft | Try official player photo, Wikimedia, Pexels, or generated fallback. |
| AI edit mutates the player | Identity check returns false/unclear | Use deterministic composite overlay from the original approved image. |
| Stylization provider timeout | `stylize_jobs` row moves to failed | Retry once from the draft, or publish the composite fallback. |
| MCP process restarts mid-job | `recover_orphans()` marks queued/running jobs failed | Resubmit stylization for the draft. |
| Duplicate approval clicks | Existing active job is returned | Poll the existing job instead of launching another expensive image call. |
| Caption needs edit after image approval | Operator asks for text-only change | Use `recaption_styled_draft`; do not restylize unless a new image is requested. |
| Instagram publish response lost | Publish path raises uncertain state | Reconcile manually before retrying to avoid duplicate posts. |
| Public image host cleanup fails | Warning is printed and delete returns false | Delete object manually from the bucket; publish remains successful. |

The general rule: fail closed on identity, relevance, and publish uncertainty;
fall back gracefully on styling and image search.
