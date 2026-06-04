# mypy: disable-error-code="attr-defined"
import asyncio

import pytest
from frappy.client import CacheItem
from frappy.protocol.messages import READREQUEST

# import xprocess
from secop_ophyd.AsyncFrappyClient import AsyncSecopClient


async def test_asycnc_secopclient_conn(cryo_sim, async_frappy_client: AsyncSecopClient):
    assert async_frappy_client.online is True


async def test_asycnc_secopclient_get_param(
    cryo_sim, async_frappy_client: AsyncSecopClient
):
    reading = await async_frappy_client.get_parameter("cryo", "value", False)

    assert isinstance(reading, CacheItem)


async def test_async_secopclient_disconnect(
    cryo_sim, async_frappy_client: AsyncSecopClient
):
    await async_frappy_client.get_parameter("cryo", "value", False)

    await async_frappy_client.disconnect(True)

    assert async_frappy_client.state == "shutdown"


async def test_async_secopclient_reconn(
    cryo_sim, async_frappy_client: AsyncSecopClient
):
    reading1: CacheItem = await async_frappy_client.get_parameter(
        "cryo", "value", False
    )
    reading2: CacheItem = await async_frappy_client.get_parameter(
        "cryo", "value", False
    )

    assert reading1.value != reading2.value

    await async_frappy_client.disconnect(False)

    # for a short period the status is still "connected"
    # (the disconn task finishes and the state is only set to a new value
    # once the reconnect thread starts)
    while async_frappy_client.state == "connected":
        await asyncio.sleep(0.001)

    while async_frappy_client.state == "reconnecting":
        await asyncio.sleep(0.001)

    while async_frappy_client.state == "activating":
        await asyncio.sleep(0.001)

    assert async_frappy_client.state == "connected"

    # ensures we are connected and getting fresh data again
    reading3: CacheItem = await async_frappy_client.get_parameter(
        "cryo", "value", False
    )
    reading4: CacheItem = await async_frappy_client.get_parameter(
        "cryo", "value", False
    )

    assert reading3.value != reading4.value


async def test_async_secopclient_shutdown_and_reconn(
    cryo_sim, async_frappy_client: AsyncSecopClient
):
    reading1: CacheItem = await async_frappy_client.get_parameter(
        "cryo", "value", False
    )
    reading2: CacheItem = await async_frappy_client.get_parameter(
        "cryo", "value", False
    )

    assert reading1.value != reading2.value

    # Shutdown
    await async_frappy_client.disconnect(True)

    assert async_frappy_client.state == "shutdown"

    await asyncio.sleep(2)

    # ensure no auto reconnect
    assert async_frappy_client.state == "shutdown"

    # manual reconn
    async_frappy_client._shutdown.clear()
    await async_frappy_client.connect(3)

    assert async_frappy_client.state == "connected"

    # ensures we are connected and getting fresh data again
    reading1 = await async_frappy_client.get_parameter("cryo", "value", False)
    reading2 = await async_frappy_client.get_parameter("cryo", "value", False)

    assert reading1.value != reading2.value


async def test_async_secopclient_set_parameter(
    cryo_sim, async_frappy_client: AsyncSecopClient
):
    result = await async_frappy_client.set_parameter("cryo", "target", 15.0)
    assert isinstance(result, CacheItem)
    assert result.value == pytest.approx(15.0)


async def test_async_secopclient_exec_command(
    cryo_sim, async_frappy_client: AsyncSecopClient
):
    _, qualifiers = await async_frappy_client.exec_command("cryo", "stop")
    assert isinstance(qualifiers, dict)


async def test_async_secopclient_get_param_trycache(
    cryo_sim, async_frappy_client: AsyncSecopClient
):
    """trycache=True returns cached CacheItem without issuing a new read request."""
    await async_frappy_client.get_parameter("cryo", "value", trycache=False)
    await async_frappy_client.disconnect(shutdown=True)
    # Offline: trycache=True must succeed from cache; trycache=False would also
    # fall through to cache, but the early-return path is unique to trycache.
    result = await async_frappy_client.get_parameter("cryo", "value", trycache=True)
    assert isinstance(result, CacheItem)


async def test_async_secopclient_descriptive_data(
    cryo_sim, async_frappy_client: AsyncSecopClient
):
    """After connect, modules/identifier/properties are fully populated."""
    assert "cryo" in async_frappy_client.modules
    cryo = async_frappy_client.modules["cryo"]
    assert "value" in cryo["parameters"]
    assert "target" in cryo["parameters"]
    assert "stop" in cryo["commands"]
    assert ("cryo", "value") in async_frappy_client.identifier
    assert async_frappy_client.properties.get("equipment_id") is not None


async def test_async_secopclient_concurrent_reads(
    cryo_sim, async_frappy_client: AsyncSecopClient
):
    """Multiple concurrent get_parameter calls all return valid CacheItems."""
    results = await asyncio.gather(
        *[async_frappy_client.get_parameter("cryo", "value", False) for _ in range(50)]
    )
    assert all(isinstance(r, CacheItem) for r in results)


async def test_async_secopclient_update_callback(
    cryo_sim, async_frappy_client: AsyncSecopClient
):
    """updateItem callback receives (module, param, entry) on each read."""
    received = []

    def updateItem(module, param, entry):
        received.append((module, param, entry))

    async_frappy_client.register_callback(
        ("cryo", "value"), updateItem, callimmediately=False
    )

    await async_frappy_client.get_parameter("cryo", "value", trycache=False)

    assert len(received) >= 1
    module, param, entry = received[-1]
    assert module == "cryo"
    assert param == "value"
    assert isinstance(entry, CacheItem)


async def test_async_secopclient_node_state_callback(
    cryo_sim, async_frappy_client: AsyncSecopClient
):
    """nodeStateChange fires immediately on registration and again on disconnect."""
    changes = []

    def nodeStateChange(online, state):
        changes.append((online, state))

    async_frappy_client.register_callback(None, nodeStateChange)

    assert len(changes) == 1
    assert changes[0] == (True, "connected")

    await async_frappy_client.disconnect(shutdown=True)

    assert (False, "shutdown") in changes


async def test_async_secopclient_connect_invalid_port(logger):
    """Connecting to an unreachable port with try_period=0 raises quickly."""
    client = AsyncSecopClient(host="localhost", port="19999", log=logger)
    with pytest.raises(Exception):
        await client.connect(try_period=0)


async def test_async_secopclient_internalize_name(logger):
    """internalize_name strips leading underscore only for non-predefined names."""
    client = AsyncSecopClient(host="localhost", port="19999", log=logger)
    assert client.internalize_name("value") == "value"
    predefined = next(iter(AsyncSecopClient.PREDEFINED_NAMES))
    assert client.internalize_name(f"_{predefined}") == f"_{predefined}"
    assert client.internalize_name("_custom_param") == "custom_param"


# ---------------------------------------------------------------------------
# connect() called in different client states
# ---------------------------------------------------------------------------


async def test_connect_already_connected_is_noop(
    cryo_sim, async_frappy_client: AsyncSecopClient
):
    """Second connect() while wired returns immediately without touching the stream."""
    writer_before = async_frappy_client._writer
    await async_frappy_client.connect()
    assert async_frappy_client._writer is writer_before
    assert async_frappy_client.state == "connected"
    assert async_frappy_client.online is True


async def test_connect_after_shutdown_without_manual_clear(
    cryo_sim, async_frappy_client: AsyncSecopClient
):
    """connect() clears _shutdown internally, so no manual clear is needed after
    shutdown."""
    await async_frappy_client.disconnect(True)
    assert async_frappy_client._shutdown.is_set()
    # connect() calls self._shutdown.clear() at line 306 — no manual clear required
    await async_frappy_client.connect(3)
    assert async_frappy_client.state == "connected"
    assert async_frappy_client.online is True
    assert async_frappy_client._writer is not None


async def test_connect_cancels_pending_reconnect_task(
    cryo_sim, async_frappy_client: AsyncSecopClient
):
    """connect() cancels a scheduled-but-not-yet-running reconnect task."""
    await async_frappy_client.disconnect(shutdown=False)
    # _reconnect_task was created by disconnect() but has not run yet
    assert async_frappy_client._reconnect_task is not None
    assert not async_frappy_client._reconnect_task.done()
    old_task = async_frappy_client._reconnect_task

    await async_frappy_client.connect(3)

    assert old_task.cancelled()
    assert async_frappy_client.state == "connected"
    assert async_frappy_client._reconnect_task is None


async def test_connect_state_transition_sequence(cryo_sim, logger):
    """connect() drives the client through connecting → activating → connected."""
    client = AsyncSecopClient(host="localhost", port="10769", log=logger)
    states: list[tuple] = []

    def nodeStateChange(online, state):
        states.append((online, state))

    # nodeStateChange always fires immediately on registration with current values
    client.register_callback(None, nodeStateChange)
    await client.connect(3)

    assert (False, "connecting") in states
    assert (True, "activating") in states
    assert (True, "connected") in states
    assert states.index((False, "connecting")) < states.index((True, "activating"))
    assert states.index((True, "activating")) < states.index((True, "connected"))

    await client.disconnect(True)


# ---------------------------------------------------------------------------
# disconnect() variants and shutdown semantics
# ---------------------------------------------------------------------------


async def test_disconnect_false_creates_reconnect_task(
    cryo_sim, async_frappy_client: AsyncSecopClient
):
    """disconnect(shutdown=False) creates a reconnect task that is not yet running."""
    assert async_frappy_client._reconnect_task is None
    await async_frappy_client.disconnect(shutdown=False)
    task = async_frappy_client._reconnect_task
    assert task is not None
    assert isinstance(task, asyncio.Task)
    assert not task.done()
    # clean up — prevent the reconnect task from running into other tests
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_disconnect_shutdown_sets_event_and_clears_tasks(
    cryo_sim, async_frappy_client: AsyncSecopClient
):
    """disconnect(shutdown=True) sets _shutdown, state=shutdown, clears writer and
    tasks."""
    assert not async_frappy_client._shutdown.is_set()
    await async_frappy_client.disconnect(shutdown=True)
    assert async_frappy_client._shutdown.is_set()
    assert async_frappy_client.state == "shutdown"
    assert async_frappy_client.online is False
    assert async_frappy_client._writer is None
    assert async_frappy_client._reconnect_task is None


async def test_request_after_shutdown_raises(
    cryo_sim, async_frappy_client: AsyncSecopClient
):
    """request() raises ConnectionError after shutdown because _writer is None."""
    await async_frappy_client.get_parameter("cryo", "value", trycache=False)
    await async_frappy_client.disconnect(True)
    ident = async_frappy_client.identifier["cryo", "value"]
    with pytest.raises(ConnectionError):
        await async_frappy_client.request(READREQUEST, ident)


async def test_disconnect_shutdown_while_reconnect_pending(
    cryo_sim, async_frappy_client: AsyncSecopClient
):
    """disconnect(True) right after disconnect(False) cancels the reconnect task and
    shuts down."""
    await async_frappy_client.disconnect(shutdown=False)
    assert async_frappy_client._reconnect_task is not None

    await async_frappy_client.disconnect(shutdown=True)

    assert async_frappy_client.state == "shutdown"
    assert async_frappy_client._reconnect_task is None
    assert async_frappy_client.online is False

    await asyncio.sleep(0.5)

    assert async_frappy_client.state == "shutdown"
    assert async_frappy_client._reconnect_task is None


# ---------------------------------------------------------------------------
# Multiple independent clients
# ---------------------------------------------------------------------------


async def test_two_independent_clients(cryo_sim, logger):
    """Two clients to the same server have independent state, caches, and streams."""
    client_a = AsyncSecopClient(host="localhost", port="10769", log=logger)
    client_b = AsyncSecopClient(host="localhost", port="10769", log=logger)

    await asyncio.gather(client_a.connect(3), client_b.connect(3))

    item_a = await client_a.get_parameter("cryo", "value", False)
    item_b = await client_b.get_parameter("cryo", "value", False)

    assert client_a.online is True
    assert client_b.online is True
    assert client_a.cache is not client_b.cache
    assert client_a._writer is not client_b._writer
    assert item_a is not item_b
    assert isinstance(item_a, CacheItem)
    assert isinstance(item_b, CacheItem)

    await asyncio.gather(client_a.disconnect(True), client_b.disconnect(True))


# ---------------------------------------------------------------------------
# Reconnect lifecycle
# ---------------------------------------------------------------------------


async def test_reconnect_task_cleared_after_success(
    async_frappy_client: AsyncSecopClient,
):
    """_reconnect_task is set to None by _reconnect() itself after successful
    reconnect."""
    await async_frappy_client.disconnect(shutdown=False)
    reconnect_task = async_frappy_client._reconnect_task
    assert reconnect_task is not None

    for _ in range(300):  # 300 × 50 ms = 15 s ceiling
        if reconnect_task.done():
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("reconnect task did not complete within 15 s")

    assert not reconnect_task.cancelled()
    assert async_frappy_client._reconnect_task is None
    assert async_frappy_client.state == "connected"
    assert async_frappy_client.online is True
