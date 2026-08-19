"""Shared primitives for explicitly composed runtime collaborators."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable
from dataclasses import dataclass

_UNBOUND = object()


@dataclass(slots=True)
class _Binding:
    value: object = _UNBOUND
    getter: Callable[[], object] | None = None
    setter: Callable[[object], None] | None = None

    def read(self, name: str) -> object:
        if self.getter is not None:
            return self.getter()
        if self.value is _UNBOUND:
            raise AttributeError(f"controller dependency is not bound: {name}")
        return self.value

    def write(self, value: object) -> None:
        if self.setter is not None:
            self.setter(value)
        else:
            self.value = value


class ControllerBindingRegistry:
    """Shared cells and named collaborator bindings owned by a composition root."""

    __slots__ = ("_bindings",)

    def __init__(self, names: Iterable[str]) -> None:
        self._bindings = {name: _Binding() for name in names}

    def read(self, name: str) -> object:
        return self._bindings[name].read(name)

    def write(self, name: str, value: object) -> None:
        self._bindings[name].write(value)

    def bind_owner(self, name: str, owner: object) -> None:
        binding = self._bindings[name]
        binding.getter = lambda: getattr(owner, name)
        binding.setter = lambda value: setattr(owner, name, value)

    def bind_attribute(self, name: str, owner: object, attribute: str) -> None:
        binding = self._bindings[name]
        binding.getter = lambda: getattr(owner, attribute)
        binding.setter = lambda value: setattr(owner, attribute, value)

    def port(self, names: Iterable[str]) -> ControllerPort:
        return ControllerPort(self, frozenset(names))


class ControllerPort:
    """An allowlisted controller dependency surface with no runtime reference."""

    __slots__ = ("_names", "_registry")

    def __init__(self, registry: ControllerBindingRegistry, names: frozenset[str]) -> None:
        self._registry = registry
        self._names = names

    def read(self, name: str) -> object:
        if name not in self._names:
            raise AttributeError(f"controller dependency is not declared: {name}")
        return self._registry.read(name)

    def write(self, name: str, value: object) -> None:
        if name not in self._names:
            raise AttributeError(f"controller dependency is not declared: {name}")
        self._registry.write(name, value)

    @property
    def declared_names(self) -> frozenset[str]:
        return self._names


class RuntimeStateAttribute:
    """Route composition-root state through shared controller cells."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __get__(self, instance: object | None, owner: type[object]) -> object:
        if instance is None:
            return self
        registry = object.__getattribute__(instance, "_controller_bindings")
        return registry.read(self.name)

    def __set__(self, instance: object, value: object) -> None:
        registry = object.__getattribute__(instance, "_controller_bindings")
        registry.write(self.name, value)


@dataclass(frozen=True, slots=True)
class ControllerDependencies[ControllerPortT]:
    """The named structural port consumed by one controller domain."""

    port: ControllerPortT


class DependencyAttribute:
    """An explicitly installed attribute on a controller's narrow port surface."""

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
        port = object.__getattribute__(dependencies, "port")
        return port.read(self.name)

    def __set__(self, instance: object, value: object) -> None:
        try:
            dependencies = object.__getattribute__(instance, "dependencies")
        except AttributeError:
            object.__getattribute__(instance, "__dict__")[self.name] = value
            return
        port = object.__getattribute__(dependencies, "port")
        port.write(self.name, value)


class ExplicitController[DependenciesT]:
    """Base class that stores an immutable responsibility-specific dependency record."""

    __slots__ = ("dependencies",)

    def __init__(self, dependencies: DependenciesT) -> None:
        self.dependencies = dependencies


class ControllerDelegate:
    """Expose one named member from an explicitly owned controller."""

    __slots__ = ("controller_name", "member_name")

    def __init__(self, controller_name: str, member_name: str) -> None:
        self.controller_name = controller_name
        self.member_name = member_name

    def __get__(self, instance: object | None, owner: type[object]) -> object:
        if instance is None:
            return self
        controllers = object.__getattribute__(instance, "controllers")
        controller = object.__getattribute__(controllers, self.controller_name)
        descriptor = inspect.getattr_static(type(controller), self.member_name)
        if isinstance(descriptor, property):
            return descriptor.__get__(controller, type(controller))
        return getattr(controller, self.member_name)

    def __call__(self, *args: object, **kwargs: object) -> object:
        raise TypeError("controller delegates must be bound to a runtime instance")

    def __set__(self, instance: object, value: object) -> None:
        controllers = object.__getattribute__(instance, "controllers")
        controller = object.__getattribute__(controllers, self.controller_name)
        setattr(controller, self.member_name, value)


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


def install_explicit_port_attributes(owner: type[object], names: tuple[str, ...]) -> None:
    """Install only the attributes declared for ``owner``'s domain contract."""

    for name in names:
        if name not in owner.__dict__:
            setattr(owner, name, DependencyAttribute(name))


def install_runtime_state_attributes(
    owner: type[object],
    names: Iterable[str],
    delegated_names: frozenset[str],
) -> None:
    for name in names:
        if name not in delegated_names and name not in owner.__dict__:
            setattr(owner, name, RuntimeStateAttribute(name))


__all__ = (
    "ControllerDelegate",
    "ControllerDependencies",
    "ControllerBindingRegistry",
    "ControllerPort",
    "DependencyAttribute",
    "ExplicitController",
    "install_controller_delegates",
    "install_explicit_port_attributes",
    "install_runtime_state_attributes",
)
