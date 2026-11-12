# nh-test

## Kick an intraday run

The schedule trigger can lag by about an hour. An owner comment starts one `ladder-intraday` run without a personal access token.

1. Create one issue titled `ladder-kick` and add the label `ladder-kick`. The workflow does not create the issue or the label.
2. On that issue, from the repository owner account, post a new comment whose body starts with `kick`.

That comment starts `ladder-intraday` once, on the same intraday path as the schedule (`LADDER_MODE=intraday`), including the KST time guards. The run queues behind any ladder job already in progress. Editing an existing comment does not start a run. Any other comment skips the job: no secret is used and no broker call is made.
