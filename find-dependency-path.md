# `find_dependency_path`

A tenth tool for `depsdevmcp`: does this package pull in that one, and by what chain?
The answer a reachability review needs, without shipping it a graph.

- **Target:** `ghcr.io/mappedsky/depsdevmcp`
- **Status:** proposed
- **Effort:** one tool, one graph walk

---

## Why it earns a place

Seizu's security graph records which package versions are *installed* and which fall in a
CVE's vulnerable range. It does not record what a package declares it needs, and the
sandbox that runs reachability analysis has no network — not even DNS — so it cannot look
it up either.

Given that gap, a sub-agent asked whether a urllib3 CVE was reachable in a repository
filled it from memory. Read off its own recorded thinking:

> *"botocore pins urllib3 < 1.27 historically, but modern botocore (>=1.29) requires
> urllib3 >= 1.25.4, < 2.0? … Actually botocore 1.29.0 was released around Dec 2022 and it
> still required urllib3<1.27… But this is a fictional future scenario (2026 versions)"*

It discarded the speculation that time. A security verdict resting on remembered version
history is the failure this prevents.

The nine existing tools close most of that gap. What none of them answers directly is the
shape the analysis actually asks for: **the repository doesn't import urllib3 — but does
botocore pull it in, and how?** Today that means fetching a resolved graph of several
hundred nodes and walking it in the sandbox: a round trip, a few thousand tokens, and a
chance to get the walk wrong.

---

## Contract

| | |
|---|---|
| **name** | `depsdev_find_dependency_path` |
| **arguments** | `system`, `name`, `version` — as the other tools take them — plus `target`, the package name to look for |
| **annotations** | `readOnlyHint: true`, matching the other nine |
| **returns** | whether the target is in the resolved graph, the versions of it that are, and one chain per version back to the root |

### Worked example — verified against the live server

```jsonc
depsdev_find_dependency_path{
  "system": "pypi", "name": "botocore", "version": "1.34.100",
  "target": "urllib3"
}

→ {
    "package": "botocore@1.34.100",
    "target":  "urllib3",
    "pulls_in": true,
    "versions": [{ "name": "urllib3", "version": "2.7.0", "relation": "DIRECT" }],
    "paths": ["urllib3@2.7.0 <-(>=1.25.4,<1.27) botocore@1.34.100"]
  }
```

Edges carry their own `requirement` string, so the chain can show the constraint that
produced each hop. That is worth including — it is the difference between "it is pulled
in" and "it is pulled in by this range".

### Two rules that must hold

**An absent target is a result, not an error.** Return `pulls_in: false` with a plain note.
If it comes back as an error, the calling agent reads "no" as a failed lookup and goes back
to guessing — which is the exact behaviour this tool exists to stop.

**Return the chain, not the graph.** `depsdev_get_dependencies` already returns the graph.
The value here is that the answer stays small enough to reason over directly.

---

## Implementation

Everything needed is already in the client the server uses,
`github.com/edoardottt/depsdev`:

```go
func (a *APIv3) GetDependencies(packageManager, packageName, version string) (def.Dependencies, error)

type Dependencies struct {
    Nodes []Node `json:"nodes,omitempty"`
    Edges []Edge `json:"edges,omitempty"`
    Error string `json:"error,omitempty"`
}

type Node struct {
    VersionKey VersionKey `json:"versionKey,omitempty"`  // System, Name, Version
    Bundled    bool       `json:"bundled,omitempty"`
    Relation   string     `json:"relation,omitempty"`    // SELF | DIRECT | INDIRECT
    Errors     []string   `json:"errors,omitempty"`
}

type Edge struct {
    FromNode    int    `json:"fromNode,omitempty"`   // index into Nodes
    ToNode      int    `json:"toNode,omitempty"`     // index into Nodes
    Requirement string `json:"requirement,omitempty"`
}
```

### The walk

1. Call `GetDependencies` — the same call `depsdev_get_dependencies` makes, so it shares
   the LRU cache entry for free.
2. Collect every node whose `VersionKey.Name` matches `target`, case-insensitively. There
   can be more than one version in a graph.
3. Build a child → parent map from `Edges` (`ToNode` → `FromNode`), keeping the first
   parent seen for each child. One parent gives one chain; that is the point.
4. From each match, walk parents to the `SELF` node, collecting `name@version` and the
   edge's `Requirement`.
5. Reverse into a readable chain and return.

---

## Things that will bite

### Only four ecosystems have resolved graphs

`npm`, `cargo`, `maven`, `pypi`. Requirements exist for `go`, `nuget` and `rubygems` too,
but resolved graphs do not — so this tool cannot answer for them. The server already
refuses cleanly:

```
unsupported package system "go"; supported systems: npm, cargo, maven, pypi
```

Reuse that path rather than returning an empty graph, which reads as "nothing depends on
it".

### `fromNode: 0` is omitted from the JSON

`omitempty` on an `int` drops the field when it is zero — and node `0` is the root.
Unmarshalling into the Go struct is fine, since the zero value is what you want. Anything
parsing the raw JSON and defaulting a missing `fromNode` to something other than `0`
silently loses every edge from the root. A prototype of this tool had exactly that bug.

### Graphs contain cycles

Bound the parent walk — a depth cap of 64 is ample and turns a cycle into a truncated chain
instead of a hang.

### Requirements and the resolved graph can look contradictory

`botocore 1.34.100` declares `urllib3 <1.27,>=1.25.4` yet resolves to `urllib3 2.7.0`. Both
are correct: the declaration carries an environment marker (`python_version < "3.10"`) and
another line covers newer Pythons. Worth a sentence in the tool description so a reader
does not treat the difference as an error — it is precisely why the requirements tool and
the graph tool both exist.

---

## Tests worth having

| Case | Input | Expect |
|---|---|---|
| Direct dependency | `pypi botocore 1.34.100 → urllib3` | `pulls_in: true`, single hop, relation `DIRECT` |
| Transitive | `npm express 4.18.2 → ms` | chain of more than one hop, ending at the `SELF` node |
| Absent target | `pypi jmespath 1.0.1 → urllib3` | `pulls_in: false`, **not** an error |
| Unsupported ecosystem | `go … → anything` | the existing "unsupported package system" error |
| Unknown version | `pypi botocore 99.99.99` | upstream not-found, surfaced as an error |
| Cycle | synthetic graph `a ↔ b` | terminates, chain within the depth cap |
| Cache reuse | same call twice | second response reports `cached: true` |

---

## What happens on the Seizu side

Nothing needs changing there. Once the tool exists it is discovered automatically and
arrives as `ext__deps__depsdev_find_dependency_path`. Its `readOnlyHint` keeps it out of
confirmation gating, results are byte-bounded by the caller, and a rate-limited upstream is
retried after the delay the refusal names.

**Still true after this ships:** no prompt, skill or `tools_required` list names any of
these tools, so under progressive disclosure a sub-agent will rarely find them. This tool
makes the capability better; it does not make it used. That is a separate change on the
Seizu side.

---

Background and the measurements behind it: `AGT-036` in
`docs/root/dev/decisions/chat-agent.md`. The Python prototype this replaces was removed in
`a751e8d`; its walk is the reference for step 3 above, minus the `fromNode` bug.
