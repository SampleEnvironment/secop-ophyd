# mypy: disable-error-code="attr-defined"
"""Generated device classes must connect and work with ``mock=True`` without a
SEC node: the mocked devices point at a port nothing listens on."""

import asyncio
from pathlib import Path

import pytest
from ophyd_async.core import get_mock, get_mock_put, set_mock_value

from secop_ophyd.AsyncFrappyClient import AsyncSecopClient
from secop_ophyd.GenNodeCode import GenNodeCode
from tests.util import import_generated

UNREACHABLE = "localhost:1"


async def _generate(sim, tmp_path: Path, logger):
    client = AsyncSecopClient(host="localhost", port=str(sim.port), log=logger)
    await client.connect(3)
    try:
        description = (await client.request("describe"))[2]
    finally:
        await client.disconnect(True)

    gen_code = GenNodeCode(path=str(tmp_path), log=logger)
    gen_code.from_json_describe(description)
    gen_code.write_gen_node_class_file()

    (node_cls,) = gen_code.node_classes
    module = import_generated(tmp_path, node_cls.name)
    return getattr(module, node_cls.name)


async def _connect_mock(node_cls, name: str):
    node = node_cls(UNREACHABLE, name=name)
    await asyncio.wait_for(node.connect(mock=True), 10)
    return node


async def test_mock_movable(cryo_sim, tmp_path, logger):
    node = await _connect_mock(await _generate(cryo_sim, tmp_path, logger), "cryo")

    assert node.name == "cryo"
    assert node.cryo.name == "cryo-cryo"

    # same names as a real connect: the readback is named after the module
    assert set(await node.read()) == {"cryo-cryo", "cryo-cryo-target"}
    assert "cryo-cryo-ramp" in await node.read_configuration()

    # mocked modules start IDLE, a move arrives instantly
    await asyncio.wait_for(node.cryo.set(12.5), 5)
    get_mock_put(node.cryo.target).assert_called_once_with(12.5)
    location = await node.cryo.locate()
    assert location["setpoint"] == location["readback"] == 12.5

    await asyncio.wait_for(node.cryo.wait_for_idle(), 5)

    # no SEC node to send 'stop' to
    await node.cryo.stop()

    # repeated connects keep the tree usable
    await node.connect(mock=True)
    assert set(await node.read()) == {"cryo-cryo", "cryo-cryo-target"}


async def test_mock_then_real_connect(cryo_sim, tmp_path, logger):
    node_cls = await _generate(cryo_sim, tmp_path, logger)

    async def keys(node):
        return (
            set(await node.read()),
            set(await node.describe()),
            set(await node.read_configuration()),
        )

    fresh = node_cls(cryo_sim.uri, name="cryo")
    await fresh.connect()
    expected = await keys(fresh)

    node = node_cls(cryo_sim.uri, name="cryo")
    await node.connect(mock=True)
    await node.connect()
    assert await keys(node) == expected

    await node.connect(force_reconnect=True)
    assert await keys(node) == expected


async def test_mock_struct_parameters_and_commands(nested_struct_sim, tmp_path, logger):
    node = await _connect_mock(
        await _generate(nested_struct_sim, tmp_path, logger), "nested"
    )

    struct = node.ophy_struct
    set_mock_value(struct.value_x, 1.5)
    reading = await node.read()
    assert reading["nested-ophy_struct-value_x"]["value"] == 1.5

    # struct arguments are keyword members, as on a real node
    await struct.test_cmd.execute(name="x", id=1, sort=True)
    get_mock(struct.test_cmd).execute.assert_awaited_once_with(
        name="x", id=1, sort=True
    )
    with pytest.raises(TypeError):
        await struct.test_cmd.execute({"name": "x", "id": 1, "sort": True})


async def test_mock_commands(motion_sim, tmp_path, logger):
    node = await _connect_mock(await _generate(motion_sim, tmp_path, logger), "motion")

    # same call convention as on a real node: 'arg', plus 'wait_for_idle'
    await node.detector.go.execute(wait_for_idle=True)
    get_mock(node.detector.go).execute.assert_awaited_once_with(True)

    await node.comm.communicate.execute(arg="ping")
    await node.comm.communicate.execute("pong", wait_for_idle=False)
    assert get_mock(node.comm.communicate).execute.await_count == 2

    # Readable, Writable and Triggerable modules start IDLE
    for module in (node.detector, node.writer):
        await asyncio.wait_for(module.wait_for_idle(), 5)
        assert await module.status_0.get_value() == "IDLE"
    await node.writer.target.set(1.0)
    assert set(await node.read())

    await node.detector.stop()


async def test_generated_meaning(cryo_sim, nested_struct_sim, tmp_path, logger):
    cryo_cls = await _generate(cryo_sim, tmp_path / "cryo", logger)
    annotations = cryo_cls.__annotations__["cryo"].__annotations__
    assert "SignalR[str]" in str(annotations["meaning_function"])
    assert "SignalR[int]" in str(annotations["meaning_importance"])

    cryo = await _connect_mock(cryo_cls, "cryo")
    assert "cryo-cryo-meaning_importance" in await cryo.read_configuration()

    # enum1 and enum2 share a class, only enum1 has a meaning: the class
    # does not annotate it, so both modules connect
    nested_cls = await _generate(nested_struct_sim, tmp_path / "nested", logger)
    enum_cls = nested_cls.__annotations__["enum1"]
    assert "meaning_function" not in enum_cls.__annotations__

    nested = nested_cls(nested_struct_sim.uri, name="nested")
    await nested.connect()
    assert await nested.enum1.meaning_function.get_value() == "flowrate"
    assert not hasattr(nested.enum2, "meaning_function")
