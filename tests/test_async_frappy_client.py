# mypy: disable-error-code="attr-defined"
import asyncio

import pytest
from frappy.client import CacheItem

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
