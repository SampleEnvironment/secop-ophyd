# import pytest
# from frappy.datatypes import (
#     ArrayOf,
#     BLOBType,
#     BoolType,
#     EnumType,
#     FloatRange,
#     IntRange,
#     ScaledInteger,
#     StringType,
#     StructOf,
#     TupleOf,
# )

# from secop_ophyd.util import NestedRaggedArray, SECoPdtype


# def test_nested_ragged_inside_struct_arrays():
#     ragged = ArrayOf(
#         StructOf(arr2=ArrayOf(FloatRange(), minlen=0, maxlen=100), name=StringType()),
#         minlen=0,
#         maxlen=100,
#     )

#     with pytest.raises(NestedRaggedArray):
#         SECoPdtype(ragged)


# def test_nested_ragged_inside_tuple_arrays():
#     ragged = ArrayOf(
#         TupleOf(ArrayOf(FloatRange(), minlen=0, maxlen=100), StringType()),
#         minlen=0,
#         maxlen=100,
#     )

#     with pytest.raises(NestedRaggedArray):
#         SECoPdtype(ragged)


# def test_nested_ragged_arrays():
#     ragged = ArrayOf(ArrayOf(FloatRange(),
#       minlen=0, maxlen=100), minlen=0, maxlen=100)

#     with pytest.raises(NestedRaggedArray):
#         SECoPdtype(ragged)


# def test_nested_arrays():

#     inner_dt_list = [
#         IntRange(),
#         FloatRange(),
#         ScaledInteger(1),
#         StringType(),
#         BoolType(),
#         BLOBType(),
#         EnumType("test", bla=0, blub=1),
#     ]

#     dtype_list = ["number", "number", "number",
#       "string", "boolean", "string", "number"]

#     for innner_dt, dtype in zip(inner_dt_list, dtype_list):

#         arr_dt = ArrayOf(
#             ArrayOf(ArrayOf(innner_dt, minlen=5, maxlen=5), minlen=5, maxlen=5),
#             minlen=0,
#             maxlen=5,
#         )

#         sdtype = SECoPdtype(arr_dt)

#         assert sdtype.dtype == dtype

#     ragged_arr = ArrayOf(
#         ArrayOf(
#             ArrayOf(IntRange(min=0, max=100), minlen=0, maxlen=5), minlen=5, maxlen=5
#         ),
#         minlen=5,
#         maxlen=5,
#     )

#     with pytest.raises(NestedRaggedArray):
#         SECoPdtype(ragged_arr)
