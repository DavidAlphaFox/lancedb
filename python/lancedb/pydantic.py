#  Copyright 2023 LanceDB Developers
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

"""Pydantic adapter for LanceDB"""

from __future__ import annotations

import inspect
import sys
import types
from abc import ABC, abstractmethod
from typing import Any, List, Type, Union, _GenericAlias

import pyarrow as pa
import pydantic
from pydantic_core import CoreSchema, core_schema


class FixedSizeListMixin(ABC):
    @staticmethod
    @abstractmethod
    def dim() -> int:
        raise NotImplementedError

    @staticmethod
    @abstractmethod
    def value_arrow_type() -> pa.DataType:
        raise NotImplementedError


def vector(
    dim: int, value_type: pa.DataType = pa.float32()
) -> Type[FixedSizeListMixin]:
    """Pydantic Vector Type.

    !!! warning
        Experimental feature.

    Parameters
    ----------
    dim : int
        The dimension of the vector.
    value_type : pyarrow.DataType, optional
        The value type of the vector, by default pa.float32()

    Examples
    --------

    >>> import pydantic
    >>> from lancedb.pydantic import vector
    ...
    >>> class MyModel(pydantic.BaseModel):
    ...     id: int
    ...     url: str
    ...     embeddings: vector(768)
    >>> schema = pydantic_to_schema(MyModel)
    >>> assert schema == pa.schema([
    ...     pa.field("id", pa.int64(), False),
    ...     pa.field("url", pa.utf8(), False),
    ...     pa.field("embeddings", pa.list_(pa.float32(), 768), False)
    ... ])
    """

    # TODO: make a public parameterized type.
    class FixedSizeList(list, FixedSizeListMixin):
        @staticmethod
        def dim() -> int:
            return dim

        @staticmethod
        def value_arrow_type() -> pa.DataType:
            return value_type

        @classmethod
        def __get_pydantic_core_schema__(
            cls, _source_type: Any, _handler: pydantic.GetCoreSchemaHandler
        ) -> CoreSchema:
            return core_schema.no_info_after_validator_function(
                cls,
                core_schema.list_schema(
                    min_length=dim,
                    max_length=dim,
                    items_schema=core_schema.float_schema(),
                ),
            )

    return FixedSizeList


def _py_type_to_arrow_type(py_type: Type[Any]) -> pa.DataType:
    """Convert Python Type to Arrow DataType.

    Raises
    ------
    TypeError
        If the type is not supported.
    """
    if py_type == int:
        return pa.int64()
    elif py_type == float:
        return pa.float64()
    elif py_type == str:
        return pa.utf8()
    elif py_type == bool:
        return pa.bool_()
    elif py_type == bytes:
        return pa.binary()
    raise TypeError(
        f"Converting Pydantic type to Arrow Type: unsupported type {py_type}"
    )


def _pydantic_model_to_fields(model: pydantic.BaseModel) -> List[pa.Field]:
    fields = []
    for name, field in model.model_fields.items():
        fields.append(_pydantic_to_field(name, field))
    return fields


def _pydantic_to_arrow_type(field: pydantic.fields.FieldInfo) -> pa.DataType:
    """Convert a Pydantic FieldInfo to Arrow DataType"""
    if isinstance(field.annotation, _GenericAlias) or (
        sys.version_info > (3, 9) and isinstance(field.annotation, types.GenericAlias)
    ):
        origin = field.annotation.__origin__
        args = field.annotation.__args__
        if origin == list:
            child = args[0]
            return pa.list_(_py_type_to_arrow_type(child))
        elif origin == Union:
            if len(args) == 2 and args[1] == type(None):
                return _py_type_to_arrow_type(args[0])
    elif inspect.isclass(field.annotation):
        if issubclass(field.annotation, pydantic.BaseModel):
            # Struct
            fields = _pydantic_model_to_fields(field.annotation)
            return pa.struct(fields)
        elif issubclass(field.annotation, FixedSizeListMixin):
            return pa.list_(field.annotation.value_arrow_type(), field.annotation.dim())
    return _py_type_to_arrow_type(field.annotation)


def is_nullable(field: pydantic.fields.FieldInfo) -> bool:
    """Check if a Pydantic FieldInfo is nullable."""
    if isinstance(field.annotation, _GenericAlias):
        origin = field.annotation.__origin__
        args = field.annotation.__args__
        if origin == Union:
            if len(args) == 2 and args[1] == type(None):
                return True
    return False


def _pydantic_to_field(name: str, field: pydantic.fields.FieldInfo) -> pa.Field:
    """Convert a Pydantic field to a PyArrow Field."""
    dt = _pydantic_to_arrow_type(field)
    return pa.field(name, dt, is_nullable(field))


def pydantic_to_schema(model: Type[pydantic.BaseModel]) -> pa.Schema:
    """Convert a Pydantic model to a PyArrow Schema.

    Parameters
    ----------
    model : Type[pydantic.BaseModel]
        The Pydantic BaseModel to convert to Arrow Schema.

    Returns
    -------
    pyarrow.Schema

    Examples
    --------

    >>> from typing import List, Optional
    >>> import pydantic
    >>> from lancedb.pydantic import pydantic_to_schema
    ...
    >>> class InnerModel(pydantic.BaseModel):
    ...     a: str
    ...     b: Optional[float]
    >>>
    >>> class FooModel(pydantic.BaseModel):
    ...     id: int
    ...     s: Optional[str] = None
    ...     vec: List[float]
    ...     li: List[int]
    ...     inner: InnerModel
    >>> schema = pydantic_to_schema(FooModel)
    >>> assert schema == pa.schema([
    ...     pa.field("id", pa.int64(), False),
    ...     pa.field("s", pa.utf8(), True),
    ...     pa.field("vec", pa.list_(pa.float64()), False),
    ...     pa.field("li", pa.list_(pa.int64()), False),
    ...     pa.field("inner", pa.struct([
    ...         pa.field("a", pa.utf8(), False),
    ...         pa.field("b", pa.float64(), True),
    ...     ]), False),
    ... ])
    """
    fields = _pydantic_model_to_fields(model)
    return pa.schema(fields)
