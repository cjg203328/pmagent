"""Lazy-loading skill registry for improved startup performance.

Instead of instantiating all skills at Agent init time, this registry
defers instantiation until the skill is first requested.
"""
from __future__ import annotations

import logging
from threading import RLock
from typing import Any, Callable, Dict, Optional, Type

from .base_skill import BaseSkill

logger = logging.getLogger(__name__)


class LazySkillRegistry:
    """Registry that instantiates skills on first use."""

    def __init__(self):
        self._skill_classes: Dict[str, Type[BaseSkill]] = {}
        self._skill_factories: Dict[str, Callable[[], BaseSkill]] = {}
        self._skill_instances: Dict[str, BaseSkill] = {}
        self._lock = RLock()
        self._context: Optional[Dict[str, Any]] = None

    def register_class(self, skill_name: str, skill_class: Type[BaseSkill]) -> None:
        """Register a skill class (not instantiated yet).

        Args:
            skill_name: Unique skill identifier
            skill_class: Skill class (subclass of BaseSkill)
        """
        with self._lock:
            self._skill_classes[skill_name] = skill_class
            logger.debug(f"Registered skill class: {skill_name}")

    def register_factory(self, skill_name: str, factory: Callable[[], BaseSkill]) -> None:
        """Register a skill factory function.

        Args:
            skill_name: Unique skill identifier
            factory: Callable that returns a skill instance
        """
        with self._lock:
            self._skill_factories[skill_name] = factory
            logger.debug(f"Registered skill factory: {skill_name}")

    def set_context(self, context: Dict[str, Any]) -> None:
        """Set context for skill instantiation.

        Args:
            context: Shared context dictionary
        """
        self._context = context

    def get_skill(self, skill_name: str) -> BaseSkill:
        """Get skill instance (lazy instantiation).

        Args:
            skill_name: Skill identifier

        Returns:
            Skill instance

        Raises:
            KeyError: If skill not registered
        """
        with self._lock:
            # Return cached instance if exists
            if skill_name in self._skill_instances:
                return self._skill_instances[skill_name]

            # Instantiate from factory
            if skill_name in self._skill_factories:
                factory = self._skill_factories[skill_name]
                instance = factory()
                self._skill_instances[skill_name] = instance
                logger.info(f"Instantiated skill from factory: {skill_name}")
                return instance

            # Instantiate from class
            if skill_name in self._skill_classes:
                skill_class = self._skill_classes[skill_name]
                if self._context is None:
                    raise RuntimeError(
                        f"Cannot instantiate skill {skill_name}: context not set"
                    )
                instance = skill_class(self._context)
                self._skill_instances[skill_name] = instance
                logger.info(f"Instantiated skill from class: {skill_name}")
                return instance

            raise KeyError(f"Skill not registered: {skill_name}")

    def has_skill(self, skill_name: str) -> bool:
        """Check if skill is registered (class, factory, or instance).

        Args:
            skill_name: Skill identifier

        Returns:
            True if skill exists
        """
        with self._lock:
            return (
                skill_name in self._skill_instances
                or skill_name in self._skill_classes
                or skill_name in self._skill_factories
            )

    def list_skills(self) -> list[str]:
        """List all registered skill names.

        Returns:
            List of skill identifiers
        """
        with self._lock:
            return sorted(
                set(self._skill_classes.keys())
                | set(self._skill_factories.keys())
                | set(self._skill_instances.keys())
            )

    def get_instantiated_count(self) -> int:
        """Get count of instantiated skills (for monitoring).

        Returns:
            Number of skills currently in memory
        """
        with self._lock:
            return len(self._skill_instances)

    def clear_cache(self) -> None:
        """Clear cached skill instances (useful for testing).

        Keeps class/factory registrations but removes instances.
        """
        with self._lock:
            count = len(self._skill_instances)
            self._skill_instances.clear()
            logger.info(f"Cleared {count} cached skill instances")
