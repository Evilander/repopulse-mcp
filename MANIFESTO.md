# MANIFESTO

Things I believe about code retrieval for AI coding agents. You are welcome
to disagree. That's the point.

## 1. Dense embeddings for code are overrated.

Every AI coding tool ships with embeddings. Most don't have the numbers to
show those embeddings are pulling their weight against a well-tuned FTS5 +
symbol graph. Embeddings are fashionable; BM25 is boring; the boring thing
often wins on code because code has identifiers and identifiers are the most
information-dense signal in the query. RepoPulse defaults to FTS5 only,
makes embeddings opt-in, and will eventually publish head-to-head numbers
rather than hand-wave about "hybrid retrieval."

## 2. The trace is the product.

If a retrieval is wrong and you can't tell *why*, the retrieval might as well
have been correct-by-coincidence the times it worked. Every agent that
writes bad code blames the LLM; half the time the LLM never saw the relevant
file. Logging *what was retrieved* is table stakes. Logging *what was
considered and rejected* is the thing. Logging it in a way the user can
replay and reason about is the product.

## 3. Agents should not be auto-updating your source tree blind.

The industry's default workflow — agent edits files, you review the diff —
is backwards. By the time you see the diff, bad retrieval has already
shaped the proposed change. Fix retrieval first. Retrieval should be a
thing the user can see and correct *before* the agent writes a single line.
`repopulse search` followed by `repopulse bad rp_abc123` is closer to the
right shape than "apply patch? y/N".

## 4. Cloud embeddings for private repos are indefensible.

Other tools ship cloud embedding pipelines as the default. Your proprietary
code becomes vectors in a database you don't own. The embedding model is
invertible enough that "we only store vectors, not code" is a thin
reassurance. RepoPulse refuses to be in this story. Everything stays on
the machine that checked out the code. If you want cloud embeddings for
performance, that's a choice to make with your eyes open — not a default.

## 5. Benchmarks beat anecdotes.

"It worked great for me" is what every code-context tool says. We will
publish numbers — ContextBench-style retrieval recall, latency at known repo
sizes, token cost per call — and update them across versions. If a
retrieval change doesn't move the numbers, we won't ship it. If it moves the
numbers the wrong direction, we'll say so instead of quietly reverting.

## 6. No monetization plan that depends on the tool being bad.

Some free-tier-to-pay-tier products design friction into the free tier.
RepoPulse won't. The CLI is MIT and will remain runnable end-to-end,
locally, no account, no telemetry, no time bomb. If RepoPulse ever makes
money, it will be from things the OSS version couldn't sensibly do alone:
aggregated team dashboards, historical trace storage for enterprise audit,
hosted evaluation benchmarks. The CLI stays yours.

## 7. The thing that looks like a CLI tool is the wrong altitude.

Individual developers won't pay for a code indexer. The customer with a
wallet is the engineering manager watching their 50-person team burn
engineer-weeks on AI-coding regressions. The correct question isn't "how do
I save this developer an hour?" — it's "how does a manager see their
team's coding agent quality drift and fix it?" RepoPulse's long-term arc
goes that direction. The CLI exists because the dashboard has to have
something to show.

---

If you read this and thought "this is wrong about X" — open an issue. I'd
rather be pushed on a strong opinion than have people politely ignore a
weak one.
