# mypy: disable-error-code="attr-defined"
from secop_ophyd.util import Path, deep_get


def test_path_nested_tuples():
    path = Path(module_name="mod", parameter_name="param")

    assert path.get_signal_name() == "param"

    path = path.append(1)
    assert path.get_signal_name() == "param-1"

    path = path.append(2)

    assert path.get_signal_name() == "param-1-2"

    path = path.append(3)
    assert path.get_signal_name() == "param-1-2-3"

    path = path.append("new_param")
    assert path.get_signal_name() == "new_param"


def test_path_memberinfo(nested_param_description):
    path = Path(module_name="mod", parameter_name="_nested_struct")

    param_desc = nested_param_description["_nested_struct"]
    datainfo: dict = deep_get(param_desc["datainfo"], path.get_memberinfo_path())

    assert datainfo == param_desc["datainfo"]

    path_struct = path.append("pos_struct")

    datainfo = deep_get(param_desc["datainfo"], path_struct.get_memberinfo_path())

    assert datainfo == param_desc["datainfo"]["members"]["pos_struct"]

    path_tupl = path.append("tupl")

    datainfo = deep_get(param_desc["datainfo"], path_tupl.get_memberinfo_path())

    assert datainfo == param_desc["datainfo"]["members"]["tupl"]

    path_tupl_2 = path_tupl.append(1)

    assert path_tupl_2.get_memberinfo_path() == ["members", "tupl", "members", 1]

    datainfo = deep_get(param_desc["datainfo"], path_tupl_2.get_memberinfo_path())

    assert datainfo == param_desc["datainfo"]["members"]["tupl"]["members"][1]


def test_path_insert(struct_val: dict):
    path = Path(module_name="mod", parameter_name="_nested_struct")

    path_struct_mem = path.append("pos_struct")
    path_tupl_mem = path.append("tupl")

    path_struct_mem = path_struct_mem.append("x")
    path_tupl_mem = path_tupl_mem.append(1)

    new_x_val = 10.0
    new_tuple_val = 15.0

    new_dic = path_struct_mem.insert_val(struct_val, new_x_val)

    assert deep_get(struct_val, path_struct_mem._dev_path) == new_x_val
    assert deep_get(new_dic, path_struct_mem._dev_path) == new_x_val

    new_dic_tup = path_tupl_mem.insert_val(struct_val, new_tuple_val)

    assert deep_get(struct_val, path_tupl_mem._dev_path) == new_tuple_val
    assert deep_get(new_dic_tup, path_tupl_mem._dev_path) == new_tuple_val

    print(struct_val)
