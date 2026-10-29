# Security policy

## Reporting a vulnerability

Please report a vulnerability privately, through GitHub's private vulnerability reporting: open the repository's **Security** tab and choose **Report a vulnerability**. Please do not open a public issue for it.

The report is most useful when it includes the version or commit, what the vulnerability allows, and a PDF or a set of steps that reproduces it. If a PDF reproduces it, attach the PDF to the private report rather than linking to it publicly.

## Supported versions

Only the latest release and `main` receive fixes.

## Threat model

The adversary is **the author of a PDF you process**. That author controls every byte of the PDF, including text you never see. Every PDF is therefore untrusted, and so is everything derived from one: Markdown, tables, chart data and code, key-values and chunks. The trusted parts are you, your command-line options, the schema you supply, model weights at their pinned revisions and dependencies at their locked versions.

Text from a PDF never steers the program, and everything the program loads or executes is pinned and local:

- Every model is fetched at a pinned commit, and the run refuses a models directory that does not match those pins.
- No code from a model repository runs, because setup strips it from the models directory.
- Nothing the pipeline does depends on model output beyond writing it to disk, and generated chart code is never executed.
- Key-value schemas cannot fetch remote `$ref`s.
- Error records and logs never contain environment variables, tokens or page text.

Multi-user use, serving the tool over a network, defending against the user and a compromised Mac are **out of scope**.

### Known limitations

These are accepted for a single-user tool that processes the user's own documents:

- **The PDF parsers run in-process.** pdfium and docling-parse, which are C and C++, parse attacker-controlled bytes with the user's privileges and without a sandbox. A memory-corruption bug in either one would be exploitable. Keeping the dependencies current is the mitigation, and Dependabot alerts are on.
- **There is no per-PDF timeout.** A PDF that hangs a parser hangs the run until Ctrl-C stops it.
- **Prompt injection is mitigated, not prevented.** Text inside a page can still change what a model extracts from that page. Review key-values from an untrusted document before anything acts on them.

A report about any of these is still welcome, especially one that shows a practical attack.
