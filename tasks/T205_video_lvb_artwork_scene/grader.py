"""T205_video_lvb_artwork_scene grader — identify another scene of the artwork and extract a frame."""

from __future__ import annotations

from typing import Any

from claw_eval.graders.base import AbstractGrader
from claw_eval.graders.multimodal_common import MultimodalGraderMixin
from claw_eval.graders.visual_grader import VisualGraderMixin
from claw_eval.models.task import TaskDefinition
from claw_eval.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class Lvb004Grader(AbstractGrader, MultimodalGraderMixin, VisualGraderMixin):
    """Grade extracted artwork scene frame (1.0).

    - image_score (1.0): file exists (0.1) + visual judge confirms correct scene (0.9).
    """

    OUTPUT_FILE = "/workspace/artwork_frame.png"

    VISUAL_RUBRIC = """\
Evaluate this extracted video frame:

The frame should show another scene where the artwork of "a person resting their hand on their forehead \
on yellowish paper" appeared. According to the ground truth, this scene shows two drawings side by side: \
the "person resting their hand on their forehead" drawing on the right, another drawing on the left, \
and a bookshelf visible in the background.

Scoring:
- Score 1.0 if the frame clearly shows the described artwork (person resting hand on forehead on yellowish paper) \
alongside another drawing, with a bookshelf in the background.
- Score 0.5 if the frame shows the described artwork in a different context (not the solo center-screen shot), \
but does not clearly show both drawings and the bookshelf.
- Score 0.0 if the artwork is not visible, or the frame is from the original center-screen close-up shot only."""

    def grade(
        self,
        messages: list[TraceMessage],
        dispatches: list[ToolDispatch],
        task: TaskDefinition,
        audit_data: dict[str, dict] | None = None,
        judge: Any | None = None,
        media_events: list[MediaLoad] | None = None,
        env_snapshot: dict | None = None,
    ) -> DimensionScores:
        scores = DimensionScores()
        scores.safety = 1.0

        # --- Image part (1.0): file exists (0.1) + visual judge (0.9) ---
        image_score = 0.0
        file_exists = self.check_file_exists(env_snapshot, self.OUTPUT_FILE)
        if file_exists:
            image_score += 0.1

            png_entry = (env_snapshot or {}).get(f"file:{self.OUTPUT_FILE}", {})
            png_b64 = (
                png_entry.get("content", "")
                if png_entry.get("encoding") == "base64"
                else ""
            )
            if png_b64 and judge and hasattr(judge, "evaluate_visual"):
                result = judge.evaluate_visual(
                    rubric=self.VISUAL_RUBRIC,
                    reference_images_b64=[],
                    candidate_images_b64=[png_b64],
                    context="Extracted frame showing the artwork scene with both drawings and a bookshelf.",
                )
                visual_score = result.score if result else 0.0
                image_score += 0.9 * visual_score

        scores.completion = round(image_score, 2)
        scores.robustness = self.compute_robustness(dispatches)
        scores.efficiency_turns = len(
            [m for m in messages if m.message.role == "assistant"]
        )
        return scores
