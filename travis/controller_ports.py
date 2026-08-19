"""Shared primitives for explicitly composed runtime collaborators."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import cast, get_origin, get_type_hints

_UNBOUND = object()


class ControllerBinding[ValueT]:
    """One explicitly named dependency cell shared at composition time."""

    __slots__ = ("_coerce", "_getter", "_setter", "_value")

    def __init__(
        self,
        value: ValueT | object = _UNBOUND,
        *,
        coerce: Callable[[object], ValueT] | None = None,
    ) -> None:
        self._coerce = coerce
        self._value = (
            coerce(value)
            if coerce is not None and value is not _UNBOUND
            else value
        )
        self._getter: Callable[[], ValueT] | None = None
        self._setter: Callable[[ValueT], None] | None = None

    def get(self) -> ValueT:
        if self._getter is not None:
            return self._getter()
        if self._value is _UNBOUND:
            raise AttributeError("controller dependency is not bound")
        return cast(ValueT, self._value)

    def set(self, value: object) -> None:
        if self._setter is not None:
            self._setter(cast(ValueT, value))
        else:
            self._value = self._coerce(value) if self._coerce is not None else value

    def swap(self, value: object) -> object:
        previous = self.get()
        self.set(value)
        return previous

    def bind_attribute(self, owner: object, attribute: str) -> None:
        def get_value() -> ValueT:
            return cast(ValueT, getattr(owner, attribute))

        def set_value(value: ValueT) -> None:
            setattr(owner, attribute, value)

        self._getter = get_value
        self._setter = set_value

    def bind_controller_attribute(self, owner: object, attribute: str) -> None:
        """Bind an owner member while preserving per-runtime compatibility overrides."""

        self._value = _UNBOUND

        def get_value() -> ValueT:
            if self._value is not _UNBOUND:
                return cast(ValueT, self._value)
            return cast(ValueT, getattr(owner, attribute))

        def set_value(value: ValueT) -> None:
            self._value = value

        self._getter = get_value
        self._setter = set_value


class ExplicitController[DependenciesT]:
    """Store one immutable responsibility-specific dependency record."""

    __slots__ = ("dependencies",)

    def __init__(self, dependencies: DependenciesT) -> None:
        self.dependencies = dependencies


class ControllerDependencyAttribute:
    """Expose one field from a controller's explicit dependency record."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __get__(self, instance: object | None, owner: type[object]) -> object:
        if instance is None:
            return self
        try:
            dependencies = object.__getattribute__(instance, "dependencies")
        except AttributeError:
            instance_dict = object.__getattribute__(instance, "__dict__")
            if self.name in instance_dict:
                return instance_dict[self.name]
            raise
        binding = getattr(dependencies, self.name)
        if not isinstance(binding, ControllerBinding):
            raise TypeError(f"controller dependency is not a binding: {self.name}")
        return binding.get()

    def __set__(self, instance: object, value: object) -> None:
        try:
            dependencies = object.__getattribute__(instance, "dependencies")
        except AttributeError:
            object.__getattribute__(instance, "__dict__")[self.name] = value
            return
        binding = getattr(dependencies, self.name)
        if not isinstance(binding, ControllerBinding):
            raise TypeError(f"controller dependency is not a binding: {self.name}")
        binding.set(value)


class RuntimeBindingAttribute:
    """Expose an explicit root binding without retaining the complete runtime."""

    __slots__ = ("binding_record_attribute", "name")

    def __init__(self, binding_record_attribute: str, name: str) -> None:
        self.binding_record_attribute = binding_record_attribute
        self.name = name

    def __get__(self, instance: object | None, owner: type[object]) -> object:
        if instance is None:
            return self
        binding_record = object.__getattribute__(instance, self.binding_record_attribute)
        binding = getattr(binding_record, self.name)
        return binding.get()

    def __set__(self, instance: object, value: object) -> None:
        binding_record = object.__getattribute__(instance, self.binding_record_attribute)
        binding = getattr(binding_record, self.name)
        binding.set(value)


def _runtime_controller_binding(instance: object, name: str) -> ControllerBinding[object] | None:
    for record_name in ("_session_bindings", "_interactive_bindings"):
        try:
            record = object.__getattribute__(instance, record_name)
            binding = getattr(record, name)
        except AttributeError:
            continue
        if isinstance(binding, ControllerBinding):
            return binding
    return None


class ControllerDelegate:
    """Expose one named member from an explicitly owned controller."""

    __slots__ = ("controller_name", "member_name")

    def __init__(self, controller_name: str, member_name: str) -> None:
        self.controller_name = controller_name
        self.member_name = member_name

    def __get__(self, instance: object | None, owner: type[object]) -> object:
        if instance is None:
            return self
        binding = _runtime_controller_binding(instance, self.member_name)
        if binding is not None:
            try:
                return binding.get()
            except AttributeError:
                pass
        controllers = object.__getattribute__(instance, "controllers")
        controller = object.__getattribute__(controllers, self.controller_name)
        descriptor = inspect.getattr_static(type(controller), self.member_name)
        if isinstance(descriptor, property):
            return descriptor.__get__(controller, type(controller))
        return getattr(controller, self.member_name)

    def __call__(self, *args: object, **kwargs: object) -> object:
        raise TypeError("controller delegates must be bound to a runtime instance")

    def __set__(self, instance: object, value: object) -> None:
        binding = _runtime_controller_binding(instance, self.member_name)
        if binding is not None:
            binding.set(value)
            return
        controllers = object.__getattribute__(instance, "controllers")
        controller = object.__getattribute__(controllers, self.controller_name)
        setattr(controller, self.member_name, value)


def _dataclass_field_names(dataclass_type: type[object]) -> tuple[str, ...]:
    raw_fields = getattr(dataclass_type, "__dataclass_fields__", None)
    if not isinstance(raw_fields, dict):
        raise TypeError(f"dependency record is not a dataclass: {dataclass_type!r}")
    names: list[str] = []
    for name in raw_fields:
        if not isinstance(name, str):
            raise TypeError("dataclass dependency field names must be strings")
        names.append(name)
    return tuple(names)


def controller_dependency_names(dependency_type: type[object]) -> tuple[str, ...]:
    """Return only dependency fields backed by explicit cells."""

    hints = get_type_hints(dependency_type)
    return tuple(
        name
        for name in _dataclass_field_names(dependency_type)
        if get_origin(hints[name]) is ControllerBinding
    )


def compose_controller_dependencies[DependenciesT](
    dependency_type: type[DependenciesT],
    bindings: object,
    **explicit_values: object,
) -> DependenciesT:
    """Build one narrow record from explicit binding fields and direct services."""

    values = [
        explicit_values[name]
        if name in explicit_values
        else getattr(bindings, name)
        for name in _dataclass_field_names(dependency_type)
    ]
    return dependency_type(*values)


def install_controller_dependency_attributes(
    owner: type[object],
    dependency_type: type[object],
) -> None:
    """Install descriptors only for fields declared by one dependency record."""

    for name in controller_dependency_names(dependency_type):
        if name not in owner.__dict__:
            setattr(owner, name, ControllerDependencyAttribute(name))


def install_controller_delegates(
    owner: type[object],
    members: dict[str, tuple[str, ...]],
) -> None:
    """Install named controller delegates without replacing explicit members."""

    for controller_name, names in members.items():
        for name in names:
            if name in owner.__dict__:
                raise ValueError(f"delegate would replace explicit runtime member: {name}")
            setattr(owner, name, ControllerDelegate(controller_name, name))


def install_runtime_binding_attributes(
    owner: type[object],
    *,
    binding_record_attribute: str,
    binding_type: type[object],
    delegated_names: frozenset[str],
) -> None:
    """Expose explicit per-instance binding fields on a compatibility runtime."""

    for name in _dataclass_field_names(binding_type):
        if name not in delegated_names and name not in owner.__dict__:
            setattr(
                owner,
                name,
                RuntimeBindingAttribute(binding_record_attribute, name),
            )


__all__ = (
    "ControllerBinding",
    "ControllerDelegate",
    "ControllerDependencyAttribute",
    "ExplicitController",
    "compose_controller_dependencies",
    "controller_dependency_names",
    "install_controller_delegates",
    "install_controller_dependency_attributes",
    "install_runtime_binding_attributes",
)
