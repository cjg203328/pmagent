"""Tests for the evolution loop (Phase 3 reflection job)."""
from artpm_agent.memory.episode_store import Episode, EpisodeStore
from artpm_agent.memory.feedback_store import FeedbackStore
from artpm_agent.evolution.strategy_store import StrategyStore
from artpm_agent.evolution.scheduler import ReflectionScheduler
from artpm_agent.evolution.reflection import (
    ImprovementProposal,
    apply_proposal,
    assess_approval,
    generate_proposals,
    mine_episodes,
    run_reflection,
)


def _seed(store, handler, *, n_fail, n_ok, error_kind="vision", feedback=None):
    for i in range(n_fail):
        store.record(
            Episode(
                turn_id=f"f{i}",
                conversation_id="c",
                handler=handler,
                success=False,
                error_kind=error_kind,
                user_input_excerpt="x",
            )
        )
    for i in range(n_ok):
        store.record(
            Episode(
                turn_id=f"o{i}",
                conversation_id="c",
                handler=handler,
                success=True,
                user_input_excerpt="x",
                feedback=feedback,
            )
        )


def test_low_risk_strategy_auto_applies(tmp_path):
    ep = EpisodeStore(str(tmp_path / "ep.db"))
    fb = FeedbackStore(str(tmp_path / "fb.db"))
    st = StrategyStore(str(tmp_path / "st.db"))
    # 5 fail / 5 ok -> 50% -> low risk strategy -> auto-applied
    _seed(ep, "skill_handler", n_fail=5, n_ok=5)

    report = run_reflection(ep, fb, st)
    assert report.proposals_total >= 1
    assert report.applied_count >= 1
    assert report.pending_count == 0
    active = st.active()
    assert len(active) == 1
    assert "50%" in active[0].rule_text


def test_high_risk_stays_pending(tmp_path):
    ep = EpisodeStore(str(tmp_path / "ep.db"))
    fb = FeedbackStore(str(tmp_path / "fb.db"))
    st = StrategyStore(str(tmp_path / "st.db"))
    # 7 fail / 3 ok -> 70% -> medium risk -> pending (awaiting approval)
    _seed(ep, "skill_handler", n_fail=7, n_ok=3)

    report = run_reflection(ep, fb, st)
    assert report.applied_count == 0
    assert report.pending_count >= 1
    assert st.active() == []


def test_feedback_becomes_preference(tmp_path):
    ep = EpisodeStore(str(tmp_path / "ep.db"))
    fb = FeedbackStore(str(tmp_path / "fb.db"))
    st = StrategyStore(str(tmp_path / "st.db"))
    _seed(ep, "model_handler", n_fail=0, n_ok=1, feedback="👎 答非所问")

    run_reflection(ep, fb, st)
    avoids = fb.active(kind="avoid")
    assert len(avoids) == 1
    assert "答非所问" in avoids[0].content


def test_insufficient_samples_no_proposal(tmp_path):
    ep = EpisodeStore(str(tmp_path / "ep.db"))
    _seed(ep, "skill_handler", n_fail=2, n_ok=0)  # below MIN_SAMPLES
    stats = mine_episodes(ep)
    proposals = generate_proposals(stats)
    assert proposals == []


def test_assess_approval_mapping():
    assert assess_approval(ImprovementProposal(kind="strategy", risk="low")) == "none"
    assert assess_approval(ImprovementProposal(kind="strategy", risk="medium")) == "user"
    assert (
        assess_approval(ImprovementProposal(kind="routing", risk="high")) == "admin"
    )


def test_auto_approve_forces_apply(tmp_path):
    fb = FeedbackStore(str(tmp_path / "fb.db"))
    st = StrategyStore(str(tmp_path / "st.db"))
    proposal = ImprovementProposal(
        kind="strategy", target="x", risk="medium", payload={"rule_text": "r"}
    )
    assert apply_proposal(proposal, st, fb, auto_approve=True) is True
    assert proposal.status == "applied"
    assert st.active()


def test_scheduler_reflects_only_episodes_added_since_last_run(tmp_path):
    ep = EpisodeStore(str(tmp_path / "ep.db"))
    fb = FeedbackStore(str(tmp_path / "fb.db"))
    st = StrategyStore(str(tmp_path / "st.db"))
    scheduler = ReflectionScheduler(str(tmp_path / "reflection.db"))

    # The first run consumes the initial backlog and creates one strategy.
    _seed(ep, "skill_handler", n_fail=5, n_ok=5)
    first = scheduler.run_if_due(
        ep,
        fb,
        st,
        interval_minutes=0,
        min_new_episodes=5,
    )
    assert first is not None
    assert first.episodes_analyzed == 10
    assert len(st.active()) == 1

    # The next run sees only the newly-added successful episodes.  If the
    # scheduler re-mined the full history, the old 50% failure pattern would
    # produce a duplicate strategy here.
    for i in range(5):
        ep.record(
            Episode(
                turn_id=f"new-ok-{i}",
                conversation_id="c",
                handler="skill_handler",
                success=True,
                user_input_excerpt="x",
            )
        )
    second = scheduler.run_if_due(
        ep,
        fb,
        st,
        interval_minutes=0,
        min_new_episodes=5,
    )
    assert second is not None
    assert second.episodes_analyzed == 5
    assert second.proposals_total == 0
    assert len(st.active()) == 1
