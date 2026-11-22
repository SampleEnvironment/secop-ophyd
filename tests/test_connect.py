# mypy: disable-error-code="attr-defined"
"""Connecting: repeated connects, naming, and checking annotated device classes
against the SEC node description."""

import json
from typing import Annotated as A

import pytest
from frappy.datatypes import ArrayOf, FloatRange, StringType
from ophyd_async.core import SignalR, SignalRW, StrictEnum, SubsetEnum

from secop_ophyd.GenNodeCode import get_type_prop
from secop_ophyd.SECoPDevices import (
    ParameterMemberType,
    ParameterType,
    SECoPMoveableDevice,
    SECoPNodeDevice,
    SECoPReadableDevice,
)
from secop_ophyd.SECoPSignal import module_property_signals, property_dtype_and_value
from secop_ophyd.util import SECoPdtype


async def test_repeated_connect(cryo_sim):
    node = SECoPNodeDevice(sec_node_uri=cryo_sim.uri)
    await node.connect()
    keys = set(await node.read())

    await node.connect()
    assert set(await node.read()) == keys

    await node.connect(force_reconnect=True)
    assert set(await node.read()) == keys
    assert set(await node.read_configuration())


async def test_connect_keeps_names(cryo_sim):
    node = SECoPNodeDevice(sec_node_uri=cryo_sim.uri, name="mynode")
    await node.connect()
    assert node.name == "mynode"
    assert node.cryo.name == "mynode-cryo"

    # without an explicit name, the first connect names the node after its
    # equipment id; later connects keep a name the user set in between
    unnamed = SECoPNodeDevice(sec_node_uri=cryo_sim.uri)
    await unnamed.connect()
    assert unnamed.name == "cryo_7-frappy-demo"

    unnamed.set_name("renamed")
    await unnamed.connect(force_reconnect=True)
    await unnamed.cryo.connect()
    assert unnamed.name == "renamed"
    assert unnamed.cryo.name == "renamed-cryo"


async def test_movable_without_annotated_status(cryo_sim):
    """status_0 is filled from the description after the annotated target and
    value; naming the device must wait for it"""

    class Cryostat(SECoPMoveableDevice):
        target: A[SignalRW[float], ParameterType()]
        value: A[SignalR[float], ParameterType()]

    cryo = Cryostat(sri=cryo_sim.uri + ":cryo", name="mycryo")
    await cryo.connect()
    assert cryo.name == "mycryo"
    location = await cryo.locate()
    assert isinstance(location["readback"], float)


async def test_missing_annotated_parameter(cryo_sim):
    class Cryostat(SECoPMoveableDevice):
        not_on_node: A[SignalR[float], ParameterType()]

    cryo = Cryostat(sri=cryo_sim.uri + ":cryo")
    with pytest.raises(Exception, match=r"cannot provision \['not_on_node'\]"):
        await cryo.connect()


async def test_missing_annotated_module(cryo_sim):
    class Node(SECoPNodeDevice):
        not_on_node: SECoPReadableDevice

    node = Node(sec_node_uri=cryo_sim.uri)
    with pytest.raises(Exception, match=r"cannot provision \['not_on_node'\]"):
        await node.connect()


async def test_module_not_on_node(cryo_sim):
    module = SECoPReadableDevice(sri=cryo_sim.uri + ":not_on_node")
    with pytest.raises(Exception, match="'not_on_node' is not provided by SEC node"):
        await module.connect()


async def test_retry_after_failed_fill(cryo_sim):
    class Cryostat(SECoPMoveableDevice):
        not_on_node: A[SignalR[float], ParameterType()]

    cryo = Cryostat(sri=cryo_sim.uri + ":cryo")
    with pytest.raises(Exception, match=r"cannot provision \['not_on_node'\]"):
        await cryo.connect()

    # the retry reports the original error instead of a "would shadow" error
    with pytest.raises(
        Exception, match=r"failed earlier \(.*cannot provision.*create a new device"
    ):
        await cryo.connect()


async def test_mock_movable_needs_annotations():
    class Cryostat(SECoPMoveableDevice):
        value: A[SignalR[float], ParameterType()]

    cryo = Cryostat(sri="localhost:1:cryo")
    with pytest.raises(Exception, match="status_0"):
        await cryo.connect(mock=True)


async def test_legitimate_annotations(cryo_sim):
    class Mode(SubsetEnum):
        RAMP = "ramp"
        PID = "pid"

    class Cryostat(SECoPMoveableDevice):
        # number kinds are interchangeable, enums may be read as str
        maxpower: A[SignalRW[int], ParameterType()]
        mode: A[SignalRW[Mode], ParameterType()]
        status_0: A[SignalR[str], ParameterMemberType()]

    cryo = Cryostat(sri=cryo_sim.uri + ":cryo")
    await cryo.connect()
    assert await cryo.mode.get_value() in ("ramp", "pid", "openloop")


async def test_wrong_annotated_datatype(cryo_sim):
    class Cryostat(SECoPMoveableDevice):
        ramp: A[SignalRW[str], ParameterType()]

    cryo = Cryostat(sri=cryo_sim.uri + ":cryo")
    with pytest.raises(Exception, match="cryo:ramp"):
        await cryo.connect()


async def test_wrong_annotated_enum_members(cryo_sim):
    class WrongMode(StrictEnum):
        RAMP = "ramp"
        PID = "pid"

    class Cryostat(SECoPMoveableDevice):
        mode: A[SignalRW[WrongMode], ParameterType()]

    cryo = Cryostat(sri=cryo_sim.uri + ":cryo")
    with pytest.raises(Exception, match="openloop"):
        await cryo.connect()


def test_empty_variable_length_array():
    # frappy delivers arrays as tuples
    dtype = SECoPdtype(ArrayOf(FloatRange(), minlen=0, maxlen=5))
    dtype.update_dtype(())
    assert dtype.get_datakey()["shape"] == [0]


@pytest.mark.parametrize(
    "prop_val",
    [
        {"slope": 1.5, "label": "abc"},
        ["temperature", 10],
        [{"a": 1}, {"a": 2}],
        {"a": None, "b": {}},
    ],
)
def test_composite_property_is_json_string(prop_val):
    datatype, value = property_dtype_and_value(prop_val)
    assert isinstance(datatype, StringType)
    assert json.loads(value) == prop_val
    assert get_type_prop(prop_val) == "str"


def test_numeric_list_property():
    # the same datatype whether the node sends 10 or 10.0
    for prop_val in ([0, 10.5], [0.0, 10.5], [1, 2]):
        assert get_type_prop(prop_val) == "ndarray"


def test_bool_property():
    datatype, value = property_dtype_and_value(True)
    assert SECoPdtype(datatype).np_datatype is bool
    assert value is True


@pytest.mark.parametrize(
    "meaning, expected",
    [
        # SECoP 1.x: (function, importance)
        (["temperature", 10], {"function": "temperature", "importance": 10}),
        # SECoP 2.0: a struct whose members are all optional
        (
            {"function": "temperature_regulation", "importance": 20, "link": "x"},
            {"function": "temperature_regulation", "importance": 20, "link": "x"},
        ),
        ({"key": "synthesis temperature", "link": "x"}, None),
    ],
)
def test_meaning_property_signals(meaning, expected):
    signals = module_property_signals({"meaning": meaning, "group": "g"})
    expected = expected or meaning
    assert {name: value for name, (_, value) in signals.items()} == {
        "group": "g",
        **{f"meaning_{key}": value for key, value in expected.items()},
    }
    if "meaning_importance" in signals:
        assert SECoPdtype(signals["meaning_importance"][0]).np_datatype is int


@pytest.mark.parametrize(
    "meaning",
    [
        {"function": "temperature", "colour": "red"},  # unknown member
        {"function": "temperature", "importance": "high"},  # wrong type
        ["temperature", 10, 3],
        {},
    ],
)
def test_malformed_meaning_is_json_string(meaning):
    signals = module_property_signals({"meaning": meaning})
    datatype, value = signals["meaning"]
    assert isinstance(datatype, StringType)
    assert json.loads(value) == meaning


async def test_meaning_signals(cryo_sim):
    node = SECoPNodeDevice(sec_node_uri=cryo_sim.uri, name="node")
    await node.connect()
    config = await node.cryo.read_configuration()
    assert config["node-cryo-meaning_function"]["value"] == "temperature_regulation"
    assert config["node-cryo-meaning_importance"]["value"] == 10
    describe = await node.cryo.describe_configuration()
    assert describe["node-cryo-meaning_importance"]["dtype"] == "integer"
