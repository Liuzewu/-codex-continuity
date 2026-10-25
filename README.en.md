# Codex Continuity

[中文说明](README.md)

Keep working standards usable across sessions and devices, with clear review steps and checks that show what actually worked.

Codex Continuity is a community practice repository for turning recurring feedback into maintainable project knowledge. It combines a runnable example, small Python tools, templates, and guides for knowledge reuse, device handoffs, project organization, and Mac workstation setup.

It does not connect to Codex, read your chats, call a model API, or install a background synchronization service. It does not train a model or promise permanent memory.

## Run the example

Python 3.9 or later is required. Everything uses the standard library: no API key, paid service, or third-party package is needed.

From the repository root:

```sh
python3 -B tools/demo.py
```

On Windows, use an installed `py -3` instead of `python3` if needed. This is a command alternative, not a claim of Windows testing.

The example copies a fictional project brief into its own temporary directory. It checks four cases: a valid baseline, a candidate promoted without adoption evidence, an output missing a required section, and a complete output after evidence is linked. Both intended failures must produce the expected issue codes. The temporary copy is removed on exit; the repository fixtures remain unchanged.

Read the [demo guide](docs/07-feedback-loop-demo.md) for expected JSON output and limits. This is an offline structural demonstration. It does not establish real human approval, model compliance, or successful work across three devices.

## What the files and tools do

- **Knowledge registry:** separates feedback type from `candidate`, `active`, and `retired` status. Active rules require matching evidence references and a route to their current text. The checker verifies registration conditions, not whether evidence is truthful or sufficient.
- **Project templates:** provide an `AGENTS.md` entry point, a purpose-based routing file, context, and acceptance checks. File discovery depends on the tool; linked documents still need to be read and applied.
- **File comparison:** compares explicitly selected local knowledge files. Matching bytes do not prove remote provenance, loaded instructions, shared chat history, sidebar consistency, or cloud bindings.
- **Publication audit:** flags some sensitive text patterns and unsuitable files. Manual privacy review remains necessary, including filenames, business meaning, attachments, and Git history.

```sh
python3 -B tools/check_knowledge.py examples/feedback-loop
python3 -B tools/audit_public.py .
python3 -B -m unittest discover -s tests -v
```

These commands let you inspect the repository yourself; listing them does not imply that your platform or environment has passed.

## Use it in your own work

Start with one non-sensitive practice project. Follow the [template instructions](templates/README.md), keep one current source for each rule, and verify a small deliverable before expanding the setup. Preserve existing files and stop when a change lacks a safe recovery path.

The detailed guides are currently in Chinese:

- [Knowledge reuse](docs/02-knowledge-reuse.md)
- [Multi-device verification](docs/03-multi-device-sync.md)
- [Projects and sidebar organization](docs/04-project-organization.md)
- [Template assets](docs/09-template-assets.md)
- [Privacy and publication](docs/05-publication-and-privacy.md)
- [Tool usage and limits](tools/README.md)

Contributions should use fictional examples and state what was tested, what failed, and what remains unknown. See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the [MIT License](LICENSE).
