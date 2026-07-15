import gc
import weakref

from artpm_agent.database.models import DatabaseManager, _ACTIVE_ENGINES


def test_engine_registry_does_not_keep_abandoned_managers_alive(tmp_path):
    baseline = len(_ACTIVE_ENGINES)
    manager = DatabaseManager(
        f"sqlite:///{(tmp_path / 'lifecycle.db').as_posix()}"
    )
    engine_ref = weakref.ref(manager.engine)
    finalizer = manager._engine_finalizer
    assert len(_ACTIVE_ENGINES) == baseline + 1

    del manager
    gc.collect()

    assert engine_ref() is None
    assert not finalizer.alive
    assert len(_ACTIVE_ENGINES) == baseline


def test_database_manager_context_closes_engine(tmp_path):
    with DatabaseManager(
        f"sqlite:///{(tmp_path / 'context.db').as_posix()}"
    ) as manager:
        engine = manager.engine
        finalizer = manager._engine_finalizer
        assert engine in _ACTIVE_ENGINES

    assert engine not in _ACTIVE_ENGINES
    assert not finalizer.alive
