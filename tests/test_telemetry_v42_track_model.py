from __future__ import annotations

import numpy as np
import pytest

from pitwall.telemetry import (
    ProjectionConfig,
    ProjectionHint,
    ProjectionStatus,
    TrackBuildConfig,
    TrackModelOutcome,
    Trajectory,
    build_track_model,
    project_to_track,
    project_trajectory,
)


def _circle_trajectories() -> list[Trajectory]:
    random = np.random.default_rng(420)
    angle = np.linspace(0.0, 2.0 * np.pi, 360, endpoint=False)
    base = np.column_stack((100.0 * np.cos(angle), 100.0 * np.sin(angle)))
    output: list[Trajectory] = []
    for index, shift in enumerate((0, 47, 121, 203)):
        points = np.roll(base, shift, axis=0) + random.normal(0.0, 0.12, base.shape)
        if index == 2:
            points = points[::-1]
        if index == 3:
            # One impossible world-position spike should be removed without
            # discarding the otherwise complete lap.
            points[90] += [500.0, -500.0]
        output.append(Trajectory(f"clean_{index}", points))
    outlier = np.column_stack((150.0 * np.cos(angle), 150.0 * np.sin(angle)))
    output.append(Trajectory("geometric_outlier", outlier))
    pit_mask = np.zeros(angle.shape, dtype=bool)
    pit_mask[40:80] = True
    output.append(Trajectory("pit_lap", base, pit_mask=pit_mask))
    return output


def _circle_result():
    return build_track_model(
        "test_circle",
        1,
        _circle_trajectories(),
        config=TrackBuildConfig(resample_points=180, min_clean_trajectories=3),
    )


def test_builds_publishable_robust_closed_centerline_from_shifted_reversed_laps() -> None:
    result = _circle_result()
    assert result.outcome is TrackModelOutcome.PUBLISHED
    assert result.model is not None
    model = result.model
    assert model.quality.publishable
    assert model.quality.clean_trajectories == 4
    assert model.quality.rejected_trajectories == 2
    assert model.length_m == pytest.approx(2.0 * np.pi * 100.0, rel=0.015)
    assert model.quality.closure_error_m is not None
    assert model.quality.closure_error_m < 0.1
    assert model.quality.p95_residual_m is not None
    # Reversing an even sampled loop can leave a half-bin phase offset, but the
    # robust path must still remain well within the publishability threshold.
    assert model.quality.p95_residual_m < 2.0
    assert model.quality.continuity_score == 1.0
    assert model.quality.coverage_ratio > 0.95
    assert model.quality.self_crossings == 0
    assert model.id.startswith("track_test_circle_v1_")
    assert len(model.checksum) == 64

    tangent_length = np.linalg.norm(model.tangents, axis=1)
    normal_length = np.linalg.norm(model.normals, axis=1)
    np.testing.assert_allclose(tangent_length, 1.0, atol=1e-9)
    np.testing.assert_allclose(normal_length, 1.0, atol=1e-9)
    np.testing.assert_allclose(
        np.sum(model.tangents * model.normals, axis=1), 0.0, atol=1e-9
    )
    reports = {report.trajectory_id: report for report in model.quality.trajectories}
    assert reports["clean_2"].accepted  # reverse-direction normalization
    assert reports["clean_3"].retained_samples == 359  # teleport rejected
    assert reports["geometric_outlier"].reason == "trajectory rejected as a geometric outlier"
    assert reports["pit_lap"].reason == "pit-lane coverage exceeds limit"


def test_model_checksum_and_geometry_are_independent_of_input_order_or_generator() -> None:
    trajectories = _circle_trajectories()
    config = TrackBuildConfig(resample_points=120, min_clean_trajectories=3)
    forward = build_track_model("test_circle", 4, (item for item in trajectories), config=config)
    reverse = build_track_model("test_circle", 4, reversed(trajectories), config=config)
    assert forward.model is not None and reverse.model is not None
    assert forward.model.checksum == reverse.model.checksum
    assert forward.model.id == reverse.model.id
    np.testing.assert_array_equal(forward.model.centerline, reverse.model.centerline)
    assert forward.quality == reverse.quality


def test_custom_rejection_hook_is_applied_and_audited_per_trajectory() -> None:
    trajectories = _circle_trajectories()[:3]
    called: list[str] = []

    def reject_first_sample(
        trajectory: Trajectory,
        points: np.ndarray,
        current_mask: np.ndarray,
    ) -> np.ndarray:
        called.append(trajectory.id)
        assert points.shape == (360, 2)
        result = current_mask.copy()
        result[0] = False
        return result

    result = build_track_model(
        "hooked_circle",
        1,
        trajectories,
        config=TrackBuildConfig(resample_points=120, min_clean_trajectories=3),
        rejection_hooks=(reject_first_sample,),
    )
    assert result.model is not None
    assert called == sorted(trajectory.id for trajectory in trajectories)
    assert all(
        report.retained_samples == 359 for report in result.quality.trajectories
    )


def test_insufficient_or_incomplete_sources_request_map_calibration() -> None:
    only_one = build_track_model(
        "lonely",
        1,
        _circle_trajectories()[:1],
        config=TrackBuildConfig(min_clean_trajectories=3),
    )
    assert only_one.outcome is TrackModelOutcome.MAP_CALIBRATION_REQUIRED
    assert only_one.model is None
    assert "need at least 3 clean trajectories" in only_one.quality.reasons[0]

    x = np.linspace(-100.0, 100.0, 200)
    open_line = np.column_stack((x, np.zeros_like(x)))
    incomplete = build_track_model(
        "open",
        1,
        [Trajectory(f"open_{index}", open_line) for index in range(3)],
        config=TrackBuildConfig(min_clean_trajectories=3),
    )
    assert incomplete.model is None
    assert incomplete.outcome is TrackModelOutcome.MAP_CALIBRATION_REQUIRED
    assert all(not report.accepted for report in incomplete.quality.trajectories)


def test_frenet_projection_returns_signed_offset_and_rejects_track_jump() -> None:
    result = _circle_result()
    assert result.model is not None
    model = result.model
    index = 20
    source = model.centerline[index] + model.normals[index] * 5.0
    projection = project_to_track(model, source)
    assert projection.status is ProjectionStatus.PROJECTED
    assert projection.s_m == pytest.approx(model.cumulative_s_m[index], abs=1.0)
    assert projection.n_m == pytest.approx(5.0, abs=0.15)
    assert projection.residual_m == pytest.approx(5.0, abs=0.15)
    assert 0.0 < projection.confidence <= 1.0

    opposite = model.centerline[model.centerline.shape[0] // 2]
    jump = project_to_track(
        model,
        opposite,
        hint=ProjectionHint(float(model.cumulative_s_m[0])),
        config=ProjectionConfig(
            local_search_radius_m=20.0,
            max_projection_distance_m=15.0,
            max_s_jump_m=40.0,
        ),
    )
    assert jump.status is ProjectionStatus.JUMP_REJECTED
    assert jump.confidence == 0.0
    assert "jump" in (jump.reason or "")


def test_temporal_local_search_resolves_crossing_ambiguity() -> None:
    angle = np.linspace(0.0, 2.0 * np.pi, 400, endpoint=False)
    figure_eight = np.column_stack(
        (100.0 * np.sin(angle), 70.0 * np.sin(angle) * np.cos(angle))
    )
    result = build_track_model(
        "figure_eight",
        1,
        [
            Trajectory(f"fig_{index}", np.roll(figure_eight, index * 31, axis=0))
            for index in range(3)
        ],
        config=TrackBuildConfig(
            resample_points=200,
            min_clean_trajectories=3,
            min_model_coverage=0.8,
            max_p95_residual_m=8.0,
            publishability_threshold=0.5,
        ),
    )
    assert result.model is not None
    assert result.outcome is TrackModelOutcome.MAP_CALIBRATION_REQUIRED
    assert result.quality.self_crossings > 0
    assert "centerline contains ambiguous self-crossings" in result.quality.reasons

    global_projection = project_to_track(result.model, [0.0, 0.0])
    assert global_projection.status is ProjectionStatus.AMBIGUOUS
    assert global_projection.s_m is not None
    local_projection = project_to_track(
        result.model,
        [0.0, 0.0],
        hint=ProjectionHint(global_projection.s_m),
        config=ProjectionConfig(
            local_search_radius_m=35.0,
            max_s_jump_m=45.0,
        ),
    )
    assert local_projection.status is ProjectionStatus.PROJECTED
    assert local_projection.used_local_search
    assert local_projection.s_m == pytest.approx(global_projection.s_m, abs=1.0)


def test_sequential_projection_carries_forward_temporal_context() -> None:
    result = _circle_result()
    assert result.model is not None
    model = result.model
    indices = np.arange(10, 30, 2)
    points = model.centerline[indices] + model.normals[indices] * 1.5
    projections = project_trajectory(model, points)
    assert all(item.status is ProjectionStatus.PROJECTED for item in projections)
    projected_s = np.asarray([item.s_m for item in projections], dtype=float)
    assert np.all(np.diff(projected_s) > 0)
    assert all(item.used_local_search for item in projections[1:])


def test_quality_threshold_can_require_manual_calibration_without_losing_preview() -> None:
    result = build_track_model(
        "strict_circle",
        1,
        _circle_trajectories(),
        config=TrackBuildConfig(
            resample_points=180,
            min_clean_trajectories=3,
            publishability_threshold=0.999999,
        ),
    )
    assert result.model is not None
    assert result.outcome is TrackModelOutcome.MAP_CALIBRATION_REQUIRED
    assert not result.quality.publishable
    assert "combined model quality is below the publishability threshold" in result.quality.reasons
