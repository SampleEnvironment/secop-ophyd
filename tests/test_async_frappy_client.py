# import xprocess
from secop_ophyd.AsyncFrappyClient import AsyncFrappyClient, SECoPReading


import asyncio


async def test_asycnc_secopclient_conn(
    cryo_sim, async_frappy_client: AsyncFrappyClient
):
    assert async_frappy_client.online is True
    await async_frappy_client.disconnect()


async def test_asycnc_secopclient_get_Param(
    cryo_sim, async_frappy_client: AsyncFrappyClient
):
    reading = await async_frappy_client.getParameter("cryo", "value", False)

    assert isinstance(reading, SECoPReading)

    await async_frappy_client.disconnect()


async def test_async_secopclient_disconnect(
    cryo_sim, async_frappy_client: AsyncFrappyClient
):
    await async_frappy_client.getParameter("cryo", "value", False)

    await async_frappy_client.disconnect(True)

    assert async_frappy_client.state == "shutdown"


async def test_async_secopclient_reconn(
    cryo_sim, async_frappy_client: AsyncFrappyClient
):
    reading1 = await async_frappy_client.getParameter("cryo", "value", False)
    reading2 = await async_frappy_client.getParameter("cryo", "value", False)

    assert reading1.get_value() != reading2.get_value()

    await async_frappy_client.disconnect(False)

    # for a short period the status is still "connected" (the disconn task finishes and the state is only set to a new value once the reconnect thread starts)
    while async_frappy_client.state == "connected":
        await asyncio.sleep(0.001)

    while async_frappy_client.state == "reconnecting":
        await asyncio.sleep(0.001)

    assert async_frappy_client.state == "connected"

    # ensures we are connected and getting fresh data again
    reading3 = await async_frappy_client.getParameter("cryo", "value", False)
    reading4 = await async_frappy_client.getParameter("cryo", "value", False)

    assert reading3.get_value() != reading4.get_value()

    await async_frappy_client.disconnect(True)


async def test_async_secopclient_shutdown_and_reconn(
    cryo_sim, async_frappy_client: AsyncFrappyClient
):
    reading1 = await async_frappy_client.getParameter("cryo", "value", False)
    reading2 = await async_frappy_client.getParameter("cryo", "value", False)

    assert reading1.get_value() != reading2.get_value()

    # Shutdown
    await async_frappy_client.disconnect(True)

    assert async_frappy_client.state == "shutdown"

    await asyncio.sleep(2)

    # ensure no auto reconnect
    assert async_frappy_client.state == "shutdown"

    # manual reconn
    async_frappy_client.client._shutdown.clear()
    await async_frappy_client.connect(3)

    assert async_frappy_client.state == "connected"

    # ensures we are connected and getting fresh data again
    reading1 = await async_frappy_client.getParameter("cryo", "value", False)
    reading2 = await async_frappy_client.getParameter("cryo", "value", False)

    assert reading1.get_value() != reading2.get_value()

    await async_frappy_client.disconnect(True)
