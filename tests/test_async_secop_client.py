# mypy: disable-error-code="attr-defined"
import asyncio

from frappy.client import CacheItem

from secop_ophyd.AsyncFrappyClient import AsyncSecopClient


async def test_async_secopclient_conn(cryo_sim, async_secop_client: AsyncSecopClient):
    assert async_secop_client.online is True


async def test_async_secopclient_get_param(
    cryo_sim, async_secop_client: AsyncSecopClient
):
    reading = await async_secop_client.get_parameter("cryo", "value", False)

    assert isinstance(reading, CacheItem)


async def test_async_secopclient_disconnect(
    cryo_sim, async_secop_client: AsyncSecopClient
):
    await async_secop_client.get_parameter("cryo", "value", False)

    await async_secop_client.disconnect(True)

    assert async_secop_client.state == "shutdown"


async def test_async_secopclient_reconn(cryo_sim, async_secop_client: AsyncSecopClient):
    reading1: CacheItem = await async_secop_client.get_parameter("cryo", "value", False)
    reading2: CacheItem = await async_secop_client.get_parameter("cryo", "value", False)

    assert reading1.value != reading2.value

    await async_secop_client.disconnect(False)

    while async_secop_client.state == "connected":
        await asyncio.sleep(0.001)

    while async_secop_client.state in ("reconnecting", "connecting"):
        await asyncio.sleep(0.001)

    while async_secop_client.state == "activating":
        await asyncio.sleep(0.001)

    assert async_secop_client.state == "connected"

    reading3: CacheItem = await async_secop_client.get_parameter("cryo", "value", False)
    reading4: CacheItem = await async_secop_client.get_parameter("cryo", "value", False)

    assert reading3.value != reading4.value


async def test_async_secopclient_shutdown_and_reconn(
    cryo_sim, async_secop_client: AsyncSecopClient
):
    reading1: CacheItem = await async_secop_client.get_parameter("cryo", "value", False)
    reading2: CacheItem = await async_secop_client.get_parameter("cryo", "value", False)

    assert reading1.value != reading2.value

    await async_secop_client.disconnect(True)

    assert async_secop_client.state == "shutdown"

    await asyncio.sleep(2)

    assert async_secop_client.state == "shutdown"

    async_secop_client._shutdown.clear()
    await async_secop_client.connect(3)

    assert async_secop_client.state == "connected"

    reading1 = await async_secop_client.get_parameter("cryo", "value", False)
    reading2 = await async_secop_client.get_parameter("cryo", "value", False)

    assert reading1.value != reading2.value
