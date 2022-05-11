from functools import partial
from typing import Callable, Optional, List

from mypy import message_registry
from mypy.nodes import StrExpr, IntExpr, DictExpr, UnaryExpr
from mypy.plugin import (
    Plugin, FunctionContext, MethodContext, MethodSigContext, AttributeContext, ClassDefContext
)
from mypy.plugins.common import try_getting_str_literals
from mypy.types import (
    FunctionLike, Type, Instance, AnyType, TypeOfAny, CallableType, NoneType, TypedDictType,
    TypeVarType, TPDICT_FB_NAMES, get_proper_type, LiteralType, TupleType
)
from mypy.subtypes import is_subtype
from mypy.typeops import make_simplified_union
from mypy.checkexpr import is_literal_type_like


class DefaultPlugin(Plugin):
    """Type checker plugin that is enabled by default."""

    def get_function_hook(self, fullname: str
                          ) -> Optional[Callable[[FunctionContext], Type]]:
        from mypy.plugins import ctypes, singledispatch

        if fullname in ('contextlib.contextmanager', 'contextlib.asynccontextmanager'):
            return contextmanager_callback
        elif fullname == 'ctypes.Array':
            return ctypes.array_constructor_callback
        elif fullname == 'functools.singledispatch':
            return singledispatch.create_singledispatch_function_callback
        return None

    def get_method_signature_hook(self, fullname: str
                                  ) -> Optional[Callable[[MethodSigContext], FunctionLike]]:
        from mypy.plugins import ctypes, singledispatch

        if fullname == 'typing.Mapping.get':
            return typed_dict_get_signature_callback
        elif fullname in {n + '.setdefault' for n in TPDICT_FB_NAMES}:
            return typed_dict_setdefault_signature_callback
        elif fullname in {n + '.pop' for n in TPDICT_FB_NAMES}:
            return typed_dict_pop_signature_callback
        elif fullname in {n + '.update' for n in TPDICT_FB_NAMES}:
            return typed_dict_update_signature_callback
        elif fullname == 'ctypes.Array.__setitem__':
            return ctypes.array_setitem_callback
        elif fullname == singledispatch.SINGLEDISPATCH_CALLABLE_CALL_METHOD:
            return singledispatch.call_singledispatch_function_callback
        return None

    def get_method_hook(self, fullname: str
                        ) -> Optional[Callable[[MethodContext], Type]]:
        from mypy.plugins import ctypes, singledispatch

        if fullname == 'typing.Mapping.get':
            return typed_dict_get_callback
        elif fullname == 'builtins.int.__pow__':
            return int_pow_callback
        elif fullname == 'builtins.int.__neg__':
            return int_neg_callback
        elif fullname in ('builtins.tuple.__mul__', 'builtins.tuple.__rmul__'):
            return tuple_mul_callback
        elif fullname in {n + '.setdefault' for n in TPDICT_FB_NAMES}:
            return typed_dict_setdefault_callback
        elif fullname in {n + '.pop' for n in TPDICT_FB_NAMES}:
            return typed_dict_pop_callback
        elif fullname in {n + '.__delitem__' for n in TPDICT_FB_NAMES}:
            return typed_dict_delitem_callback
        elif fullname == 'ctypes.Array.__getitem__':
            return ctypes.array_getitem_callback
        elif fullname == 'ctypes.Array.__iter__':
            return ctypes.array_iter_callback
        elif fullname == singledispatch.SINGLEDISPATCH_REGISTER_METHOD:
            return singledispatch.singledispatch_register_callback
        elif fullname == singledispatch.REGISTER_CALLABLE_CALL_METHOD:
            return singledispatch.call_singledispatch_function_after_register_argument
        return None

    def get_attribute_hook(self, fullname: str
                           ) -> Optional[Callable[[AttributeContext], Type]]:
        from mypy.plugins import ctypes
        from mypy.plugins import enums

        if fullname == 'ctypes.Array.value':
            return ctypes.array_value_callback
        elif fullname == 'ctypes.Array.raw':
            return ctypes.array_raw_callback
        elif fullname in enums.ENUM_NAME_ACCESS:
            return enums.enum_name_callback
        elif fullname in enums.ENUM_VALUE_ACCESS:
            return enums.enum_value_callback
        return None

    def get_class_decorator_hook(self, fullname: str
                                 ) -> Optional[Callable[[ClassDefContext], None]]:
        from mypy.plugins import attrs
        from mypy.plugins import dataclasses

        if fullname in attrs.attr_class_makers:
            return attrs.attr_class_maker_callback
        elif fullname in attrs.attr_dataclass_makers:
            return partial(
                attrs.attr_class_maker_callback,
                auto_attribs_default=True,
            )
        elif fullname in attrs.attr_frozen_makers:
            return partial(
                attrs.attr_class_maker_callback,
                auto_attribs_default=None,
                frozen_default=True,
            )
        elif fullname in attrs.attr_define_makers:
            return partial(
                attrs.attr_class_maker_callback,
                auto_attribs_default=None,
            )
        elif fullname in dataclasses.dataclass_makers:
            return dataclasses.dataclass_tag_callback

        return None

    def get_class_decorator_hook_2(self, fullname: str
                                   ) -> Optional[Callable[[ClassDefContext], bool]]:
        from mypy.plugins import dataclasses
        from mypy.plugins import functools

        if fullname in dataclasses.dataclass_makers:
            return dataclasses.dataclass_class_maker_callback
        elif fullname in functools.functools_total_ordering_makers:
            return functools.functools_total_ordering_maker_callback

        return None


def contextmanager_callback(ctx: FunctionContext) -> Type:
    """Infer a better return type for 'contextlib.contextmanager'."""
    # Be defensive, just in case.
    if ctx.arg_types and len(ctx.arg_types[0]) == 1:
        arg_type = get_proper_type(ctx.arg_types[0][0])
        default_return = get_proper_type(ctx.default_return_type)
        if (isinstance(arg_type, CallableType)
                and isinstance(default_return, CallableType)):
            # The stub signature doesn't preserve information about arguments so
            # add them back here.
            return default_return.copy_modified(
                arg_types=arg_type.arg_types,
                arg_kinds=arg_type.arg_kinds,
                arg_names=arg_type.arg_names,
                variables=arg_type.variables,
                is_ellipsis_args=arg_type.is_ellipsis_args)
    return ctx.default_return_type


def typed_dict_get_signature_callback(ctx: MethodSigContext) -> CallableType:
    """Try to infer a better signature type for TypedDict.get.

    This is used to get better type context for the second argument that
    depends on a TypedDict value type.
    """
    signature = ctx.default_signature
    if (isinstance(ctx.type, TypedDictType)
            and len(ctx.args) == 2
            and len(ctx.args[0]) == 1
            and isinstance(ctx.args[0][0], StrExpr)
            and len(signature.arg_types) == 2
            and len(signature.variables) == 1
            and len(ctx.args[1]) == 1):
        key = ctx.args[0][0].value
        value_type = get_proper_type(ctx.type.items.get(key))
        ret_type = signature.ret_type
        if value_type:
            default_arg = ctx.args[1][0]
            if (isinstance(value_type, TypedDictType)
                    and isinstance(default_arg, DictExpr)
                    and len(default_arg.items) == 0):
                # Caller has empty dict {} as default for typed dict.
                value_type = value_type.copy_modified(required_keys=set())
            # Tweak the signature to include the value type as context. It's
            # only needed for type inference since there's a union with a type
            # variable that accepts everything.
            tv = signature.variables[0]
            assert isinstance(tv, TypeVarType)
            return signature.copy_modified(
                arg_types=[signature.arg_types[0],
                           make_simplified_union([value_type, tv])],
                ret_type=ret_type)
    return signature


def typed_dict_get_callback(ctx: MethodContext) -> Type:
    """Infer a precise return type for TypedDict.get with literal first argument."""
    if (isinstance(ctx.type, TypedDictType)
            and len(ctx.arg_types) >= 1
            and len(ctx.arg_types[0]) == 1):
        keys = try_getting_str_literals(ctx.args[0][0], ctx.arg_types[0][0])
        if keys is None:
            return ctx.default_return_type

        output_types: List[Type] = []
        for key in keys:
            value_type = get_proper_type(ctx.type.items.get(key))
            if value_type is None:
                return ctx.default_return_type

            if len(ctx.arg_types) == 1:
                output_types.append(value_type)
            elif (len(ctx.arg_types) == 2 and len(ctx.arg_types[1]) == 1
                  and len(ctx.args[1]) == 1):
                default_arg = ctx.args[1][0]
                if (isinstance(default_arg, DictExpr) and len(default_arg.items) == 0
                        and isinstance(value_type, TypedDictType)):
                    # Special case '{}' as the default for a typed dict type.
                    output_types.append(value_type.copy_modified(required_keys=set()))
                else:
                    output_types.append(value_type)
                    output_types.append(ctx.arg_types[1][0])

        if len(ctx.arg_types) == 1:
            output_types.append(NoneType())

        return make_simplified_union(output_types)
    return ctx.default_return_type


def typed_dict_pop_signature_callback(ctx: MethodSigContext) -> CallableType:
    """Try to infer a better signature type for TypedDict.pop.

    This is used to get better type context for the second argument that
    depends on a TypedDict value type.
    """
    signature = ctx.default_signature
    str_type = ctx.api.named_generic_type('builtins.str', [])
    if (isinstance(ctx.type, TypedDictType)
            and len(ctx.args) == 2
            and len(ctx.args[0]) == 1
            and isinstance(ctx.args[0][0], StrExpr)
            and len(signature.arg_types) == 2
            and len(signature.variables) == 1
            and len(ctx.args[1]) == 1):
        key = ctx.args[0][0].value
        value_type = ctx.type.items.get(key)
        if value_type:
            # Tweak the signature to include the value type as context. It's
            # only needed for type inference since there's a union with a type
            # variable that accepts everything.
            tv = signature.variables[0]
            assert isinstance(tv, TypeVarType)
            typ = make_simplified_union([value_type, tv])
            return signature.copy_modified(
                arg_types=[str_type, typ],
                ret_type=typ)
    return signature.copy_modified(arg_types=[str_type, signature.arg_types[1]])


def typed_dict_pop_callback(ctx: MethodContext) -> Type:
    """Type check and infer a precise return type for TypedDict.pop."""
    if (isinstance(ctx.type, TypedDictType)
            and len(ctx.arg_types) >= 1
            and len(ctx.arg_types[0]) == 1):
        keys = try_getting_str_literals(ctx.args[0][0], ctx.arg_types[0][0])
        if keys is None:
            ctx.api.fail(message_registry.TYPEDDICT_KEY_MUST_BE_STRING_LITERAL, ctx.context)
            return AnyType(TypeOfAny.from_error)

        value_types = []
        for key in keys:
            if key in ctx.type.required_keys:
                ctx.api.msg.typeddict_key_cannot_be_deleted(ctx.type, key, ctx.context)

            value_type = ctx.type.items.get(key)
            if value_type:
                value_types.append(value_type)
            else:
                ctx.api.msg.typeddict_key_not_found(ctx.type, key, ctx.context)
                return AnyType(TypeOfAny.from_error)

        if len(ctx.args[1]) == 0:
            return make_simplified_union(value_types)
        elif (len(ctx.arg_types) == 2 and len(ctx.arg_types[1]) == 1
              and len(ctx.args[1]) == 1):
            return make_simplified_union([*value_types, ctx.arg_types[1][0]])
    return ctx.default_return_type


def typed_dict_setdefault_signature_callback(ctx: MethodSigContext) -> CallableType:
    """Try to infer a better signature type for TypedDict.setdefault.

    This is used to get better type context for the second argument that
    depends on a TypedDict value type.
    """
    signature = ctx.default_signature
    str_type = ctx.api.named_generic_type('builtins.str', [])
    if (isinstance(ctx.type, TypedDictType)
            and len(ctx.args) == 2
            and len(ctx.args[0]) == 1
            and isinstance(ctx.args[0][0], StrExpr)
            and len(signature.arg_types) == 2
            and len(ctx.args[1]) == 1):
        key = ctx.args[0][0].value
        value_type = ctx.type.items.get(key)
        if value_type:
            return signature.copy_modified(arg_types=[str_type, value_type])
    return signature.copy_modified(arg_types=[str_type, signature.arg_types[1]])


def typed_dict_setdefault_callback(ctx: MethodContext) -> Type:
    """Type check TypedDict.setdefault and infer a precise return type."""
    if (isinstance(ctx.type, TypedDictType)
            and len(ctx.arg_types) == 2
            and len(ctx.arg_types[0]) == 1
            and len(ctx.arg_types[1]) == 1):
        keys = try_getting_str_literals(ctx.args[0][0], ctx.arg_types[0][0])
        if keys is None:
            ctx.api.fail(message_registry.TYPEDDICT_KEY_MUST_BE_STRING_LITERAL, ctx.context)
            return AnyType(TypeOfAny.from_error)

        default_type = ctx.arg_types[1][0]

        value_types = []
        for key in keys:
            value_type = ctx.type.items.get(key)

            if value_type is None:
                ctx.api.msg.typeddict_key_not_found(ctx.type, key, ctx.context)
                return AnyType(TypeOfAny.from_error)

            # The signature_callback above can't always infer the right signature
            # (e.g. when the expression is a variable that happens to be a Literal str)
            # so we need to handle the check ourselves here and make sure the provided
            # default can be assigned to all key-value pairs we're updating.
            if not is_subtype(default_type, value_type):
                ctx.api.msg.typeddict_setdefault_arguments_inconsistent(
                    default_type, value_type, ctx.context)
                return AnyType(TypeOfAny.from_error)

            value_types.append(value_type)

        return make_simplified_union(value_types)
    return ctx.default_return_type


def typed_dict_delitem_callback(ctx: MethodContext) -> Type:
    """Type check TypedDict.__delitem__."""
    if (isinstance(ctx.type, TypedDictType)
            and len(ctx.arg_types) == 1
            and len(ctx.arg_types[0]) == 1):
        keys = try_getting_str_literals(ctx.args[0][0], ctx.arg_types[0][0])
        if keys is None:
            ctx.api.fail(message_registry.TYPEDDICT_KEY_MUST_BE_STRING_LITERAL, ctx.context)
            return AnyType(TypeOfAny.from_error)

        for key in keys:
            if key in ctx.type.required_keys:
                ctx.api.msg.typeddict_key_cannot_be_deleted(ctx.type, key, ctx.context)
            elif key not in ctx.type.items:
                ctx.api.msg.typeddict_key_not_found(ctx.type, key, ctx.context)
    return ctx.default_return_type


def typed_dict_update_signature_callback(ctx: MethodSigContext) -> CallableType:
    """Try to infer a better signature type for TypedDict.update."""
    signature = ctx.default_signature
    if (isinstance(ctx.type, TypedDictType)
            and len(signature.arg_types) == 1):
        arg_type = get_proper_type(signature.arg_types[0])
        assert isinstance(arg_type, TypedDictType)
        arg_type = arg_type.as_anonymous()
        arg_type = arg_type.copy_modified(required_keys=set())
        return signature.copy_modified(arg_types=[arg_type])
    return signature


def int_pow_callback(ctx: MethodContext) -> Type:
    """Infer a more precise return type for int.__pow__."""
    # int.__pow__ has an optional modulo argument,
    # so we expect 2 argument positions
    if (len(ctx.arg_types) == 2
            and len(ctx.arg_types[0]) == 1 and len(ctx.arg_types[1]) == 0):
        arg = ctx.args[0][0]
        if isinstance(arg, IntExpr):
            exponent = arg.value
        elif isinstance(arg, UnaryExpr) and arg.op == '-' and isinstance(arg.expr, IntExpr):
            exponent = -arg.expr.value
        else:
            # Right operand not an int literal or a negated literal -- give up.
            return ctx.default_return_type
        if exponent >= 0:
            return ctx.api.named_generic_type('builtins.int', [])
        else:
            return ctx.api.named_generic_type('builtins.float', [])
    return ctx.default_return_type


def int_neg_callback(ctx: MethodContext) -> Type:
    """Infer a more precise return type for int.__neg__.

    This is mainly used to infer the return type as LiteralType
    if the original underlying object is a LiteralType object
    """
    if isinstance(ctx.type, Instance) and ctx.type.last_known_value is not None:
        value = ctx.type.last_known_value.value
        fallback = ctx.type.last_known_value.fallback
        if isinstance(value, int):
            if is_literal_type_like(ctx.api.type_context[-1]):
                return LiteralType(value=-value, fallback=fallback)
            else:
                return ctx.type.copy_modified(last_known_value=LiteralType(
                    value=-value,
                    fallback=ctx.type,
                    line=ctx.type.line,
                    column=ctx.type.column,
                ))
    elif isinstance(ctx.type, LiteralType):
        value = ctx.type.value
        fallback = ctx.type.fallback
        if isinstance(value, int):
            return LiteralType(value=-value, fallback=fallback)
    return ctx.default_return_type


def tuple_mul_callback(ctx: MethodContext) -> Type:
    """Infer a more precise return type for tuple.__mul__ and tuple.__rmul__.

    This is used to return a specific sized tuple if multiplied by Literal int
    """
    if not isinstance(ctx.type, TupleType):
        return ctx.default_return_type

    arg_type = get_proper_type(ctx.arg_types[0][0])
    if isinstance(arg_type, Instance) and arg_type.last_known_value is not None:
        value = arg_type.last_known_value.value
        if isinstance(value, int):
            return ctx.type.copy_modified(items=ctx.type.items * value)
    elif isinstance(ctx.type, LiteralType):
        value = arg_type.value
        if isinstance(value, int):
            return ctx.type.copy_modified(items=ctx.type.items * value)

    return ctx.default_return_type
