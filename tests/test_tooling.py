import hashlib
import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

CI_WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"


def _load_ci_workflow() -> dict:
    """Parse .github/workflows/ci.yml.

    Shared by TestCIWorkflow and TestReleaseWorkflow, which guard two jobs of the
    same file: a second loader is exactly the drift these classes exist to catch.
    pyyaml is a dev-only dependency, imported lazily so a missing parser fails
    those classes rather than module collection.
    """
    import yaml

    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


class TestCIWorkflow:
    """The GitHub Actions CI workflow ships in .github/workflows/ci.yml.

    CI fails *silently*: a dropped step or a runner swapped to Linux (where the
    darwin-only mlx the pipeline runs on can't install) would still show a green
    check on whatever it does run.
    These assertions pin the invariants that keep CI a faithful mirror of the
    documented local gates.
    """

    WORKFLOW = CI_WORKFLOW

    def _workflow(self) -> dict:
        return _load_ci_workflow()

    def _run_commands(self) -> str:
        steps = self._workflow()["jobs"]["check"]["steps"]
        return "\n".join(step["run"] for step in steps if "run" in step)

    def test_workflow_exists_and_parses(self) -> None:
        assert self.WORKFLOW.is_file()
        self._workflow()  # raises yaml.YAMLError on a syntax error

    # The Apple Silicon runner labels GitHub currently supports. An allow-list
    # rather than the `major >= 14` floor this replaces, which was satisfiable by
    # two separate broken states: it stayed green on `macos-14` right through that
    # image's deprecation (brownout failures from 2026-10-05, retired 2026-11-02),
    # and it admits the x64 `macos-*-intel` / `macos-*-large` labels, where the
    # arm64-only mlx wheels don't install at all. GitHub keeps only the latest two
    # OS versions, so the valid set is a moving window: when an image is deprecated
    # (actions/runner-images issues), move this list forward — don't widen it back
    # into a floor, which is what let a retiring runner pass unnoticed.
    APPLE_SILICON_RUNNERS = frozenset({"macos-latest", "macos-15", "macos-26"})

    def test_runs_on_apple_silicon(self) -> None:
        # Load-bearing: the PDF pipeline runs on mlx/mlx-metal, which uv.lock pins
        # to sys_platform == 'darwin', so a Linux runner can't install it (plan
        # §2.1). But `sys_platform == 'darwin'` is true on Intel macOS too, where
        # the arm64-only mlx wheels won't install.
        runs_on = self._workflow()["jobs"]["check"]["runs-on"]
        assert runs_on in self.APPLE_SILICON_RUNNERS, (
            f"{runs_on!r} is not a supported Apple Silicon runner label; expected "
            f"one of {sorted(self.APPLE_SILICON_RUNNERS)}"
        )

    def test_delegates_to_the_shared_quality_gate(self) -> None:
        # The four commands documented in CLAUDE.md / README "Development" live in
        # scripts/gate.sh so that CI and the Claude Code Stop hook cannot drift
        # apart; TestHooksConfig pins what the script actually runs. Spelling them
        # out here again would reintroduce precisely that drift.
        assert "scripts/gate.sh" in self._run_commands()

    def test_install_is_lockfile_pinned(self) -> None:
        # --locked makes CI fail on a stale lockfile instead of silently resolving
        # a different dependency set than uv.lock records.
        assert "uv sync --locked" in self._run_commands()

    def test_triggers_on_push_to_main_and_pull_requests(self) -> None:
        # PyYAML (YAML 1.1) parses the bare `on:` key as the boolean True, so the
        # trigger block lands under the True key rather than the string "on".
        data = self._workflow()
        triggers = data.get("on", data.get(True)) or {}
        assert "main" in triggers["push"]["branches"]
        assert "pull_request" in triggers

    def test_every_action_is_pinned_to_a_commit_sha(self) -> None:
        # A tag can be moved to new code after review; a commit SHA cannot (plan
        # §3.13 rule 9). The `# v…` comment is required too, since a bare SHA
        # says nothing about which release it is. Read from the raw text because
        # YAML parsing discards comments.
        uses = [
            line.split("uses:", 1)[1].strip()
            for line in self.WORKFLOW.read_text(encoding="utf-8").splitlines()
            if line.lstrip().removeprefix("- ").startswith("uses:")
        ]
        assert uses, "no `uses:` lines found; the parser no longer matches ci.yml"
        unpinned = [
            u
            for u in uses
            if not re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40} # v\S+", u)
        ]
        assert not unpinned, f"not pinned to a SHA with a `# v…` comment: {unpinned}"


# Stub executables for the subprocess-driven suites below (TestReleaseWorkflow,
# TestHooksConfig). Behavior is driven entirely by $STUB_* environment variables
# so the files themselves never change — see the `stub_bin` fixture for why that
# matters. Every variable is always set by the helpers; an unset one would make
# `exit ""` a shell error rather than a readable test failure.
_STUB_SOURCES = {
    "gh": """#!/bin/sh
printf '%s\\n' "gh $*" >>"$STUB_LOG"
case "$*" in
"api "*) printf '%s\\n' "$STUB_PROBE_ERROR" >&2; exit "$STUB_PROBE_EXIT" ;;
"release list"*) printf '%s' "$STUB_DRAFT"; exit "$STUB_LIST_EXIT" ;;
"release create "*) exit "$STUB_CREATE_EXIT" ;;
"release edit "*) exit "$STUB_EDIT_EXIT" ;;
esac
exit 0
""",
    "uv": """#!/bin/sh
printf '%s\\n' "uv $*" >>"$STUB_LOG"
[ -n "$STUB_UV_OUTPUT" ] && printf '%s\\n' "$STUB_UV_OUTPUT"
exit "$STUB_UV_EXIT"
""",
}


@pytest.fixture(scope="session")
def stub_bin(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The stub executables, written — and so scanned — once per session.

    macOS scans every *newly created* executable the first time it runs. Measured
    on this machine: 241 ms for a freshly written script against 4 ms to re-run
    one already scanned, or to run it through a fresh symlink. Writing throwaway
    `gh`/`uv` stubs per test made that the dominant cost of the entire suite —
    ~17 s of a 21 s gate, none of it the tests' own logic, and paid again on
    every Stop hook.

    So the stubs are written once and varied through the environment rather than
    by regenerating their source. Tests still get a private bin directory and
    symlink in only the tools that case wants, which keeps `omit=` honest: a tool
    left out is genuinely unreachable, not merely unstubbed.
    """
    bin_dir = tmp_path_factory.mktemp("stubs")
    for tool, source in _STUB_SOURCES.items():
        stub = bin_dir / tool
        stub.write_text(source, encoding="utf-8")
        stub.chmod(0o755)
    return bin_dir


def _link_stubs(bin_dir: Path, stub_bin: Path, *tools: str) -> None:
    """Symlink the named stubs into a test's private bin directory."""
    bin_dir.mkdir(exist_ok=True)
    for tool in tools:
        link = bin_dir / tool
        if not link.exists():  # the memo cases run the same repo twice
            link.symlink_to(stub_bin / tool)


class TestReleaseWorkflow:
    """Auto-tag-and-publish ships in .github/workflows/ci.yml + scripts/release.sh.

    The silent-degradation guard with the least forgiving failure mode, alongside
    TestCIWorkflow and TestHooksConfig. Automation that stops
    firing is indistinguishable from a stretch with no version bumps, and
    automation that fires from the wrong place — a pull request, a red gate, an
    unmerged commit — has already published a tag by the time anyone looks. None
    of it is reachable by running the app.
    """

    ROOT = Path(__file__).parent.parent
    WORKFLOW = CI_WORKFLOW
    SCRIPT = ROOT / "scripts" / "release.sh"
    _stub_bin: Path

    @pytest.fixture(autouse=True)
    def _use_stub_bin(self, stub_bin: Path) -> None:
        # Autouse so the 21 test signatures below stay about the release logic
        # rather than carrying a fixture they only forward to the helper.
        self._stub_bin = stub_bin

    def _workflow(self) -> dict:
        return _load_ci_workflow()

    def _job(self) -> dict:
        return self._workflow()["jobs"]["release"]

    def test_release_script_is_executable(self) -> None:
        # The workflow invokes it directly, not through `sh`, so a dropped +x bit
        # breaks the release with a green gate above it.
        assert self.SCRIPT.is_file()
        assert os.access(self.SCRIPT, os.X_OK), "scripts/release.sh is not executable"

    def test_release_job_delegates_to_the_script(self) -> None:
        # Inlining it back into YAML would put every behavioral assertion below
        # out of reach: an escaped one-liner cannot be run or shellchecked.
        runs = "\n".join(step["run"] for step in self._job()["steps"] if "run" in step)
        assert "scripts/release.sh" in runs

    def test_release_requires_a_green_gate(self) -> None:
        # A tag is public the moment it exists and is what users install; cutting
        # one from a build that failed lint, types or tests is worse than not
        # cutting one at all.
        needs = self._job()["needs"]
        assert "check" in ([needs] if isinstance(needs, str) else needs)

    def test_release_only_fires_on_a_push_to_main(self) -> None:
        # Without the event guard the job would also run on pull_request, where a
        # fork could get a version bump tagged without review.
        condition = self._job()["if"]
        assert "github.event_name == 'push'" in condition
        assert "refs/heads/main" in condition

    def test_only_the_release_job_can_write(self) -> None:
        # Least privilege, and the reason the token grant sits on the job rather
        # than the workflow: `check` runs a pull request's code, including a
        # fork's, and must not be able to push a tag.
        workflow = self._workflow()
        assert workflow["permissions"]["contents"] == "read"
        assert workflow["jobs"]["release"]["permissions"]["contents"] == "write"
        assert "permissions" not in workflow["jobs"]["check"]

    def test_release_is_never_cancelled_in_flight(self) -> None:
        # Two pushes to main in quick succession race for the same tag. Cancelling
        # the older run is the wrong resolution: it can land between the draft and
        # the publish, stranding a draft that then blocks every later release.
        concurrency = self._job()["concurrency"]
        assert concurrency["group"]
        assert concurrency["cancel-in-progress"] is False

    def test_no_job_interpolates_into_the_shell(self) -> None:
        # ${{ }} inside `run:` is substituted textually before any shell sees it,
        # which is the whole of GitHub Actions script injection. Checked across
        # every job, not just this one: `release` is already confined to pushes to
        # main, while `check` is the job that runs a fork's pull-request code with
        # attacker-controlled github.head_ref / PR title in scope.
        for name, job in self._workflow()["jobs"].items():
            for step in job["steps"]:
                assert "${{" not in step.get("run", ""), (
                    f"interpolation inside run: in job {name!r}"
                )

    def test_release_job_is_given_a_token_and_a_repository(self) -> None:
        # GH_REPO names the repository outright instead of leaving gh to infer it
        # from the checkout's git remote; unset outside Actions, where inference is
        # what you want.
        step = next(
            step
            for step in self._job()["steps"]
            if step.get("run", "").strip().endswith("release.sh")
        )
        assert "GH_TOKEN" in step["env"]
        assert "GH_REPO" in step["env"]

    # There is deliberately no test asserting uv.lock's recorded project version
    # matches pyproject.toml's. uv.lock does carry that version, and CI's
    # `uv sync --locked` does fail on a stale one — but every gate command runs
    # under `uv run`, which re-locks before pytest is even imported (verified: a
    # bump to 0.2.0 left uv.lock reading 0.2.0 by the time the assertion ran, and
    # reverting pyproject.toml silently rewrote it back). Such an assertion can
    # therefore never fail from this side, and the real mistake it would be aimed
    # at — committing pyproject.toml without the regenerated uv.lock — is git
    # hygiene that no in-process test can observe. See the release procedure in
    # CLAUDE.md, which documents the coupling instead.

    def _run_release(
        self,
        tmp_path: Path,
        *,
        tag_exists: bool = False,
        probe_error: str = "gh: Not Found (HTTP 404)",
        existing_draft: bool = False,
        version: str = "9.9.9",
        uv_exit: int = 0,
        create_exit: int = 0,
        edit_exit: int = 0,
        list_exit: int = 0,
        sha: str | None = "c0ffeeb",
        actions: bool = True,
        omit: str | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        """Run release.sh in a throwaway repo with `gh` and `uv` stubbed.

        Source assertions are as weak here as they were for gate.sh, and for a
        sharper reason: the script spells `--draft` twice — once to create the
        draft, once as `--draft=false` to publish it — so a substring check
        cannot distinguish the two-step publish from a create that never
        publishes, which is exactly the mutation that would leave every release
        sitting unpublished. "Exits 0 when already tagged" is pure control flow
        with no distinctive string at all. Stubbing also keeps the test hermetic:
        the real script publishes a real release.

        Both stubs log their argv, so what was *invoked* is assertable and not
        just what came back — `uv version` and `uv version --short` return the
        same thing here but not in production.

        Returns the completed process and the stub calls it recorded, in order,
        each prefixed with the tool name.
        """
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        # Symlinked, not copied: a copy is a brand-new executable and costs a
        # ~240 ms scan on macOS (see `stub_bin`). The script still resolves the
        # throwaway repo, since it locates its root from the *working directory*
        # via `git rev-parse --show-toplevel`, and its `dirname $0` fallback is
        # pure string work that lands in tmp_path either way.
        (scripts / "release.sh").symlink_to(self.SCRIPT)

        log = tmp_path / "calls.log"
        bin_dir = tmp_path / "bin"
        _link_stubs(bin_dir, self._stub_bin, *(t for t in _STUB_SOURCES if t != omit))

        # A stubbed-out tool has to be unreachable, not merely unstubbed, so the
        # PATH drops everything but the stubs and the system tools git needs.
        path = f"{bin_dir}" if omit else f"{bin_dir}:{os.environ['PATH']}"
        # The stubs dispatch on these rather than on regenerated source. `release
        # list` emits one draft tag per line, matching the --jq the script asks for.
        env: dict[str, str] = {
            **os.environ,
            "PATH": f"{path}:/usr/bin:/bin",
            "STUB_LOG": str(log),
            "STUB_PROBE_ERROR": probe_error,
            "STUB_PROBE_EXIT": "0" if tag_exists else "1",
            "STUB_DRAFT": f"v{version}" if existing_draft else "",
            "STUB_LIST_EXIT": str(list_exit),
            "STUB_CREATE_EXIT": str(create_exit),
            "STUB_EDIT_EXIT": str(edit_exit),
            "STUB_UV_OUTPUT": version,
            "STUB_UV_EXIT": str(uv_exit),
        }
        env.pop("GITHUB_SHA", None)
        env.pop("GITHUB_ACTIONS", None)
        if sha is not None:
            env["GITHUB_SHA"] = sha
        if actions:
            env["GITHUB_ACTIONS"] = "true"

        result = subprocess.run(
            [str(scripts / "release.sh")],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,  # a non-zero exit is the thing under test
            env=env,
        )
        calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        return result, calls

    @staticmethod
    def _released(calls: list[str]) -> bool:
        return any(call.startswith("gh release create") for call in calls)

    def test_an_already_tagged_version_releases_nothing(self, tmp_path: Path) -> None:
        # The idempotence contract: every push to main runs this, and all but the
        # ones that bumped the version must be no-ops that still exit 0, or the
        # job goes red on ordinary commits.
        result, calls = self._run_release(tmp_path, tag_exists=True)
        assert result.returncode == 0
        assert not self._released(calls), "re-released an already-tagged version"

    def test_an_untagged_version_is_drafted_then_published(
        self, tmp_path: Path
    ) -> None:
        # Both halves matter. Draft-only leaves the release invisible and, because
        # a draft carries no git tag, re-attempts it forever; publishing without
        # drafting first gives up the property that a failure mid-release leaves
        # no tag behind to be mistaken for a completed one.
        result, calls = self._run_release(tmp_path)
        assert result.returncode == 0
        create = next(
            i for i, c in enumerate(calls) if c.startswith("gh release create")
        )
        publish = next(
            i for i, c in enumerate(calls) if c.startswith("gh release edit")
        )
        assert create < publish, "published before drafting"
        assert "--draft" in calls[create]
        assert "--draft=false" in calls[publish]

    def test_the_version_is_read_with_the_short_flag(self, tmp_path: Path) -> None:
        # Load-bearing and otherwise untestable: bare `uv version` prints
        # "<name> <version>", which the plausibility guard would then refuse,
        # bricking releases permanently. The stub returns the same string either
        # way, so only the recorded argv can tell them apart.
        _, calls = self._run_release(tmp_path)
        assert "uv version --short" in calls

    def test_notes_are_generated_and_the_title_matches_the_tag(
        self, tmp_path: Path
    ) -> None:
        # --generate-notes is what makes this a release rather than a bare tag;
        # the explicit title pins the v<version> convention every release so far
        # follows, which --generate-notes would otherwise synthesize for itself.
        _, calls = self._run_release(tmp_path, version="1.2.3")
        create = next(c for c in calls if c.startswith("gh release create"))
        assert "--generate-notes" in create
        assert "--title v1.2.3" in create

    def test_the_tag_is_the_pyproject_version_prefixed_with_v(
        self, tmp_path: Path
    ) -> None:
        _, calls = self._run_release(tmp_path, version="1.2.3")
        assert any(c.startswith("gh release create v1.2.3 ") for c in calls)

    def test_the_existing_tag_probe_is_an_exact_match(self, tmp_path: Path) -> None:
        # git/ref/tags/<tag> resolves one ref; the plural git/refs/tags/<tag> form
        # prefix-matches — verified against the live API, git/refs/tags/v0.1
        # answers 200 off the existing v0.1.0 — so a bump to 0.1 would report
        # itself already released and silently never ship.
        _, calls = self._run_release(tmp_path, version="1.2.3")
        probe = next(c for c in calls if c.startswith("gh api"))
        assert "git/ref/tags/v1.2.3" in probe
        assert "git/refs/tags/" not in probe

    def test_an_inconclusive_probe_refuses_to_release(self, tmp_path: Path) -> None:
        # Only a definite 404 means "not released yet". A 5xx, a rate limit or an
        # expired token fails the probe identically, and reading those as "absent"
        # would re-release a version that already shipped.
        result, calls = self._run_release(
            tmp_path, probe_error="gh: Bad credentials (HTTP 401)"
        )
        assert result.returncode != 0
        assert not self._released(calls)

    def test_the_release_targets_the_commit_ci_validated(self, tmp_path: Path) -> None:
        # Defaulting to the branch tip would tag whatever landed while the gate
        # was running — an untested commit, under a tag that says it passed.
        _, calls = self._run_release(tmp_path, sha="deadbee")
        create = next(c for c in calls if c.startswith("gh release create"))
        assert "--target deadbee" in create

    def test_a_stranded_draft_is_adopted_rather_than_duplicated(
        self, tmp_path: Path
    ) -> None:
        # A draft carries no tag, so the probe cannot see one and GitHub accepts a
        # *second* draft for the same tag_name. Creating a rival leaves two drafts
        # racing for one tag, with `gh release edit` resolving whichever it finds
        # first — so the tag can land on the abandoned run's commit. Adopt and
        # retarget instead.
        result, calls = self._run_release(
            tmp_path, existing_draft=True, version="1.2.3"
        )
        assert result.returncode == 0
        assert not self._released(calls), "created a rival draft for the same tag"
        publish = next(c for c in calls if c.startswith("gh release edit"))
        assert "--target c0ffeeb" in publish, "adopted the draft without retargeting it"
        assert "--draft=false" in publish

    def test_a_failed_publish_names_the_recovery(self, tmp_path: Path) -> None:
        # The one state needing a human. Asserting merely on "draft" would pass on
        # the *create* failure's message too — this pins the sentence that says
        # how to unstick the pipeline.
        result, _ = self._run_release(tmp_path, edit_exit=1)
        assert result.returncode != 0
        assert "publish or delete the draft" in result.stderr

    def test_a_failed_draft_fails(self, tmp_path: Path) -> None:
        result, _ = self._run_release(tmp_path, create_exit=1)
        assert result.returncode != 0

    def test_an_unlistable_release_set_refuses_to_release(self, tmp_path: Path) -> None:
        # Without the draft listing there is no way to tell a first attempt from a
        # retry, and guessing "first attempt" is what creates the rival draft.
        result, calls = self._run_release(tmp_path, list_exit=1)
        assert result.returncode != 0
        assert not self._released(calls)

    @pytest.mark.parametrize(
        "reported",
        [
            pytest.param("uv 0.6.14", id="uv-own-version"),
            pytest.param("granite-document-intelligence 9.9.9", id="name-and-version"),
            pytest.param("0.1.0 (from pyproject.toml)", id="decorated"),
            pytest.param("", id="empty"),
        ],
    )
    def test_a_version_it_does_not_recognise_is_refused(
        self, tmp_path: Path, reported: str
    ) -> None:
        # `uv version --short` is the contract; the first two are what the other
        # shapes of `uv version` print. Tagging one would cut a release named
        # after the toolchain. The third is why the guard cannot check only the
        # first character: it leads with a digit and would be tagged verbatim,
        # spaces and all.
        result, calls = self._run_release(tmp_path, version=reported)
        assert result.returncode != 0
        assert not self._released(calls)

    def test_an_unreadable_version_is_refused(self, tmp_path: Path) -> None:
        result, calls = self._run_release(tmp_path, uv_exit=1)
        assert result.returncode != 0
        assert not self._released(calls)

    @pytest.mark.parametrize("tool", ["gh", "uv"])
    def test_a_missing_tool_is_named(self, tmp_path: Path, tool: str) -> None:
        # Neither is a project dependency — both are ambient on the runner — so
        # the failure has to name the tool rather than surface as a mis-attributed
        # "could not read a version".
        # Asserting merely that the name appears is too weak: without the guard,
        # `sh` says "gh: not found" on its own and the probe then refuses as
        # inconclusive, so the tool name reaches stderr either way. This pins the
        # script's own diagnosis.
        result, calls = self._run_release(tmp_path, omit=tool)
        assert result.returncode != 0
        assert f"{tool} is required" in result.stderr
        assert not self._released(calls)

    def test_an_unresolvable_commit_is_refused(self, tmp_path: Path) -> None:
        # Outside Actions there is no GITHUB_SHA, and the HEAD fallback has
        # nothing to resolve in a repo without commits. `gh release create` reads
        # an empty --target as "use the default branch", so an unguarded fallback
        # would tag a commit nobody chose.
        result, calls = self._run_release(tmp_path, sha=None)
        assert result.returncode != 0
        assert not self._released(calls)

    def test_a_hand_run_off_main_is_refused(self, tmp_path: Path) -> None:
        # Outside Actions nothing confines this to main — the workflow's `if:` is
        # not in play and HEAD is whatever branch the caller is on. A hand-run on
        # a feature branch would otherwise publish a public tag pointing at an
        # unreviewed commit, which is precisely what the workflow guard prevents
        # for CI.
        result, calls = self._run_release(tmp_path, actions=False)
        assert result.returncode != 0
        assert "origin/main" in result.stderr
        assert not self._released(calls)


class TestHooksConfig:
    """Claude Code hooks ship in .claude/settings.json and scripts/gate.sh.

    The Stop gate fails the same way the theme config and CI do — silently. It
    spent its whole lifetime chaining the four gates with `&&` and letting the
    exit code fall through, but Claude Code treats *only* exit 2 as blocking
    (ruff, ty and pytest all exit 1, which is a non-blocking error), so a red
    gate printed a message and the turn ended anyway. Nothing caught that,
    because nothing asserted on it. These pin the invariants that keep it a real
    gate rather than a notification.
    """

    ROOT = Path(__file__).parent.parent
    SETTINGS = ROOT / ".claude" / "settings.json"
    GATE = ROOT / "scripts" / "gate.sh"
    _stub_bin: Path

    @pytest.fixture(autouse=True)
    def _use_stub_bin(self, stub_bin: Path) -> None:
        self._stub_bin = stub_bin

    def _settings(self) -> dict:
        return json.loads(self.SETTINGS.read_text(encoding="utf-8"))

    def _commands(self, event: str) -> str:
        return "\n".join(
            hook["command"]
            for matcher in self._settings()["hooks"][event]
            for hook in matcher["hooks"]
        )

    def _gate(self) -> str:
        return self.GATE.read_text(encoding="utf-8")

    def test_settings_exists_and_parses(self) -> None:
        assert self.SETTINGS.is_file()
        self._settings()  # raises JSONDecodeError on a syntax error

    def test_gate_script_is_executable(self) -> None:
        # CI and the Stop hook both invoke it directly rather than through `sh`,
        # so a dropped +x bit breaks both callers at once.
        assert self.GATE.is_file()
        assert os.access(self.GATE, os.X_OK), "scripts/gate.sh is not executable"

    def test_gate_script_runs_all_four_documented_gates(self) -> None:
        gate = self._gate()
        for command in (
            "uv run ruff check .",
            "uv run ruff format --check .",
            "uv run ty check",
            "uv run pytest",
        ):
            assert command in gate, f"missing gate: {command!r}"

    def _run_gate(
        self,
        tmp_path: Path,
        uv_exit: int,
        uv_output: str = "DIAGNOSTIC",
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run gate.sh in a throwaway repo with `uv` stubbed to a fixed outcome.

        Asserting on the script's *source* is too weak to be worth much: `exit 2`
        and `>&2` both appear in its unrelated `cd`-failure branch, so a substring
        check stays green even when the failure path itself is mutated to exit 1
        or to drop the redirection (verified — both mutations passed a substring
        assertion). Executing it is the only way these invariants can actually
        fail. The stub also keeps the test hermetic and instant: the real gate
        would run the suite recursively.

        Idempotent, so the memo cases below can call it twice against the same
        throwaway repo and watch the second run decide whether to skip.
        """
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        scripts = tmp_path / "scripts"
        scripts.mkdir(exist_ok=True)
        # Symlinked rather than copied, and the stub is shared — both to avoid
        # macOS re-scanning a newly created executable. See `stub_bin`.
        gate = scripts / "gate.sh"
        if not gate.exists():
            gate.symlink_to(self.GATE)

        bin_dir = tmp_path / "bin"
        _link_stubs(bin_dir, self._stub_bin, "uv")

        return subprocess.run(
            [str(gate)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,  # a non-zero exit is the thing under test
            env={
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "STUB_LOG": str(tmp_path / "calls.log"),
                "STUB_UV_OUTPUT": uv_output,
                "STUB_UV_EXIT": str(uv_exit),
                # The real environment may carry either of these; the memo cases
                # set them deliberately, so neutralise them for everyone else.
                "GITHUB_ACTIONS": "",
                "GATE_FORCE": "",
                **(extra_env or {}),
            },
        )

    def test_gate_blocks_with_exit_2(self, tmp_path: Path) -> None:
        # 1 is the conventional Unix failure code, and the one every tool the gate
        # runs actually returns — but Claude Code reads it as a non-blocking error
        # and lets the turn end on a red gate. Only 2 blocks.
        assert self._run_gate(tmp_path, uv_exit=1).returncode == 2

    def test_gate_reports_failures_on_stderr(self, tmp_path: Path) -> None:
        # stderr is the stream fed back on a block, while ruff and pytest print
        # their diagnostics to stdout; an unredirected failure blocks with no
        # explanation attached.
        result = self._run_gate(tmp_path, uv_exit=1, uv_output="RUFF-SAYS-NO")
        assert "RUFF-SAYS-NO" in result.stderr
        assert "RUFF-SAYS-NO" not in result.stdout

    def test_gate_names_every_failing_gate(self, tmp_path: Path) -> None:
        # No short-circuit: one block should report everything that is wrong,
        # since Claude Code allows only 8 consecutive blocks to fix it all.
        stderr = self._run_gate(tmp_path, uv_exit=1).stderr
        for name in ("ruff check", "ruff format --check", "ty check", "pytest"):
            assert name in stderr, f"failure not attributed to {name!r}"

    def test_gate_passes_when_every_gate_passes(self, tmp_path: Path) -> None:
        assert self._run_gate(tmp_path, uv_exit=0).returncode == 0

    def test_gate_explains_a_silent_death(self, tmp_path: Path) -> None:
        # A gate killed without writing anything — OOM, hook timeout, a
        # half-created .venv — must not block with a blank message, which is the
        # "blocked with no reason" failure the exit-2 change exists to remove.
        result = self._run_gate(tmp_path, uv_exit=137, uv_output="")
        assert result.returncode == 2
        assert "no output" in result.stderr

    def test_gate_skips_a_tree_that_already_passed(self, tmp_path: Path) -> None:
        # The memo's whole purpose: a turn that changed nothing costs ~0.09s
        # instead of the full gate. It is the only skip the gate permits.
        first = self._run_gate(tmp_path, uv_exit=0)
        assert "all passed" in first.stdout
        second = self._run_gate(tmp_path, uv_exit=0)
        assert second.returncode == 0
        assert "skipped" in second.stdout

    def test_gate_reruns_when_a_source_file_changes(self, tmp_path: Path) -> None:
        self._run_gate(tmp_path, uv_exit=0)
        (tmp_path / "added.py").write_text("x = 1\n", encoding="utf-8")
        result = self._run_gate(tmp_path, uv_exit=0)
        assert "skipped" not in result.stdout, "skipped a tree that had changed"
        assert "all passed" in result.stdout

    def test_gate_records_nothing_on_a_red_run(self, tmp_path: Path) -> None:
        # Memoizing a failure would reproduce the `stop_hook_active` bug exactly:
        # block once, then wave the still-broken tree through on the next Stop.
        assert self._run_gate(tmp_path, uv_exit=1).returncode == 2
        second = self._run_gate(tmp_path, uv_exit=1)
        assert second.returncode == 2, "a red gate was memoized as passing"
        assert "skipped" not in second.stdout

    @pytest.mark.parametrize("override", ["GITHUB_ACTIONS", "GATE_FORCE"])
    def test_gate_never_skips_when_overridden(
        self, tmp_path: Path, override: str
    ) -> None:
        # CI must always run the real thing. A fresh checkout has no memo, but
        # caching the workspace would restore one, so this is explicit rather
        # than inherited from how the runner happens to be configured.
        self._run_gate(tmp_path, uv_exit=0)
        result = self._run_gate(tmp_path, uv_exit=0, extra_env={override: "1"})
        assert "skipped" not in result.stdout, f"{override} did not force a run"
        assert "all passed" in result.stdout

    def test_gate_runs_when_the_fingerprint_is_uncomputable(
        self, tmp_path: Path
    ) -> None:
        # Hashing an empty file list yields e3b0c442… — stable and *non-empty* —
        # so letting it stand in for an answer would let it match a stored memo
        # and disable the gate silently. An unlistable tree is a refusal.
        self._run_gate(tmp_path, uv_exit=0)
        (tmp_path / ".gitignore").write_text("*\n", encoding="utf-8")
        memo = tmp_path / ".git" / "gate-ok"
        memo.write_text(hashlib.sha256(b"").hexdigest() + "\n", encoding="utf-8")
        result = self._run_gate(tmp_path, uv_exit=0)
        assert "skipped" not in result.stdout, "the empty-input hash was trusted"
        assert "all passed" in result.stdout

    def test_stop_hook_delegates_to_the_gate_script(self) -> None:
        # Inlining the chain back into settings.json would put it beyond the reach
        # of every assertion above: a JSON-escaped one-liner cannot be run, linted
        # or shellchecked.
        assert "scripts/gate.sh" in self._commands("Stop")

    def test_stop_hook_runs_unconditionally(self) -> None:
        # Two short-circuits were tried and both lost more than they saved. A
        # `git status --porcelain` check skipped exactly the turns that had just
        # committed work (committing clears dirtiness without clearing risk), and
        # it failed open — a held index.lock silently disabled the gate. A
        # `stop_hook_active` check made the gate one-shot: it blocked once, then
        # let the next Stop through without re-running anything, so it never
        # verified its own fix. Claude Code already caps a Stop hook at 8
        # consecutive blocks, so neither guard was needed.
        stop = self._commands("Stop")
        assert "git status" not in stop
        assert "stop_hook_active" not in stop

    def test_format_hook_covers_python_and_markdown(self) -> None:
        # ruff >= 0.16 formats python fences inside Markdown and CI's
        # `ruff format --check .` reaches CLAUDE.md, so .md is in scope too. The
        # exit-2 contract both extensions owe is asserted behaviorally below,
        # not as a substring: the hook spells its failure exit as `rc=2` in two
        # branches and `exit $rc` once, so no `exit 2` literal survives to match.
        post = self._commands("PostToolUse")
        assert "*.py" in post
        assert "*.md" in post

    def _run_format_hook(
        self, tmp_path: Path, file_name: str, uv_exit: int = 0
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        """Run the PostToolUse command with `uv` stubbed to a logging dispatcher.

        Source assertions cannot see *order*, and order is the whole invariant:
        both `ruff check --fix` and `ruff format` appear in the command under
        either arrangement, so only executing it distinguishes them. Logging
        argv is what makes `check < format` assertable at all. The stub also
        keeps this hermetic — the real hook shells out to uv twice per case.
        """
        bin_dir = tmp_path / "bin"
        _link_stubs(bin_dir, self._stub_bin, "uv")
        log = tmp_path / "uv.log"

        result = subprocess.run(
            ["sh", "-c", self._commands("PostToolUse")],
            input=json.dumps({"tool_input": {"file_path": str(tmp_path / file_name)}}),
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,  # a non-zero exit is the thing under test
            env={
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "STUB_LOG": str(log),
                "STUB_UV_OUTPUT": "",
                "STUB_UV_EXIT": str(uv_exit),
            },
        )
        calls = log.read_text(encoding="utf-8").splitlines() if log.is_file() else []
        return result, calls

    def test_format_hook_lints_before_formatting(self, tmp_path: Path) -> None:
        # ruff's own documented order, and it is load-bearing rather than
        # stylistic: `ruff check --fix` can *produce* unformatted code — deleting
        # a module's last import strands the two blank lines that followed it —
        # and the formatter is what removes them. Format first and that fix lands
        # after the last pass that would have tidied it, so the Stop gate fails
        # `ruff format --check` on a file this hook just exited 0 on. Verified
        # against ruff 0.16.2 and again 0.16.8 (2026-09-21): format-then-fix
        # leaves "\n\ndef f():".
        _, calls = self._run_format_hook(tmp_path, "mod.py")
        assert len(calls) == 2, f"expected lint then format, got {calls}"
        assert calls[0].startswith("uv run ruff check --fix "), calls
        assert calls[1].startswith("uv run ruff format "), calls

    def test_format_hook_formats_even_when_lint_fails(self, tmp_path: Path) -> None:
        # Putting the linter first must not let an unfixable lint error skip the
        # formatter: that would block *and* leave the file badly formatted, so
        # the Stop gate reports a second failure this hook could have removed.
        # Same rule gate.sh follows — one report naming everything that is wrong.
        result, calls = self._run_format_hook(tmp_path, "mod.py", uv_exit=1)
        assert len(calls) == 2, f"formatter skipped after a lint failure: {calls}"
        assert result.returncode == 2
        assert "ruff check:" in result.stderr
        assert "ruff format:" in result.stderr

    def test_format_hook_lints_python_only(self, tmp_path: Path) -> None:
        # `ruff check` on Markdown reports "No Python files found" and exits 0;
        # formatting is the half that reaches doc snippets.
        _, calls = self._run_format_hook(tmp_path, "doc.md")
        assert len(calls) == 1, f"expected format only, got {calls}"
        assert calls[0].startswith("uv run ruff format "), calls

    def test_format_hook_blocks_on_a_markdown_failure(self, tmp_path: Path) -> None:
        # The .md branch owes the same exit-2 contract .py gets: PostToolUse
        # cannot block the edit, so 2-with-stderr is the only way the diagnostic
        # reaches Claude at all (ruff writes to stdout, and exit 1 is discarded).
        result, _ = self._run_format_hook(tmp_path, "doc.md", uv_exit=1)
        assert result.returncode == 2
        assert "ruff format:" in result.stderr

    def test_format_hook_ignores_other_extensions(self, tmp_path: Path) -> None:
        result, calls = self._run_format_hook(tmp_path, "data.json")
        assert calls == [], f"uv invoked for an unformattable file: {calls}"
        assert result.returncode == 0

    def test_secret_guard_covers_env_lockfile_and_secrets(self) -> None:
        # An accident guardrail, not a security boundary: it matches Edit/Write/
        # MultiEdit only, never Bash. The .env.example exemption keeps the
        # committed template editable.
        pre = self._commands("PreToolUse")
        for pattern in ("*/.env", "*/secrets.toml", "*/uv.lock"):
            assert pattern in pre, f"unguarded path: {pattern}"
        assert "*/.env.example" in pre
        assert "exit 2" in pre

    def _run_secret_guard(self, path: str) -> subprocess.CompletedProcess[str]:
        """Run the PreToolUse guard against a real payload.

        The patterns are a shell `case`, and a substring assertion cannot see
        which *forms* of a path a pattern covers: `*/secrets.toml` and a bare
        `secrets.toml` read alike and behave differently — the guard shipped
        matching only the former, so a relative `secrets.toml` passed. Only
        executing it distinguishes them. It runs `jq` and echoes, touching
        neither filesystem nor network, so it needs no stubbing.
        """
        hooks = [
            hook["command"]
            for matcher in self._settings()["hooks"]["PreToolUse"]
            for hook in matcher["hooks"]
        ]
        assert len(hooks) == 1, (
            "PreToolUse holds more than the secret guard; running them as one "
            "script would hide the guard's exit status behind the last command"
        )
        return subprocess.run(
            ["sh", "-c", hooks[0]],
            input=json.dumps({"tool_input": {"file_path": path}}),
            capture_output=True,
            text=True,
            check=False,
        )

    @pytest.mark.parametrize(
        "path, blocked",
        [
            pytest.param(".env", True, id="env-bare"),
            pytest.param("/repo/.env", True, id="env-nested"),
            pytest.param("/repo/.env.local", True, id="env-suffixed-nested"),
            pytest.param(".env.local", True, id="env-suffixed-bare"),
            pytest.param("secrets.toml", True, id="secrets-bare"),
            pytest.param(".streamlit/secrets.toml", True, id="secrets-nested"),
            pytest.param("uv.lock", True, id="lock-bare"),
            pytest.param("/repo/uv.lock", True, id="lock-nested"),
            pytest.param(".env.example", False, id="example-bare-exempt"),
            pytest.param("/repo/.env.example", False, id="example-nested-exempt"),
            pytest.param("streamlit_app.py", False, id="ordinary-file"),
        ],
    )
    def test_secret_guard_blocks_every_form_of_a_guarded_path(
        self, path: str, blocked: bool
    ) -> None:
        # Both the bare and nested form of each guarded name, because they are
        # separate `case` patterns: uv.lock carried both from the start while
        # secrets.toml carried only the nested one, and no static check could
        # tell. Exit 2 is the contract — Claude Code blocks on 2 alone.
        result = self._run_secret_guard(path)

        if blocked:
            assert result.returncode == 2, f"unguarded path: {path}"
            assert "Blocked:" in result.stderr
        else:
            assert result.returncode == 0, f"wrongly blocked: {path}"
            assert result.stderr == ""


class TestGitignore:
    """.gitignore configures git, ruff and gate.sh's memo, and fails silently in all three.

    An unignored output folder puts model-generated `chart-NNN.py` under the ruff
    gates and into the memo's fingerprint, which then never matches; an unignored
    weights folder is a 5 GB commit; and an over-broad pattern quietly hides the
    committed eval corpus (plan §5, §8). `--no-index` evaluates the patterns alone,
    so none of these paths needs to exist.
    """

    ROOT = Path(__file__).parent.parent

    @pytest.mark.parametrize(
        "path, ignored",
        [
            pytest.param("out/x/chart-001.py", True, id="pipeline-output"),
            pytest.param("models/a.safetensors", True, id="model-weights"),
            pytest.param("report.pdf", True, id="root-pdf"),
            pytest.param("eval/private/x.pdf", True, id="private-corpus"),
            pytest.param("eval/runs/x/run.json", True, id="eval-runs"),
            pytest.param("debug_report/x", True, id="docling-debug"),
            pytest.param("eval/corpus/x.pdf", False, id="public-corpus"),
            pytest.param("eval/expected/x/chart-001.csv", False, id="expected"),
            pytest.param("eval/results/x.json", False, id="eval-results"),
            pytest.param(".env.example", False, id="env-example"),
            pytest.param("docs/plans/pdf-pipeline.md", False, id="plan"),
        ],
    )
    def test_path_is_ignored_or_not(self, path: str, ignored: bool) -> None:
        # check-ignore exits 0 when the path is ignored, 1 when it is not, and
        # anything else on an error — which must fail rather than read as "not".
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", path],
            cwd=self.ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode in (0, 1), result.stderr
        assert (result.returncode == 0) is ignored, (
            f"{path} should {'' if ignored else 'not '}be ignored"
        )


class TestPackaging:
    """pyproject.toml metadata that no build step checks today."""

    ROOT = Path(__file__).parent.parent

    def test_every_license_file_exists(self) -> None:
        # A license-files entry naming a missing file fails only at `uv build`,
        # which nothing runs until packaging lands (plan §9.1 rule 1).
        with (self.ROOT / "pyproject.toml").open("rb") as f:
            entries = tomllib.load(f)["project"]["license-files"]
        missing = [e for e in entries if not (self.ROOT / e).is_file()]
        assert not missing, f"license-files entries with no file: {missing}"

    def test_the_console_script_points_at_the_cli(self) -> None:
        with (self.ROOT / "pyproject.toml").open("rb") as f:
            project = tomllib.load(f)["project"]
        assert project["scripts"] == {"gdi": "granite_document_intelligence.cli:main"}


class TestImportCost:
    """The gate stays fast (plan §3.14 rule 9): ≤ 15 s, run on every Stop.

    Importing docling or mlx-vlm costs seconds and brings torch with it, so no
    test module may do it at module level — only inside the test that needs it —
    and no library module may either, so `gdi --help` stays instant.
    """

    ROOT = Path(__file__).parent.parent
    HEAVY = frozenset({"docling", "torch", "mlx_vlm", "mlx", "transformers"})

    def _top_level_imports(self, path: Path) -> set[str]:
        import ast

        names: set[str] = set()
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.add(node.module.split(".")[0])
        return names

    @pytest.mark.parametrize(
        "folder", ["tests", "src/granite_document_intelligence", "eval"]
    )
    def test_no_module_imports_a_heavy_library_at_top_level(self, folder: str) -> None:
        offenders = {
            str(path.relative_to(self.ROOT)): sorted(
                self._top_level_imports(path) & self.HEAVY
            )
            for path in sorted((self.ROOT / folder).glob("*.py"))
        }
        offenders = {k: v for k, v in offenders.items() if v}
        assert not offenders, f"move these imports inside the function: {offenders}"
