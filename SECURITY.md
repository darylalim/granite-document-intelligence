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
- Error records, logs and the `--events` progress file never contain environment variables, tokens or page text.

Multi-user use, serving the tool over a network, defending against the user and a compromised Mac are **out of scope**.

### Known limitations

These are accepted for a single-user tool that processes the user's own documents:

- **The PDF parsers run in-process.** pdfium and docling-parse, which are C and C++, parse attacker-controlled bytes with the user's privileges and without a sandbox. A memory-corruption bug in either one would be exploitable. Keeping the dependencies current is the mitigation, and Dependabot alerts are on.
- **There is no per-PDF timeout.** A PDF that hangs a parser hangs the run until Ctrl-C stops it.
- **Prompt injection is mitigated, not prevented.** Text inside a page can still change what a model extracts from that page. Review key-values from an untrusted document before anything acts on them.

A report about any of these is still welcome, especially one that shows a practical attack.

## The UI

`gdi ui` adds two things the command line never had: a listening socket, and a browser that renders text derived from the PDF. Two more parties can now reach the tool: web pages you visit, and other web apps running on your Mac.

The UI listens on the loopback address `127.0.0.1` only. **Exposing it to a network is unsupported**, whether by a proxy, a tunnel or a changed address. It is for the person at the Mac.

What protects it:

- **Its security settings cannot be weakened by a Streamlit config file.** `gdi ui` forces every security-relevant Streamlit option on its command line, which takes precedence over `~/.streamlit/config.toml` and any working-directory config. The forced options include the loopback address, the allowed hosts (`localhost` and `127.0.0.1`), XSRF and CORS protection, the allowed origins, and telemetry turned off.
- **It makes no network request of its own.** Usage statistics are off, and Streamlit's lookup of the Mac's external IP address is disabled.
- **Nothing from a PDF makes the browser fetch anything.** Images and links in model-written Markdown are reduced to text before rendering, except images inside the PDF's own output folder. Every other string from the output tree or a file name is shown as plain text, never as Markdown, a label or an alert. Nothing from a PDF goes into the page's address or title.
- **Generated chart code is shown and never run.**
- **Uploaded file names never become paths unchecked.** An upload is stored under its content hash with its name contained.

### Known limitations of the UI

- **Another web app on `localhost` can drive the UI.** Streamlit's origin check compares only the host name, so a page served from `localhost` on any port is accepted. A malicious local app is a compromised Mac and out of scope. A *vulnerable* local development server on another port is the realistic case, and it is accepted and named here.
- **A DNS-rebinding page can read what plain HTTP requests return.** The allowed-hosts check guards the WebSocket that controls the app and the upload endpoint, not the HTTP routes. Such a page can read the generic app shell, Streamlit's host configuration, the fonts, and media files whose content-hash URLs it already knows. It cannot drive the app or upload anything.
- **Any web page can frame the UI.** Streamlit sends no `frame-ancestors` or `X-Frame-Options` header, and no option adds one. A framing page can tell when the UI is running, read its address and title (which hold no document data) and try to lure clicks. It cannot type into the UI or change its settings. A framed session starts empty, so Convert is disabled. **Cancel** asks for a second click before it stops a conversion. The worst case is a stopped conversion: the PDFs that finished are kept, and the current PDF's previous results stay untouched.
