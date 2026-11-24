# ALEMBIC

[简体中文](README.md)

ALEMBIC is a local agent workspace for working with files and code on your own machine. It brings model configuration, tools, approvals, workspaces, and persistent conversations together in a browser interface.

The backend is built with Python, FastAPI, and LangChain; the frontend uses Vue, Vite, and TypeScript. They communicate through a local API and SSE. Application data is stored in the current user's data directory by default.

## Features

- Configure multiple providers and models, including per-model context, output, and reasoning settings.
- Use native model tool calling for file operations, search, Git, commands, and background processes.
- Delegate self-contained multi-step work to subagents with their own context and tool surface, including background completion and follow-up messages.
- Work with documents, images, web content, and conversation attachments.
- Persist conversations, compact context, track tasks, stream output, and receive background-task updates.
- Review tool approvals and use read-only mode, command policies, and credential-path protections.
- Extend the workspace with tool plugins, MCP servers, and Agent Skills.
- Use the browser workspace, command-line entry point, or the Windows desktop installer.

## Quick Start

### Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Node.js and npm
- Windows 10 1809+ x64 for the native command sandbox

### Install and Run

```powershell
uv sync
cd frontend
npm install
cd ..

# Windows command sandbox; run once as administrator
.\scripts\setup-sandbox.ps1

# Start the backend and frontend, then open the browser
.\scripts\dev.ps1
```

The default address is `http://127.0.0.1:5173`.

To run the services separately:

```powershell
# Terminal 1
.\.venv\Scripts\python.exe -m web

# Terminal 2
cd frontend
npm run dev
```

Sandbox initialization can be skipped when command tools are not needed. To run commands, initialize the sandbox and review the approval and network policies first.

### Desktop Installer

```powershell
# One-time build dependencies
uv sync
cd frontend;  npm install; cd ..
cd desktop;   npm install; cd ..

# Build the NSIS installer
powershell -ExecutionPolicy Bypass -File packaging\build-release.ps1

# Developer build (the artifact/exe names get a 开发版 suffix; the UI stays unlabeled)
powershell -ExecutionPolicy Bypass -File packaging\build-release.ps1 -Dev
```

The artifact is `build\release\out\ALEMBIC-Setup-<version>-x64.exe`. The installer is
native NSIS and runs the standard Modern UI2 wizard: the target directory is chosen on
the directory page, followed by one options page for the two shortcuts. The bundled
runtimes and the Windows command sandbox are always installed. Both the installer and
the app default to administrator elevation. See
[Desktop packaging and release](docs/packaging.md) for versioning, the install flow,
developer naming, extension points, and code signing.

### Command Line

```powershell
.\.venv\Scripts\python.exe main.py "Inspect the current workspace"
```

The CLI requires `DEEPSEEK_API_KEY` and `MODEL_NAME`. Configure browser providers in the setup wizard or settings.

## Workflow

1. Select or add a workspace.
2. Configure a model and enable the tools you need.
3. Describe the task in a conversation. The active policy may ask for approval before file changes or commands.
4. Review work steps, live command output, and file diffs.

## Security

- The API listens on the loopback interface by default. Before exposing it to a network, configure an access token, restrict the bind address and CORS origins, and use protected transport.
- Never commit API keys, MCP credentials, or other secrets. Treat application configuration and conversation files as sensitive data.
- Tool calls are governed by approval, tool enablement, and command policy. Native plugins and MCP servers are trusted extensions; install and enable only sources you have reviewed.
- The Windows command sandbox uses dedicated local users, workspace ACLs, process-tree control, and network policy. Command execution fails closed when the sandbox is unavailable.
- Model output, web content, plugin descriptions, and MCP results are untrusted data. They do not grant authorization or replace security review.

## Development

```powershell
# Backend tests
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "*_test.py"

# Frontend type check and production build
cd frontend
npm run build
```

See the [extension guide](docs/extensions.md), [subagents](docs/subagents.md), [portable runtimes](docs/runtimes.md), repository conventions in [AGENTS.md](AGENTS.md), and the UI design system in [DESIGN.md](DESIGN.md).

## Configuration and Data

Default data directories:

- Windows: `%APPDATA%\Alembic\`
- macOS: `~/Library/Application Support/Alembic/`
- Linux: `$XDG_DATA_HOME/Alembic/`, or `~/.local/share/Alembic/` when unset

Set `ALEMBIC_HOME` to override the data root. It contains model and tool configuration, workspace settings, conversation history, task lists, and background-process logs.

## License

This project is licensed under the [MIT License](LICENSE).
