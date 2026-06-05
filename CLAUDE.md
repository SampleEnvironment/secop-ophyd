# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Key References

- ophyd-async (upstream base library): https://github.com/bluesky/ophyd-async
- SECoP protocol specification: https://github.com/SampleEnvironment/SECoP

## What This Is

SECoP-Ophyd bridges **SECoP** (Sample Environment Communication Protocol) hardware devices with the **Bluesky** experiment framework via **ophyd-async**. It auto-discovers SECoP node structure at runtime, creates typed ophyd-async device/signal hierarchies, and maps SECoP interface classes to Bluesky-compatible device types.

## Commands

```bash
# Install with dev dependencies
uv sync --extra dev

# Run all tests
pytest

# Run a single test file
pytest tests/test_secop_device.py

# Run a single test
pytest tests/test_secop_device.py::test_name

# Lint / format checks (runs black, isort, flake8, mypy)
pre-commit run --all-files

# Build docs
sphinx-build -b html docs docs/_build/html
```

Tests require the `frappy` simulator; `pytest-xprocess` spawns it automatically. The default test timeout is 240s.

## Architecture

### Device Hierarchy

```
SECoPNodeDevice (host:port)        ← root; one per SECoP node
  └─ Modules (auto-created)        ← one per SECoP module
      ├─ Parameters (SignalRW/R)   ← readable/writable values
      ├─ Properties (SignalR)      ← read-only metadata
      └─ SECoPCMDDevice            ← one per SECoP command
          ├─ argument (SignalRW)
          └─ result (SignalR)
```

Module type is selected automatically from the module's `interface_classes`:
- `Readable` → `SECoPReadableDevice`
- `Writable` → `SECoPWritableDevice`
- `Drivable` → `SECoPMoveableDevice`
- `Triggerable` → `SECoPTriggerableDevice`
- Communicator → `SECoPCommunicatorDevice`

### Connection Flow

1. Instantiate `SECoPNodeDevice('host:port')`
2. Inside `init_devices()`, call `.connect()`
3. `SECoPDeviceConnector.connect_real()` introspects the node via `AsyncSecopClient`
4. Dynamically fills signals, child devices, and command wrappers
5. Assigns `StandardReadableFormat` hints (HINTED_SIGNAL, CONFIG_SIGNAL, etc.)

### Key Modules

| File | Responsibility |
|------|---------------|
| `SECoPDevices.py` | All device/connector classes; signal format assignment logic |
| `AsyncFrappyClient.py` | Pure-asyncio TCP client; reconnection; message framing via frappy |
| `SECoPSignal.py` | `SECoPBackend` (deferred init), `LocalBackend`, `SECoPXBackend` (trigger) |
| `GenNodeCode.py` | Jinja2 code-gen: produces typed Python device classes from a live node |
| `util.py` | `SECoPdtype` (frappy ↔ numpy conversion), `Path`, `SECoPReading` |

### Non-Obvious Patterns

- **Deferred backend init**: `SECoPBackend` is created empty and filled later via `init_parameter_from_introspection()` / `init_property_from_introspection()` once the node description arrives.
- **Client caching**: `SECoPDevice._clients` is a class-level dict keyed by `host:port`; multiple devices reuse one `AsyncSecopClient` per node.
- **Status signal exclusion**: Status signals are excluded from format assignment due to composite-dtype limitations in tiled.
- **Enum name normalisation**: SECoP names like `"Low Energy"` are converted to Python identifiers via `secop_enum_name_to_python()` in `util.py`.
- **Async-only**: No synchronous wrappers; all client I/O is native coroutines. Never add `to_thread` calls.
- **Signal format priority**: annotations > interface-class defaults > `_signal_format` property > `CONFIG_SIGNAL` fallback.

## Code Style

- Python 3.11+, strict mypy (`check_untyped_defs = true`)
- black (line-length 88) + isort (black profile)
- Pre-commit enforces all of the above — run before committing
